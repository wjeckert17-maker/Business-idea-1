"""Section scoring: pure functions from raw signals to {quality, difficulty, confidence, components}."""
from .config import ScoringConfig, SourceWeights
from .models import GradeObservation, SentimentObservation, SyllabusFeatures, SectionInput, SectionScore, Component, LeniencyModel
from .score import score_corpus

__all__ = ["ScoringConfig", "SourceWeights", "GradeObservation", "SentimentObservation", "SyllabusFeatures", "SectionInput",
           "SectionScore", "Component", "LeniencyModel", "score_corpus"]
