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
