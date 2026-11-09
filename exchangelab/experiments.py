from collections import Counter
from dataclasses import asdict, replace
import hashlib
import json
import platform
import resource
import statistics
import sys
import time
import numpy as np
from .orders import Command
from .book import Exchange
from .reference import ReferenceExchange
from .book_v0 import ExchangeV0
from .events import EventLoop
from .agents import MarketMaker, BackgroundTrader, LatencyProbe

ENGINES = {"fast": Exchange, "v0": ExchangeV0, "reference": ReferenceExchange}

def load_average():
    try:
        import os
        return list(os.getloadavg())
    except OSError:
        return None

def environment():
    return {"python": sys.version, "platform": platform.platform(), "numpy": np.__version__,
            "cpu": "Apple M2" if platform.machine() == "arm64" and sys.platform == "darwin" else platform.processor(),
            "load_average_1_5_15": load_average()}

def rss_mib():
    r = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return r / (1024**2 if sys.platform == "darwin" else 1024)

def workload(count, seed=42, symbols=10):
    rng = np.random.default_rng(seed)
    kinds = rng.choice(3, count, p=[.6, .2, .2])
    syms = rng.integers(symbols, size=count)
    sides = rng.integers(2, size=count)
    prices = rng.integers(9985, 10016, size=count)
    qtys = rng.integers(1, 11, size=count)
    history = []
    for i in range(count):
        if kinds[i] == 2 and history:
            oid, owner, symbol = history[int(rng.integers(max(0, len(history)-2000), len(history)))]
            yield Command("cancel", oid, owner, symbol, time=i)
        else:
            kind = "limit" if kinds[i] != 1 else "market"
            c = Command(kind, i, 10 + i % 32, int(syms[i]), "B" if sides[i] else "S",
                        int(qtys[i]), int(prices[i]), i)
            if kind == "limit":
                history.append((i, c.owner, c.symbol))
            yield c

def workload_v2(count, seed=7, symbols=10, owners=8):
    """Richer mix exercising every order type: GTC/IOC/post-only limits, markets, cancels.

    Only 8 owners, so self-trade prevention is triggered often.
    Approximate mix: 40% GTC, 10% IOC, 10% post-only, 15% market, 25% cancel.
    """
    rng = np.random.default_rng(seed)
    kinds = rng.choice(5, count, p=[.40, .10, .10, .15, .25])
    syms = rng.integers(symbols, size=count)
    sides = rng.integers(2, size=count)
    prices = rng.integers(9980, 10021, size=count)
    qtys = rng.integers(1, 11, size=count)
    who = rng.integers(owners, size=count)
    history = []
    for i in range(count):
        k = kinds[i]
        if k == 4 and history:
            oid, owner, symbol = history[int(rng.integers(max(0, len(history)-1500), len(history)))]
            yield Command("cancel", oid, owner, symbol, time=i)
            continue
        side = "B" if sides[i] else "S"
        owner = 100 + int(who[i])
        if k == 3:
            yield Command("market", i, owner, int(syms[i]), side, int(qtys[i]), time=i)
            continue
        tif = "IOC" if k == 1 else "GTC"
        c = Command("limit", i, owner, int(syms[i]), side, int(qtys[i]), int(prices[i]), i,
                    tif=tif, post_only=bool(k == 2))
        if tif == "GTC":
            history.append((i, owner, c.symbol))
        yield c

def replay(commands, reference=False, check_every=0, instrument=False, engine=None):
    engine = engine or ("reference" if reference else "fast")
    reference = engine == "reference"
    book = ENGINES[engine]()
    reasons, kinds = Counter(), Counter()
    delays = []
    peak_orders = peak_levels = 0
    volume = trades = count = 0
    start = time.perf_counter()
    for count, c in enumerate(commands, 1):
        tick = time.perf_counter_ns() if instrument else 0
        out = book.process(c)
        if instrument:
            delays.append(time.perf_counter_ns() - tick)
        kinds[c.kind] += 1
        if not out.accepted:
            reasons[out.reason] += 1
        volume += sum(t.qty for t in out.trades)
        trades += len(out.trades)
        if engine == "fast" and (count % 1000 == 0 or count == 1):
            peak_orders = max(peak_orders, len(book.orders))
            peak_levels = max(peak_levels, sum(len(s.levels) for pair in book.books.values() for s in pair))
        if check_every and engine == "fast" and count % check_every == 0:
            book.check()
    seconds = time.perf_counter() - start
    if engine == "fast":
        book.check()
    digest = hashlib.sha256(json.dumps(book.state(), separators=(",", ":")).encode()).hexdigest()
    return {"engine": engine, "commands": count, "seconds": seconds, "commands_per_second": count / max(seconds, 1e-12),
            "p95_us": float(np.percentile(delays, 95) / 1000) if delays else None,
            "rejections": dict(reasons), "command_mix": dict(kinds), "trades": trades,
            "volume": volume, "state_sha256": digest, "peak_live_orders_sampled": peak_orders,
            "peak_price_levels_sampled": peak_levels, "peak_process_rss_mib": rss_mib()}

