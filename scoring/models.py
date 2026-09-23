from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Dict, List, Optional


@dataclass(frozen=True)
class GradeObservation:
    """One term's grade distribution for the section (or the section's course+instructor)."""
    term_date: date
    letter_counts: Dict[str, int]          # {'A': 12, 'B': 20, ...}; W excluded
    w_count: int = 0
    mean_gpa: Optional[float] = None       # if None, computed from letter_counts

    def graded_n(self) -> int:
        return sum(self.letter_counts.values())


@dataclass(frozen=True)
class SentimentObservation:
    """An aggregate rating snapshot (e.g. a rating-site export) for the section's instructor+course."""
    observed_date: date
    n: int
    rating: Optional[float] = None               # 1..5
    difficulty: Optional[float] = None           # 1..5
    would_take_again: Optional[float] = None     # 0..1


@dataclass(frozen=True)
class SyllabusFeatures:
    exam_weight: Optional[float] = None          # 0..1 share of grade from exams
    attendance_required: Optional[bool] = None
    recorded_lectures: Optional[bool] = None


@dataclass(frozen=True)
class SectionInput:
    section_id: str
    department: str
    level: int                                    # 100, 200, ... (or 1000, 2000)
    grades: List[GradeObservation] = field(default_factory=list)
    sentiment: List[SentimentObservation] = field(default_factory=list)
    syllabus: Optional[SyllabusFeatures] = None


@dataclass(frozen=True)
class Component:
    """One input's journey to the score. Everything the UI needs to explain a number."""
    name: str
    source: str                      # grades | sentiment | syllabus | prior
    axis: str                        # quality | difficulty | confidence
    raw: Optional[float]             # observed value (decayed mean), None if no data
    adjusted: Optional[float]        # after shrinkage (and leniency adjustment where applicable)
    effective_n: float               # decayed sample size behind `raw`
    weight: float                    # weight in the axis formula (0 = switched off)
    contribution: float              # weight × standardized adjusted value (what it added to the axis)
    detail: str                      # human-readable derivation


@dataclass(frozen=True)
class LeniencyModel:
    """rating_shrunk = intercept + slope * gpa_shrunk, fitted across the corpus."""
    intercept: float
    slope: float
    r_squared: float
    n_sections: int
    residual_sd: float

    def predict(self, gpa: float) -> float:
        return self.intercept + self.slope * gpa


@dataclass(frozen=True)
class SectionScore:
    section_id: str
    quality: float                   # standardized: 0 = corpus-typical after leniency adjustment; +1 ≈ one sd better
    difficulty: float                # percentile within department, 0..100
    confidence: float                # 0..1 from effective sample size; never rounded
    confidence_by_source: Dict[str, float]
    effective_n: Dict[str, float]
    components: List[Component]
    notes: List[str] = field(default_factory=list)
