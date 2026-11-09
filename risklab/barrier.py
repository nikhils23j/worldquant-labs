"""Continuously monitored knock-out options by Monte Carlo, with a Brownian-bridge correction.

A simulated path only shows prices at the grid dates. Checking the barrier only there
misses crossings *between* dates, so a naive estimator prices a discretely monitored
option, which is worth more than the continuously monitored contract (knock-outs are missed).

Bridge correction: conditional on the log-prices at two consecutive dates (x0, x1), both on the
safe side of the barrier b, a log-Brownian path crosses b in between with probability
    p = exp(-2 * (x0 - b) * (x1 - b) / (sigma^2 * dt))
(exact for GBM; drift cancels when conditioning on endpoints). Instead of killing the path we
multiply the payoff by prod(1 - p). The estimator is the conditional expectation of the
continuous-monitoring payoff given the grid values: unbiased, and lower variance than sampling
the crossing.
"""
import math
import time
import numpy as np
from scipy.special import ndtr
from .estimators import Moments


def down_and_out_call(spot, strike, barrier, maturity, rate, vol, dividend=0.0):
    """Closed form for a continuously monitored down-and-out call with barrier <= strike, no rebate."""
    if not (0 < barrier <= strike and barrier < spot):
        raise ValueError("formula requires barrier <= strike and barrier < spot")
    T, s = maturity, vol
    sq = s * math.sqrt(T)
    d1 = (math.log(spot / strike) + (rate - dividend + .5 * s * s) * T) / sq
    call = spot * math.exp(-dividend * T) * ndtr(d1) - strike * math.exp(-rate * T) * ndtr(d1 - sq)
    lam = (rate - dividend + .5 * s * s) / (s * s)
    y = math.log(barrier * barrier / (spot * strike)) / sq + lam * sq
    down_in = (spot * math.exp(-dividend * T) * (barrier / spot) ** (2 * lam) * ndtr(y)
               - strike * math.exp(-rate * T) * (barrier / spot) ** (2 * lam - 2) * ndtr(y - sq))
    return float(call - down_in)


def barrier_mc(spot, strike, barrier, maturity, rate, vol, direction="down", call=True,
               steps=52, paths=200000, seed=42, correction=True, batch_size=20000, dividend=0.0):
    if direction not in ("down", "up"):
        raise ValueError("direction must be down or up")
    if (direction == "down" and barrier >= spot) or (direction == "up" and barrier <= spot):
        raise ValueError("spot already knocked out")
    rng = np.random.default_rng(seed)
    dt = maturity / steps
    drift = (rate - dividend - .5 * vol * vol) * dt
    scale = vol * math.sqrt(dt)
    lb = math.log(barrier)
    disc = math.exp(-rate * maturity)
    sign = 1 if call else -1
    mom = Moments()
    killed = 0
    started = time.perf_counter()
    for start in range(0, paths, batch_size):
        n = min(batch_size, paths - start)
        x = np.full(n, math.log(spot))
        alive = np.ones(n, dtype=bool)
        survive = np.ones(n)
        for _ in range(steps):
            nx = x + drift + scale * rng.standard_normal(n)
            if direction == "down":
                alive &= nx > lb
            else:
                alive &= nx < lb
            if correction:
                # distances to barrier, positive on the safe side
                a = (x - lb) if direction == "down" else (lb - x)
                b = (nx - lb) if direction == "down" else (lb - nx)
                p = np.exp(-2.0 * np.maximum(a, 0) * np.maximum(b, 0) / (vol * vol * dt))
                survive *= 1.0 - p
            x = nx
        payoff = disc * np.maximum(sign * (np.exp(x) - strike), 0) * alive
        if correction:
            payoff = payoff * survive
        killed += int((~alive).sum())
        mom.add(payoff)
    est, se, var = mom.result()
    return {"estimate": est, "standard_error": se, "half_width": 1.959963984540054 * se,
            "steps": steps, "paths": paths, "seed": seed, "correction": correction,
            "discretely_knocked_out_fraction": killed / paths, "seconds": time.perf_counter() - started,
            "spec": {"spot": spot, "strike": strike, "barrier": barrier, "maturity": maturity,
                     "rate": rate, "vol": vol, "direction": direction, "call": call, "dividend": dividend}}
