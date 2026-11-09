from dataclasses import replace
import numpy as np
from .paths import payoff_batches
from .estimators import Moments

def sensitivities(c, m, paths=100000, seed=42, spot_bump=.1, vol_bump=.001, batch_size=10000):
    if spot_bump <= 0 or vol_bump <= 0 or spot_bump >= m.spot or vol_bump > m.volatility:
        raise ValueError("Bumps must be positive and remain inside the model domain")
    if paths < 2 or batch_size < 1:
        raise ValueError("Invalid path/batch count")
    result = {}
    for name, hi, lo, width in (
        ("delta", replace(m, spot=m.spot+spot_bump), replace(m, spot=m.spot-spot_bump), 2*spot_bump),
        ("vega_per_vol_point", replace(m, volatility=m.volatility+vol_bump), replace(m, volatility=m.volatility-vol_bump), 200*vol_bump)):
        mom = Moments()
        # Identical seed and batching pair every high/low path, including payoff kinks.
        for (a,_), (b,_) in zip(payoff_batches(c, hi, paths, seed, batch_size),
                                 payoff_batches(c, lo, paths, seed, batch_size), strict=True):
            mom.add((a-b)/width)
        estimate, se, _ = mom.result()
        result[name] = {"estimate": estimate, "standard_error": se,
                        "ci95": [estimate-1.959964*se, estimate+1.959964*se]}
    result["configuration"] = {"paths": paths, "seed": seed, "spot_bump": spot_bump,
                               "vol_bump": vol_bump, "batch_size": batch_size}
    return result


# ---------------------------------------------------------------------------------------------
# Pathwise and likelihood-ratio (score-function) estimators.
#
# GBM with exact steps: log S_i = log S0 + mu*t_i + sigma*W_i, mu = r - q - sigma^2/2.
#   Pathwise:  dS_i/dS0 = S_i/S0;  dS_i/dsigma = S_i * (W_i - sigma*t_i).
#              d/dtheta [disc * max(U-K,0)] = disc * 1{U>K} * dU/dtheta  (needs a.s. Lipschitz payoff).
#   LR:        multiply the payoff by the derivative of the log-density of the shocks.
#              delta: Z_1 / (S0 sigma sqrt(dt))  (only the first increment depends on S0)
#              vega:  sum_j [(Z_j^2 - 1)/sigma - Z_j sqrt(dt)]   (every step depends on sigma)
# LR works for discontinuous payoffs (digitals) where pathwise silently returns 0, but its
# vega weight has variance growing with the number of steps.
# ---------------------------------------------------------------------------------------------
import math as _math
import time as _time
from .analytics import analytic_greeks as _analytic_greeks


def _payoff_parts(c, m, z):
    """Return discounted payoff, its pathwise delta and vega, and LR weights, per path."""
    n, steps = z.shape
    T = c.maturity
    dt = T / steps
    sig = m.volatility
    t = T * np.arange(1, steps + 1) / steps
    w = np.cumsum(z, axis=1) * _math.sqrt(dt)
    logs = _math.log(m.spot) + (m.rate - m.dividend - .5 * sig * sig) * t + sig * w
    s = np.exp(logs)
    if c.kind == "european":
        under, d_spot, d_sig = s[:, -1], s[:, -1] / m.spot, s[:, -1] * (w[:, -1] - sig * T)
    elif c.kind == "asian":
        under = s.mean(axis=1)
        d_spot = under / m.spot
        d_sig = (s * (w - sig * t)).mean(axis=1)
    else:
        logmean = logs.mean(axis=1)
        under = np.exp(logmean)
        d_spot = under / m.spot
        d_sig = under * (w - sig * t).mean(axis=1)
    disc = _math.exp(-m.rate * T)
    sign = 1.0 if c.call else -1.0
    itm = (sign * (under - c.strike)) > 0
    payoff = disc * np.where(itm, sign * (under - c.strike), 0.0)
    pw_delta = disc * np.where(itm, sign * d_spot, 0.0)
    pw_vega = disc * np.where(itm, sign * d_sig, 0.0)
    lr_delta_w = z[:, 0] / (m.spot * sig * _math.sqrt(dt))
    lr_vega_w = ((z * z - 1) / sig - z * _math.sqrt(dt)).sum(axis=1)
    return payoff, pw_delta, pw_vega, lr_delta_w, lr_vega_w, under


