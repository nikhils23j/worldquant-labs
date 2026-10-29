import math
import numpy as np

def summaries_from_shocks(shocks, model, maturity):
    """Testable vector kernel: rows=paths, columns=equally spaced future dates."""
    n, steps = shocks.shape
    dt = maturity / steps
    logspot = np.full(n, math.log(model.spot))
    total, logtotal = np.zeros(n), np.zeros(n)
    drift = (model.rate-model.dividend-.5*model.volatility**2)*dt
    scale = model.volatility * math.sqrt(dt)
    for j in range(steps):
        logspot += drift + scale * shocks[:, j]
        total += np.exp(logspot)
        logtotal += logspot
    return np.exp(logspot), total/steps, np.exp(logtotal/steps)

def scalar_summaries(shocks, model, maturity):
    rows = []
    dt = maturity / shocks.shape[1]
    for path in shocks:
        value = math.log(model.spot)
        total = logs = 0.0
        for z in path:
            value += (model.rate-model.dividend-.5*model.volatility**2)*dt + model.volatility*math.sqrt(dt)*float(z)
            total += math.exp(value)
            logs += value
        rows.append((math.exp(value), total/len(path), math.exp(logs/len(path))))
    return tuple(np.asarray(rows)[:, i] for i in range(3))

def payoff_batches(c, m, paths, seed, batch_size=10000, antithetic=False):
    """Stream payoff pairs. Antithetic output rows are pair averages.

    Batch size is part of the reproducibility contract: changing it changes
    how the seeded RNG stream maps to paths. Same seed/batch keeps bumped
    scenarios paired for common-random-number Greeks.
    """
    if antithetic and paths % 2:
        raise ValueError("Antithetic path count must be even")
    rng = np.random.default_rng(seed)
    total_units = paths//2 if antithetic else paths
    steps = 1 if c.kind == "european" else c.fixings
    dt = c.maturity/steps
    drift = (m.rate-m.dividend-.5*m.volatility**2)*dt
    scale = m.volatility*math.sqrt(dt)
    discount = math.exp(-m.rate*c.maturity)
    sign = 1 if c.call else -1
    for start in range(0, total_units, batch_size):
        n = min(batch_size, total_units-start)
        logspot = np.full(n, math.log(m.spot))
        total, logtotal = np.zeros(n), np.zeros(n)
        if antithetic:
            opposite = logspot.copy()
            negtotal, neglogs = np.zeros(n), np.zeros(n)
        for _ in range(steps):
            z = rng.standard_normal(n)
            logspot += drift + scale*z
            total += np.exp(logspot)
            logtotal += logspot
            if antithetic:
                opposite += drift-scale*z
                negtotal += np.exp(opposite)
                neglogs += opposite
        underlying = np.exp(logspot) if c.kind == "european" else (np.exp(logtotal/steps) if c.kind == "geometric" else total/steps)
        y = discount*np.maximum(sign*(underlying-c.strike), 0)
        geometric = discount*np.maximum(sign*(np.exp(logtotal/steps)-c.strike), 0)
        if antithetic:
            negunder = np.exp(opposite) if c.kind == "european" else (np.exp(neglogs/steps) if c.kind == "geometric" else negtotal/steps)
            y = (y+discount*np.maximum(sign*(negunder-c.strike), 0))/2
            geometric = (geometric+discount*np.maximum(sign*(np.exp(neglogs/steps)-c.strike), 0))/2
        yield y, geometric
