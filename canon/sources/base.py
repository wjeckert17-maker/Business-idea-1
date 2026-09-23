"""Ground-truth adapter contract. One adapter per source system; the core never changes.

An adapter yields three kinds of records from a raw payload directory (or a live session):
  CourseRef         nodes with their as-of attributes
  AssertionRecord   directed claims between nodes
  CoverageRecord    (from_institution, to_institution, period) triples the source covers exhaustively
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import ClassVar, Dict, Iterator, Type, Union

from ..models import AssertionRecord, CourseRef, CoverageRecord

Record = Union[CourseRef, AssertionRecord, CoverageRecord]


class GroundTruthAdapter(ABC):
    code: ClassVar[str]
    name: ClassVar[str]
    kind: ClassVar[str]           # articulation | common_numbering
    base_trust: ClassVar[float]   # how much a bare assertion from this source is worth (0..1)
    notes: ClassVar[str] = ""

    def __init__(self, path: str, **options):
        self.path = path
        self.options = options

    @abstractmethod
    def records(self) -> Iterator[Record]:
        """Yield nodes, assertions and coverage. Should be deterministic for a given raw cache."""


_REGISTRY: Dict[str, Type[GroundTruthAdapter]] = {}


def register(cls):
    _REGISTRY[cls.code] = cls
    return cls


def get_adapter(code: str) -> Type[GroundTruthAdapter]:
    if code not in _REGISTRY:
        raise KeyError(f"unknown source {code!r}; known: {sorted(_REGISTRY)}")
    return _REGISTRY[code]


def all_adapters():
    return dict(_REGISTRY)
