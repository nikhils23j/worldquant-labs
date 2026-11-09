from dataclasses import asdict, replace
import math
import time
import numpy as np
from .analytics import analytic_price
from .paths import payoff_batches

METHODS = ("plain", "antithetic", "control", "antithetic_control")


class Moments:
    """Stable parallel-Welford aggregation of independent sampling units."""
    def __init__(self):
        self.n, self.mean, self.m2 = 0, 0., 0.
    def add(self, x):
        if not len(x):
            return
        n = len(x)
        mean = float(np.mean(x))
        m2 = float(np.sum((x-mean)**2))
        delta = mean-self.mean
        total = self.n+n
        self.m2 += m2 + delta*delta*self.n*n/total
        self.mean += delta*n/total
        self.n = total
    def result(self):
        variance = self.m2/(self.n-1) if self.n > 1 else 0.
        return self.mean, math.sqrt(variance/self.n), variance

def price(c, m, method="control", paths=100000, seed=42, batch_size=10000, pilot_paths=10000):
    """Monte Carlo price with a 95% normal interval.

    methods: plain | antithetic | control (geometric-Asian control, independent pilot) |
             antithetic_control (both: antithetic pairs are the sampling units and the
             control coefficient is fitted on an independent antithetic pilot).
    """
    if method not in METHODS:
        raise ValueError("Unknown estimator")
    anti = method in ("antithetic", "antithetic_control")
    ctrl = method in ("control", "antithetic_control")
    if type(paths) is not int or paths < 2 or type(batch_size) is not int or batch_size < 1:
        raise ValueError("At least two paths and a positive integer batch_size required")
    if ctrl and c.kind != "asian":
        raise ValueError("Geometric-Asian control applies only to arithmetic-Asian contracts")
    if anti and (paths % 2 or paths < 4):
        raise ValueError("At least two complete antithetic pairs required")
    if ctrl and (type(pilot_paths) is not int or pilot_paths < 2 or (anti and pilot_paths % 2)):
        raise ValueError("At least two independent pilot paths required")
    started = time.perf_counter()
    streams = np.random.SeedSequence(seed).spawn(2)
    coefficient, expected, pilot = 0., 0., 0
    if ctrl:
        pilot = pilot_paths
        ys, xs = [], []
        for y, x in payoff_batches(c, m, pilot, streams[0], batch_size, anti):
            ys.append(y); xs.append(x)
        y, x = np.concatenate(ys), np.concatenate(xs)
        centered = x-x.mean()
        denominator = float(centered@centered)
        coefficient = float(centered@(y-y.mean())/denominator) if denominator > 1e-20 else 0.
        expected = analytic_price(replace(c, kind="geometric"), m)
    moments = Moments()
    for y, x in payoff_batches(c, m, paths, streams[1], batch_size, anti):
        moments.add(y-coefficient*(x-expected))
    estimate, se, variance = moments.result()
    half = 1.959963984540054*se
    return {"estimate": estimate, "standard_error": se, "ci95": [estimate-half, estimate+half],
            "half_width": half, "sample_variance": variance, "independent_units": moments.n,
            "paths": paths, "pilot_paths": pilot, "total_generated_paths": paths+pilot,
            "control_coefficient": coefficient, "method": method, "seed": seed,
            "batch_size": batch_size, "seconds": time.perf_counter()-started,
            "contract": asdict(c), "model": asdict(m),
            "interval_note": "Normal-approximation Monte Carlo interval; excludes model error."}
