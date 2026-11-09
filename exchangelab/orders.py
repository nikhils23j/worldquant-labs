from dataclasses import dataclass
from typing import NamedTuple

SIDES = frozenset(("B", "S"))
TIFS = frozenset(("GTC", "IOC"))


@dataclass(frozen=True, slots=True)
class Command:
    """One message to the exchange. Prices are integer ticks, time is simulated microseconds.

    kind: "limit" | "market" | "cancel"
    tif:  "GTC" rests any residual; "IOC" cancels the residual after matching.
    post_only: a GTC limit that would cross on arrival is rejected instead of trading.
    """
    kind: str
    oid: int
    owner: int
    symbol: int
    side: str = "B"
    qty: int = 1
    price: int = 10000
    time: int = 0  # simulated microseconds
    tif: str = "GTC"
    post_only: bool = False

    def valid(self):
        # Written as early returns because this runs once per message on the hot path.
        if (type(self.oid) is not int or type(self.owner) is not int
                or type(self.symbol) is not int or type(self.time) is not int):
            return False
        if self.symbol < 0 or self.time < 0:
            return False
        kind = self.kind
        if kind == "cancel":
            return True
        if kind != "limit" and kind != "market":
            return False
        if self.side not in SIDES or type(self.qty) is not int or self.qty <= 0:
            return False
        if self.tif not in TIFS or type(self.post_only) is not bool:
            return False
        if kind == "market":
            return not self.post_only
        return type(self.price) is int and self.price > 0 and not (self.post_only and self.tif == "IOC")


class Trade(NamedTuple):
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
    trades: list
    unfilled: int = 0
    stp_cancelled: tuple = ()  # order ids removed by self-trade prevention
