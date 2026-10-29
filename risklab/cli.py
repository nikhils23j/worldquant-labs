import argparse
import json
from pathlib import Path
from .contracts import Contract, Model
from .estimators import price
from .greeks import sensitivities

def main():
    p=argparse.ArgumentParser(description="Reproducible Monte Carlo experiments")
    p.add_argument('command',choices=['price','sweep','benchmark','validate','compare','stress-case','greeks'])
    p.add_argument('--kind',choices=['asian','european','geometric'],default='asian')
    p.add_argument('--method',choices=['plain','antithetic','control'],default='control')
    p.add_argument('--paths',type=int,default=100000)
    p.add_argument('--fixings',type=int,default=252)
    p.add_argument('--strike',type=float,default=100)
    p.add_argument('--spot',type=float,default=100)
    p.add_argument('--volatility',type=float,default=.3)
    p.add_argument('--rate',type=float,default=.03)
    p.add_argument('--maturity',type=float,default=1.)
    p.add_argument('--seed',type=int,default=42)
    p.add_argument('--put',action='store_true')
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args()
    a.output.parent.mkdir(parents=True,exist_ok=True)
    if a.command=='price':
        r=price(Contract(a.kind,a.strike,a.maturity,a.fixings,not a.put),Model(a.spot,a.volatility,a.rate),a.method,a.paths,a.seed)
    elif a.command=='greeks':
        r=sensitivities(Contract(a.kind,a.strike,a.maturity,a.fixings,not a.put),Model(a.spot,a.volatility,a.rate),a.paths,a.seed)
    else:
        from . import experiments as e
        if a.command=='stress-case':r=e.stress_case(a.strike,a.seed)
        elif a.command=='sweep':r=e.sweep(a.paths)
        elif a.command=='validate':r=e.validate()
        elif a.command=='compare':r=e.matched_estimators(a.paths)
        else:r=e.benchmark(a.output.parent/'stress')
    a.output.write_text(json.dumps(r,indent=2)+'\n')
    print(f'Saved {a.output}')

if __name__=='__main__':main()
