"""Independent, deliberately slow list scan oracle for matching semantics."""
from .orders import Trade, Outcome

class ReferenceExchange:
    def __init__(self, max_live=10000):
        self.max_live = max_live
        self.rows = []  # command, remaining quantity, arrival index
        self.seen = set()
        self.sequence = 0
        self.last_time = -1

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
        if c.kind == "limit" and len(self.rows) >= self.max_live:
            return Outcome(False, "capacity", [])
        self.seen.add(c.oid)
        qty = c.qty
        trades = []
        while qty:
            candidates = [r for r in self.rows if r[0].symbol == c.symbol
                          and r[0].side != c.side and (c.kind == "market"
                          or (r[0].price <= c.price if c.side == "B" else r[0].price >= c.price))]
            if not candidates:
                break
            r = min(candidates, key=lambda r: (r[0].price if c.side == "B" else -r[0].price, r[2]))
            n = min(qty, r[1])
            buyer, seller = (c.owner, r[0].owner) if c.side == "B" else (r[0].owner, c.owner)
            trades.append(Trade(c.symbol, r[0].price, n, r[0].oid, c.oid, buyer, seller, c.time))
            qty -= n
            r[1] -= n
            if r[1] == 0:
                self.rows.remove(r)
        if qty and c.kind == "limit":
            self.rows.append([c, qty, self.sequence])
        self.sequence += 1
        return Outcome(True, "accepted", trades, qty)

    def state(self):
        return sorted((c.oid, c.owner, c.symbol, c.side, c.price, qty, c.time)
                      for c, qty, _ in self.rows)
