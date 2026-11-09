"""Heston stochastic-volatility paths with the full-truncation Euler scheme.

dS/S = (r - q) dt + sqrt(v) dW_s,  dv = kappa (theta - v) dt + xi sqrt(v) dW_v,  corr(dW_s, dW_v) = rho.

Euler can push v below zero. "Full truncation" (Lord, Koekkoek & van Dijk, 2010) keeps the
raw v but uses v+ = max(v, 0) in both drift and diffusion; it is the least-biased of the
simple fixes. The log-price is stepped exactly given v+, so S stays positive. The scheme is
biased in dt, which is why the study reports bias vs steps against QuantLib's semi-analytic price.
When the Feller condition 2*kappa*theta > xi^2 fails, v hits zero often and the bias is larger.
"""
from dataclasses import dataclass, asdict
import math
import time
import numpy as np
from .estimators import Moments


@dataclass(frozen=True)
class HestonParams:
    v0: float = 0.04
    kappa: float = 2.0
    theta: float = 0.04
    xi: float = 0.3
    rho: float = -0.7

    @property
    def feller(self):
        return 2 * self.kappa * self.theta > self.xi ** 2


def heston_call_mc(spot, strike, maturity, rate, p, steps=52, paths=200000, seed=42,
                   batch_size=20000, dividend=0.0, call=True):
    rng = np.random.default_rng(seed)
    dt = maturity / steps
    sq = math.sqrt(dt)
    rho_c = math.sqrt(1 - p.rho ** 2)
    disc = math.exp(-rate * maturity)
    sign = 1 if call else -1
    mom = Moments()
    negative_steps = 0
    started = time.perf_counter()
    for start in range(0, paths, batch_size):
        n = min(batch_size, paths - start)
        x = np.full(n, math.log(spot))
        v = np.full(n, p.v0)
        for _ in range(steps):
            z1 = rng.standard_normal(n)
            z2 = p.rho * z1 + rho_c * rng.standard_normal(n)
            vp = np.maximum(v, 0.0)
            negative_steps += int((v < 0).sum())
            root = np.sqrt(vp)
            x += (rate - dividend - .5 * vp) * dt + root * sq * z2
            v = v + p.kappa * (p.theta - vp) * dt + p.xi * root * sq * z1
        mom.add(disc * np.maximum(sign * (np.exp(x) - strike), 0))
    est, se, _ = mom.result()
    return {"estimate": est, "standard_error": se, "steps": steps, "paths": paths, "seed": seed,
            "negative_variance_fraction": negative_steps / (paths * steps),
            "seconds": time.perf_counter() - started, "params": asdict(p), "feller": p.feller,
            "spec": {"spot": spot, "strike": strike, "maturity": maturity, "rate": rate,
                     "dividend": dividend, "call": call}}
