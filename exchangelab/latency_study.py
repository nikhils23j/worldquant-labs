"""Latency x seed sweeps over the agent market in market.py, with paired confidence intervals.

Every latency setting is run on the same seeds. Because each agent draws from its own
spawned RNG stream, the fundamental path, noise flow, joiners and competitor jitter are
identical across latencies for a given seed (common random numbers). We therefore report
both the per-latency mean and the *paired* difference versus the fastest setting, whose
interval is usually much tighter.
"""
import math
import statistics
import time
from scipy import stats
from .market import run_market, HORIZONS_US
from .experiments import environment

TAKER_LATENCIES = (0, 10, 20, 30, 40, 50, 60, 70, 80, 100, 150, 200, 300, 500, 1000, 2000)
PASSIVE_LATENCIES = (0, 10, 25, 50, 75, 100, 150, 200, 300, 500, 1000, 2000)


def ci95(xs):
    xs = [x for x in xs if x is not None]
    n = len(xs)
    if n == 0:
        return {"mean": None, "half_width": None, "n": 0}
    mean = statistics.fmean(xs)
    if n == 1:
        return {"mean": mean, "half_width": None, "n": 1}
    half = stats.t.ppf(.975, n - 1) * statistics.stdev(xs) / math.sqrt(n)
    return {"mean": mean, "half_width": float(half), "n": n}


def _metrics(r):
    p = r["probe"]
    out = {"filled_qty": p["filled_qty"], "orders": p["orders"], "fill_rate": p["fill_rate"],
           "competitor_filled_qty": r["competitor_filled_qty"],
           "mean_queue_ahead_on_arrival": p["mean_queue_ahead_on_arrival"],
           "picked_off_by_arbs_qty": p["fills_by_counterparty"].get("competitor_arb", 0),
           "rested_fill_fraction": (p["rested_orders_filled"] / p["rested_orders"]) if p["rested_orders"] else None}
    for h in HORIZONS_US:
        out[f"pnl_ticks_{h}us"] = p["pnl_ticks"][str(h)]
        out[f"markout_ticks_per_lot_{h}us"] = p["markout_ticks_per_lot"][str(h)]
    return out


def curve(mode, latencies, seeds, **overrides):
    rows = []
    for L in latencies:
        for seed in seeds:
            r = run_market(seed, probe_latency_us=L, probe_mode=mode, **overrides)
            rows.append({"latency_us": L, "seed": seed, "jumps": r["jumps"], **_metrics(r)})
    base = {row["seed"]: row for row in rows if row["latency_us"] == latencies[0]}
    keys = [k for k in rows[0] if k not in ("latency_us", "seed")]
    summary = []
    for L in latencies:
        data = [row for row in rows if row["latency_us"] == L]
        item = {"latency_us": L}
        for k in keys:
            item[k] = ci95([d[k] for d in data])
            if k.startswith("pnl") or k in ("filled_qty", "picked_off_by_arbs_qty"):
                item[k + "_paired_diff_vs_fastest"] = ci95(
                    [d[k] - base[d["seed"]][k] for d in data
                     if d[k] is not None and base[d["seed"]][k] is not None])
        summary.append(item)
    return {"mode": mode, "latencies_us": list(latencies), "seeds": list(seeds), "summary": summary, "runs": rows}


def skew_study(seeds, skews=(0.0, 0.15)):
    out = []
    for skew in skews:
        mms = [run_market(s, probe_mode="off", inventory_skew=skew)["market_maker"] for s in seeds]
        out.append({"inventory_skew": skew,
                    "mean_abs_inventory": ci95([m["mean_abs_inventory_sampled"] for m in mms]),
                    "max_abs_inventory": ci95([m["max_abs_inventory_sampled"] for m in mms]),
                    "pnl_ticks": ci95([m["pnl_ticks_marked_to_final_value"] for m in mms]),
                    "markout_1ms_ticks_per_lot": ci95([m["markout_ticks_per_lot"]["1000"] for m in mms])})
    return out


def study(seeds=30, taker=TAKER_LATENCIES, passive=PASSIVE_LATENCIES):
    from .market import MarketConfig
    from dataclasses import asdict
    started = time.perf_counter()
    seed_list = list(range(1, seeds + 1))
    result = {"environment": environment(), "base_config": asdict(MarketConfig()),
              "horizons_us": list(HORIZONS_US),
              "taker": curve("taker", taker, seed_list),
              "passive": curve("passive", passive, seed_list),
              "competitor_latency_check": curve("taker", (0, 50, 100), seed_list[:10], competitor_latency_us=500),
              "inventory_skew": skew_study(seed_list)}
    result["seconds"] = time.perf_counter() - started
    result["note"] = ("Synthetic agent market. Markouts are versus the hidden fundamental value. "
                      "Intervals are t-based 95% over seeds; paired differences use the same seeds.")
    return result
