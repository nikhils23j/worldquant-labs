"""CPU Monte Carlo pricing under explicitly specified synthetic models."""
from .contracts import Contract, Model
from .estimators import price
__all__ = ["Contract", "Model", "price"]
