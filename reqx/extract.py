"""Step 1: pull requirement text out of a catalog page while keeping the heading hierarchy.

Supported inputs
  * CourseLeaf HTML (catalog.*.edu program pages): the `sc_courselist` table plus surrounding headings
    and paragraphs. Row classes carry structure: areaheader / areasubheader / orclass / listsum, comment
    rows ("Choose one:"), and indented choice lists.
  * PDF: text via pypdf; headings inferred from short title-case lines; course rows by regex.
  * Plain text (pasted): same heuristics as PDF.

Output: an Outline — a tree of Sections, each holding the verbatim Lines beneath it. Nothing is
interpreted here beyond "what is a heading, what is a table row, and what is nested under what";
every original string is kept so later stages can be checked against it.
"""
from __future__ import annotations

import html as htmlmod
import json
import re
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional, Tuple



@dataclass
class Line:
    kind: str                 # course | or_course | comment | subtotal | total | text
    text: str                 # verbatim, cells joined with ' | '
    codes: List[str] = field(default_factory=list)   # normalized codes found in the code cell
    title: Optional[str] = None
    units: Optional[str] = None
    indent: int = 0
    footnotes: List[str] = field(default_factory=list)


@dataclass
class Section:
    title: str
    level: int                # 1 = page, 2 = h2 / area, 3 = sub-area, ...
    kind: str                 # heading | area | subarea
    lines: List[Line] = field(default_factory=list)
    children: List["Section"] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {"title": self.title, "level": self.level, "kind": self.kind,
                "lines": [asdict(l) for l in self.lines], "children": [c.to_dict() for c in self.children]}

    def all_text(self) -> str:
        parts = [self.title] + [l.text for l in self.lines]
        for c in self.children:
            parts.append(c.all_text())
        return "\n".join(parts)


@dataclass
class Outline:
    program_title: str
    stated_total_units: Optional[str]
    preamble: List[str]
    root: Section
    source: str

    def to_dict(self) -> Dict[str, Any]:
        return {"program_title": self.program_title, "stated_total_units": self.stated_total_units,
                "preamble": self.preamble, "source": self.source, "root": self.root.to_dict()}

    def groups(self) -> List[Section]:
        """Top-level requirement groups to send to the model one at a time."""
        return [c for c in self.root.children if c.kind in ("area", "heading") and (c.lines or c.children)]


# ---------------------------------------------------------------------------
def _strip(s: str) -> str:
    return re.sub(r"\s+", " ", htmlmod.unescape(re.sub(r"<[^>]+>", " ", s))).strip()


def normalize_code(subject: str, number: str) -> str:
    return f"{subject.upper()} {number.lstrip('0').upper() or '0'}"


def codes_in(text: str) -> List[str]:
    """'MAT/BIS 027A' -> ['MAT 27A', 'BIS 27A'];  'BIO 001 & 001L' -> ['BIO 1', 'BIO 1L']; 'ECS 122A' -> ['ECS 122A']."""
    out: List[str] = []
    last_subj = None
    for m in re.finditer(r"([A-Z]{2,4}(?:/[A-Z]{2,4})*)?\s?(?<![A-Za-z])0*(\d{1,4}[A-Z]{0,3})\b", text):
        subj, num = m.group(1), m.group(2)
        if subj:
            last_subj = subj
        if not last_subj or not num or (not subj and not re.search(r"(&|and|,|/)\s*$", text[:m.start()].rstrip() + " ") and not re.search(r"(&|and|,|/)\s*(?:[A-Z]{2,4}\s?)?$", text[:m.start()])):
            if not subj:
                continue
        for s in last_subj.split("/"):
            c = normalize_code(s, num)
            if c not in out:
                out.append(c)
    return out


def parse_courseleaf(html: str, source: str = "html") -> Outline:
    h = re.sub(r"<(script|style).*?</\1>", " ", html, flags=re.S)
    title_m = re.search(r'<h1[^>]*class="page-title"[^>]*>(.*?)</h1>', h, re.S) or re.search(r"<h1[^>]*>(.*?)</h1>", h, re.S)
    program_title = _strip(title_m.group(1)) if title_m else "Untitled program"
    m = re.search(r'id="requirementstextcontainer"(.*?)(?:<div id="[a-z]*textcontainer"|<footer|$)', h, re.S)
    seg = m.group(1) if m else (re.search(r'id="textcontainer"(.*?)(?:<footer|$)', h, re.S) or [None, h])[1]
    preamble: List[str] = []
    stated_total = None
    root = Section(program_title, 1, "heading")
    stack: List[Section] = [root]

    def push(sec: Section):
        while len(stack) > 1 and stack[-1].level >= sec.level:
            stack.pop()
        stack[-1].children.append(sec)
        stack.append(sec)

    for tag, cls, body in re.findall(r"<(h[2-5]|tr|p)(?:\s+class=\"([^\"]*)\")?[^>]*>(.*?)</\1>", seg, re.S):
        cls = cls or ""
        if tag.startswith("h"):
            push(Section(_strip(body), int(tag[1]), "heading"))
            continue
        if tag == "p":
            t = _strip(body)
            if not t:
                continue
            if len(stack) == 1:
                preamble.append(t)
            else:
                stack[-1].lines.append(Line("text", t))
            tm = re.search(r"(?:minimum number of units|total units|minimum of|units required)[^.]*?(\d{2,3})", t, re.I)
            if tm and stated_total is None:
                stated_total = tm.group(1)
            continue
        # table row
        if "hidden" in cls:
            continue
        cells = re.findall(r"<td[^>]*>(.*?)</td>", body, re.S)
        texts = [_strip(c) for c in cells]
        if not any(texts):
            continue
        indent = 1 if re.search(r'margin-left|class="[^"]*indent', body) else 0
        sup = [_strip(x) for x in re.findall(r"<sup>(.*?)</sup>", body, re.S)]
        clean = [re.sub(r"\s*\d\s*$", "", t) if i == 1 else t for i, t in enumerate(texts)]  # footnote digit glued to title
        if "areaheader" in cls:
            push(Section(texts[0], 2, "area"))
            continue
        if "areasubheader" in cls:
            sec = Section(texts[0], 3, "subarea")
            push(sec)
            if len(texts) > 2 and texts[2]:
                sec.lines.append(Line("comment", f"{texts[0]} | {texts[2]}", units=texts[2], footnotes=sup))
            continue
        code_cell, title_cell = texts[0], (texts[1] if len(texts) > 1 else "")
        units = texts[2] if len(texts) > 2 and texts[2] else None
        joined = " | ".join(t for t in texts if t)
        if "listsum" in cls:
            stack[-1].lines.append(Line("total", joined, units=units))
            if stated_total is None and units:
                stated_total = units
            continue
        if re.search(r"subtotal", code_cell, re.I):
            stack[-1].lines.append(Line("subtotal", joined, units=units))
            continue
        codes = codes_in(code_cell.replace("or ", "", 1) if "orclass" in cls else code_cell)
        if codes:
            stack[-1].lines.append(Line("or_course" if "orclass" in cls else "course", joined, codes=codes,
                                        title=clean[1] if len(clean) > 1 else None, units=units, indent=indent, footnotes=sup))
        else:
            stack[-1].lines.append(Line("comment", joined, units=units, indent=indent, footnotes=sup))
    return Outline(program_title, stated_total, preamble, root, source)


