from dataclasses import replace
import json
import math
import platform
import statistics
import subprocess
import sys
import time
from pathlib import Path
import numpy as np
from .contracts import Contract, Model
from .estimators import price
from .analytics import analytic_price, analytic_greeks
from .greeks import sensitivities
from .paths import scalar_summaries, summaries_from_shocks
from .validation import quantlib_reference
from exchangelab.experiments import rss_mib

def environment():
    import scipy, QuantLib
    return {"python":sys.version, "platform":platform.platform(), "numpy":np.__version__,
            "scipy":scipy.__version__, "QuantLib":QuantLib.__version__, "hardware":"Apple M2, 8 GiB"}

def sweep(paths=100000):
    rows = []
    for strike in (80,90,100,110,120):
        for vol in (.15,.30):
            for maturity in (.5,1.):
                c,m = Contract(strike=strike,maturity=maturity), Model(volatility=vol)
                for method in ("plain","antithetic","control"):
                    # Equal total generated-path budgets including independent pilot.
                    r = price(c,m,method,paths-10000 if method == 'control' else paths,42)
                    rows.append(r)
    return {"environment":environment(), "rows":rows, "scenario_count":20,
            "budget_note":"100,000 total generated paths/method; control uses 10,000 pilot + 90,000 main."}

def validate():
    checks = []
    for strike in (80,90,100,110,120):
        for vol in (.15,.30):
            for maturity in (.5,1.):
                for kind in ("european", "geometric"):
                    c,m=Contract(kind,strike,maturity),Model(volatility=vol)
                    a=analytic_price(c,m); q=quantlib_reference(c,m)
                    error=abs(a-q['estimate'])
                    if error > 1e-9: raise AssertionError((c,m,a,q))
                    checks.append({"contract":c.__dict__,"model":m.__dict__,"error":error,"reference":q})
    asian=[]
    for strike, call in ((80,True),(100,True),(120,True),(100,False)):
        c,m=Contract(strike=strike,call=call),Model()
        a=price(c,m,'control',200000,42)
        q=quantlib_reference(c,m,200000,71)
        combined=math.hypot(a['standard_error'],q['standard_error'])
        z=abs(a['estimate']-q['estimate'])/combined
        if z > 5: raise AssertionError((c,a,q,z))
        asian.append({"local":a,"reference":q,"difference_in_combined_se":z})
    c,m=Contract('geometric',fixings=252),Model()
    truth=analytic_price(c,m)
    coverage=[]
    for seed in range(100):
        r=price(c,m,'plain',10000,seed)
        coverage.append({"seed":seed,"estimate":r['estimate'],"ci95":r['ci95'],
                         "covers":r['ci95'][0]<=truth<=r['ci95'][1]})
    greek_rows=[]
    c=Contract('european')
    truth_g=analytic_greeks(c,m)
    for bump in (.5,.1,.02):
        r=sensitivities(c,m,200000,7,spot_bump=bump,vol_bump=bump/100)
        for key in ('delta','vega_per_vol_point'):
            if abs(r[key]['estimate']-truth_g[key]) > 5*r[key]['standard_error']+.001:
                raise AssertionError((key,r,truth_g))
        greek_rows.append(r)
    rng=np.random.default_rng(77)
    shocks=rng.standard_normal((10000,252))
    t=time.perf_counter(); scalar=scalar_summaries(shocks,m,1.); scalar_t=time.perf_counter()-t
    t=time.perf_counter(); vector=summaries_from_shocks(shocks,m,1.); vector_t=time.perf_counter()-t
    errors=[float(np.max(np.abs(a-b))) for a,b in zip(scalar,vector)]
    if max(errors)>1e-9:raise AssertionError(errors)
    return {"environment":environment(),"analytic_checks":checks,"asian_checks":asian,
            "coverage":{"truth":truth,"nominal":.95,"covered":sum(x['covers'] for x in coverage),"runs":coverage},
            "greeks":{"analytic":truth_g,"bump_runs":greek_rows},
            "scalar_vector":{"shape":[10000,252],"max_errors":errors,"scalar_seconds":scalar_t,"vector_seconds":vector_t}}

def matched_estimators(paths=100000):
    rows=[]
    for seed in range(20):
        for method in ('plain','antithetic','control'):
            n=paths-10000 if method=='control' else paths
            rows.append(price(Contract(),Model(),method,n,seed))
    summary={}
    for method in ('plain','antithetic','control'):
        data=[r for r in rows if r['method']==method]
        summary[method]={"variance_across_20_seeds":statistics.variance(r['estimate'] for r in data),
                         "mean_reported_variance_of_mean":statistics.mean(r['standard_error']**2 for r in data),
                         "median_seconds":statistics.median(r['seconds'] for r in data),
                         "generated_paths_per_run":paths}
    return {"environment":environment(),"summary":summary,"runs":rows}

def stress_case(strike=100,seed=42):
    r=price(Contract(strike=strike),Model(),'control',1000000,seed)
    r['peak_rss_mib']=rss_mib()
    r['precision_target_met']=r['half_width']<=.05
    r['memory_target_met']=r['peak_rss_mib']<1024
    return r

def benchmark(output_dir):
    output_dir=Path(output_dir);output_dir.mkdir(parents=True,exist_ok=True)
    rows=[]
    for strike in (80,100,120):
        for rep in range(3):
            target=output_dir/f"stress-k{strike}-r{rep}.json"
            subprocess.run([sys.executable,'-m','risklab.cli','stress-case','--strike',str(strike),
                            '--seed',str(42+rep),'--output',str(target)],check=True)
            rows.append(json.loads(target.read_text()))
    return {"environment":environment(),"runs":rows,
            "max_half_width":max(r['half_width'] for r in rows),
            "max_peak_rss_mib":max(r['peak_rss_mib'] for r in rows),
            "median_seconds_by_strike":{str(k):statistics.median(r['seconds'] for r in rows if r['contract']['strike']==k) for k in (80,100,120)}}
