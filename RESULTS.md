# Recorded results

Validated 23 October 2026. Generated implementation and executed experiments by Codex for Nikhil Jain. Synthetic workloads; no live market or production deployment claim. All numbers below are measured, not proposal targets.

52 tests passed, including CLI roundtrips and Streamlit controls/surface calculation. Both dashboards were also opened and visually inspected in a real browser.

## ExchangeLab

- Independent matching oracle: 100,000 commands, 0 mismatches.
- Million-command replay: median 2.370795 s over five timed runs after warm-up; 421,800 commands/s. Apple M2, 10 symbols, seed 42.
- Timings (s): 2.531660, 2.450480, 2.365329, 2.353686, 2.370795.
- Separately instrumented 100,000-command p95: 5.250 microseconds. Do not treat this as network or client latency.
- Stress run checked book/ledger invariants every 1,000 events and at the end; local fill quantities checked on every match. Sampled peak live orders: 1,000; sampled peak price levels: 143.
- Actual mix: {'market': 199664, 'limit': 600193, 'cancel': 200143}; rejected commands: {'unknown_order': 178787}. All commands count in throughput.
- Process RSS high-water mark across warm-up and repetitions: 286.81 MiB, including the pre-generated workload. This is not isolated order-book memory.
- All five timed runs produced identical final-state hashes. Equal-size list/reference timing and state comparisons are in exchange-benchmark.json.

| One-way delay (ms) | Mean fill rate | Mean execution cost (bps) |
| --- | ---: | ---: |
| 0 | 0.9969 | 1.2444 |
| 1 | 0.9969 | 1.2131 |
| 10 | 0.7262 | -0.3519 |
| 50 | 0.6129 | -4.9134 |

Five paired seeds per delay, three synthetic symbols. Cost excludes unfilled orders. Differences reflect this model and flow, not guaranteed benefits from faster real-world trading.

## RiskLab

- 40 European/geometric-Asian checks against QuantLib; maximum absolute discrepancy 1.14e-13 currency units.
- Four independent arithmetic-Asian checks: maximum discrepancy 0.819 combined standard errors (acceptance threshold 5). Includes a put case.
- Empirical nominal-95% interval coverage: 98/100 seeded geometric-Asian replications at 10,000 paths. This finite experiment is not a coverage guarantee.
- 20-scenario grid × 3 estimators; each has 100,000 generated paths including control pilot. Three sensitivities/bump-size comparisons passed against analytic European Greeks.
- Nine fresh-process stress runs: strikes 80/100/120 × seeds 42/43/44; S0=100, volatility=30%, rate=3%, T=1, 252 future fixings. Each uses 1,000,000 main paths plus 10,000 independent pilot paths.
- Maximum 95% interval half-width: 0.00118202 currency units; proposal target ≤0.05.
- Maximum process RSS: 54.8594 MiB; proposal target <1,024 MiB. Fresh CLI process including libraries, excluding dashboard.
- Median price runtimes by strike (seconds): {'80': 2.107731750002131, '100': 2.0918374579632655, '120': 2.116755875060335}.
- Identical-shock scalar/vectorized check: 10,000 paths × 252 steps; max absolute discrepancy 0. Scalar 0.4840s; vectorized 0.0119s on that equal workload.

| Estimator | Variance of estimates across 20 seeds | Median seconds | Total paths/run |
| --- | ---: | ---: | ---: |
| plain | 0.0019705309 | 0.2034 | 100,000 |
| antithetic | 0.00070505089 | 0.1462 | 100,000 |
| control | 2.6753529e-06 | 0.2040 | 100,000 |

Intervals measure sampling uncertainty under the stated constant-volatility model. They exclude model risk. Antithetic pair averages are independent units; controls use a separate pilot. Reproduce with the same seed, batching and versions. See README.md for exact contracts, conventions and benchmark exclusions.

## Reproduction

Run `python scripts/evaluate.py`, then `pytest -q --junitxml=results/tests.xml`, then `python scripts/summarize.py`. See raw JSON and CSV in results/ and environment metadata within each numerical report.
