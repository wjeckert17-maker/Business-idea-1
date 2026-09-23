# sched — schedule engine (OR-Tools CP-SAT)

`plan(PlanRequest) -> PlanResult`: the top-K schedules with distinct course sets, ranked by weighted points,
each with a machine-readable explanation; or, when nothing is feasible, the minimum-cost set of constraints
to drop and the schedules that exist once they are dropped.

## Model (`sched/engine.py`)

Variables: `x[section]` chosen, `y[course] = Σ x[section of course]` (≤ 1 by construction), `day[d]` used,
`gap_max` longest idle gap in minutes.

Hard: pairwise overlap clauses; prerequisites (a course is not a candidate unless one of its prerequisite
alternatives is fully completed); credits in `[min, max]` (tenths, integral); blocked windows; at least one
course; `required_courses`.

Objective, maximized, in integer thousandths of a point:

```
Σ_sections x_s · points(s)  −  w_days · Σ day[d]  −  w_gap/60 · gap_max
points(s) = w_c.quality · quality(s) − w_c.difficulty · difficulty(s)/100
          − w_c.start_time · start_fraction(s) [or 1−start_fraction for 'later']
          + w_c.unlock · unlocks(course)/max_unlocks − w_c.fullness · overfull(s)
```

where `w_c = weights.for_course(course)`: the default `WeightVector` unless `weights.per_course[course]`
overrides it. Quality/difficulty/start/unlock/fullness are per-course terms; days on campus and idle gap
are schedule-level and take the default vector's weights (a per-course override of those two is ignored —
they do not decompose over courses). `weights_for_groups(tree, {"Major": WeightVector(quality=3), "Gen Ed": WeightVector(difficulty=3)})`
turns requirement-group titles into per-course overrides.

## Top-K

After each solve the exact chosen course set is forbidden (`Σ_{c∈C} y_c − Σ_{c∉C} y_c ≤ |C|−1`) and the
model is re-solved, so every returned schedule has a different set of courses; within a course set only
the optimal section assignment is returned. Results are re-evaluated in plain Python (`evaluate`) — the
solver's objective is never the reported number.

## Explanation (`Schedule.explanation`, `ChosenSection.runner_up`)

* `objective_points`: signed points per soft objective for the whole schedule.
* `rank_by_objective`, `won`, `lost`: rank among the returned set; `won` = best on that objective, `lost` = worst.
* `metrics`: credits, quality_sum, difficulty_mean, days_on_campus, longest_gap_min, mean_start_min, unlock_count, overfull_sections.
* per chosen section: its weight vector, its per-objective points, and `runner_up` — the best alternative
  section for that course with everything else fixed: `section_id`, `score`, `delta_total_points`,
  `feasible_swap` (false if it would clash with another chosen section or a blocked window) and `why_not`.
  This is the backup ladder: a feasible runner-up with a small delta is a safe substitute.

## Infeasibility

Every relaxable constraint (`min_credits`, `max_credits`, each `blocked[i]`, each `required[code]`) gets an
indicator; a second model minimizes Σ cost × dropped (`PlanRequest.relaxation_costs`, defaults
1 / 1 / 2 / 3). The result carries `relaxation.relaxed` and `relaxation.reason`, `feasible=False`, and every
returned schedule lists what was dropped in `Schedule.relaxed`. Structural impossibilities (no candidate
sections at all) return no schedules and no relaxation.

Tests: `tests/test_sched.py` — 16 hand-built fixtures covering each hard constraint, each soft objective
in isolation, per-course weight flips, distinct-course-set top-K, explanation contents, and both relaxation
cases (blocked window vs. required course, credit floor).
