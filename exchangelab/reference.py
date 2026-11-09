"""Independent, deliberately slow list-scan oracle for matching semantics.

It shares only the message/result types with book.py. Every decision (best price,
time priority, post-only crossing, self-trade prevention) is re-derived by scanning
a flat list, so a bug in the fast engine's indexing cannot hide here.
"""
from .orders import Trade, Outcome


class ReferenceExchange:
    def __init__(self, max_live=10000, stp="none"):
        if stp not in ("none", "cancel_resting", "cancel_taker"):
            raise ValueError("unknown stp mode")
        self.max_live = max_live
        self.stp = stp
        self.rows = []  # [command, remaining quantity, arrival index]
        self.seen = set()
        self.sequence = 0
        self.last_time = -1

    def _crossing(self, c):
        return [r for r in self.rows if r[0].symbol == c.symbol and r[0].side != c.side
                and (c.kind == "market"
                     or (r[0].price <= c.price if c.side == "B" else r[0].price >= c.price))]

    def process(self, c):
        if not c.valid():
            return Outcome(False, "invalid", [])
        if c.time < self.last_time:
            return Outcome(False, "time_regression", [])
        self.last_time = c.time
        if c.kind == "cancel":
            found = next((r for r in self.rows if r[0].oid == c.oid), None)
            if found is None:
                return Outcome(False, "unknown_order", [])
            if (found[0].owner, found[0].symbol) != (c.owner, c.symbol):
                return Outcome(False, "not_owner_or_symbol", [])
            self.rows.remove(found)
            return Outcome(True, "cancelled", [])
        if c.oid in self.seen:
            return Outcome(False, "duplicate_id", [])
        will_rest = c.kind == "limit" and c.tif == "GTC"
        if will_rest and len(self.rows) >= self.max_live:
            return Outcome(False, "capacity", [])
        self.seen.add(c.oid)
        if c.post_only and self._crossing(c):
            return Outcome(False, "post_only_would_cross", [])
        qty = c.qty
        trades, removed = [], []
        stopped = False
        while qty:
            candidates = self._crossing(c)
            if not candidates:
                break
            r = min(candidates, key=lambda r: (r[0].price if c.side == "B" else -r[0].price, r[2]))
            if self.stp != "none" and r[0].owner == c.owner:
                if self.stp == "cancel_resting":
                    self.rows.remove(r)
                    removed.append(r[0].oid)
                    continue
                removed.append(c.oid)
                stopped = True
                break
            n = min(qty, r[1])
            buyer, seller = (c.owner, r[0].owner) if c.side == "B" else (r[0].owner, c.owner)
            trades.append(Trade(c.symbol, r[0].price, n, r[0].oid, c.oid, buyer, seller, c.time))
            qty -= n
            r[1] -= n
            if r[1] == 0:
                self.rows.remove(r)
        if qty and will_rest and not stopped:
            self.rows.append([c, qty, self.sequence])
        self.sequence += 1
        return Outcome(True, "accepted", trades, qty, tuple(removed))

    def queue_ahead(self, oid):
        me = next((r for r in self.rows if r[0].oid == oid), None)
        if me is None:
            return None
        return sum(r[1] for r in self.rows if r[0].symbol == me[0].symbol and r[0].side == me[0].side
                   and r[0].price == me[0].price and r[2] < me[2])

    def state(self):
        return sorted((c.oid, c.owner, c.symbol, c.side, c.price, qty, c.time)
                      for c, qty, _ in self.rows)