def greek_estimators(c, m, paths=200000, seed=42, batch_size=20000, spot_bump=.1, vol_bump=.001):
    """Delta and vega (per 1 vol point) by pathwise, likelihood-ratio and CRN central bumps.

    All three use independent streams of the same size; per-path variance is reported so
    estimators can be compared at equal path counts.
    """
    if c.kind not in ("european", "asian", "geometric"):
        raise ValueError("unsupported contract")
    steps = 1 if c.kind == "european" else c.fixings
    rng = np.random.default_rng(seed)
    mom = {k: Moments() for k in ("pathwise_delta", "pathwise_vega", "lr_delta", "lr_vega")}
    started = _time.perf_counter()
    for start in range(0, paths, batch_size):
        n = min(batch_size, paths - start)
        z = rng.standard_normal((n, steps))
        payoff, pd, pv, lrd, lrv, _ = _payoff_parts(c, m, z)
        mom["pathwise_delta"].add(pd)
        mom["pathwise_vega"].add(pv / 100)
        mom["lr_delta"].add(payoff * lrd)
        mom["lr_vega"].add(payoff * lrv / 100)
    seconds_pw_lr = _time.perf_counter() - started
    out = {}
    for k, mo in mom.items():
        est, se, var = mo.result()
        out[k] = {"estimate": est, "standard_error": se, "per_path_variance": var}
    started = _time.perf_counter()
    bump = sensitivities(c, m, paths, seed + 1, spot_bump=spot_bump, vol_bump=vol_bump, batch_size=10000)
    out["bump_seconds"] = _time.perf_counter() - started
    out["pathwise_lr_seconds_shared"] = seconds_pw_lr
    for k, src in (("crn_bump_delta", "delta"), ("crn_bump_vega", "vega_per_vol_point")):
        se = bump[src]["standard_error"]
        out[k] = {"estimate": bump[src]["estimate"], "standard_error": se, "per_path_variance": se * se * paths}
    out["configuration"] = {"paths": paths, "seed": seed, "steps": steps, "spot_bump": spot_bump,
                            "vol_bump": vol_bump, "contract": c.__dict__, "model": m.__dict__}
    if c.kind == "european":
        out["analytic"] = _analytic_greeks(c, m)
    return out


def digital_delta(m, strike=100.0, maturity=1.0, paths=200000, seed=42, spot_bump=.5):
    """Cash-or-nothing digital call (pays 1): pathwise delta is identically 0 (wrong);
    LR and CRN bump-and-revalue are not. Analytic: e^{-rT} phi(d2) / (S sigma sqrt T)."""
    rng = np.random.default_rng(seed)
    z = rng.standard_normal(paths)
    T, sig = maturity, m.volatility
    disc = _math.exp(-m.rate * T)

    def terminal(spot):
        return spot * np.exp((m.rate - m.dividend - .5 * sig * sig) * T + sig * _math.sqrt(T) * z)
    pay = disc * (terminal(m.spot) > strike)
    lr = pay * z / (m.spot * sig * _math.sqrt(T))
    bump = disc * ((terminal(m.spot + spot_bump) > strike).astype(float)
                   - (terminal(m.spot - spot_bump) > strike)) / (2 * spot_bump)
    d2 = (_math.log(m.spot / strike) + (m.rate - m.dividend - .5 * sig * sig) * T) / (sig * _math.sqrt(T))
    truth = disc * _math.exp(-.5 * d2 * d2) / _math.sqrt(2 * _math.pi) / (m.spot * sig * _math.sqrt(T))

    def summary(x):
        return {"estimate": float(x.mean()), "standard_error": float(x.std(ddof=1) / _math.sqrt(len(x)))}
    return {"analytic": truth, "pathwise": {"estimate": 0.0, "standard_error": 0.0,
                                            "note": "derivative of an indicator is 0 almost surely"},
            "likelihood_ratio": summary(lr), "crn_bump": summary(bump),
            "configuration": {"paths": paths, "seed": seed, "spot_bump": spot_bump, "strike": strike}}
