"""Step 2: the prompt and the strict output schema. Shown separately from the validator on purpose."""
from __future__ import annotations

import json
from typing import Any, Dict, List

SYSTEM_PROMPT = """You convert one section of a university degree-requirements catalog into a requirement expression tree.

You are a transcriber, not an advisor. The rules below are absolute.

1. Never invent a requirement. Every course code, count, unit figure and title you output must appear verbatim in the section text you are given. If the section does not say it, you do not say it.
2. Never resolve ambiguity. If the text delegates a decision ("consult your advisor", "or an approved substitute", "with departmental approval", "selected in consultation with", "see the department for the current list", "recommended", "may be substituted", "petition") or lists a requirement whose courses are not enumerated here, emit a node of kind "needs_review" carrying the original text verbatim. Do not guess what the department would accept. A needs_review node is a correct answer, not a failure.
3. Structure follows the text's own structure. Headings, indentation, "Choose one:", "or", "&", "and", "one of the following", "N units from" define groups:
   - a list of required courses under a heading -> operator "all"
   - "Choose N" / "select N" / "one of the following" -> operator "any_n" with min_count N
   - "N units from ..." / "N credits of ..." -> operator "credits" with min_credits N
   - an "or" row (marked [OR]) is an alternative to the row immediately above it -> wrap both in an "any_n" group with min_count 1
   - "A & B" in one row means both courses are taken together -> an "all" group containing both
   - "MAT/BIS 27A" is one course listed under two codes -> a single course node with the first code and the other code in `aliases`
4. Units: copy the unit figure shown for the row or the choice ("3-4" stays "3-4"). If none is shown, leave units null. Never compute or estimate units.
5. Footnotes and [NOTE]/[TEXT] lines are part of the requirement. If a note changes the meaning (restricts choices, adds conditions) and you cannot express it exactly with the operators above, attach it as a needs_review node inside the affected group with the note's text.
6. Report a confidence in [0,1] for the section as a whole and for each group: 1.0 means the tree is a mechanical transcription with no judgement; below 0.8 means you had to interpret wording. Any needs_review node caps the confidence of its group at 0.8.
7. Output only the JSON object described by the schema. Use the exact course code spelling from the text (subject, space, number).

You are given: the program title, the stated total units for the degree if the catalog states one, and the text of ONE section rendered as an indented outline with tags [AREA], [SUBAREA], [NOTE], [OR], [SUBTOTAL], [TOTAL], [TEXT], (footnote n)."""


def _leaf_props() -> Dict[str, Any]:
    return {
        "kind": {"type": "string", "enum": ["group", "course", "course_list", "filter", "needs_review"]},
        "title": {"type": ["string", "null"]},
        "operator": {"type": ["string", "null"], "enum": ["all", "any_n", "credits", None]},
        "min_count": {"type": ["integer", "null"]},
        "min_credits": {"type": ["number", "null"]},
        "code": {"type": ["string", "null"], "description": "kind=course: the course code exactly as written"},
        "aliases": {"type": "array", "items": {"type": "string"}, "description": "kind=course: cross-listed codes for the same course"},
        "units": {"type": ["string", "null"], "description": "unit figure as shown, e.g. '4' or '3-4'"},
        "courses": {"type": "array", "items": {"type": "string"}, "description": "kind=course_list: codes exactly as written"},
        "subject_codes": {"type": "array", "items": {"type": "string"}, "description": "kind=filter: subjects the text names"},
        "min_level": {"type": ["integer", "null"]},
        "max_level": {"type": ["integer", "null"]},
        "original_text": {"type": ["string", "null"], "description": "kind=needs_review: the catalog text verbatim"},
        "reason": {"type": ["string", "null"], "description": "kind=needs_review: why this cannot be transcribed mechanically"},
        "source_lines": {"type": "array", "items": {"type": "string"}, "description": "verbatim lines of the section this node came from"},
        "confidence": {"type": "number"},
    }


def _node(depth: int) -> Dict[str, Any]:
    props = _leaf_props()
    if depth > 0:
        props["children"] = {"type": "array", "items": _node(depth - 1)}
    else:
        props["children"] = {"type": "array", "items": {"type": "object", "properties": {}, "additionalProperties": False}, "maxItems": 0}
    return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}


# Fixed nesting depth (area -> subarea -> choice -> paired courses) instead of a recursive $ref, so the
# schema is accepted by strict structured-output mode.
OUTPUT_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "section_title": {"type": "string"},
        "confidence": {"type": "number"},
        "notes": {"type": ["string", "null"], "description": "anything the reviewer should know; never a requirement"},
        "group": _node(4),
    },
    "required": ["section_title", "confidence", "notes", "group"],
    "additionalProperties": False,
}


def user_message(program_title: str, stated_total: str, group_text: str, preamble: List[str]) -> str:
    pre = ("\n".join("  " + p for p in preamble)) if preamble else "  (none)"
    return (f"Program: {program_title}\n"
            f"Stated total units for the degree: {stated_total or 'not stated on this page'}\n"
            f"Page preamble (context only, not part of this section):\n{pre}\n\n"
            f"Section to transcribe:\n\n{group_text}\n")


def show(program_title: str, stated_total: str, group_text: str, preamble: List[str]) -> str:
    return ("=== SYSTEM ===\n" + SYSTEM_PROMPT + "\n\n=== USER ===\n" + user_message(program_title, stated_total, group_text, preamble)
            + "\n=== OUTPUT SCHEMA (output_config.format, json_schema) ===\n" + json.dumps(OUTPUT_SCHEMA, indent=1))
