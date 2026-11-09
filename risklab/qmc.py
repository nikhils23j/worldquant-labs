"""Randomised quasi-Monte Carlo (scrambled Sobol) with Brownian-bridge path construction.

Why the bridge matters: Sobol points are most evenly spread in their first few
coordinates. With the usual step-by-step construction, coordinate j drives only the
j-th increment, so the "good" coordinates are wasted on the first few days. The bridge
spends coordinate 0 on W(T), coordinate 1 on W(T/2), then quarter points and so on,
so the low-index coordinates decide the large-scale shape of the path that the payoff
actually depends on (lower "effective dimension").

Error bars for QMC: a single Sobol estimate has no usable sample variance, so we use
R independent scramblings (randomised QMC). Each scrambling gives an unbiased estimate;
the spread across scramblings gives the standard error.
"""
from functools import lru_cache
import math
import numpy as np
from scipy.special import ndtri
from scipy.stats import qmc

_EPS = 2.0 ** -53


@lru_cache(maxsize=32)
def bridge_plan(steps):
    """Return a BFS bisection order: list of (index, left, right, w_left, w_right, sd) on unit time grid.

    Times are t_i = (i+1)/steps for i in [0, steps); left == -1 means time 0 where W = 0.
    Coordinate 0 sets W at the final index; coordinate k+1 fills plan[k].
    """
    t = [(i + 1) / steps for i in range(steps)]
    plan = []
    queue = [(-1, steps - 1)]
    head = 0
    while head < len(queue):
        left, right = queue[head]
        head += 1
        if right - left <= 1:
            continue
        mid = (left + right) // 2
        tl = 0.0 if left < 0 else t[left]
        tr, tm = t[right], t[mid]
        plan.append((mid, left, right, (tr - tm) / (tr - tl), (tm - tl) / (tr - tl),
                     math.sqrt((tm - tl) * (tr - tm) / (tr - tl))))
        queue.append((left, mid))
        queue.append((mid, right))
    assert len(plan) == steps - 1
    return tuple(plan)


def brownian(z, maturity, bridge):
    """Map standard normals z (n, steps) to Brownian values W(t_i) (n, steps)."""
    n, steps = z.shape
    if not bridge:
        return np.cumsum(z, axis=1) * math.sqrt(maturity / steps)
    w = np.empty_like(z)
    w[:, steps - 1] = z[:, 0]  # unit-time grid first, scaled by sqrt(T) at the end
    for k, (mid, left, right, wl, wr, sd) in enumerate(bridge_plan(steps), start=1):
        base = wr * w[:, right]
        if left >= 0:
            base = base + wl * w[:, left]
        w[:, mid] = base + sd * z[:, k]
    return w * math.sqrt(maturity)


class NormalSource:
    """Streams standard normals from a seeded PRNG or a scrambled Sobol sequence.

    Sobol draws must come in powers of two to keep the net balance properties; the
    stream is sequential, so drawing 2^a then 2^a again equals the first 2^(a+1) points.
    """
    def __init__(self, kind, dims, seed):
        if kind not in ("prng", "sobol"):
            raise ValueError("kind must be prng or sobol")
        self.kind, self.dims = kind, dims
        if kind == "prng":
            self.rng = np.random.default_rng(seed)
        else:
            self.sampler = qmc.Sobol(d=dims, scramble=True, seed=np.random.default_rng(seed))

    def draw(self, n):
        if self.kind == "prng":
            return self.rng.standard_normal((n, self.dims))
        if n & (n - 1):
            raise ValueError("Sobol batches must be powers of two")
        u = self.sampler.random(n)
        return ndtri(np.clip(u, _EPS, 1 - _EPS))


def path_summaries(w, model, maturity):
    """Exact GBM from Brownian values: returns (S_T, arithmetic mean, geometric mean) over the fixings."""
    steps = w.shape[1]
    t = maturity * np.arange(1, steps + 1) / steps
    logs = math.log(model.spot) + (model.rate - model.dividend - .5 * model.volatility ** 2) * t + model.volatility * w
    s = np.exp(logs)
    return s[:, -1], s.mean(axis=1), np.exp(logs.mean(axis=1))


def payoffs(c, model, z, bridge, antithetic=False):
    """Discounted (target, geometric-control) payoffs per path (pair averages when antithetic)."""
    steps = 1 if c.kind == "european" else c.fixings
    disc = math.exp(-model.rate * c.maturity)
    sign = 1 if c.call else -1

    def one(zz):
        st, arith, geo = path_summaries(brownian(zz, c.maturity, bridge), model, c.maturity)
        under = st if c.kind == "european" else (geo if c.kind == "geometric" else arith)
        return (disc * np.maximum(sign * (under - c.strike), 0),
                disc * np.maximum(sign * (geo - c.strike), 0))
    assert z.shape[1] == steps
    y, x = one(z)
    if antithetic:
        y2, x2 = one(-z)
        y, x = (y + y2) / 2, (x + x2) / 2
    return y, x


def prefix_estimates(c, model, kind, bridge, seed, log2_sizes, antithetic=False,
                     control=None, batch=4096):
    """One replication: estimates at N = 2^k for every k in log2_sizes, as prefixes of one stream.

    control: None, or (beta, expected) for a geometric-Asian control variate with fixed beta.
    For antithetic, N counts paths (N/2 pairs, each using z and -z).
    """
    steps = 1 if c.kind == "european" else c.fixings
    src = NormalSource(kind, steps, seed)
    targets = sorted(1 << k for k in log2_sizes)
    total, done, out = 0.0, 0, {}
    per = 2 if antithetic else 1
    while done < targets[-1]:
        n_units = min(batch, (targets[-1] - done) // per)
        # stop at the next checkpoint so prefixes are exact
        nxt = next(t for t in targets if t > done)
        n_units = min(n_units, (nxt - done) // per)
        y, x = payoffs(c, model, src.draw(n_units), bridge, antithetic)
        if control is not None:
            beta, expected = control
            y = y - beta * (x - expected)
        total += float(y.sum())
        done += n_units * per
        if done in targets:
            out[done] = total / (done // per)
    return out
