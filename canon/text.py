"""Normalization of course codes, titles and institution names. Deterministic and dependency-free."""
from __future__ import annotations

import hashlib
import re
import unicodedata
from typing import Dict, List, Optional, Tuple

_ABBREV: Dict[str, str] = {
    "intro": "introduction", "introductory": "introduction", "prin": "principles", "princ": "principles",
    "fund": "fundamentals", "fundamental": "fundamentals", "calc": "calculus",  # "comp" is ambiguous (composition/computer): left alone
    "prog": "programming", "programing": "programming", "eng": "english", "engl": "english",
    "engr": "engineering", "engineer": "engineering", "gen": "general", "chem": "chemistry",
    "bio": "biology", "biol": "biology", "phys": "physics", "psych": "psychology", "psy": "psychology",
    "soc": "sociology", "econ": "economics", "micro": "microeconomics", "macro": "macroeconomics",
    "stat": "statistics", "stats": "statistics", "statistic": "statistics", "elem": "elementary",
    "elementry": "elementary", "amer": "american", "hist": "history", "govt": "government", "gov": "government",
    "mgmt": "management", "mgt": "management", "acct": "accounting", "acc": "accounting",
    "lab": "laboratory", "labs": "laboratory", "sci": "science", "sciences": "science", "tech": "technology",
    "w": "with", "&": "and", "+": "and", "1st": "first", "2nd": "second", "3rd": "third",
    "anat": "anatomy", "physio": "physiology", "phy": "physics", "diff": "differential", "eqns": "equations",
    "eq": "equations", "alg": "algebra", "trig": "trigonometry", "geom": "geometry", "lit": "literature",
    "comm": "communication", "communications": "communication", "org": "organic", "inorg": "inorganic",
    "humn": "human", "dev": "development", "envir": "environmental", "env": "environmental",
    "comput": "computer", "computing": "computer", "computers": "computer", "sys": "systems", "system": "systems",
    "struct": "structures", "structure": "structures", "app": "applied", "appl": "applied", "beg": "beginning",
    "adv": "advanced", "interm": "intermediate", "us": "united states", "u.s.": "united states",
}
_STOP = {"of", "the", "and", "to", "for", "in", "a", "an", "with", "on", "its", "or", "at", "by", "from",
         "course", "courses", "study", "studies"}
_ROMAN = {"i": "1", "ii": "2", "iii": "3", "iv": "4", "v": "5", "vi": "6", "one": "1", "two": "2", "three": "3"}
_SEQ_LETTER = re.compile(r"^[a-d]$")


def strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def norm_subject(s: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", strip_accents(s or "").upper())


def norm_number(n: str) -> str:
    """'018' -> '18', '101L' -> '101L', ' 2301.0' -> '2301'. Keeps letter suffixes/prefixes."""
    s = strip_accents(n or "").upper().strip()
    s = re.sub(r"\.0+$", "", s)
    s = re.sub(r"[\s\-]", "", s)
    m = re.match(r"^([A-Z]*)0*(\d+)(.*)$", s)
    if m:
        return f"{m.group(1)}{m.group(2)}{m.group(3)}"
    return s


def number_digits(number_key: str) -> Optional[str]:
    m = re.search(r"\d+", number_key or "")
    return m.group(0) if m else None


def norm_title(t: str) -> str:
    s = strip_accents(t or "").lower()
    s = s.replace("&", " and ").replace("+", " and ").replace("/", " ")
    s = re.sub(r"[^a-z0-9\s\.]", " ", s)
    toks = []
    for w in s.split():
        w = w.strip(".")
        if not w:
            continue
        w = _ABBREV.get(w, w)
        w = _ROMAN.get(w, w)
        if w in _STOP:
            continue
        toks.extend(w.split())
    return " ".join(toks)


def title_tokens(t: str) -> List[str]:
    return norm_title(t).split()


_LETTER_SEQ = {"a": "1", "b": "2", "c": "3", "d": "4"}


def sequence_marker(t: str, number_key: Optional[str] = None) -> Optional[str]:
    """Position in a course sequence, normalised across numbering styles so that 'Calculus I',
    'Calculus 1', 'Calculus A' and a course numbered '21A' all yield '1'. None if no marker."""
    toks = norm_title(t).split()
    for w in reversed(toks):
        if w in ("1", "2", "3", "4", "5", "6"):
            return w
        if _SEQ_LETTER.match(w):
            return _LETTER_SEQ[w]
        m = re.match(r"^\d([a-d])$", w)
        if m:
            return _LETTER_SEQ[m.group(1)]
    if number_key:
        m = re.match(r"^\d+([A-D])$", number_key)
        if m:
            return _LETTER_SEQ[m.group(1).lower()]
    return None


def institution_name_key(name: str) -> str:
    s = strip_accents(name or "").lower()
    s = re.sub(r"\(.*?\)", " ", s)               # '(INACTIVE AS OF ...)'
    s = re.sub(r"[^a-z0-9\s]", " ", s)
    toks = [w for w in s.split() if w not in ("the", "of", "at", "and")]
    return " ".join(toks)


def guess_institution_kind(name: str) -> str:
    n = (name or "").lower()
    if "community college" in n or ("college" in n and "university" not in n and "state college" not in n):
        return "community_college"
    if "university" in n or "state college" in n or "institute" in n:
        return "university"
    return "other"


def stable_hash(*parts: str) -> str:
    return hashlib.sha1("\x1f".join(p or "" for p in parts).encode("utf-8")).hexdigest()


def hash_bucket(key: str, salt: str, buckets: int = 100) -> int:
    return int(hashlib.sha1(f"{salt}\x1f{key}".encode()).hexdigest()[:8], 16) % buckets


def parse_units(s) -> Tuple[Optional[float], Optional[float]]:
    if s is None:
        return None, None
    if isinstance(s, (int, float)):
        return float(s), float(s)
    m = re.match(r"^\s*(\d+(?:\.\d+)?)\s*(?:-|to)\s*(\d+(?:\.\d+)?)\s*$", str(s))
    if m:
        return float(m.group(1)), float(m.group(2))
    m = re.match(r"^\s*(\d+(?:\.\d+)?)", str(s))
    if m:
        v = float(m.group(1))
        return v, v
    return None, None
