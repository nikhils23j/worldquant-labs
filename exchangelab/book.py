"""Fast price-time (FIFO) matching engine.

Layout per symbol: two Sides. A Side maps price -> Level and keeps a lazy heap of
prices so the best level is O(1) amortised. A Level is a deque of Resting orders.
Cancels are O(1): the order is marked dead (qty = 0) and skipped when it reaches
the head of the deque. The original OrderedDict engine lives in book_v0.py and is
kept only as a benchmark baseline; reference.py is the slow independent oracle.
"""
from collections import defaultdict, deque
import heapq
from .orders import Trade, Outcome

STP_MODES = ("none", "cancel_resting", "cancel_taker")


class Resting:
    __slots__ = ("command", "oid", "owner", "price", "qty")

    def __init__(self, command, qty):
        self.command = command
        self.oid = command.oid
        self.owner = command.owner
        self.price = command.price
        self.qty = qty


class Level:
    __slots__ = ("queue", "live")

    def __init__(self):
        self.queue = deque()
        self.live = 0

    def head(self):
        q = self.queue
        while not q[0].qty:  # skip lazily-cancelled orders
            q.popleft()
        return q[0]

    def orders(self):
        return [r for r in self.queue if r.qty]


class Side:
    __slots__ = ("buy", "levels", "heap")

    def __init__(self, buy):
        self.buy = buy
        self.levels = {}
        self.heap = []

    def add(self, resting):
        p = resting.price
        level = self.levels.get(p)
        if level is None:
            level = self.levels[p] = Level()
            heapq.heappush(self.heap, -p if self.buy else p)
        level.queue.append(resting)
        level.live += 1

    def best(self):
        heap, levels = self.heap, self.levels
        if self.buy:
            while heap:
                if -heap[0] in levels:
                    return -heap[0]
                heapq.heappop(heap)
        else:
            while heap:
                if heap[0] in levels:
                    return heap[0]
                heapq.heappop(heap)
        return None

    def drop(self, resting):
        """Remove a resting order (cancel or full fill). O(1) apart from heap compaction."""
        p = resting.price
        level = self.levels[p]
        resting.qty = 0
        level.live -= 1
        if not level.live:
            del self.levels[p]
            if len(self.heap) > 2 * len(self.levels) + 32:
                self.heap = [-x if self.buy else x for x in self.levels]
                heapq.heapify(self.heap)


