# ExchangeLab: interview notes

## 1. What it is

A Python limit-order-book exchange (price-time FIFO, IOC, post-only, self-trade prevention) that I verified against a
slow, independent oracle on 1.6 million random commands. On top of it sits a microsecond discrete-event market with a
market maker, noise, momentum and queue-joining traders, and a competitor arbitrageur. I used it to measure what
latency actually costs a trader who races for stale quotes or tries to cancel them in time.

## 2. How it works: key decisions and why

- **Integer ticks and integer microseconds.** No floating-point prices, so equality and priority are exact and runs are bit-reproducible.
- **Book layout.** price → level dict, a lazy heap for best price, and a deque per level. A cancel marks the order dead
  (O(1)) and the matcher skips dead heads. Why: the original OrderedDict design was fine, but profiling showed the real
  costs were per-message Python overhead (frozen dataclass construction, a generator in `valid()`), not the data structure.
- **Correctness first: a differential oracle.** `reference.py` re-derives every decision from a flat list scan. I feed
  both engines identical streams and compare *every* response, plus the full book every 1,000 commands. I trust this
  more than hand-written unit tests because it explores millions of interleavings I'd never think of.
- **Frozen baseline.** `book_v0.py` is the original engine copied verbatim, with its own message types, so the speedup
  isn't contaminated by my changes to shared code. Runs are interleaved v0/fast to spread background load evenly.
- **STP semantics.** `cancel_resting` drops my own resting order and keeps matching. `cancel_taker` stops at my first
  own order and kills the incoming residual. A rejected post-only order still consumes its ID, as on real venues.
- **Event loop.** A heap of (time, sequence) gives deterministic tie-breaking. Every message leg (signal, market data,
  order, fill report) has the agent's one-way latency plus exponential jitter. Causality is enforced by the scheduler
  and tested: an order can't arrive sooner than decision + latency.
- **Experiment design: common random numbers.** Each agent gets its own spawned RNG stream, so changing the probe's
  latency doesn't change the fundamental path or anyone else's flow. That lets me report *paired* differences per seed,
  with much tighter intervals than comparing two independent means.
- **Markouts against a hidden fundamental** rather than the mid, because the mid is itself moved by the agents I'm measuring.

## 3. Key results (all measured, see RESULTS.md)

- Fast engine vs oracle: 1,000,000 original commands plus 3 × 200,000 commands with IOC/post-only under each STP mode.
  **0 mismatches** out of 1.6M.
- 1M-command replay: original **2.336 s** → optimised **1.848 s** median (1.26x). 5 interleaved runs each, spread under
  1%, load ~2.2. The original 2.37 s claim reproduces as 2.336 s.
- Taker probe vs a 50 µs competitor, 30 seeds: PnL flat up to 30 µs. At 50 µs it's −22% (paired −300 ± 36 ticks), and
  at 70 µs −44% (−594 ± 44). Past ~500 µs the market maker's own cancel wins: −80%.
- Same race vs a 500 µs competitor: going from 0 to 100 µs changes PnL by −6.5 ± 55.9 ticks. Not detectable.
- Passive probe, 0 → 75 µs: lots picked off by arbs 3.4 → 25.3 per run; markout/lot 2.36 → 1.74 ticks; fill fraction 0.55 → 0.61.
- Inventory skew cuts the MM's mean |inventory| from 60.1 ± 11.0 to 15.2 ± 1.4 lots.

**What these do NOT show:** anything about real markets or real profits. The agents are my own simple rules, and the
PnL magnitudes are artefacts of my parameters. The robust result is qualitative: the cost of latency is a cliff at the
competitor's latency. Python speed (~541k commands/s) is not competitive with production engines. The oracle match is
strong evidence, not a proof.

## 4. STAR answer (~2 minutes)

**Situation.** I kept reading that trading firms spend huge amounts to shave microseconds, and I didn't really get
why tens of microseconds could matter. I'd already built a basic matching engine with AI
pair-programming help, but its latency experiment was coarse: four delay settings in milliseconds, with no error bars.

