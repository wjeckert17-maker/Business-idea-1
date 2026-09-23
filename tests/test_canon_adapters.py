import json
import os
import tempfile

from canon.models import AssertionRecord, CourseRef, CoverageRecord, SATISFIES, SATISFIES_JOINTLY, DENIED, MEMBER_OF
from canon.sources.assist import AssistAdapter
from canon.sources.scns import parse_line, FIELDS
from canon.sources.tccns import split_local


def test_tccns_cell_parsing():
    assert split_local("ACC 2303/2000") == [("ACC", "2303"), ("ACC", "2000")]
    assert split_local("ACCT 0000") == []
    assert split_local("ACCT 2301") == [("ACCT", "2301")]
    assert split_local("") == []


def test_scns_fixed_width():
    line = "".join(v.ljust(w) for v, w in zip(
        ["0000001", "001", "ENC", "1", "1", "0", "1", "", "0141249", "N", "A", "FRESHMAN ENGLISH I", "3.0", "", "CC", "08182023", "",
         "02062006", "05082024", "N", "N", "Y", "N", "N", "N", "N", "", "", "0000000", "ENGLISH COMPOSITION", "N", "Y", "EL", "A", "N"],
        [w for _, w in FIELDS]))
    r = parse_line(line)
    assert (r["ID_PREFIX"], r["CD_LEVEL"], r["ID_CENTURY"] + r["ID_DECADE"] + r["ID_UNIT"], r["IC_DS_TITLE"]) == ("ENC", "1", "101", "FRESHMAN ENGLISH I")


def test_assist_groups_and_denials():
    agreement = {"isSuccessful": True, "_url": "u", "result": {
        "receivingInstitution": json.dumps({"id": 39, "names": [{"name": "San Jose State University"}], "isCommunityCollege": False}),
        "sendingInstitution": json.dumps({"id": 2, "names": [{"name": "Evergreen Valley College"}], "isCommunityCollege": True}),
        "academicYear": json.dumps({"id": 74, "code": "2023-2024", "beginDate": "2023-10-01T00:00:00", "endDate": "2024-10-01T00:00:00"}),
        "articulations": json.dumps([{"name": "Math", "articulations": [
            {"type": "Course", "course": {"courseIdentifierParentId": 1, "prefix": "MATH", "courseNumber": "30", "courseTitle": "Calculus I", "begin": "F2014", "end": ""},
             "sendingArticulation": {"noArticulationReason": None,
                                     "deniedCourses": [{"courseIdentifierParentId": 9, "prefix": "MATH", "courseNumber": "63", "courseTitle": "Business Calc"}],
                                     "items": [{"type": "CourseGroup", "courseConjunction": "Or", "items": [
                                         {"type": "Course", "courseIdentifierParentId": 5, "prefix": "MATH", "courseNumber": "071", "courseTitle": "Calculus I"},
                                         {"type": "Course", "courseIdentifierParentId": 6, "prefix": "MATH", "courseNumber": "071H", "courseTitle": "Honors Calculus I"}]},
                                         {"type": "CourseGroup", "courseConjunction": "And", "items": [
                                             {"type": "Course", "courseIdentifierParentId": 7, "prefix": "MATH", "courseNumber": "070A", "courseTitle": "Calc IA"},
                                             {"type": "Course", "courseIdentifierParentId": 8, "prefix": "MATH", "courseNumber": "070B", "courseTitle": "Calc IB"}]}],
                                     "courseGroupConjunctions": [{"groupConjunction": "Or"}]}}]}])}}
    with tempfile.TemporaryDirectory() as d:
        json.dump({39: {"id": 39, "names": [{"name": "San Jose State University"}], "isCommunityCollege": False},
                   2: {"id": 2, "names": [{"name": "Evergreen Valley College"}], "isCommunityCollege": True}}.__class__(
            [(k, v) for k, v in {}.items()]), open(os.path.join(d, "institutions.json"), "w"))
        json.dump([{"id": 39, "names": [{"name": "San Jose State University"}], "isCommunityCollege": False},
                   {"id": 2, "names": [{"name": "Evergreen Valley College"}], "isCommunityCollege": True}], open(os.path.join(d, "institutions.json"), "w"))
        json.dump([{"id": 74}], open(os.path.join(d, "academic_years.json"), "w"))
        json.dump(agreement, open(os.path.join(d, "agreement_74_2_to_39.json"), "w"))
        recs = list(AssistAdapter(d).records())
    cov = [r for r in recs if isinstance(r, CoverageRecord)]
    assert len(cov) == 1 and cov[0].from_institution.name == "Evergreen Valley College"
    asserts = [r for r in recs if isinstance(r, AssertionRecord)]
    rel = sorted((a.from_ref.number, a.relation) for a in asserts)
    assert rel == [("070A", SATISFIES_JOINTLY), ("070B", SATISFIES_JOINTLY), ("071", SATISFIES), ("071H", SATISFIES), ("63", DENIED)]
    joint = [a for a in asserts if a.relation == SATISFIES_JOINTLY]
    assert joint[0].group_key == joint[1].group_key and joint[0].effective_from == "2023-10-01"
    assert all(a.to_ref.number == "30" and a.to_ref.institution.kind == "university" for a in asserts)
