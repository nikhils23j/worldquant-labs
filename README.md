# ExchangeLab and RiskLab

Two local, reproducible Python projects: a synthetic exchange with latency experiments, and a Monte Carlo option-pricing laboratory. Generated implementation and validation by Codex on 23 October 2026 for Nikhil Jain. Neither the code nor benchmark results establish that Nikhil independently authored or reviewed every implementation detail. Read, run and understand the projects before describing personal ownership in an interview.

## Setup

Python 3.11+ is required; recorded runs used Python 3.12 on an Apple M2 with 8 GiB RAM.

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-lock.txt
python -m pip install -e '.[test]'
pytest -q
```

`requirements-lock.txt` records the versions used; create a new virtual environment when cloning this repository. No secrets, API keys, paid datasets or environment variables are required. `.env.example` documents the optional dashboard port setting. QuantLib is a test/reference dependency, not the pricing implementation.

## ExchangeLab

```sh
exchangelab demo --latency 0 --seed 1 --output results/demo-zero.json
exchangelab demo --latency 50 --seed 1 --output results/demo-delay.json
exchangelab generate --count 100000 --output results/workload.jsonl
exchangelab replay --input results/workload.jsonl --output results/replayed.json
exchangelab validate --count 100000 --output results/differential.json
exchangelab compare-latency --output results/latency.json
exchangelab benchmark --count 1000000 --repetitions 5 --output results/exchange-benchmark.json
streamlit run exchangelab/dashboard.py --server.address 127.0.0.1
```

Prices and quantities use integers. Display prices divide ticks by 100; matching never uses floating-point prices. `Command` supports limit, market and cancel messages, with simulated timestamps in microseconds. `Exchange.process()` returns acceptance, reason, trades and residual quantity. A market residual is cancelled; a limit residual rests. Equal-price orders use FIFO; trades occur at the resting price. Cancellations require matching owner and symbol. IDs are not reused after acceptance. Self-trades are allowed in this educational model. A new limit order is rejected before matching if 10,000 orders already rest, even if it might cross. Unknown cancels and other rejects remain part of command-throughput counts.

`book.py` implements indexed FIFO queues, price heaps and bounded stale-entry compaction. `reference.py` independently scans a list for price-time priority. `events.py` implements deterministic timestamp/sequence ordering. `agents.py` defines a maker, background trader and latency probe. `experiments.py` keeps seeded background commands fixed between paired latency runs; quotes delivered to the probe are delayed separately from its submitted orders.

Correctness tests compare every response/trade on the reference workload, periodically compare complete resting state, and check ledger conservation. The million-command stress run checks local positive fill quantities on every fill and complete book/ledger invariants every 1,000 commands and at the end. This is not a mathematical proof for all possible orders.

Benchmark scope: the main timed loop includes command dispatch, matching and incremental metrics; workload generation, GUI rendering and final serialization are excluded. End-to-end experiment time is also reported. Per-command p95 is measured separately because instrumentation changes timings. The RSS high-water mark includes the pre-generated workload and prior repetitions; it is not the incremental memory of the book alone. Live-order/price-level peaks are sampled every 1,000 commands. Initial book is empty. Generated mix is approximately 60% limit/20% market/20% cancellation, with actual counts in JSON. Some cancels reference filled orders and are intentionally rejected.

Latency reports define quantity fill rate as executed/submitted probe quantity. Execution cost is side-adjusted price difference from the midpoint observed at decision time, in basis points, quantity weighted. Waiting times start at arrival at the exchange. Unfilled orders are counted and excluded from fill-time/cost averages. A missed fill can lower the observed cost, so compare fill rate and cost together. Synthetic experiments do not establish live trading returns or exchange performance.

## RiskLab

```sh
risklab price --kind asian --method control --paths 100000 --output results/price.json
risklab price --kind european --method antithetic --put --output results/put.json
risklab greeks --kind european --output results/greeks.json
risklab sweep --output results/grid.json
risklab validate --output results/validation.json
risklab compare --output results/estimators.json
risklab benchmark --output results/risk-benchmark.json
streamlit run risklab/dashboard.py --server.address 127.0.0.1 --server.port 8502
```

Supported contracts: European, arithmetic-average Asian and geometric-average Asian calls/puts. The model is constant-parameter geometric Brownian motion under the pricing measure; it does not calibrate or make investment recommendations. Fixings are equally spaced future observations i*T/n, excluding time zero. Rates/dividends are continuously compounded, annual volatility is a decimal, and output is currency per option. Vega is per one percentage-point volatility change.

`paths.py` generates exact GBM transitions in float64 batches and retains current prices and running averages. `estimators.py` implements ordinary Monte Carlo, antithetic pairs and a geometric-Asian control for arithmetic-Asian payoffs. The control coefficient is fitted on an independent pilot. Antithetic pair averages, not individual correlated paths, are the independent units in the error calculation. Stable Welford aggregation avoids subtracting nearly equal second moments. Confidence intervals use the normal approximation and describe Monte Carlo sampling error only. Rare-tail/very small samples may have inadequate coverage. No clipping is applied to control estimates; a finite-sample estimate can be slightly negative even for a nonnegative true price.

Equal-budget comparisons include pilot paths: the 100,000-path control runs use 10,000 pilot and 90,000 main paths. Stress cases use 1,000,000 main plus 10,000 pilot paths. Reproducibility requires the same seed, method, parameters and batch size. Changing batch size changes how random draws map to trajectories. Common random numbers pair spot/volatility bumps; the Greeks implementation reports sampling uncertainty, while bump-size experiments examine discretization effects separately.

`analytics.py` supplies European and discrete geometric-Asian references. `validation.py` independently checks against QuantLib. To make all 252 equally spaced fixings exact with calendar-day dates, it rescales r/q by T/Tcalendar and volatility by sqrt(T/Tcalendar). Every fixing has the same GBM distribution and discount factor as the project convention. The wrapper deliberately only supports the recorded 0.5/1-year, 252-fixing reference grid. QuantLib's previous global evaluation date is restored after each call.

The CLI `--paths` argument is the main sample for a direct price command. Grid/comparison commands interpret it as the total matched budget and reserve 10,000 paths for a control pilot. Keep it above 10,001 and even for those experiments. The dashboard uses its own explicitly counted 1,000-path pilot. Memory benchmarks launch fresh CLI processes per run; peak RSS includes Python/loaded libraries but excludes the dashboard.

## Validation and artifacts

```sh
python scripts/evaluate.py
```

Runs the acceptance experiments sequentially to avoid concurrent benchmark contention. Results include the reference comparison, five exchange timings after warm-up, 20 latency configurations, 40 analytic QuantLib checks, four independent arithmetic-Asian comparisons, 100 interval-coverage replications, 60 grid evaluations, 60 matched-estimator evaluations and nine fresh-process million-path stress runs. See `RESULTS.md` and the committed JSON result files for measured outcomes. Run the commands above to produce fresh logs and test reports.

Tests include both CLI output and Streamlit AppTest interactions; these exercise controls and rendering code but are not a substitute for checking appearance in a real browser.

## References and attribution

- ABIDES market simulation background: https://github.com/abides-sim/abides
- QuantLib Asian option examples: https://github.com/lballabio/QuantLib/blob/master/Examples/AsianOption/AsianOption.cpp

These inspired problem scope and independent comparisons. The core implementations here are separate; no contribution to those upstream projects or university affiliation is claimed.
