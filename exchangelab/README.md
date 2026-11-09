# ExchangeLab

> **Does being 50 microseconds slower actually cost you anything?**

I kept reading that HFT firms pay a fortune for microseconds, and I wanted to see *why* with my own eyes. So I built
a toy exchange, made sure its matching was actually correct, then put robots on it and made one of them slower.

Short answer from my sim: **it depends entirely on who you're racing.** Against a 50 µs competitor, going from
20 µs to 70 µs one-way cut the latency-arb probe's PnL by **44%**. Against a 500 µs competitor, being 100 µs slower
cost nothing I could detect.

(Built with AI pair-programming help. All numbers below come from [`../results/`](../results/); see [RESULTS.md](../RESULTS.md).)

## What's inside

```
                 signal feed (fundamental jumps)          one-way latency L on every leg
                        │                                 + exponential jitter (mean 10 µs)
       ┌────────────────┼──────────────────────┬───────────────────────┐
       ▼                ▼                      ▼                       ▼
  Market maker     Competitor arb          Probe (you)           Momentum / noise /
  400 µs, skews    50 µs, IOC on           L swept 0–2000 µs     queue joiners
  by inventory     stale quotes            taker or passive
       │                │                      │                       │
       └────── orders ──┴──────────┬───────────┴───────────────────────┘
                                   ▼
                     ┌──────────────────────────────┐
                     │ Exchange (book.py)           │ price-time FIFO, IOC, post-only,
                     │ discrete-event loop (µs)     │ self-trade prevention, queue position
                     └──────────────────────────────┘
                                   │ trades + quotes, delayed back to each agent
```

- **`book.py`** is the fast matching engine. Each price level is a deque with *lazy cancels*: a cancelled order is
  marked dead in O(1) and skipped when it reaches the front. A lazy heap tracks the best price. It supports GTC/IOC,
  post-only, three self-trade-prevention modes (`none`, `cancel_resting`, `cancel_taker`) and `queue_ahead(oid)`.
- **`reference.py`** is a deliberately dumb oracle that re-derives every decision by scanning a flat list.
- **`book_v0.py`** is the original engine, frozen verbatim (including its own `Trade`/`valid()`) so the speed comparison is fair.
- **`market.py`** is the agent world: a hidden fundamental that jumps ±1–5 ticks about 40 times/s; a market maker quoting
  ±2 ticks with inventory skew that keeps queue priority unless its price changes; noise takers; a momentum trader
  reading a delayed tape; slow "queue joiners" who build depth; a competitor arb; and the probe.
- **`latency_study.py`** sweeps probe latency × 30 seeds. Every agent has its own RNG stream, so the same seed gives the
  same fundamental path and order flow at every latency (common random numbers). That lets me report **paired**
  differences, which have much tighter intervals.

## Results

### 1. Is the engine right?

| Check | Commands | Mismatches |
| --- | ---: | ---: |
| Original workload (limit/market/cancel), 10 symbols | 1,000,000 | 0 |
| New workload with IOC + post-only, STP off | 200,000 | 0 |
| ... STP `cancel_resting` (13,651 STP cancels) | 200,000 | 0 |
| ... STP `cancel_taker` (13,501 STP cancels) | 200,000 | 0 |

Every response is compared: accept/reject reason, each trade, residual and STP cancels. The full book state is compared
every 1,000 commands. Invariants (FIFO, no crossed book, cash and position conservation) are checked along the way.

### 2. Is it fast?

1,000,000 commands, 5 interleaved runs each after warm-up, load average ~2.2:

| Engine | Median | Range |
| --- | ---: | ---: |
| original (`book_v0.py`) | 2.336 s | 2.322–2.339 s |
| optimised (`book.py`) | **1.848 s** | 1.844–1.850 s |

That's 1.26x. A modest gain, honestly: it's still pure Python, and the timed loop includes per-command bookkeeping
shared by both engines. The wins came from lazy O(1) cancels, caching the best price inside the match loop, and
replacing the frozen-dataclass `Trade` and generator-based `valid()`, which profiling showed were surprisingly hot.

### 3. The latency race (taker probe, competitor at 50 µs)

PnL is marked to the hidden fundamental 1 ms after each fill, in ticks (1 tick ≈ 1 bp). Values are 30 seeds, ±95% CI.

```
PnL/run @1ms (ticks)
1343 ┤■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■  0–30 µs   (you're faster than the competitor)
1253 ┤■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■     40 µs     (jitter: sometimes you lose)
1043 ┤■■■■■■■■■■■■■■■■■■■■■■■■■■          50 µs     (coin flip)
 749 ┤■■■■■■■■■■■■■■■■■■■                 70–300 µs (2nd place: you get the leftovers)
 269 ┤■■■■■■■                             500 µs    (the market maker's cancel beats you too)
 199 ┤■■■■■                               2000 µs
```