def parse_text(text: str, source: str = "text", program_title: str = "Untitled program") -> Outline:
    """Pasted text or PDF text. Headings: short lines without a course code that end without a period
    and are followed by content. Course rows: lines starting with a code. Units: trailing number."""
    root = Section(program_title, 1, "heading")
    stack: List[Section] = [root]
    preamble: List[str] = []
    stated_total = None
    for raw in text.splitlines():
        line = raw.rstrip()
        t = line.strip()
        if not t:
            continue
        indent = 1 if (len(line) - len(line.lstrip()) >= 2 or t.startswith(("•", "-", "*"))) else 0
        t = t.lstrip("•-* ").strip()
        tm = re.search(r"(?:minimum number of units|total units|minimum of|units required|total credit hours)[^.]*?(\d{2,3})", t, re.I)
        if tm and stated_total is None:
            stated_total = tm.group(1)
        codes = codes_in(t) if re.match(r"^(or\s+)?[A-Z]{2,4}(/[A-Z]{2,4})*\s?\d", t) else []
        units_m = re.search(r"(\d{1,2}(?:\.\d)?(?:\s*-\s*\d{1,2}(?:\.\d)?)?)\s*(?:units?|credits?|hours?|cr\.?)?\s*$", t)
        units = units_m.group(1) if units_m and (codes or re.search(r"choose|select|units|credits|hours", t, re.I)) else None
        if codes:
            stack[-1].lines.append(Line("or_course" if t.lower().startswith("or ") else "course", t, codes=codes, units=units, indent=indent))
        elif re.search(r"^total", t, re.I) and units:
            stack[-1].lines.append(Line("total", t, units=units))
            stated_total = stated_total or units
        elif len(t) <= 80 and not t.endswith((".", ":", ";")) and not re.search(r"\d\s*$", t) and t[0].isupper() and not indent:
            level = 2 if t.isupper() or len(stack) == 1 else min(stack[-1].level + 1, 4)
            sec = Section(t, level, "area" if level == 2 else "subarea")
            while len(stack) > 1 and stack[-1].level >= level:
                stack.pop()
            stack[-1].children.append(sec); stack.append(sec)
        else:
            (preamble if len(stack) == 1 else stack[-1].lines).append(t if len(stack) == 1 else Line("comment", t, units=units, indent=indent))
    return Outline(program_title, stated_total, preamble, root, source)


def parse_pdf(path: str, program_title: str = None) -> Outline:
    from pypdf import PdfReader
    reader = PdfReader(path)
    text = "\n".join((p.extract_text() or "") for p in reader.pages)
    first = next((l.strip() for l in text.splitlines() if l.strip()), "Untitled program")
    return parse_text(text, source=path, program_title=program_title or first)


def load(path: str) -> Outline:
    if path.lower().endswith(".pdf"):
        return parse_pdf(path)
    data = open(path, encoding="utf-8", errors="replace").read()
    if "<html" in data[:2000].lower() or "sc_courselist" in data:
        return parse_courseleaf(data, source=path)
    return parse_text(data, source=path)


def render_group(sec: Section, depth: int = 0) -> str:
    """Indented plain text of one group, exactly what the model sees for that group."""
    tag = {"area": "AREA", "subarea": "SUBAREA", "heading": "HEADING"}[sec.kind]
    out = ["  " * depth + f"[{tag}] {sec.title}"]
    for l in sec.lines:
        marker = {"course": "", "or_course": "[OR] ", "comment": "[NOTE] ", "subtotal": "[SUBTOTAL] ", "total": "[TOTAL] ", "text": "[TEXT] "}[l.kind]
        fn = f"  (footnote {','.join(l.footnotes)})" if l.footnotes else ""
        out.append("  " * (depth + 1 + l.indent) + marker + l.text + fn)
    for c in sec.children:
        out.append(render_group(c, depth + 1))
    return "\n".join(out)
