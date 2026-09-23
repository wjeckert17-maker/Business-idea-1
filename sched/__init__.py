"""Schedule engine on OR-Tools CP-SAT: top-K diverse schedules, per-course weights, explanations, relaxation."""
from .models import (Course, Section, Meeting, TimeWindow, Constraints, WeightVector, Weights, PlanRequest, PlanResult,
                     Schedule, ChosenSection, Explanation, Relaxation)
from .api import plan, weights_for_groups

__all__ = ["Course", "Section", "Meeting", "TimeWindow", "Constraints", "WeightVector", "Weights", "PlanRequest", "PlanResult",
           "Schedule", "ChosenSection", "Explanation", "Relaxation", "plan", "weights_for_groups"]
