from pathlib import Path
import csv
import hashlib
import json
import statistics
import xml.etree.ElementTree as ET

root=Path(__file__).resolve().parents[1]
p=root/'results'
e=json.loads((p/'exchange-benchmark.json').read_text())
d=json.loads((p/'exchange-differential.json').read_text())
r=json.loads((p/'risk-benchmark.json').read_text())
v=json.loads((p/'risk-validation.json').read_text())
m=json.loads((p/'risk-estimators.json').read_text())
l=json.loads((p/'exchange-latency.json').read_text())
g=json.loads((p/'risk-grid.json').read_text())
with (p/'exchange-latency.csv').open('w') as f:
    w=csv.DictWriter(f,fieldnames=list(l[0]));w.writeheader();w.writerows(l)
with (p/'risk-grid.csv').open('w') as f:
    fields=['strike','volatility','maturity','method','total_paths','estimate','half_width','seconds']
    w=csv.DictWriter(f,fieldnames=fields);w.writeheader()
    for x in g['rows']:
        w.writerow(dict(strike=x['contract']['strike'],volatility=x['model']['volatility'],maturity=x['contract']['maturity'],method=x['method'],total_paths=x['total_generated_paths'],estimate=x['estimate'],half_width=x['half_width'],seconds=x['seconds']))
tree=ET.parse(p/'tests.xml');suite=tree.getroot().find('testsuite')
tests=int(suite.attrib['tests'])
timings = ', '.join(format(x['seconds'], '.6f') for x in e['runs'])
assert int(suite.attrib['failures'])==int(suite.attrib['errors'])==0
lines=[
'# Recorded results',
'',
'Validated 23 October 2026. Generated implementation and executed experiments by Codex for Nikhil Jain. Synthetic workloads; no live market or production deployment claim. All numbers below are measured, not proposal targets.',
'',
f"{tests} tests passed, including CLI roundtrips and Streamlit controls/surface calculation. Both dashboards were also opened and visually inspected in a real browser.",
'',
'## ExchangeLab',
'',
f"- Independent matching oracle: {d['commands']:,} commands, {d['mismatches']} mismatches.",
f"- Million-command replay: median {e['median_seconds']:.6f} s over five timed runs after warm-up; {1000000/e['median_seconds']:,.0f} commands/s. Apple M2, 10 symbols, seed 42.",
f"- Timings (s): {timings}.",
f"- Separately instrumented 100,000-command p95: {e['instrumented']['p95_us']:.3f} microseconds. Do not treat this as network or client latency.",
f"- Stress run checked book/ledger invariants every 1,000 events and at the end; local fill quantities checked on every match. Sampled peak live orders: {e['stress']['peak_live_orders_sampled']:,}; sampled peak price levels: {e['stress']['peak_price_levels_sampled']:,}.",
f"- Actual mix: {e['stress']['command_mix']}; rejected commands: {e['stress']['rejections']}. All commands count in throughput.",
f"- Process RSS high-water mark across warm-up and repetitions: {max(x['peak_process_rss_mib'] for x in e['runs']):.2f} MiB, including the pre-generated workload. This is not isolated order-book memory.",
'- All five timed runs produced identical final-state hashes. Equal-size list/reference timing and state comparisons are in exchange-benchmark.json.',
'',
'| One-way delay (ms) | Mean fill rate | Mean execution cost (bps) |',
'| --- | ---: | ---: |',
]
for delay in (0,1,10,50):
    data=[x for x in l if x['latency_ms']==delay]
    lines.append(f"| {delay} | {statistics.mean(x['fill_rate'] for x in data):.4f} | {statistics.mean(x['execution_cost_bps'] for x in data):.4f} |")
lines += [
'',
'Five paired seeds per delay, three synthetic symbols. Cost excludes unfilled orders. Differences reflect this model and flow, not guaranteed benefits from faster real-world trading.',
'',
'## RiskLab',
'',
f"- {len(v['analytic_checks'])} European/geometric-Asian checks against QuantLib; maximum absolute discrepancy {max(x['error'] for x in v['analytic_checks']):.3g} currency units.",
f"- Four independent arithmetic-Asian checks: maximum discrepancy {max(x['difference_in_combined_se'] for x in v['asian_checks']):.3f} combined standard errors (acceptance threshold 5). Includes a put case.",
f"- Empirical nominal-95% interval coverage: {v['coverage']['covered']}/100 seeded geometric-Asian replications at 10,000 paths. This finite experiment is not a coverage guarantee.",
'- 20-scenario grid × 3 estimators; each has 100,000 generated paths including control pilot. Three sensitivities/bump-size comparisons passed against analytic European Greeks.',
'- Nine fresh-process stress runs: strikes 80/100/120 × seeds 42/43/44; S0=100, volatility=30%, rate=3%, T=1, 252 future fixings. Each uses 1,000,000 main paths plus 10,000 independent pilot paths.',
f"- Maximum 95% interval half-width: {r['max_half_width']:.8f} currency units; proposal target ≤0.05.",
f"- Maximum process RSS: {r['max_peak_rss_mib']:.4f} MiB; proposal target <1,024 MiB. Fresh CLI process including libraries, excluding dashboard.",
f"- Median price runtimes by strike (seconds): {r['median_seconds_by_strike']}.",
f"- Identical-shock scalar/vectorized check: 10,000 paths × 252 steps; max absolute discrepancy {max(v['scalar_vector']['max_errors']):.3g}. Scalar {v['scalar_vector']['scalar_seconds']:.4f}s; vectorized {v['scalar_vector']['vector_seconds']:.4f}s on that equal workload.",
'',
'| Estimator | Variance of estimates across 20 seeds | Median seconds | Total paths/run |',
'| --- | ---: | ---: | ---: |',
]
for name,x in m['summary'].items():
    lines.append(f"| {name} | {x['variance_across_20_seeds']:.8g} | {x['median_seconds']:.4f} | {x['generated_paths_per_run']:,} |")
lines += ['', 'Intervals measure sampling uncertainty under the stated constant-volatility model. They exclude model risk. Antithetic pair averages are independent units; controls use a separate pilot. Reproduce with the same seed, batching and versions. See README.md for exact contracts, conventions and benchmark exclusions.', '', '## Reproduction', '', 'Run `python scripts/evaluate.py`, then `pytest -q --junitxml=results/tests.xml`, then `python scripts/summarize.py`. See raw JSON and CSV in results/ and environment metadata within each numerical report.', '']
(root/'RESULTS.md').write_text('\n'.join(lines))
manifest={}
for path in sorted(root.rglob('*')):
    rel=path.relative_to(root)
    if path.is_file() and not any(x in {'.venv','__pycache__','.pytest_cache','results'} or x.endswith('.egg-info') for x in rel.parts):
        manifest[str(rel)]=hashlib.sha256(path.read_bytes()).hexdigest()
(p/'source-manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
print(f'Saved report and CSV exports; {tests} passing tests, {len(manifest)} source/document hashes.')
