"""The RiskLab v2 experiments: QMC convergence, variance reduction, Greeks, barriers, Heston, scaling."""
from dataclasses import replace
import json
import math
import statistics
import subprocess
import sys
import time
from pathlib import Path
import numpy as np
from .contracts import Contract, Model
from .analytics import analytic_price
from .estimators import price, METHODS
from .greeks import greek_estimators, digital_delta
from .barrier import barrier_mc, down_and_out_call
from .heston import HestonParams, heston_call_mc
from .qmc import prefix_estimates, NormalSource, payoffs
from .validation import quantlib_european_greeks, quantlib_barrier, quantlib_heston, quantlib_reference
from .experiments import environment


def _load():
    import os
    try:
        return list(os.getloadavg())
    except OSError:
        return None


# ---------------------------------------------------------------- QMC convergence ---------------
QMC_METHODS = {
    # name: (normal source, bridge, antithetic, control)
    "prng_plain": ("prng", False, False, False),
    "prng_antithetic": ("prng", False, True, False),
    "prng_control": ("prng", False, False, True),
    "prng_antithetic_control": ("prng", False, True, True),
    "sobol_standard": ("sobol", False, False, False),
    "sobol_bridge": ("sobol", True, False, False),
    "sobol_bridge_control": ("sobol", True, False, True),
}


def fitted_beta(c, m, pilot=1 << 14, seed=999):
    src = NormalSource("prng", c.fixings, seed)
    y, x = payoffs(c, m, src.draw(pilot), bridge=False)
    xc = x - x.mean()
    return float(xc @ (y - y.mean()) / (xc @ xc))


def arithmetic_reference(c, m, beta, scrambles=32, log2_n=16, seed0=50_000):
    """High-precision arithmetic-Asian value: RQMC Sobol+bridge+control, independent scrambles."""
    expected = analytic_price(replace(c, kind="geometric"), m)
    ests = [prefix_estimates(c, m, "sobol", True, seed0 + s, [log2_n], control=(beta, expected))[1 << log2_n]
            for s in range(scrambles)]
    mean = statistics.fmean(ests)
    se = statistics.stdev(ests) / math.sqrt(scrambles)
    return {"estimate": mean, "standard_error": se, "scrambles": scrambles, "points_per_scramble": 1 << log2_n}


def qmc_convergence(reps=20, log2_sizes=tuple(range(10, 17)), fixings=252):
    """RMSE vs N for each estimator. Each replication is one independent seed/scrambling and
    estimates at every N are prefixes of the same stream (Sobol prefixes of length 2^k are nets)."""
    m = Model()
    targets = {
        "geometric_asian": Contract("geometric", 100.0, 1.0, fixings),
        "arithmetic_asian": Contract("asian", 100.0, 1.0, fixings),
    }
    out = {"reps": reps, "log2_sizes": list(log2_sizes), "model": m.__dict__, "targets": {}}
    for name, c in targets.items():
        started = time.perf_counter()
        if c.kind == "geometric":
            truth = {"estimate": analytic_price(c, m), "standard_error": 0.0, "source": "closed form"}
            beta = None
            methods = [k for k, v in QMC_METHODS.items() if not v[3]]
        else:
            beta = fitted_beta(c, m)
            truth = arithmetic_reference(c, m, beta, log2_n=max(log2_sizes))
            truth["source"] = f"RQMC Sobol+bridge+control, 32 independent scrambles x 2^{max(log2_sizes)}"
            if fixings == 252:  # the QuantLib wrapper only supports the 252-fixing grid
                truth["quantlib_mc_check"] = quantlib_reference(c, m, 200000, 71)
            methods = list(QMC_METHODS)
        expected = analytic_price(replace(c, kind="geometric"), m)
        per_method = {}
        for meth in methods:
            kind, bridge, anti, ctrl = QMC_METHODS[meth]
            t0 = time.perf_counter()
            reps_out = [prefix_estimates(c, m, kind, bridge, 10_000 * (1 + list(QMC_METHODS).index(meth)) + r,
                                         log2_sizes, antithetic=anti,
                                         control=(beta, expected) if ctrl else None) for r in range(reps)]
            secs = time.perf_counter() - t0
            rows = []
            for k in log2_sizes:
                n = 1 << k
                e = np.array([x[n] for x in reps_out])
                err = e - truth["estimate"]
                rows.append({"paths": n, "rmse": float(np.sqrt(np.mean(err ** 2))),
                             "bias": float(err.mean()), "std": float(e.std(ddof=1))})
            per_method[meth] = {"rows": rows, "seconds_all_reps_largest_n": secs,
                                "seconds_per_path": secs / (reps * (1 << max(log2_sizes)))}
        base = per_method["prng_plain"]
        for meth, d in per_method.items():
            for row, brow in zip(d["rows"], base["rows"]):
                row["variance_reduction_vs_plain"] = (brow["rmse"] / row["rmse"]) ** 2
                # Work-normalised: how many times less CPU time for the same RMSE.
                row["efficiency_gain_vs_plain"] = row["variance_reduction_vs_plain"] * (
                    base["seconds_per_path"] / d["seconds_per_path"])
            # Fitted log-log slope (RMSE ~ N^slope); -0.5 is the Monte Carlo rate.
            xs = [math.log(r["paths"]) for r in d["rows"]]
            ys = [math.log(r["rmse"]) for r in d["rows"]]
            d["loglog_slope"] = float(np.polyfit(xs, ys, 1)[0])
        out["targets"][name] = {"contract": c.__dict__, "truth": truth, "control_beta": beta,
                                "methods": per_method, "seconds": time.perf_counter() - started}
    return out