class Exchange:
    """Price-time FIFO matching. Market and IOC residuals cancel; order IDs never reuse.

    New resting (GTC limit) orders are conservatively rejected at the global
    resting-order capacity before matching, including orders that might cross.
    Trades print at the resting order's price.

    Self-trade prevention (stp):
      "none"            self-trades are allowed (the original behaviour);
      "cancel_resting"  an own resting order met during matching is cancelled, matching continues;
      "cancel_taker"    matching stops at the first own resting order and the taker residual is cancelled.
    """

    def __init__(self, max_live=10000, stp="none"):
        if stp not in STP_MODES:
            raise ValueError(f"stp must be one of {STP_MODES}")
        self.max_live = max_live
        self.stp = stp
        self.books = {}
        self.orders = {}
        self.seen = set()
        self.cash = defaultdict(int)
        self.positions = defaultdict(int)
        self.trade_count = 0
        self.volume = 0
        self.last_time = -1

    def sides(self, symbol):
        pair = self.books.get(symbol)
        if pair is None:
            pair = self.books[symbol] = (Side(True), Side(False))
        return pair

    def process(self, c):
        if not c.valid():
            return Outcome(False, "invalid", [])
        now = c.time
        if now < self.last_time:
            return Outcome(False, "time_regression", [])
        self.last_time = now
        kind = c.kind
        orders = self.orders
        if kind == "cancel":
            r = orders.get(c.oid)
            if r is None:
                return Outcome(False, "unknown_order", [])
            if r.owner != c.owner or r.command.symbol != c.symbol:
                return Outcome(False, "not_owner_or_symbol", [])
            self.sides(c.symbol)[0 if r.command.side == "B" else 1].drop(r)
            del orders[c.oid]
            return Outcome(True, "cancelled", [])
        oid = c.oid
        if oid in self.seen:
            return Outcome(False, "duplicate_id", [])
        rests = kind == "limit" and c.tif == "GTC"
        if rests and len(orders) >= self.max_live:
            return Outcome(False, "capacity", [])
        self.seen.add(oid)
        buy = c.side == "B"
        symbol = c.symbol
        pair = self.books.get(symbol) or self.sides(symbol)
        opposite, own = (pair[1], pair[0]) if buy else pair
        limit = c.price if kind == "limit" else None
        price = opposite.best()
        if c.post_only and price is not None and (price <= limit if buy else price >= limit):
            return Outcome(False, "post_only_would_cross", [])
        remaining = c.qty
        trades = []
        removed = []
        owner = c.owner
        stp = self.stp
        levels = opposite.levels
        cash, positions = self.cash, self.positions
        stopped = False
        # `price` is the current best opposite level; it is only recomputed when a level empties.
        while remaining and price is not None:
            if limit is not None and (price > limit if buy else price < limit):
                break
            queue = levels[price].queue
            r = queue[0]
            while not r.qty:  # skip lazily-cancelled orders
                queue.popleft()
                r = queue[0]
            if stp != "none" and r.owner == owner:
                if stp == "cancel_resting":
                    opposite.drop(r)
                    del orders[r.oid]
                    removed.append(r.oid)
                    if price not in levels:
                        price = opposite.best()
                    continue
                removed.append(oid)
                stopped = True
                break
            rqty = r.qty
            qty = remaining if remaining < rqty else rqty
            if buy:
                buyer, seller = owner, r.owner
            else:
                buyer, seller = r.owner, owner
            trades.append(Trade(symbol, price, qty, r.oid, oid, buyer, seller, now))
            notional = qty * price
            cash[buyer] -= notional
            cash[seller] += notional
            positions[buyer, symbol] += qty
            positions[seller, symbol] -= qty
            remaining -= qty
            if qty == rqty:
                queue.popleft()
                opposite.drop(r)
                del orders[r.oid]
                if price not in levels:
                    price = opposite.best()
            else:
                r.qty = rqty - qty
        if trades:
            self.trade_count += len(trades)
            self.volume += c.qty - remaining
        if remaining and rests and not stopped:
            r = Resting(c, remaining)
            own.add(r)
            orders[oid] = r
        return Outcome(True, "accepted", trades, remaining, tuple(removed))

    def quote(self, symbol):
        bid, ask = self.sides(symbol)
        return bid.best(), ask.best()

    def depth(self, symbol, count=10):
        return [[(p, sum(r.qty for r in s.levels[p].orders()))
                 for p in sorted(s.levels, reverse=s.buy)[:count]] for s in self.sides(symbol)]

    def queue_ahead(self, oid):
        """Resting quantity with strictly better time priority at the same price (None if not resting)."""
        r = self.orders.get(oid)
        if r is None:
            return None
        side = self.sides(r.command.symbol)[0 if r.command.side == "B" else 1]
        ahead = 0
        for x in side.levels[r.price].queue:
            if x is r:
                return ahead
            ahead += x.qty
        raise AssertionError("resting order missing from its level")

    def state(self):
        return sorted((i, r.owner, r.command.symbol, r.command.side,
                       r.price, r.qty, r.command.time) for i, r in self.orders.items())

    def check(self):
        indexed = set()
        for symbol, pair in self.books.items():
            b, a = self.quote(symbol)
            assert b is None or a is None or b < a, "crossed resting book"
            for s in pair:
                assert len(s.heap) <= 2 * len(s.levels) + 33
                for p, level in s.levels.items():
                    live = level.orders()
                    assert live and len(live) == level.live
                    times = []
                    for r in live:
                        assert r.qty > 0 and r.price == p
                        assert self.orders[r.oid] is r
                        assert (r.command.side == "B") == s.buy
                        indexed.add(r.oid)
                        times.append(r.command.time)
                    assert times == sorted(times), "FIFO timestamps"
        assert indexed == set(self.orders)
        assert len(indexed) <= self.max_live
        assert sum(self.cash.values()) == 0
        sums = defaultdict(int)
        for (_, symbol), qty in self.positions.items():
            sums[symbol] += qty
        assert all(v == 0 for v in sums.values())
