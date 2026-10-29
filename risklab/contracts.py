from dataclasses import dataclass
import math

@dataclass(frozen=True)
class Contract:
    kind: str = "asian"
    strike: float = 100.0
    maturity: float = 1.0
    fixings: int = 252
    call: bool = True

    def __post_init__(self):
        if self.kind not in {"european", "asian", "geometric"}:
            raise ValueError("Unsupported payoff")
        if not math.isfinite(self.strike) or self.strike <= 0 or not math.isfinite(self.maturity) or self.maturity < 0:
            raise ValueError("Positive strike and nonnegative maturity required")
        if type(self.fixings) is not int or self.fixings < 1:
            raise ValueError("fixings must be a positive integer")
        if type(self.call) is not bool:
            raise ValueError("call must be boolean")

@dataclass(frozen=True)
class Model:
    spot: float = 100.0
    volatility: float = .3
    rate: float = .03
    dividend: float = 0.0

    def __post_init__(self):
        if not all(math.isfinite(x) for x in (self.spot, self.volatility, self.rate, self.dividend)):
            raise ValueError("Model parameters must be finite")
        if self.spot <= 0 or self.volatility < 0:
            raise ValueError("Positive spot and nonnegative volatility required")