def differential(count=100000, seed=42, mix="v1", stp="none", state_every=1000):
    """Feed one stream to the fast engine and the list-scan oracle; compare every outcome.

    Every response (accept/reject, reason, each trade, residual, STP cancels) must be
    identical, the full resting state is compared every `state_every` commands, and the
    fast engine's invariants (FIFO, no crossed book, cash/position conservation) are checked.
    """
    fast, slow = Exchange(stp=stp), ReferenceExchange(stp=stp)
    stream = workload(count, seed) if mix == "v1" else workload_v2(count, seed)
    reasons, kinds, stp_events = Counter(), Counter(), 0
    trades = 0
    start = time.perf_counter()
    for i, c in enumerate(stream, 1):
        a, b = fast.process(c), slow.process(c)
        if a != b:
            raise AssertionError((i, c, a, b))
        kinds[c.kind if c.kind != "limit" else ("post_only" if c.post_only else c.tif)] += 1
        reasons[a.reason] += 1
        trades += len(a.trades)
        stp_events += len(a.stp_cancelled)
        if i % state_every == 0:
            fast.check()
            assert fast.state() == slow.state(), i
    assert fast.state() == slow.state()
    fast.check()
    return {"commands": count, "mismatches": 0, "seconds": time.perf_counter()-start,
            "seed": seed, "mix": mix, "stp": stp, "final_orders": len(fast.orders),
            "trades": trades, "stp_cancellations": stp_events, "reasons": dict(reasons),
            "command_mix": dict(kinds), "state_compared_every": state_every}

def differential_suite(v1_count=1000000, v2_count=200000):
    runs = [differential(v1_count, 42)]
    for stp in ("none", "cancel_resting", "cancel_taker"):
        runs.append(differential(v2_count, 7, mix="v2", stp=stp))
    return {"environment": environment(), "runs": runs,
            "total_commands": sum(r["commands"] for r in runs),
            "total_mismatches": sum(r["mismatches"] for r in runs)}

def simulate(seed=1, latency_ms=0, duration_ms=2000, symbols=1, trace=False):
    rng = np.random.default_rng(seed)
    loop, book = EventLoop(), Exchange()
    maker, background, probe = MarketMaker(), BackgroundTrader(), LatencyProbe()
    history, trades = [], []
    delay = int(latency_ms * 1000)

    def deliver_quote(symbol, quote, origin):
        probe.observe(symbol, quote, origin, loop.now)

    def process(c):
        c = replace(c, time=loop.now)
        out = book.process(c)
        trades.extend(out.trades)
        q = book.quote(c.symbol)
        loop.schedule(loop.now + delay, deliver_quote, c.symbol, q, loop.now)
        if trace:
            history.append({"time_us": loop.now, "command": asdict(c), "accepted": out.accepted,
                            "reason": out.reason, "trades": [t._asdict() for t in out.trades],
                            "depth": book.depth(c.symbol), "quote": q})

    def decide(i):
        c = probe.order(loop.now, i, i % symbols)
        if c:
            loop.schedule(loop.now + delay, process, c)
            loop.schedule(loop.now + delay + 60000, process, replace(c, kind="cancel"))

    for cycle, ms in enumerate(range(0, duration_ms, 10)):
        for c in maker.quotes(ms * 1000, cycle, symbols):
            loop.schedule(c.time, process, c)
            loop.schedule(c.time + 40000, process, replace(c, kind="cancel"))
    for i in range(1, duration_ms):
        c = background.order(rng, i, symbols)
        loop.schedule(c.time, process, c)
    for i, ms in enumerate(range(5, duration_ms - 100, 30)):
        loop.schedule(ms * 1000, decide, i)
    loop.run()
    book.check()
    decision_by_id = {d["oid"]: d for d in probe.decisions}
    fills = {oid: [] for oid in decision_by_id}
    for t in trades:
        for oid in (t.maker, t.taker):
            if oid in fills:
                fills[oid].append(t)
    submitted = sum(d["qty"] for d in probe.decisions)
    filled = sum(t.qty for ts in fills.values() for t in ts)
    costs, first, complete = [], [], []
    for oid, ts in fills.items():
        d = decision_by_id[oid]
        total = sum(t.qty for t in ts)
        if total:
            first.append((ts[0].time - d["time"] - delay) / 1000)
            if total == d["qty"]:
                complete.append((ts[-1].time - d["time"] - delay) / 1000)
            for t in ts:
                costs.extend([(t.price - d["mid"]) / d["mid"] * 10000 * (1 if d["side"] == "B" else -1)] * t.qty)
        d["filled_qty"] = total
    report = {"seed": seed, "latency_ms": latency_ms, "symbols": symbols,
              "submitted_qty": submitted, "filled_qty": filled,
              "fill_rate": filled / submitted if submitted else None,
              "unfilled_orders": sum(not x for x in fills.values()),
              "first_fill_mean_ms": statistics.mean(first) if first else None,
              "complete_fill_mean_ms": statistics.mean(complete) if complete else None,
              "execution_cost_bps": statistics.mean(costs) if costs else None,
              "decisions": probe.decisions, "trades": [t._asdict() for t in trades if t.maker in fills or t.taker in fills],
              "history": history}
    report["sha256"] = hashlib.sha256(json.dumps(report, sort_keys=True).encode()).hexdigest()
    return report

