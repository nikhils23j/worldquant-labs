import argparse
import json
from pathlib import Path
from .contracts import Contract, Model
from .estimators import price, METHODS
from .greeks import sensitivities

COMMANDS = ['price', 'sweep', 'benchmark', 'validate', 'compare', 'stress-case', 'greeks',
            'greeks-compare', 'qmc', 'barrier', 'heston', 'study', 'scale-case']


def main():
    p = argparse.ArgumentParser(description='Reproducible Monte Carlo experiments')
    p.add_argument('command', choices=COMMANDS)
    p.add_argument('--kind', choices=['asian', 'european', 'geometric'], default='asian')
    p.add_argument('--method', choices=list(METHODS), default='control')
    p.add_argument('--paths', type=int, default=100000)
    p.add_argument('--fixings', type=int, default=252)
    p.add_argument('--strike', type=float, default=100)
    p.add_argument('--spot', type=float, default=100)
    p.add_argument('--volatility', type=float, default=.3)
    p.add_argument('--rate', type=float, default=.03)
    p.add_argument('--maturity', type=float, default=1.)
    p.add_argument('--seed', type=int, default=42)
    p.add_argument('--put', action='store_true')
    p.add_argument('--barrier', type=float, default=90.0, help='barrier level (barrier command)')
    p.add_argument('--steps', type=int, default=52, help='time steps (barrier/heston commands)')
    p.add_argument('--no-correction', action='store_true', help='barrier: disable Brownian-bridge correction')
    p.add_argument('--xi', type=float, default=.3, help='heston vol-of-vol')
    p.add_argument('--batch', type=int, default=10000, help='scale-case batch size')
    p.add_argument('--materialize', action='store_true', help='scale-case: hold all paths in memory')
    p.add_argument('--reps', type=int, default=8, help='qmc: independent replications')
    p.add_argument('--max-log2', type=int, default=16, help='qmc: largest sample size is 2^max_log2')
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    a.output.parent.mkdir(parents=True, exist_ok=True)
    contract = lambda: Contract(a.kind, a.strike, a.maturity, a.fixings, not a.put)
    model = lambda: Model(a.spot, a.volatility, a.rate)
    if a.command == 'price':
        r = price(contract(), model(), a.method, a.paths, a.seed)
    elif a.command == 'greeks':
        r = sensitivities(contract(), model(), a.paths, a.seed)
    elif a.command == 'greeks-compare':
        from .greeks import greek_estimators
        r = greek_estimators(contract(), model(), a.paths, a.seed)
    elif a.command == 'barrier':
        from .barrier import barrier_mc
        from .validation import quantlib_barrier
        direction = 'down' if a.barrier < a.spot else 'up'
        r = barrier_mc(a.spot, a.strike, a.barrier, a.maturity, a.rate, a.volatility, direction, not a.put,
                       a.steps, a.paths, a.seed, correction=not a.no_correction)
        r['quantlib_continuous'] = quantlib_barrier(a.spot, a.strike, a.barrier, a.maturity, a.rate,
                                                    a.volatility, direction, not a.put)
    elif a.command == 'heston':
        from .heston import HestonParams, heston_call_mc
        from .validation import quantlib_heston
        hp = HestonParams(xi=a.xi)
        r = heston_call_mc(a.spot, a.strike, a.maturity, a.rate, hp, a.steps, a.paths, a.seed, call=not a.put)
        r['quantlib_analytic'] = quantlib_heston(a.spot, a.strike, a.maturity, a.rate, hp, call=not a.put)
    elif a.command == 'qmc':
        from .studies import qmc_convergence
        r = qmc_convergence(reps=a.reps, log2_sizes=tuple(range(8, a.max_log2 + 1)), fixings=a.fixings)
    elif a.command == 'scale-case':
        from .studies import scale_case
        r = scale_case(a.paths, a.batch, a.materialize)
    elif a.command == 'study':
        from .studies import study
        r = study(a.output.parent)
    else:
        from . import experiments as e
        if a.command == 'stress-case': r = e.stress_case(a.strike, a.seed)
        elif a.command == 'sweep': r = e.sweep(a.paths)
        elif a.command == 'validate': r = e.validate()
        elif a.command == 'compare': r = e.matched_estimators(a.paths)
        else: r = e.benchmark(a.output.parent / 'stress')
    a.output.write_text(json.dumps(r, indent=2) + '\n')
    print(f'Saved {a.output}')


if __name__ == '__main__': main()
