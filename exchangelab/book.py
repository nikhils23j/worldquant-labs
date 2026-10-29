from collections import OrderedDict, defaultdict
from dataclasses import dataclass
import heapq
from .orders import Command, Trade, Outcome

@dataclass(slots=True)
class Resting:
    command: Command
    qty: int

class Side:
    def __init__(self, buy):
        self.buy = buy
        self.levels = {}
        self.heap = []

    def add(self, resting):
        p = resting.command.price
        if p not in self.levels:
            self.levels[p] = OrderedDict()
            heapq.heappush(self.heap, -p if self.buy else p)
        self.levels[p][resting.command.oid] = resting

    def best(self):
        while self.heap:
            p = -self.heap[0] if self.buy else self.heap[0]
            if p in self.levels:
                return p
            heapq.heappop(self.heap)
        return None

    def remove(self, oid, price):
        del self.levels[price][oid]
        if not self.levels[price]:
            del self.levels[price]
        if len(self.heap) > 2 * len(self.levels) + 32:
            self.heap = [-p if self.buy else p for p in self.levels]
            heapq.heapify(self.heap)

class Exchange:
    """Price-time FIFO matching. Market residuals cancel; order IDs never reuse.

    New limit orders are conservatively rejected at the global resting-order
    capacity before matching, including orders that might immediately cross.
    Trades at a resting order's price; self-trades are allowed and disclosed.
    """
    def __init__(self, max_live=10000):
        self.max_live = max_live
        self.books = {}
        self.orders = {}
        self.seen = set()
        self.cash = defaultdict(int)
        self.positions = defaultdict(int)
        self.trade_count = 0
        self.volume = 0
        self.last_time = -1

    def sides(self, symbol):
        if symbol not in self.books:
            self.books[symbol] = (Side(True), Side(False))
        return self.books[symbol]

    def process(self, c):
        if not c.valid():
            return Outcome(False, "invalid", [])
        if c.time < self.last_time:
            return Outcome(False, "time_regression", [])
        self.last_time = c.time
        if c.kind == "cancel":
            r = self.orders.get(c.oid)
            if r is None:
                return Outcome(False, "unknown_order", [])
            if (r.command.owner, r.command.symbol) != (c.owner, c.symbol):
                return Outcome(False, "not_owner_or_symbol", [])
            side = self.sides(c.symbol)[0 if r.command.side == "B" else 1]
            side.remove(c.oid, r.command.price)
            del self.orders[c.oid]
            return Outcome(True, "cancelled", [])
        if c.oid in self.seen:
            return Outcome(False, "duplicate_id", [])
        if c.kind == "limit" and len(self.orders) >= self.max_live:
            return Outcome(False, "capacity", [])
        self.seen.add(c.oid)
        bid, ask = self.sides(c.symbol)
        opposite = ask if c.side == "B" else bid
        own = bid if c.side == "B" else ask
        remaining = c.qty
        trades = []
        while remaining:
            price = opposite.best()
            if price is None:
                break
            if c.kind == "limit" and ((c.side == "B" and price > c.price)
                                      or (c.side == "S" and price < c.price)):
                break
            r = next(iter(opposite.levels[price].values()))
            qty = min(remaining, r.qty)
            assert qty > 0
            buyer, seller = (c.owner, r.command.owner) if c.side == "B" else (r.command.owner, c.owner)
            t = Trade(c.symbol, price, qty, r.command.oid, c.oid, buyer, seller, c.time)
            trades.append(t)
            self.cash[buyer] -= qty * price
            self.cash[seller] += qty * price
            self.positions[buyer, c.symbol] += qty
            self.positions[seller, c.symbol] -= qty
            self.trade_count += 1
            self.volume += qty
            remaining -= qty
            r.qty -= qty
            if not r.qty:
                opposite.remove(r.command.oid, price)
                del self.orders[r.command.oid]
        if remaining and c.kind == "limit":
            r = Resting(c, remaining)
            own.add(r)
            self.orders[c.oid] = r
        return Outcome(True, "accepted", trades, remaining)

    def quote(self, symbol):
        bid, ask = self.sides(symbol)
        return bid.best(), ask.best()

    def depth(self, symbol, count=10):
        return [[(p, sum(r.qty for r in s.levels[p].values()))
                 for p in sorted(s.levels, reverse=s.buy)[:count]] for s in self.sides(symbol)]

    def state(self):
        return sorted((i, r.command.owner, r.command.symbol, r.command.side,
                       r.command.price, r.qty, r.command.time) for i, r in self.orders.items())

    def check(self):
        indexed = set()
        for symbol, pair in self.books.items():
            b, a = self.quote(symbol)
            assert b is None or a is None or b < a, "crossed resting book"
            for s in pair:
                assert len(s.heap) <= 2 * len(s.levels) + 33
                for p, level in s.levels.items():
                    assert level
                    times = []
                    for oid, r in level.items():
                        assert r.qty > 0 and r.command.price == p
                        assert self.orders[oid] is r
                        indexed.add(oid)
                        times.append(r.command.time)
                    assert times == sorted(times), "FIFO timestamps"
        assert indexed == set(self.orders)
        assert len(indexed) <= self.max_live
        assert sum(self.cash.values()) == 0
        sums = defaultdict(int)
        for (_, symbol), qty in self.positions.items():
            sums[symbol] += qty
        assert all(v == 0 for v in sums.values())
