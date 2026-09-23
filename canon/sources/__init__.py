from .base import GroundTruthAdapter, all_adapters, get_adapter, register
from . import assist, cid, scns, tccns  # noqa: F401  register on import

__all__ = ["GroundTruthAdapter", "all_adapters", "get_adapter", "register"]