def latency_sweep():
    rows = []
    for seed in range(1, 6):
        for delay in (0, 1, 10, 50):
            r = simulate(seed, delay, symbols=3)
            rows.append({k:v for k,v in r.items() if k not in {"history", "decisions", "trades"}})
    for row in rows:
        baseline = next(r for r in rows if r["seed"] == row["seed"] and r["latency_ms"] == 0)
        for key in ("fill_rate", "execution_cost_bps", "first_fill_mean_ms"):
            row[key + "_delta_vs_zero"] = (row[key] - baseline[key]
                                          if row[key] is not None and baseline[key] is not None else None)
    return rows

def wait_for_quiet(max_load=2.5, max_wait=180, poll=10):
    """Wait (bounded) for the 1-minute load average to drop before timing."""
    waited = 0
    while waited < max_wait:
        la = load_average()
        if la is None or la[0] <= max_load:
            break
        time.sleep(poll)
        waited += poll
    return {"waited_seconds": waited, "load_after_wait": load_average(), "threshold": max_load}

def benchmark(count=1000000, repetitions=5, quiet_wait=True):
    """Time the original (v0) and optimised engines on the same pre-generated stream.

    Runs are interleaved v0, fast, v0, fast, ... so background load hits both alike.
    Only the replay loop is timed (dispatch, matching and incremental metrics).
    """
    started = time.perf_counter()
    commands = list(workload(count))
    generation = time.perf_counter() - started
    quiet = wait_for_quiet() if quiet_wait else None
    load_before = load_average()
    replay(commands, engine="v0"); replay(commands, engine="fast")  # warm-up
    runs = {"v0": [], "fast": []}
    for _ in range(repetitions):
        for name in ("v0", "fast"):
            r = replay(commands, engine=name)
            r["load_average_after"] = load_average()
            runs[name].append(r)
    load_after = load_average()
    hashes = {r["state_sha256"] for rs in runs.values() for r in rs}
    assert len(hashes) == 1, "engines disagree on final state"
    stress = replay(commands, check_every=1000)
    small = {}
    for n in (10000, min(100000, count)):
        small[str(n)] = {"fast": replay(commands[:n]), "reference": replay(commands[:n], engine="reference")}
        assert small[str(n)]["fast"]["state_sha256"] == small[str(n)]["reference"]["state_sha256"]
    med = {k: statistics.median(r["seconds"] for r in v) for k, v in runs.items()}
    return {"environment": environment(), "seed": 42, "count": count, "repetitions": repetitions,
            "quiet_wait": quiet, "load_average_before": load_before, "load_average_after": load_after,
            "generation_seconds": generation, "runs": runs,
            "median_seconds": med, "min_seconds": {k: min(r["seconds"] for r in v) for k, v in runs.items()},
            "max_seconds": {k: max(r["seconds"] for r in v) for k, v in runs.items()},
            "speedup_median": med["v0"] / med["fast"],
            "identical_final_state_sha256": hashes.pop(), "stress": stress,
            "instrumented": replay(commands[:100000], instrument=True), "comparison": small,
            "end_to_end_seconds": time.perf_counter() - started}