**Task.** I wanted to answer a specific question honestly: does being 50 µs slower cost anything, and when? That meant
first making sure the engine was trustworthy, then building a market where speed could actually matter, and measuring with real confidence intervals.

**Action.** I added IOC, post-only and self-trade prevention to the fast engine and, independently, to a slow
list-scan oracle. Then I ran 1.6 million random commands through both and compared every response. I profiled the
engine, kept the original as a frozen baseline, and got a measured 1.26x speedup. For the experiment I built a
discrete-event market: a market maker with inventory skew, noise and momentum traders, a competitor arb at 50 µs, and
my probe at a latency I swept from 0 to 2,000 µs over 30 seeds. I gave each agent its own random stream so I could pair
runs across latencies. I used AI help heavily for scaffolding and reviewing code, and I made the modelling calls and
checked the outputs myself.

**Result.** Zero mismatches on 1.6M commands. The latency curve was a cliff, not a slope. Nothing happened until my
probe got slower than the competitor, then going from 20 to 70 µs cut its PnL by 44%, with a paired 95% interval of
about ±3 percentage points. Against a 500 µs competitor the same slowdown cost nothing measurable. My takeaway: latency
is relative. It matters only versus whoever you're racing, which is why it turns into an arms race.

## 5. Likely follow-ups

- **Why trust the oracle if you wrote both?** They share only the message types. The oracle re-derives priority by
  brute-force scanning with no indexes, so the bug classes differ. I also hand-injected three bugs into `book.py` (matching the wrong end
  of a FIFO queue, an off-by-one limit check, letting IOC residuals rest). The differential test caught each within
  342 commands; the IOC bug was caught only by the v2 workload, which is why it exists. It's still not a formal proof.
- **Why only 1.26x faster?** It's pure Python and the timed loop includes shared bookkeeping. The next real step would
  be a C++/Rust core or numba, but I'd rather report a modest honest number.
- **Why does the slow passive probe get *more* fills?** Adverse selection. When it's slow to cancel a stale quote, an
  arb hits it. Those extra fills are exactly the bad ones, so markout per lot falls.
- **Why paired differences?** The same seed gives the same fundamental and flow at every latency, so seed-to-seed noise
  cancels. The paired CI at 70 µs (±44) is tighter than either mean's CI (±53 to ±75).
- **What's the role of jitter?** Without it, the result would be a perfect step at exactly 50 µs. With 10 µs exponential
  jitter per leg, races between 40 and 60 µs become probabilistic, which is why the drop is spread out.
- **What would make it more realistic?** Multiple symbols with a lead-lag signal instead of an oracle signal, fees and
  rebates, more than one competitor with a latency distribution, and calibration to real tick data.
- **How did you pick the parameters?** By hand, aiming for plausible orders of magnitude (MM ~400 µs, competitor 50 µs).
  I'd present the shape, not the numbers.
- **What was the queue-position finding?** Mean queue ahead on arrival actually *fell* with latency (5.9 → 4.2 lots).
  My hypothesis is that slow orders land on stale, thinning levels. I haven't proven it.

## 6. Explain-cold checklist

- [ ] Price-time priority, and why trades print at the resting price
- [ ] IOC vs GTC vs post-only; the two STP modes and when each is used
- [ ] Lazy cancellation: why it's O(1) and why `check()` still verifies FIFO
- [ ] How the differential test works and what "0 mismatches" covers
- [ ] Why v0 is self-contained and why the runs are interleaved
- [ ] Discrete-event loop: (time, sequence) ordering and causality
- [ ] What one-way latency applies to (all four legs) and where the jitter comes from
- [ ] Common random numbers, paired differences and t-based CIs over 30 seeds
- [ ] Markout definition: side × (V(t+h) − fill price), against the hidden fundamental
- [ ] The three regimes of the taker curve (faster than competitor / second place / slower than the MM's cancel)
- [ ] Adverse selection in the passive results
- [ ] What the project does *not* show
