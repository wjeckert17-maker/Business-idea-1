"""Validator and extractor tests. The model is never called: trees here are hand-written fixtures that
exercise each rule, including the ones a fabricating model would trip."""
import copy
import json

import pytest

from reqx.courses import Course, CourseTable
from reqx.extract import Line, Outline, Section, codes_in, parse_text, render_group
from reqx.prompt import OUTPUT_SCHEMA
from reqx.validate import Validator, finish
from reqx.emit import emit

COURSES = CourseTable([Course("MAT 21A", "Calculus", 4, 4), Course("MAT 21B", "Calculus", 4, 4), Course("MAT 22A", "Linear Algebra", 3, 3),
                       Course("MAT 27A", "Linear Algebra with Bio", 4, 4, aliases=("BIS 27A",)), Course("ECS 36A", "Programming", 4, 4),
                       Course("ECS 122A", "Algorithms", 4, 4), Course("ECS 122B", "Algorithms II", 4, 4)])


def section():
    s = Section("Preparatory Subject Matter", 2, "area")
    m = Section("Mathematics", 3, "subarea")
    m.lines = [Line("course", "MAT 021A | Calculus | 4", ["MAT 21A"], "Calculus", "4"),
               Line("course", "MAT 021B | Calculus | 4", ["MAT 21B"], "Calculus", "4"),
               Line("comment", "Choose one: | 3-4", units="3-4"),
               Line("course", "MAT 022A | Linear Algebra", ["MAT 22A"], "Linear Algebra", indent=1),
               Line("course", "MAT/BIS 027A | Linear Algebra with Applications to Biology", ["MAT 27A", "BIS 27A"], indent=1),
               Line("comment", "or an approved substitute selected in consultation with the advisor", indent=1)]
    s.children = [m]
    s.lines.append(Line("subtotal", "Preparatory Subject Matter Subtotal | 11-12", units="11-12"))
    return s


def leaf(kind, **kw):
    base = {"kind": kind, "title": None, "operator": None, "min_count": None, "min_credits": None, "code": None, "aliases": [], "units": None,
            "courses": [], "subject_codes": [], "min_level": None, "max_level": None, "original_text": None, "reason": None,
            "source_lines": [], "confidence": 1.0, "children": []}
    base.update(kw)
    return base


def good_tree():
    choose = leaf("group", title="Choose one:", operator="any_n", min_count=1, units="3-4", children=[
        leaf("course", code="MAT 022A", units=None),
        leaf("course", code="MAT 027A", aliases=["BIS 027A"]),
        leaf("needs_review", original_text="or an approved substitute selected in consultation with the advisor", reason="delegates to advisor", confidence=0.5)])
    math = leaf("group", title="Mathematics", operator="all", children=[leaf("course", code="MAT 021A", units="4"), leaf("course", code="MAT 021B", units="4"), choose])
    return {"section_title": "Preparatory Subject Matter", "confidence": 0.95, "notes": None,
            "group": leaf("group", title="Preparatory Subject Matter", operator="all", children=[math])}


def outline():
    return Outline("Test BS", "103", [], Section("Test BS", 1, "heading", children=[section()]), "test")


def test_schema_accepts_fixture():
    import jsonschema
    jsonschema.validate(good_tree(), OUTPUT_SCHEMA)


def test_clean_tree_passes_with_review_flag_for_advisor_clause():
    v = Validator(COURSES, outline())
    gr, fs = v.validate(section(), good_tree())
    assert not [f for f in fs if f.severity == "error"], fs
    assert gr.needs_review and any("needs_review" in r for r in gr.review_reasons)
    assert gr.confidence <= 0.8                       # advisor clause caps confidence
    assert (gr.credits_min, gr.credits_max) == (11.0, 12.0)   # 4 + 4 + one of (3, 4)


def test_fabricated_code_is_an_error():
    t = good_tree()
    t["group"]["children"][0]["children"].append(leaf("course", code="MAT 021C"))   # not in the section text
    gr, fs = Validator(COURSES, outline()).validate(section(), t)
    assert any(f.code == "fabricated_code" for f in fs) and gr.needs_review and gr.confidence < 0.5


def test_unknown_course_is_an_error_but_alias_saves_crosslist():
    t = good_tree()
    gr, fs = Validator(CourseTable([c for c in COURSES.by_code.values() if c.code != "MAT 27A"]), outline()).validate(section(), t)
    assert any(f.code == "unknown_course" and "027A" in f.message for f in fs)
    # with only the alias present the course is still known
    t2 = good_tree()
    tbl = CourseTable([Course("BIS 27A", "x", 4, 4), Course("MAT 21A", "", 4, 4), Course("MAT 21B", "", 4, 4), Course("MAT 22A", "", 3, 3)])
    _, fs2 = Validator(tbl, outline()).validate(section(), t2)
    assert not any(f.code == "unknown_course" for f in fs2)


def test_unsatisfiable_and_credit_mismatch():
    t = good_tree()
    t["group"]["children"][0]["children"][2]["min_count"] = 5      # choose 5 of 3
    gr, fs = Validator(COURSES, outline()).validate(section(), t)
    assert any(f.code == "unsatisfiable" for f in fs)
    t = good_tree()
    t["group"]["children"][0]["children"][0]["units"] = "9"        # not in source, and breaks the subtotal
    gr, fs = Validator(COURSES, outline()).validate(section(), t)
    assert any(f.code == "units_not_in_source" for f in fs) and any(f.code == "credit_mismatch" for f in fs)


def test_unescalated_ambiguity_is_flagged():
    t = good_tree()
    t["group"]["children"][0]["children"][2]["children"].pop()     # model silently dropped the advisor clause
    gr, fs = Validator(COURSES, outline()).validate(section(), t)
    assert any(f.code == "unescalated_ambiguity" for f in fs) and gr.needs_review


def test_review_text_must_be_verbatim():
    t = good_tree()
    t["group"]["children"][0]["children"][2]["children"][2]["original_text"] = "students may pick any approved course"
    _, fs = Validator(COURSES, outline()).validate(section(), t)
    assert any(f.code == "review_text_not_verbatim" for f in fs)


def test_emit_and_degree_total():
    v = Validator(COURSES, outline())
    gr, fs = v.validate(section(), good_tree())
    rep = finish(outline(), [gr], fs)
    assert any(f.code == "degree_total_mismatch" for f in rep.findings)   # 11-12 units vs stated 103
    out = emit(rep, "BS-TEST", "2026-2027")
    assert any(g["title"] == "NEEDS_REVIEW" and g["needs_review"] for g in out["requirement_group"])
    assert out["review_queue"] and out["requirement"][0]["course_code"] == "MAT 21A"
    assert any(r.get("aliases") == ["BIS 27A"] for r in out["requirement"])


def test_codes_in_and_text_parser():
    assert codes_in("MAT/BIS 027A") == ["MAT 27A", "BIS 27A"]
    assert codes_in("BIO 001 & 001L") == ["BIO 1", "BIO 1L"]
    assert codes_in("or ECS 122B") == ["ECS 122B"]
    o = parse_text("Core Requirements\nCS 1301 Introduction to Computing 3\nCS 1331 Object-Oriented Programming 3\nChoose one of the following: 3\n  MATH 2550 Multivariable Calculus 3\n  MATH 2551 Multivariable Calculus 4\nTotal Credit Hours 122\n", program_title="X")
    g = o.groups()
    assert g and g[0].title == "Core Requirements" and o.stated_total_units == "122"
    assert [l.kind for l in g[0].lines][:3] == ["course", "course", "comment"]
    assert "[NOTE] Choose one of the following" in render_group(g[0])