# ------------------------------------------------------------- variance reduction (matched) ------
def vr_comparison(paths=100000, seeds=20):
    """Equal generated-path budgets (pilot included), 252 fixings, arithmetic Asian ATM call."""
    rows = []
    for seed in range(seeds):
        for method in METHODS:
            ctrl = "control" in method
            n = paths - 10000 if ctrl else paths
            rows.append(price(Contract(), Model(), method, n, seed))
    summary = {}
    plain_var = None
    for method in METHODS:
        data = [r for r in rows if r["method"] == method]
        var = statistics.variance(r["estimate"] for r in data)
        plain_var = plain_var or var
        summary[method] = {"variance_across_seeds": var,
                           "mean_reported_variance_of_mean": statistics.mean(r["standard_error"] ** 2 for r in data),
                           "variance_reduction_vs_plain": plain_var / var,
                           "median_seconds": statistics.median(r["seconds"] for r in data),
                           "generated_paths_per_run": paths}
    return {"seeds": seeds, "summary": summary, "runs": rows}


# ------------------------------------------------------------------------- Greeks --------------
def _geometric_fd_greeks(c, m, h=1e-4):
    up, dn = analytic_price(c, replace(m, spot=m.spot + h)), analytic_price(c, replace(m, spot=m.spot - h))
    vu = analytic_price(c, replace(m, volatility=m.volatility + h))
    vd = analytic_price(c, replace(m, volatility=m.volatility - h))
    return {"delta": (up - dn) / (2 * h), "vega_per_vol_point": (vu - vd) / (2 * h) / 100,
            "source": "central difference of the closed-form geometric-Asian price, h=1e-4"}


def greeks_study(paths=200000):
    m = Model()
    out = {}
    for kind in ("european", "asian", "geometric"):
        c = Contract(kind)
        r = greek_estimators(c, m, paths, 7)
        if kind == "european":
            r["quantlib"] = quantlib_european_greeks(c, m)
            r["truth"] = {"delta": r["quantlib"]["delta"], "vega_per_vol_point": r["quantlib"]["vega_per_vol_point"]}
        elif kind == "geometric":
            r["truth"] = _geometric_fd_greeks(c, m)
        if "truth" in r:
            for est in ("pathwise", "lr", "crn_bump"):
                for g, tk in (("delta", "delta"), ("vega", "vega_per_vol_point")):
                    e = r[f"{est}_{g}"]
                    e["z_vs_truth"] = (e["estimate"] - r["truth"][tk]) / e["standard_error"]
        out[kind] = r
    out["digital"] = digital_delta(m)
    d = out["digital"]
    for k in ("likelihood_ratio", "crn_bump"):
        d[k]["z_vs_truth"] = (d[k]["estimate"] - d["analytic"]) / d[k]["standard_error"]
    return out


# ------------------------------------------------------------------------ barriers -------------
BARRIER_CASES = (
    {"name": "down_and_out_call_B90", "strike": 100.0, "barrier": 90.0, "direction": "down"},
    {"name": "down_and_out_call_B80_K90", "strike": 90.0, "barrier": 80.0, "direction": "down"},
    {"name": "up_and_out_call_B130", "strike": 100.0, "barrier": 130.0, "direction": "up"},
)


