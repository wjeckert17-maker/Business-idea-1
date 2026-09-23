"""Adapter contract. A new school = one subclass (or, for another Banner 9 school, just a TOML)."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, ClassVar, Dict, Iterator, List, Type

from ..config import SchoolConfig
from ..http import PoliteSession
from ..models import SectionRecord, TermInfo


@dataclass(frozen=True)
class RawSection:
    """One section exactly as the source returned it, plus the key we'd log it under."""
    source_key: str      # e.g. CRN, for log lines
    payload: Any         # JSON-serializable


class SourceAdapter(ABC):
    name: ClassVar[str]

    def __init__(self, http: PoliteSession, config: SchoolConfig):
        self.http = http
        self.config = config

    @abstractmethod
    def list_terms(self) -> List[TermInfo]:
        """Terms the source offers, newest first."""

    @abstractmethod
    def fetch_sections(self, term: TermInfo) -> Iterator[RawSection]:
        """Yield every section in the term as raw payloads. Network errors propagate."""

    @abstractmethod
    def parse_section(self, term: TermInfo, raw: RawSection) -> SectionRecord:
        """Raise ParseError (or anything) on a payload that can't be normalized."""

    def finish(self, term: TermInfo) -> None:
        """Optional cleanup after a term (e.g. reset server-side search state)."""


_REGISTRY: Dict[str, Type[SourceAdapter]] = {}


def register(cls: Type[SourceAdapter]) -> Type[SourceAdapter]:
    _REGISTRY[cls.name] = cls
    return cls


def get_adapter(name: str) -> Type[SourceAdapter]:
    try:
        return _REGISTRY[name]
    except KeyError:
        raise KeyError(f"unknown adapter {name!r}; known: {sorted(_REGISTRY)}") from None
