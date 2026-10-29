"""Run the recorded acceptance experiments sequentially to avoid benchmark contention."""
from pathlib import Path
import subprocess
import sys
import time

root=Path(__file__).resolve().parents[1]
results=root/'results';results.mkdir(exist_ok=True)
commands=[
 ('exchange-differential','exchangelab.cli',['validate','--count','100000']),
 ('exchange-benchmark','exchangelab.cli',['benchmark','--count','1000000','--repetitions','5']),
 ('exchange-latency','exchangelab.cli',['compare-latency']),
 ('exchange-demo-0ms','exchangelab.cli',['demo','--latency','0','--seed','1']),
 ('exchange-demo-50ms','exchangelab.cli',['demo','--latency','50','--seed','1']),
 ('risk-validation','risklab.cli',['validate']),
 ('risk-grid','risklab.cli',['sweep']),
 ('risk-estimators','risklab.cli',['compare']),
 ('risk-benchmark','risklab.cli',['benchmark']),
]
for name,module,args in commands:
    started=time.perf_counter()
    print(f'START {name}',flush=True)
    subprocess.run([sys.executable,'-m',module,*args,'--output',str(results/(name+'.json'))],check=True,cwd=root)
    print(f'DONE {name}: {time.perf_counter()-started:.2f}s',flush=True)
