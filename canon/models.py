from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# Relations. Direction is always from -> to: "from satisfies to".
SATISFIES = "satisfies"                  # from (a course) satisfies to (a course)
SATISFIES_JOINTLY = "satisfies_jointly"  # from is one member of an AND-group that jointly satisfies to
MEMBER_OF = "member_of"                  # from (a course) carries hub to (common number); symmetric by policy
DENIED = "denied"                        # the receiving side explicitly refuses from for to
NOT_ARTICULATED = "not_articulated"      # source states nothing at from-institution articulates to `to`

POSITIVE_RELATIONS = (SATISFIES, MEMBER_OF)
NEGATIVE_RELATIONS = (DENIED, NOT_ARTICULATED)


@dataclass(frozen=True)
class InstitutionRef:
    source_code: str
    external_key: str
    name: str
    state: Optional[str] = None
    kind: Optional[str] = None        # community_college | university | other


@dataclass(frozen=True)
class CourseRef:
    """A course or hub as one source sees it. subject/number are the raw published strings."""
    subject: str
    number: str
    institution: Optional[InstitutionRef] = None   # None => hub
    system_code: Optional[str] = None              # hubs only: scns | tccns | cid
    title: Optional[str] = None
    description: Optional[str] = None
    units_min: Optional[float] = None
    units_max: Optional[float] = None
    effective_from: Optional[str] = None
    effective_to: Optional[str] = None
    status: Optional[str] = None
    attrs: Dict[str, Any] = field(default_factory=dict)

    @property
    def is_hub(self) -> bool:
        return self.institution is None


@dataclass(frozen=True)
class AssertionRecord:
    from_ref: CourseRef
    to_ref: CourseRef
    relation: str
    confidence: float
    source_ref: str
    effective_from: Optional[str] = None
    effective_to: Optional[str] = None
    group_key: Optional[str] = None
    evidence: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CoverageRecord:
    from_institution: InstitutionRef
    to_institution: InstitutionRef
    effective_from: Optional[str]
    effective_to: Optional[str]


@dataclass
class MentionRecord:
    source_code: str
    raw_name: str
    institution: Optional[InstitutionRef] = None
    email: Optional[str] = None
    external_id: Optional[str] = None
    department: Optional[str] = None
    courses: List[str] = field(default_factory=list)   # 'CS 1301'
    terms: List[str] = field(default_factory=list)     # '2026-fall'
    role: Optional[str] = None
    extra: Dict[str, Any] = field(default_factory=dict)
    observed_at: Optional[str] = None
    source_ref: Optional[str] = None
