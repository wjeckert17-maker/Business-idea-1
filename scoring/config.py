from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Dict, Optional


@dataclass(frozen=True)
class SourceWeights:
    """Relative weight of each input source. A source at 0.0 is switched off everywhere:
    no shrinkage, no regression, no components, no share of confidence."""
    grades: float = 1.0
    sentiment: float = 1.0
    syllabus: float = 0.5


@dataclass(frozen=True)
class ScoringConfig:
    prior_strength_m: float = 15.0          # shrinkage: adjusted = (n*xbar + m*mu) / (n + m)
    decay_lambda: float = 0.3               # weight = exp(-lambda * years_ago)
    as_of: Optional[date] = None            # None => the latest observation date in the corpus (keeps scoring pure)
    weights: SourceWeights = SourceWeights()
    # quality axis: standardized components and their weights (within the source weight above)
    quality_components: Dict[str, float] = field(default_factory=lambda: {
        "sentiment_rating_residual": 1.0,          # leniency-adjusted rating (the teaching-quality residual)
        "sentiment_would_take_again_residual": 0.5,
        "syllabus_recorded_lectures": 0.3,
        "syllabus_attendance_required": -0.2,
    })
    # difficulty axis: standardized within department, then percentile-ranked within department
    difficulty_components: Dict[str, float] = field(default_factory=lambda: {
        "grade_dfw_rate": 1.0,
        "grade_mean_gpa_inverted": 1.0,
        "sentiment_difficulty": 0.7,
        "syllabus_exam_weight": 0.3,
    })
    gpa_points: Dict[str, float] = field(default_factory=lambda: {
        "A+": 4.0, "A": 4.0, "A-": 3.7, "B+": 3.3, "B": 3.0, "B-": 2.7, "C+": 2.3, "C": 2.0, "C-": 1.7,
        "D+": 1.3, "D": 1.0, "D-": 0.7, "F": 0.0})
    dfw_letters: tuple = ("D+", "D", "D-", "F")   # W is counted separately from w_count
    min_dept_sections_for_percentile: int = 2
