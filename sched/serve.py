"""JSON bridge for the web UI: `python -m sched.serve < request.json > result.json`.

Request:
  {"completed": [...], "courses": {code: {credits, prereqs: [[...],...], title}}, "sections": [{section_id, course_code,
   meetings: [{weekday, start, end}], instructor, capacity, enrolled, quality, difficulty, credits}],
   "requirement_tree": {...}, "constraints": {min_credits, max_credits, blocked: [{weekday,start,end}], required_courses,
   full_threshold, allow_courses_outside_requirements}, "weights": {"default": {...}, "per_course": {code: {...}}},
   "k": 5, "time_limit_s": 10}
Result: PlanResult as plain JSON (dataclasses expanded)."""
from __future__ import annotations

import dataclasses
import json
import sys
from typing import Any, Dict

from .api import plan
from .models import Constraints, Course, Meeting, PlanRequest, Section, WeightVector, Weights


def _wv(d: Dict[str, Any] | None) -> WeightVector:
    return WeightVector(**d) if d else WeightVector()


def request_from_json(j: Dict[str, Any]) -> PlanRequest:
    courses = {code: Course(code, float(c["credits"]), tuple(tuple(a) for a in c.get("prereqs") or ()), c.get("title")) for code, c in j["courses"].items()}
    sections = [Section(s["section_id"], s["course_code"], tuple(Meeting(m["weekday"], m["start"], m["end"]) for m in s.get("meetings") or ()),
                        s.get("instructor"), s.get("capacity"), s.get("enrolled"), s.get("quality"), s.get("difficulty"), s.get("credits"))
                for s in j["sections"]]
    c = j.get("constraints") or {}
    constraints = Constraints(float(c.get("min_credits", 12)), float(c.get("max_credits", 18)),
                              tuple(Meeting(b["weekday"], b["start"], b["end"]) for b in c.get("blocked") or ()),
                              tuple(c.get("required_courses") or ()), float(c.get("full_threshold", 0.9)),
                              bool(c.get("allow_courses_outside_requirements", False)))
    w = j.get("weights") or {}
    weights = Weights(_wv(w.get("default")), {k: _wv(v) for k, v in (w.get("per_course") or {}).items()})
    return PlanRequest(frozenset(j.get("completed") or []), courses, sections, j.get("requirement_tree"), constraints, weights,
                       int(j.get("k", 5)), float(j.get("time_limit_s", 10)), j.get("relaxation_costs") or PlanRequest.__dataclass_fields__["relaxation_costs"].default_factory())


def to_json(obj: Any) -> Any:
    if dataclasses.is_dataclass(obj):
        return {f.name: to_json(getattr(obj, f.name)) for f in dataclasses.fields(obj)}
    if isinstance(obj, (list, tuple)):
        return [to_json(x) for x in obj]
    if isinstance(obj, (set, frozenset)):
        return sorted(to_json(x) for x in obj)
    if isinstance(obj, dict):
        return {str(k): to_json(v) for k, v in obj.items()}
    return obj


def main() -> int:
    req = request_from_json(json.load(sys.stdin))
    json.dump(to_json(plan(req)), sys.stdout)
    return 0


if __name__ == "__main__":
    sys.exit(main())