| Probe latency | Lots / run | PnL / run | Paired change vs 0 µs |
| ---: | ---: | ---: | ---: |
| 20 µs | 448.6 ± 14.0 | 1343.2 ± 72.5 | +0.1 ± 28.6 |
| 40 µs | 420.1 ± 12.9 | 1252.8 ± 71.4 | −90.3 ± 38.0 |
| 50 µs | 347.8 ± 9.2 | 1042.9 ± 62.0 | −300.2 ± 36.0 |
| 70 µs | 256.9 ± 11.5 | 749.4 ± 52.7 | −593.7 ± 43.8 |
| 500 µs | 90.5 ± 6.8 | 269.0 ± 32.3 | −1074.1 ± 60.5 |

Control: with the competitor at 500 µs, probe PnL at 0/50/100 µs was 1295/1337/1289 ticks, and the paired change at
100 µs was −6.5 ± 55.9. No detectable cost.

### 4. Being slow as a *maker* (passive probe)

The passive probe rests 1-lot quotes at the touch and tries to cancel the stale side when the fundamental jumps.

| Latency | Lots picked off by arbs / run | Markout / lot @1ms | Fraction of rested orders filled |
| ---: | ---: | ---: | ---: |
| 0 µs | 3.4 ± 0.6 | 2.36 ± 0.11 | 0.549 |
| 50 µs | 14.2 ± 1.4 | 2.03 ± 0.09 | 0.581 |
| 75 µs | 25.3 ± 2.1 | 1.74 ± 0.09 | 0.612 |
| 2000 µs | 23.1 ± 1.6 | 1.59 ± 0.10 | 0.644 |

### 5. Inventory skew works

With the probe off, the market maker's mean |inventory| was **60.1 ± 11.0 lots without skew vs 15.2 ± 1.4 with 0.15 ticks/lot skew**.
PnL was also less volatile: 4394 ± 1271 vs 5289 ± 346 ticks.

## What surprised me

- **The cost of latency is a cliff, not a slope.** Nothing happens until you cross the competitor's latency, then
  roughly half the PnL vanishes inside ~30 µs. A second cliff comes when the market maker's own cancel beats you.
- **Slow traders win *fewer* races but not *worse* ones.** The taker's markout per lot barely moves (3.05 → 2.91
  ticks from 0 to 100 µs). The money is lost in quantity: you just don't get filled.
- **Slow makers get filled *more*.** That's adverse selection. When you're slow to cancel, the fills you get are the
  ones arbs wanted, so fill rate goes up while markout goes down.
- **The queue-position number was backwards from what I expected.** Average queue-ahead on arrival *falls* with
  latency (5.9 → 4.2 lots). My guess is that slow orders more often land on stale, thinning levels. I haven't fully proven that.
- The `frozen=True` dataclass for `Trade` was one of the most expensive lines in the original engine.

## Break it yourself

```sh
exchangelab market --latency 45 --mode taker --seed 3 --output /tmp/m.json   # one run, µs latency
exchangelab market --latency 75 --mode passive --output /tmp/p.json
exchangelab latency-study --seeds 10 --output /tmp/curve.json                 # ~15 s
exchangelab validate --count 50000 --mix v2 --stp cancel_resting --output /tmp/v.json
exchangelab benchmark --count 200000 --repetitions 3 --no-quiet-wait --output /tmp/b.json
```

Things to try: set `competitor_latency_us=10` or `jitter_us=0` in `MarketConfig` (the cliff becomes a perfect step),
`inventory_skew=0`, `mm_latency_us=50` (stale quotes barely exist), or break `book.py` on purpose and watch the oracle catch it. When I tried
three bugs (matching the wrong end of a level, an off-by-one limit check, IOC residuals resting), each was caught within
342 commands of `exchangelab validate`.

Original commands still work: `generate`, `replay`, `validate`, `benchmark`, `compare-latency`, `demo`.

## What this isn't

- Not a real exchange. One symbol in the agent market, no network stack, no auctions, no hidden or iceberg orders, no fees.
- The agents are simple rules I made up. The *shape* of the latency curve (the cliff at the competitor's latency) is
  the robust finding. The specific PnL numbers depend completely on my parameters (jump rate, MM latency, sizes) and say
  nothing about real profits.
- Markouts use a hidden "true value" that real traders never see.
- "Microseconds" are simulated clock units. Wall-clock speed of the Python engine (~541k commands/s) is a separate,
  much less impressive story than a C++ engine's.
- The oracle comparison shows the fast and slow engines agree on 1.6M random commands. That's strong evidence, not a proof.
