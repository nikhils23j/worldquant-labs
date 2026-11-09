"""Run every recorded experiment sequentially (no concurrent jobs, so timings don't fight).

Writes results/<name>.json for each step plus results/eval-log.json with the exact command,
wall time and 1/5/15-minute load average before and after every step.
Usage: python scripts/evaluate.py [--only name1,name2]
"""
from pathlib import Path
import argparse
import datetime
import json
import os
import platform
import subprocess
import sys
import time

root = Path(__file__).resolve().parents[1]
results = root / 'results'
results.mkdir(exist_ok=True)
STEPS = [
    # name, module, args. Timing-sensitive benchmarks first, while the machine is fresh.
    ('exchange-benchmark', 'exchangelab.cli', ['benchmark', '--count', '1000000', '--repetitions', '5']),
    ('exchange-differential', 'exchangelab.cli', ['validate', '--count', '100000']),
    ('exchange-differential-suite', 'exchangelab.cli', ['validate-suite', '--count', '1000000', '--v2-count', '200000']),
    ('exchange-latency', 'exchangelab.cli', ['compare-latency']),
    ('exchange-demo-0ms', 'exchangelab.cli', ['demo', '--latency', '0', '--seed', '1']),
    ('exchange-demo-50ms', 'exchangelab.cli', ['demo', '--latency', '50', '--seed', '1']),
    ('exchange-latency-study', 'exchangelab.cli', ['latency-study', '--seeds', '30']),
    ('risk-benchmark', 'risklab.cli', ['benchmark']),
    ('risk-validation', 'risklab.cli', ['validate']),
    ('risk-grid', 'risklab.cli', ['sweep']),
    ('risk-estimators', 'risklab.cli', ['compare']),
    ('risk-study', 'risklab.cli', ['study']),
]


def load():
    try:
        return list(os.getloadavg())
    except OSError:
        return None


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--only', default='')
    a = p.parse_args()
    only = set(filter(None, a.only.split(',')))
    log_path = results / 'eval-log.json'
    log = json.loads(log_path.read_text()) if only and log_path.exists() else {}
    log.setdefault('steps', {})
    log['python'] = sys.version
    log['platform'] = platform.platform()
    log['started'] = datetime.datetime.now().isoformat(timespec='seconds')
    total = time.perf_counter()
    for name, module, args in STEPS:
        if only and name not in only:
            continue
        cmd = [sys.executable, '-m', module, *args, '--output', str(results / (name + '.json'))]
        before = load()
        started = time.perf_counter()
        print(f'START {name}  load={before}', flush=True)
        subprocess.run(cmd, check=True, cwd=root)
        seconds = time.perf_counter() - started
        log['steps'][name] = {'command': ' '.join(['python', '-m', module, *args, '--output', f'results/{name}.json']),
                              'seconds': seconds, 'load_before': before, 'load_after': load()}
        print(f'DONE {name}: {seconds:.2f}s', flush=True)
    log['total_seconds_this_invocation'] = time.perf_counter() - total
    log['finished'] = datetime.datetime.now().isoformat(timespec='seconds')
    log_path.write_text(json.dumps(log, indent=2) + '\n')


if __name__ == '__main__':
    main()
