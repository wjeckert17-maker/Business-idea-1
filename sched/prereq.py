"""Prerequisite logic and requirement-tree helpers (pure)."""
from __future__ import annotations

from typing import Any, Dict, FrozenSet, Iterator, List, Optional, Set, Tuple

from .models import Course


def prereqs_satisfied(course: Course, completed: FrozenSet[str]) -> bool:
    if not course.prereqs:
        return True
    return any(all(c in completed for c in alt) for alt in course.prereqs)


def unlock_count(code: str, needed: Set[str], courses: Dict[str, Course], completed: FrozenSet[str]) -> int:
    """How many still-needed, not-yet-eligible courses become eligible once `code` is completed."""
    after = completed | {code}
    n = 0
    for d in needed:
        if d == code or d in completed or d not in courses:
            continue
        c = courses[d]
        if not prereqs_satisfied(c, completed) and prereqs_satisfied(c, after):
            n += 1
    return n


def _walk(node: Dict[str, Any]) -> Iterator[Dict[str, Any]]:
    yield node
    for c in node.get("children") or []:
        yield from _walk(c)


def needed_courses(tree: Optional[Dict[str, Any]], completed: FrozenSet[str]) -> Tuple[Set[str], Dict[str, List[str]]]:
    """Courses referenced by requirement leaves that are not yet satisfied. Returns (codes, groups_of[code]).
    A course leaf is satisfied when completed; a course_list leaf is satisfied when >= min_count of its
    courses are completed. Groups are evaluated conservatively (a group is satisfied only if all its
    leaves are), which can only over-include candidates, never exclude a needed one."""
    if not tree:
        return set(), {}
    needed: Set[str] = set()
    groups: Dict[str, List[str]] = {}
    path: List[str] = []

    def visit(node: Dict[str, Any]):
        kind = node.get("kind")
        title = node.get("title") or ""
        if kind == "group":
            path.append(title)
            for c in node.get("children") or []:
                visit(c)
            path.pop()
        elif kind == "course" and node.get("code"):
            code = _norm(node["code"])
            if code not in completed:
                needed.add(code); groups.setdefault(code, list(path))
        elif kind == "course_list":
            codes = [_norm(c) for c in node.get("courses") or []]
            done = sum(1 for c in codes if c in completed)
            if done < (node.get("min_count") or 1):
                for c in codes:
                    if c not in completed:
                        needed.add(c); groups.setdefault(c, list(path))
    visit(tree)
    return needed, groups


def _norm(code: str) -> str:
    parts = code.strip().upper().split()
    return " ".join(parts) if len(parts) == 2 else code.strip().upper()
