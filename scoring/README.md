# scoring — section quality / difficulty

Pure functions, no I/O, no clock. `score_corpus(sections, config) -> ({section_id: SectionScore}, LeniencyModel | None)`.

Pipeline (in this order, `scoring/score.py`):

1. **Recency decay.** Every observation gets weight `exp(-λ · years_ago)` (`decay_lambda`, default 0.3) relative to `config.as_of`, which defaults to the latest date in the corpus. Decayed means and effective sample sizes `n_eff = Σ w·n` per statistic.
2. **Shrinkage.** `adjusted = (n_eff·x̄ + m·μ) / (n_eff + m)` (`prior_strength_m`, default 15) where μ is the effective-n-weighted mean for the section's department and level, falling back to department, then corpus. Applied to mean GPA, DFW rate, rating, difficulty rating and would-take-again. No data ⇒ exactly the prior.
3. **Leniency.** OLS of shrunk rating on shrunk mean GPA over every section that has both. The fitted `LeniencyModel` (intercept, slope, R², n, residual sd) is returned. A section's teaching-quality signal is the residual: rating minus what its grade generosity predicts. Would-take-again gets the same treatment with its own fit.
4. **Difficulty.** Shrunk DFW rate, inverted shrunk GPA, shrunk difficulty rating and syllabus exam weight are each standardized **within department**, combined with `difficulty_components` weights, and percentile-ranked within department (0–100).

Quality = Σ weight × z(component) over `quality_components` (residuals and syllabus flags, standardized across the corpus); 0 is corpus-typical, +1 about one standard deviation better.

**Sources can be switched off**: `SourceWeights(sentiment=0.0)` removes sentiment from shrinkage, the regression, components and confidence; the output is identical to scoring a corpus with no sentiment rows (tested).

**Confidence** = Σ source_weight × n_eff/(n_eff+m) over active sources, normalized. It is a float; a section with one rating has confidence ≈ 0.06, not 0. `confidence_by_source` and `effective_n` are also returned.

**Components** (`SectionScore.components`): one row per input with `name, source, axis, raw, adjusted, effective_n, weight, contribution, detail`, where `detail` is a sentence a user can read, e.g.

> observed rating 4.120 over n_eff 2000.0; shrunk toward CS level 100 prior 3.588 with m=15 → 4.116; leniency model predicts 4.104 from shrunk GPA 3.89 (R²=0.94); residual +0.012 = z +0.05; weight 1.00 → contribution +0.052

Tests: `tests/test_scoring.py` — formula limits, decay, department/level prior pull, the lenient-grader-scores-neutral case, R² reporting, sentiment weight zero ≡ no sentiment data, within-department percentiles comparable across fields, zero-data and one-observation confidence, component completeness, determinism.
