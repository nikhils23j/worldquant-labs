"""Microsecond-resolution market with heterogeneous agents, built on the matching engine.

Question: does being a few tens of microseconds slower cost a trader anything?

World (one symbol, prices in integer ticks, 1 tick = 1 bp of the 10,000-tick start price):
  * A hidden fundamental value V(t) jumps by +/-1..5 ticks at Poisson times. Every agent
    learns of a jump through a "signal feed" after its own one-way latency.
  * MarketMaker: one bid and one ask of `mm_size` lots at V_mm -/+ half_spread, shifted by
    -inventory_skew * inventory. V_mm is the MM's (delayed) view of V. It only cancels and
    replaces a quote whose price changed or whose size fell below half, so it keeps queue
    priority otherwise. All quotes are post-only.
  * NoiseTraders: Poisson market orders, random side, 1-5 lots, no latency model.
  * MomentumTrader: watches the delayed trade tape; if the last trade price moved >= 2 ticks
    over 20 ms, it sends a 3-lot market order in that direction (10 ms cooldown).
  * QueueJoiners: slow passive traders who join the best bid/ask they last saw (1 ms data
    delay) and cancel after an exponential lifetime; they create queue depth behind the MM.
  * Competitor: a latency-arbitrageur at a fixed one-way latency (default 50 us + jitter).
  * Probe: the agent under study, identical logic, one-way latency swept by the experiment.
      mode "taker":   on a jump, send an IOC at the stale quote if it is >= 1 tick through
                      the new value (a stale-quote race against the Competitor).
      mode "passive": rest 1-lot post-only orders at the observed touch, and on a jump
                      cancel the side that became stale (a cancel race against arbs).

Latency semantics: a one-way latency L applies to every message leg: signal -> agent,
market data -> agent, agent -> exchange, fill report -> agent. Each leg adds independent
exponential jitter (mean `jitter_us`). Each agent has its own RNG stream spawned from the
seed, so changing the probe latency leaves every other agent's random draws unchanged
(common random numbers across the sweep); paths still diverge once the probe trades.

Markouts are measured against the hidden fundamental, not the book midpoint:
  markout_h = side * (V(t_fill + h) - fill_price), in ticks per lot. Positive = good fill.
"""
from bisect import bisect_right
from dataclasses import asdict, dataclass, replace
import math
import numpy as np
from .book import Exchange
from .events import EventLoop
from .orders import Command

HORIZONS_US = (1_000, 10_000, 100_000)

MM, NOISE, MOMENTUM, JOINERS, COMPETITOR, PROBE = 1, 2, 3, 4, 5, 6
NAMES = {MM: "market_maker", NOISE: "noise", MOMENTUM: "momentum", JOINERS: "queue_joiners",
         COMPETITOR: "competitor_arb", PROBE: "probe"}


@dataclass(frozen=True)
class MarketConfig:
    duration_us: int = 5_000_000
    start_price: int = 10_000
    jump_rate_per_s: float = 40.0
    max_jump_ticks: int = 5
    mm_latency_us: int = 400
    mm_half_spread: int = 2
    mm_size: int = 8
    inventory_skew: float = 0.15   # ticks of quote shift per lot of inventory
    mm_refresh_us: int = 50_000
    noise_rate_per_s: float = 150.0
    joiner_rate_per_s: float = 120.0
    joiner_latency_us: int = 1_000
    joiner_life_us: int = 200_000
    momentum_latency_us: int = 1_000
    competitor_latency_us: int = 50
    jitter_us: float = 10.0
    arb_size: int = 5
    min_edge_ticks: int = 1
    probe_latency_us: int = 0
    probe_mode: str = "taker"      # "taker" | "passive" | "off"
    passive_every_us: int = 10_000
    passive_max_age_us: int = 100_000
    stp: str = "cancel_taker"


class Ids:
    def __init__(self):
        self.next = 1

    def __call__(self):
        self.next += 1
        return self.next