def barrier_study(paths=200000, steps=(4, 12, 52, 252)):
    S, T, r, v = 100.0, 1.0, .03, .3
    out = []
    for case in BARRIER_CASES:
        ql_value = quantlib_barrier(S, case["strike"], case["barrier"], T, r, v, case["direction"])
        own = (down_and_out_call(S, case["strike"], case["barrier"], T, r, v)
               if case["direction"] == "down" else None)
        rows = []
        for n in steps:
            for corr in (False, True):
                res = barrier_mc(S, case["strike"], case["barrier"], T, r, v, case["direction"],
                                 steps=n, paths=paths, seed=1000 + n, correction=corr)
                res["error_vs_quantlib"] = res["estimate"] - ql_value
                res["z_vs_quantlib"] = res["error_vs_quantlib"] / res["standard_error"]
                rows.append(res)
        out.append({**case, "quantlib_continuous": ql_value, "own_closed_form": own,
                    "own_vs_quantlib_abs_diff": abs(own - ql_value) if own is not None else None, "runs": rows})
    return out


# -------------------------------------------------------------------------- Heston -------------
HESTON_CASES = (
    ("feller_ok", HestonParams(v0=.04, kappa=2.0, theta=.04, xi=.3, rho=-.7)),
    ("feller_violated", HestonParams(v0=.04, kappa=1.0, theta=.04, xi=.9, rho=-.7)),
)


def heston_study(paths=200000, steps=(12, 52, 252), strikes=(90.0, 100.0, 110.0)):
    out = []
    for name, p in HESTON_CASES:
        for k in strikes:
            ref = quantlib_heston(100.0, k, 1.0, .03, p)
            for n in steps:
                res = heston_call_mc(100.0, k, 1.0, .03, p, steps=n, paths=paths, seed=2000 + n)
                res["case"] = name
                res["quantlib_analytic"] = ref
                res["bias"] = res["estimate"] - ref
                res["z_vs_quantlib"] = res["bias"] / res["standard_error"]
                out.append(res)
    return out


# ------------------------------------------------------------------------- scaling -------------
def scale_case(paths, batch_size, materialize=False, seed=5):
    """One fresh-process measurement: arithmetic-Asian plain MC, 252 fixings."""
    from exchangelab.experiments import rss_mib
    import resource
    c, m = Contract(), Model()
    before = rss_mib()
    started = time.perf_counter()
    if materialize:
        rng = np.random.default_rng(seed)
        z = rng.standard_normal((paths, c.fixings))  # whole path matrix in memory
        dt = c.maturity / c.fixings
        logs = math.log(m.spot) + np.cumsum((m.rate - .5 * m.volatility ** 2) * dt + m.volatility * math.sqrt(dt) * z, axis=1)
        y = math.exp(-m.rate) * np.maximum(np.exp(logs).mean(axis=1) - c.strike, 0)
        est, se = float(y.mean()), float(y.std(ddof=1) / math.sqrt(paths))
    else:
        r = price(c, m, "plain", paths, seed, batch_size=batch_size)
        est, se = r["estimate"], r["standard_error"]
    return {"paths": paths, "batch_size": batch_size, "materialize": materialize, "estimate": est,
            "standard_error": se, "seconds": time.perf_counter() - started,
            "rss_before_mib": before, "peak_rss_mib": rss_mib()}


SCALE_GRID = ([(10_000, 10_000, False), (100_000, 10_000, False), (1_000_000, 10_000, False),
               (1_000_000, 1_000, False), (1_000_000, 100_000, False),
               (10_000, 0, True), (100_000, 0, True)])


def scaling(output_dir, repeats=1):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    for paths, batch, mat in SCALE_GRID:
        for rep in range(repeats):
            target = output_dir / f"scale-p{paths}-b{batch}-{'mat' if mat else 'stream'}-r{rep}.json"
            cmd = [sys.executable, "-m", "risklab.cli", "scale-case", "--paths", str(paths),
                   "--batch", str(batch), "--output", str(target)] + (["--materialize"] if mat else [])
            load = _load()
            subprocess.run(cmd, check=True, capture_output=True)
            row = json.loads(target.read_text())
            row["load_average_before"] = load
            rows.append(row)
    return rows


def study(output_dir):
    started = time.perf_counter()
    res = {"environment": environment(), "load_average_start": _load()}
    for name, fn in (("qmc_convergence", qmc_convergence), ("variance_reduction", vr_comparison),
                     ("greeks", greeks_study), ("barrier", barrier_study), ("heston", heston_study)):
        t0 = time.perf_counter()
        res[name] = fn()
        res[name + "_seconds"] = time.perf_counter() - t0
        print(f"  risklab study: {name} {res[name + '_seconds']:.1f}s", flush=True)
    t0 = time.perf_counter()
    res["scaling"] = scaling(Path(output_dir) / "scaling")
    res["scaling_seconds"] = time.perf_counter() - t0
    res["load_average_end"] = _load()
    res["seconds"] = time.perf_counter() - started
    return res
