from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Dict, List, Optional, Sequence, Tuple

MINUTES_PER_DAY = 24 * 60


@dataclass(frozen=True)
class Meeting:
    weekday: int          # ISO 1=Mon .. 7=Sun
    start: int            # minutes after midnight
    end: int              # exclusive

    def overlaps(self, other: "Meeting") -> bool:
        return self.weekday == other.weekday and self.start < other.end and other.start < self.end


TimeWindow = Meeting     # a blocked window (work shift) has the same shape


@dataclass(frozen=True)
class Course:
    code: str
    credits: float
    prereqs: Tuple[Tuple[str, ...], ...] = ()   # DNF: any inner tuple fully completed satisfies. () = none
    title: Optional[str] = None


@dataclass(frozen=True)
class Section:
    section_id: str
    course_code: str
    meetings: Tuple[Meeting, ...]
    instructor: Optional[str] = None
    capacity: Optional[int] = None
    enrolled: Optional[int] = None
    quality: Optional[float] = None        # step-4 quality (standardized, ~[-3, 3]); None = unknown
    difficulty: Optional[float] = None     # step-4 difficulty percentile 0..100; None = unknown
    credits: Optional[float] = None        # override of the course credits (variable-credit sections)

    def fullness(self) -> Optional[float]:
        if self.capacity and self.enrolled is not None and self.capacity > 0:
            return self.enrolled / self.capacity
        return None

    def earliest_start(self) -> Optional[int]:
        return min((m.start for m in self.meetings), default=None)


@dataclass(frozen=True)
class WeightVector:
    """Weights of the soft objectives. All are 'importance' magnitudes >= 0; sign conventions are fixed:
    quality is maximized, difficulty / idle gap / days / fullness are minimized, start time follows
    `start_preference`. Units: one unit of weight = one point per unit of the normalized metric."""
    quality: float = 1.0
    difficulty: float = 1.0
    days_on_campus: float = 0.0        # schedule-level (per-course overrides ignored)
    idle_gap: float = 0.0              # schedule-level (per-course overrides ignored)
    start_time: float = 0.0
    start_preference: str = "earlier"  # earlier | later
    unlock: float = 0.0                # prefer courses that unlock more downstream requirements
    fullness: float = 0.0              # penalty for sections above `full_threshold`

    def merged(self, override: Optional["WeightVector"]) -> "WeightVector":
        return override if override is not None else self


@dataclass(frozen=True)
class Weights:
    default: WeightVector = WeightVector()
    per_course: Dict[str, WeightVector] = field(default_factory=dict)

    def for_course(self, code: str) -> WeightVector:
        return self.per_course.get(code, self.default)


@dataclass(frozen=True)
class Constraints:
    min_credits: float = 12.0
    max_credits: float = 18.0
    blocked: Tuple[TimeWindow, ...] = ()
    required_courses: Tuple[str, ...] = ()      # must appear in every schedule
    full_threshold: float = 0.9
    allow_courses_outside_requirements: bool = False


@dataclass(frozen=True)
class PlanRequest:
    completed: frozenset                       # course codes
    courses: Dict[str, Course]                 # catalog for every course referenced
    sections: Sequence[Section]                # the term's offerings
    requirement_tree: Optional[Dict[str, Any]] = None   # step-1/reqx tree: nodes with kind group|course|course_list
    constraints: Constraints = Constraints()
    weights: Weights = Weights()
    k: int = 5
    time_limit_s: float = 10.0
    relaxation_costs: Dict[str, float] = field(default_factory=lambda: {"min_credits": 1.0, "max_credits": 1.0, "blocked": 2.0, "required": 3.0})


@dataclass(frozen=True)
class ChosenSection:
    course_code: str
    section_id: str
    score: float                               # this section's contribution (points) under the course's weights
    weights: WeightVector
    terms: Dict[str, float]                    # per-objective points, signed
    runner_up: Optional[Dict[str, Any]]        # {section_id, score, delta, feasible_swap} or None if no alternative


@dataclass(frozen=True)
class Explanation:
    metrics: Dict[str, float]                  # quality_sum, difficulty_mean, days_on_campus, longest_gap_min, mean_start_min, unlock_count, overfull_sections
    objective_points: Dict[str, float]         # signed points per soft objective (sum over courses + schedule-level)
    won: List[str]                             # objectives on which this schedule is best among the returned set
    lost: List[str]                            # ... worst among the returned set
    rank_by_objective: Dict[str, int]          # 1 = best


@dataclass(frozen=True)
class Schedule:
    rank: int
    total_points: float
    credits: float
    courses: Tuple[str, ...]
    sections: List[ChosenSection]
    explanation: Explanation
    relaxed: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class Relaxation:
    relaxed: List[str]                         # constraint names dropped, minimum total cost
    cost: float
    reason: str


@dataclass(frozen=True)
class PlanResult:
    schedules: List[Schedule]
    feasible: bool
    relaxation: Optional[Relaxation]
    candidate_courses: List[str]
    excluded_courses: Dict[str, str]           # course -> why it was not a candidate
    solver_status: str