class Market:
    def __init__(self, cfg, seed):
        if cfg.probe_mode not in ("taker", "passive", "off"):
            raise ValueError("probe_mode must be taker, passive or off")
        self.cfg = cfg
        self.seed = seed
        ss = np.random.SeedSequence(seed)
        (self.rng_value, self.rng_noise, self.rng_mom, self.rng_join,
         self.rng_comp, self.rng_probe, self.rng_mm) = [np.random.default_rng(s) for s in ss.spawn(7)]
        self.loop = EventLoop()
        self.book = Exchange(stp=cfg.stp)
        self.ids = Ids()
        self.trades = []
        self.owner_of = {}       # oid -> owner
        self.order_info = {}     # oid -> dict for probe / mm orders
        self.rejects = {}
        # Fundamental path
        self.jump_times, self.values = [0], [cfg.start_price]
        # Market maker state (its delayed view)
        self.mm_view = cfg.start_price
        self.mm_inventory = 0
        self.mm_quotes = {"B": None, "S": None}  # side -> (oid, price, size_at_post)
        self.mm_fill_left = {}
        self.inventory_samples = []
        # Delayed public data views
        self.tape = []           # (delivery-independent) trades: (time, price)
        self.mom_last_action = -10**12
        self.joiner_view = (None, None)
        # Arb / probe state
        self.views = {COMPETITOR: (None, None), PROBE: (None, None)}
        self.value_views = {COMPETITOR: cfg.start_price, PROBE: cfg.start_price}
        self.probe_live = {"B": None, "S": None}
        self.stats = {"opportunities_seen": 0, "probe_orders": 0, "probe_cancels_sent": 0}

    # ---- helpers -------------------------------------------------------------------------
    def jitter(self, rng):
        j = self.cfg.jitter_us
        return int(rng.exponential(j)) if j > 0 else 0

    def value_at(self, t):
        return self.values[bisect_right(self.jump_times, t) - 1]

    def send(self, owner, latency, rng, command_fields):
        """Agent -> exchange, arriving after one-way latency + jitter."""
        arrive = self.loop.now + latency + (self.jitter(rng) if rng is not None else 0)
        self.loop.schedule(arrive, self.arrive, owner, command_fields)

    def arrive(self, owner, f):
        if f["oid"] is None:  # noise / momentum market orders get ids at arrival
            f = dict(f, oid=self.ids())
        c = Command(f["kind"], f["oid"], owner, 0, f.get("side", "B"), f.get("qty", 1),
                    f.get("price", 1), self.loop.now, f.get("tif", "GTC"), f.get("post_only", False))
        if c.kind != "cancel":
            self.owner_of[c.oid] = owner
        out = self.book.process(c)
        if not out.accepted:
            self.rejects[out.reason] = self.rejects.get(out.reason, 0) + 1
            if c.kind == "limit" and owner == MM:
                quote = self.mm_quotes[c.side]
                if quote is not None and quote[0] == c.oid:
                    self.mm_quotes[c.side] = None  # MM learns of the reject only via its next requote
            if c.kind == "limit" and owner == PROBE:
                self.loop.schedule(self.loop.now + self.cfg.probe_latency_us, self.probe_done, c.oid)
        if c.kind != "cancel" and c.oid in self.order_info:
            info = self.order_info[c.oid]
            info["arrival_us"] = self.loop.now
            info["accepted"] = out.accepted
            info["reason"] = out.reason
            info["queue_ahead"] = self.book.queue_ahead(c.oid)
        for t in out.trades:
            self.trades.append(t)
            self.tape.append((t.time, t.price))
            self.report_fill(t)
        if out.accepted:
            self.publish()

    def publish(self):
        q = self.book.quote(0)
        now = self.loop.now
        self.loop.schedule(now + self.cfg.joiner_latency_us, self.joiner_quote, q)
        for who, lat, rng in ((COMPETITOR, self.cfg.competitor_latency_us, self.rng_comp),
                              (PROBE, self.cfg.probe_latency_us, self.rng_probe)):
            self.loop.schedule(now + lat + self.jitter(rng), self.arb_quote, who, q, now)

    def report_fill(self, t):
        for oid in (t.maker, t.taker):
            owner = self.owner_of.get(oid)
            if owner == MM:
                is_buy = t.buyer == MM
                self.loop.schedule(self.loop.now + self.cfg.mm_latency_us, self.mm_fill, oid,
                                   t.qty if is_buy else -t.qty)
            elif owner == PROBE and self.cfg.probe_mode == "passive":
                self.loop.schedule(self.loop.now + self.cfg.probe_latency_us, self.probe_done, oid)

    def probe_done(self, oid):
        for side, live in self.probe_live.items():
            if live is not None and live[0] == oid:
                self.probe_live[side] = None

    # ---- fundamental -----------------------------------------------------------------------
    def schedule_jumps(self):
        cfg, rng = self.cfg, self.rng_value
        t, v = 0, cfg.start_price
        while True:
            t += max(1, int(rng.exponential(1e6 / cfg.jump_rate_per_s)))
            if t >= cfg.duration_us:
                break
            v += int(rng.integers(1, cfg.max_jump_ticks + 1)) * (1 if rng.random() < .5 else -1)
            self.jump_times.append(t)
            self.values.append(v)
        for t, v in zip(self.jump_times[1:], self.values[1:]):
            self.loop.schedule(t, self.on_jump, v)

    def on_jump(self, v):
        now = self.loop.now
        self.loop.schedule(now + self.cfg.mm_latency_us, self.mm_signal, v)
        self.loop.schedule(now + self.cfg.competitor_latency_us + self.jitter(self.rng_comp),
                           self.arb_signal, COMPETITOR, v)
        if self.cfg.probe_mode != "off":
            self.loop.schedule(now + self.cfg.probe_latency_us + self.jitter(self.rng_probe),
                               self.arb_signal, PROBE, v)

    # ---- market maker ----------------------------------------------------------------------
    def mm_signal(self, v):
        self.mm_view = v
        self.mm_requote()

    def mm_fill(self, oid, signed_qty):
        self.mm_inventory += signed_qty
        self.mm_fill_left[oid] = self.mm_fill_left.get(oid, 0) + abs(signed_qty)
        self.mm_requote()

    def mm_timer(self):
        self.inventory_samples.append(self.book.positions.get((MM, 0), 0))
        self.mm_requote()
        nxt = self.loop.now + self.cfg.mm_refresh_us
        if nxt < self.cfg.duration_us:
            self.loop.schedule(nxt, self.mm_timer)

    def mm_requote(self):
        cfg = self.cfg
        center = self.mm_view - cfg.inventory_skew * self.mm_inventory
        targets = {"B": math.floor(center) - cfg.mm_half_spread,
                   "S": math.ceil(center) + cfg.mm_half_spread}
        if targets["B"] >= targets["S"]:
            targets["S"] = targets["B"] + 1
        for side, price in targets.items():
            current = self.mm_quotes[side]
            if current is not None:
                oid, cur_price, size = current
                left = size - self.mm_fill_left.get(oid, 0)
                if cur_price == price and left * 2 >= size:
                    continue
                self.send(MM, cfg.mm_latency_us, None, {"kind": "cancel", "oid": oid})
            oid = self.ids()
            self.mm_quotes[side] = (oid, price, cfg.mm_size)
            self.send(MM, cfg.mm_latency_us, None, {"kind": "limit", "oid": oid, "side": side,
                                                    "qty": cfg.mm_size, "price": price,
                                                    "post_only": True})

    # ---- noise / momentum / joiners -----------------------------------------------------------
    def schedule_noise(self):
        cfg, rng = self.cfg, self.rng_noise
        t = 0
        while True:
            t += max(1, int(rng.exponential(1e6 / cfg.noise_rate_per_s)))
            if t >= cfg.duration_us:
                break
            side = "B" if rng.random() < .5 else "S"
            qty = int(rng.integers(1, 6))
            self.loop.schedule(t, self.arrive, NOISE, {"kind": "market", "oid": None, "side": side, "qty": qty})

    def momentum_tick(self):
        cfg = self.cfg
        now = self.loop.now
        visible = now - cfg.momentum_latency_us
        lo = bisect_right(self.tape, (visible - 20_000, float("inf")))
        hi = bisect_right(self.tape, (visible, float("inf")))
        if hi - lo >= 2 and now - self.mom_last_action >= 10_000:
            move = self.tape[hi - 1][1] - self.tape[lo][1]
            if abs(move) >= 2:
                self.mom_last_action = now
                self.send(MOMENTUM, cfg.momentum_latency_us, self.rng_mom,
                          {"kind": "market", "oid": None, "side": "B" if move > 0 else "S", "qty": 3})
        nxt = now + 2_000
        if nxt < cfg.duration_us:
            self.loop.schedule(nxt, self.momentum_tick)

    def joiner_quote(self, q):
        self.joiner_view = q

    def schedule_joiners(self):
        cfg, rng = self.cfg, self.rng_join
        t = 0
        while True:
            t += max(1, int(rng.exponential(1e6 / cfg.joiner_rate_per_s)))
            if t >= cfg.duration_us:
                break
            side = "B" if rng.random() < .5 else "S"
            qty = int(rng.integers(1, 4))
            life = int(rng.exponential(cfg.joiner_life_us))
            self.loop.schedule(t, self.joiner_post, side, qty, life)

    def joiner_post(self, side, qty, life):
        price = self.joiner_view[0 if side == "B" else 1]
        if price is None:
            return
        oid = self.ids()
        self.send(JOINERS, self.cfg.joiner_latency_us, self.rng_join,
                  {"kind": "limit", "oid": oid, "side": side, "qty": qty, "price": price, "post_only": True})
        self.loop.schedule(self.loop.now + self.cfg.joiner_latency_us + life, self.arrive, JOINERS,
                           {"kind": "cancel", "oid": oid})

    # ---- arbitrageurs (competitor and taker probe share logic) ---------------------------------
    def arb_quote(self, who, q, origin):
        self.views[who] = q

    def latency_of(self, who):
        return (self.cfg.competitor_latency_us, self.rng_comp) if who == COMPETITOR else \
               (self.cfg.probe_latency_us, self.rng_probe)

    def arb_signal(self, who, v):
        old = self.value_views[who]
        self.value_views[who] = v
        if who == PROBE and self.cfg.probe_mode == "passive":
            return self.passive_on_jump(v - old)
        bid, ask = self.views[who]
        lat, rng = self.latency_of(who)
        edge = self.cfg.min_edge_ticks
        for side, price in (("B", ask), ("S", bid)):
            if price is None:
                continue
            gain = (v - price) if side == "B" else (price - v)
            if gain >= edge:
                oid = self.ids()
                if who == PROBE:
                    self.stats["opportunities_seen"] += 1
                    self.stats["probe_orders"] += 1
                    self.order_info[oid] = {"oid": oid, "side": side, "price": price, "qty": self.cfg.arb_size,
                                            "decision_us": self.loop.now, "value_at_decision": v,
                                            "kind": "ioc"}
                self.send(who, lat, rng, {"kind": "limit", "oid": oid, "side": side,
                                          "qty": self.cfg.arb_size, "price": price, "tif": "IOC"})

    # ---- passive probe ----------------------------------------------------------------------------
    def passive_tick(self):
        cfg = self.cfg
        bid, ask = self.views[PROBE]
        for side, price in (("B", bid), ("S", ask)):
            live = self.probe_live[side]
            if live is not None:
                oid, sent = live
                if self.loop.now - sent >= cfg.passive_max_age_us:
                    self.probe_cancel(side)
                continue
            if price is None:
                continue
            oid = self.ids()
            self.probe_live[side] = (oid, self.loop.now)
            self.stats["probe_orders"] += 1
            self.order_info[oid] = {"oid": oid, "side": side, "price": price, "qty": 1,
                                    "decision_us": self.loop.now, "kind": "passive"}
            self.send(PROBE, cfg.probe_latency_us, self.rng_probe,
                      {"kind": "limit", "oid": oid, "side": side, "qty": 1, "price": price, "post_only": True})
        nxt = self.loop.now + cfg.passive_every_us
        if nxt < cfg.duration_us:
            self.loop.schedule(nxt, self.passive_tick)

    def probe_cancel(self, side):
        live = self.probe_live[side]
        if live is None:
            return
        self.probe_live[side] = None
        self.stats["probe_cancels_sent"] += 1
        self.send(PROBE, self.cfg.probe_latency_us, self.rng_probe, {"kind": "cancel", "oid": live[0]})

    def passive_on_jump(self, move):
        # Value went up -> our resting ask is now cheap; value went down -> our bid is rich.
        if move > 0:
            self.probe_cancel("S")
        elif move < 0:
            self.probe_cancel("B")

    # ---- run ----------------------------------------------------------------------------------------
    def run(self):
        cfg = self.cfg
        self.schedule_jumps()
        self.schedule_noise()
        self.schedule_joiners()
        self.loop.schedule(0, self.mm_timer)
        self.loop.schedule(1_000, self.momentum_tick)
        if cfg.probe_mode == "passive":
            self.loop.schedule(5_000, self.passive_tick)
        self.loop.run()
        self.book.check()
        return self.report()

    def report(self):
        cfg = self.cfg
        probe_fills, by_counterparty = [], {}
        mm_markouts = {h: [] for h in HORIZONS_US}
        for t in self.trades:
            for oid, is_maker in ((t.maker, True), (t.taker, False)):
                owner = self.owner_of.get(oid)
                side = 1 if (t.buyer == owner) else -1
                if owner == MM:
                    for h in HORIZONS_US:
                        mm_markouts[h].extend([side * (self.value_at(t.time + h) - t.price)] * t.qty)
                if owner == PROBE:
                    other = self.owner_of.get(t.taker if is_maker else t.maker)
                    by_counterparty[NAMES.get(other, "?")] = by_counterparty.get(NAMES.get(other, "?"), 0) + t.qty
                    probe_fills.append({"oid": oid, "time_us": t.time, "price": t.price, "qty": t.qty,
                                        "side": side, "as_maker": is_maker,
                                        "markouts": {str(h): side * (self.value_at(t.time + h) - t.price)
                                                     for h in HORIZONS_US}})
        filled_qty = sum(f["qty"] for f in probe_fills)
        markout = {str(h): (sum(f["markouts"][str(h)] * f["qty"] for f in probe_fills) / filled_qty
                            if filled_qty else None) for h in HORIZONS_US}
        pnl_100ms = sum(f["markouts"][str(HORIZONS_US[-1])] * f["qty"] for f in probe_fills)
        infos = [i for i in self.order_info.values() if "arrival_us" in i]
        accepted = [i for i in infos if i["accepted"]]
        queue = [i["queue_ahead"] for i in accepted if i.get("queue_ahead") is not None]
        submitted = sum(i["qty"] for i in self.order_info.values())
        fills_by_oid = {}
        for f in probe_fills:
            fills_by_oid[f["oid"]] = fills_by_oid.get(f["oid"], 0) + f["qty"]
        # Queue position vs outcome for passive orders that actually rested.
        rested = [i for i in accepted if i.get("queue_ahead") is not None]
        filled_rested = [i for i in rested if fills_by_oid.get(i["oid"])]
        mm_inv = self.book.positions.get((MM, 0), 0)
        jumps = len(self.jump_times) - 1
        out = {
            "seed": self.seed, "config": asdict(cfg), "jumps": jumps, "trades": len(self.trades),
            "volume": self.book.volume,
            "probe": {
                "orders": self.stats["probe_orders"], "submitted_qty": submitted,
                "filled_qty": filled_qty, "fill_rate": filled_qty / submitted if submitted else None,
                "markout_ticks_per_lot": markout,
                "pnl_ticks_100ms": pnl_100ms,
                "pnl_ticks": {str(h): sum(f["markouts"][str(h)] * f["qty"] for f in probe_fills) for h in HORIZONS_US},
                "pnl_ticks_100ms_per_jump": pnl_100ms / jumps if jumps else None,
                "fills_by_counterparty": by_counterparty,
                "mean_queue_ahead_on_arrival": (sum(queue) / len(queue)) if queue else None,
                "rested_orders": len(rested), "rested_orders_filled": len(filled_rested),
                "cancels_sent": self.stats["probe_cancels_sent"],
                "rejected": sum(1 for i in infos if not i["accepted"]),
            },
            "market_maker": {
                "final_inventory": mm_inv,
                "mean_abs_inventory_sampled": (sum(abs(x) for x in self.inventory_samples)
                                               / len(self.inventory_samples)) if self.inventory_samples else None,
                "max_abs_inventory_sampled": max((abs(x) for x in self.inventory_samples), default=None),
                "pnl_ticks_marked_to_final_value": self.book.cash.get(MM, 0) + mm_inv * self.values[-1],
                "markout_ticks_per_lot": {str(h): (sum(v) / len(v) if v else None) for h, v in mm_markouts.items()},
                "filled_qty": len(mm_markouts[HORIZONS_US[0]]),
            },
            "competitor_filled_qty": sum(t.qty for t in self.trades
                                         if COMPETITOR in (self.owner_of.get(t.maker), self.owner_of.get(t.taker))),
            "rejects": self.rejects,
        }
        return out


def run_market(seed=1, **overrides):
    return Market(replace(MarketConfig(), **overrides), seed).run()
