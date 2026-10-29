from dataclasses import dataclass, field
from .orders import Command

@dataclass
class MarketMaker:
    owner: int = 1
    def quotes(self, time, cycle, symbols=1):
        commands = []
        for s in range(symbols):
            center = 10000 + ((cycle // 3) % 9 - 4) * 2
            for j, side in enumerate(("B", "S")):
                oid = 10000000 + cycle * 100 + s * 2 + j
                commands.append(Command("limit", oid, self.owner, s, side, 15,
                                        center + (-2 if side == "B" else 2), time))
        return commands

@dataclass
class BackgroundTrader:
    owner: int = 2
    def order(self, rng, i, symbols):
        return Command("market", 20000000 + i, self.owner, int(rng.integers(symbols)),
                       "B" if rng.random() < .5 else "S", int(rng.integers(1, 8)), time=i * 1000)

@dataclass
class LatencyProbe:
    owner: int = 3
    observations: dict = field(default_factory=dict)
    decisions: list = field(default_factory=list)
    def observe(self, symbol, quote, exchange_time, delivered_time):
        assert exchange_time <= delivered_time
        self.observations[symbol] = (quote, exchange_time, delivered_time)

    def order(self, time, i, symbol):
        observation = self.observations.get(symbol)
        if observation is None or None in observation[0]:
            return None
        (bid, ask), exchange_time, delivered_time = observation
        assert exchange_time <= delivered_time <= time
        side = "B" if i % 2 == 0 else "S"
        c = Command("limit", 30000000 + i, self.owner, symbol, side, 4,
                    ask if side == "B" else bid, time)
        self.decisions.append({"oid": c.oid, "symbol": symbol, "time": time, "side": side,
                               "qty": c.qty, "mid": (bid + ask) / 2,
                               "observed_exchange_time": exchange_time,
                               "observation_delivery_time": delivered_time})
        return c
