"""Resolver behaviour on a tiny in-memory SQLite graph: precedence, vetoes, as-of, corrections, replay."""
import sqlite3

from canon.models import AssertionRecord, CourseRef, InstitutionRef, MEMBER_OF, SATISFIES, DENIED
from canon.resolve import equivalents, explain, resolve
from canon.store import Store


def fresh():
    st = Store(sqlite3.connect(":memory:"), "sqlite")
    st.create_schema()
    st.ensure_source("assist", "ASSIST", "articulation", 1.0)
    st.ensure_source("scns", "SCNS", "common_numbering", 0.98)
    return st


CC = InstitutionRef("assist", "2", "Evergreen Valley College", "CA", "community_college")
U = InstitutionRef("assist", "39", "San Jose State University", "CA", "university")
FL1 = InstitutionRef("scns", "1", "South Florida State College", "FL")
FL2 = InstitutionRef("scns", "22", "University of South Florida", "FL")


def test_ground_truth_beats_matcher_and_negatives_veto_inferred():
    st = fresh()
    run = st.start_run("assist")
    a = CourseRef("CS", "22A", CC, title="Python for Everyone")
    b = CourseRef("CS", "46A", U, title="Introduction to Programming")
    c = CourseRef("CS", "49J", U, title="Programming in Java")
    st.add_assertion(AssertionRecord(a, b, SATISFIES, 1.0, "74/2/to/39", "2023-10-01", "2024-10-01"), "assist", run)
    st.add_assertion(AssertionRecord(a, c, DENIED, 1.0, "74/2/to/39", "2023-10-01", "2024-10-01"), "assist", run)
    ia, ib, ic = st.node_id(a, "assist", run), st.node_id(b, "assist", run), st.node_id(c, "assist", run)
    mrun = st.start_run("matcher")
    for t in (ib, ic):
        st.insert("assertion", {"from_node_id": ia, "to_node_id": t, "relation": SATISFIES, "confidence": 0.995, "source_code": "matcher",
                                "run_id": mrun, "source_ref": "m", "asserted_at": "2026-01-01", "evidence": {}, "dedup_key": f"m{t}"})
    st.commit()
    stats = resolve(st, matcher_threshold=0.9)
    eq = equivalents(st, ia)
    assert [e["to_node_id"] for e in eq] == [ib]
    assert eq[0]["basis"] == "ground_truth" and eq[0]["confidence"] == 1.0
    assert stats["inferred_vetoed_by_negative"] == 1
    ex = explain(st, ia, ic)
    assert ex["resolved_edges"] == [] and {x["relation"] for x in ex["assertions"]} == {DENIED, SATISFIES}


def test_as_of_catalog_and_system_time_and_corrections_replay():
    st = fresh()
    run = st.start_run("assist")
    a = CourseRef("MATH", "1", CC, title="Calculus I")
    b = CourseRef("MATH", "30", U, title="Calculus I")
    b2 = CourseRef("MATH", "30X", U, title="Calculus I (new number)")
    st.add_assertion(AssertionRecord(a, b, SATISFIES, 1.0, "k1", "2022-10-01", "2023-10-01"), "assist", run)
    st.add_assertion(AssertionRecord(a, b2, SATISFIES, 1.0, "k2", "2023-10-01", "2024-10-01"), "assist", run)
    st.commit()
    ia, ib, ib2 = (st.node_id(x, "assist", run) for x in (a, b, b2))
    resolve(st)
    t1 = st.query("SELECT max(system_from) s FROM resolved_edge")[0]["s"]
    assert [e["to_node_id"] for e in equivalents(st, ia, effective_at="2023-01-15")] == [ib]
    assert [e["to_node_id"] for e in equivalents(st, ia, effective_at="2024-01-15")] == [ib2]
    # a registrar rejects the 2023 mapping; correction outranks ground truth and survives re-resolution
    st.add_correction("registrar@sjsu.edu", "course", "reject", "not equivalent after 2023 revision", from_node_id=ia, to_node_id=ib2)
    resolve(st)
    assert equivalents(st, ia, effective_at="2024-01-15") == []
    # what we believed at t1 is still answerable
    assert [e["to_node_id"] for e in equivalents(st, ia, effective_at="2024-01-15", system_at=t1)] == [ib2]
    # replay after a "retrain": resolve again, correction still wins, nothing duplicated
    before = st.query("SELECT count(*) n FROM resolved_edge")[0]["n"]
    resolve(st)
    assert st.query("SELECT count(*) n FROM resolved_edge")[0]["n"] == before


def test_hub_membership_is_directed_and_transitive_with_discount():
    st = fresh()
    run = st.start_run("scns")
    hub = CourseRef("ENC", "101", system_code="scns", title="English Composition")
    x = CourseRef("ENC", "1101", FL1, title="Freshman English I")
    y = CourseRef("ENC", "1101", FL2, title="Composition I")
    st.add_assertion(AssertionRecord(x, hub, MEMBER_OF, 1.0, "l1"), "scns", run)
    st.add_assertion(AssertionRecord(y, hub, MEMBER_OF, 1.0, "l2"), "scns", run)
    st.commit()
    ix, iy = st.node_id(x, "scns", run), st.node_id(y, "scns", run)
    resolve(st)
    eq = equivalents(st, ix)
    derived = [e for e in eq if e["to_node_id"] == iy]
    assert derived and derived[0]["basis"] == "derived_via_hub:scns" and abs(derived[0]["confidence"] - 0.98 * 0.98) < 1e-6
    assert len(derived[0]["path"]) == 2
