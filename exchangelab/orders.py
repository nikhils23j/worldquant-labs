from dataclasses import dataclass

@dataclass(frozen=True, slots=True)
class Command:
    kind: str
    oid: int
    owner: int
    symbol: int
    side: str = "B"
    qty: int = 1
    price: int = 10000
    time: int = 0  # simulated microseconds

    def valid(self):
        return (self.kind in {"limit", "market", "cancel"}
                and all(type(x) is int for x in (self.oid, self.owner, self.symbol, self.time))
                and self.symbol >= 0 and self.time >= 0
                and (self.kind == "cancel" or (self.side in {"B", "S"}
                     and type(self.qty) is int and self.qty > 0
                     and (self.kind == "market" or (type(self.price) is int and self.price > 0)))))

@dataclass(frozen=True, slots=True)
class Trade:
    symbol: int
    price: int
    qty: int
    maker: int
    taker: int
    buyer: int
    seller: int
    time: int

@dataclass(slots=True)
class Outcome:
    accepted: bool
    reason: str
    trades: list[Trade]
    unfilled: int = 0
