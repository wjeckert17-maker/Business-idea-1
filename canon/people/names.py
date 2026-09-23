"""Person-name parsing and compatibility. Conservative by design: when in doubt, 'weak' or 'conflict'."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional, Set, Tuple

from ..text import strip_accents

HONORIFICS = {"dr", "prof", "professor", "mr", "mrs", "ms", "miss", "mx", "sir", "dame", "rev", "fr", "hon", "instructor", "lecturer"}
SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v", "phd", "ph.d", "md", "m.d", "esq", "dds", "jd", "mba", "cpa", "pe", "msc", "bsc", "dsc", "mfa", "edd", "rn", "dvm"}
PARTICLES = {"van", "von", "de", "del", "della", "di", "da", "la", "le", "du", "des", "der", "den", "ter", "bin", "ibn", "al", "el", "st", "san", "dos", "das", "y"}
NICKNAMES = {
    "bill": "william", "billy": "william", "will": "william", "willie": "william", "liz": "elizabeth", "beth": "elizabeth", "betsy": "elizabeth",
    "eliza": "elizabeth", "lisa": "elizabeth", "bob": "robert", "rob": "robert", "bobby": "robert", "robbie": "robert", "bert": "robert",
    "dick": "richard", "rick": "richard", "rich": "richard", "richie": "richard", "jim": "james", "jimmy": "james", "jamie": "james",
    "mike": "michael", "mick": "michael", "tom": "thomas", "tommy": "thomas", "dave": "david", "davey": "david", "dan": "daniel", "danny": "daniel",
    "chris": "christopher", "kate": "katherine", "katie": "katherine", "kathy": "katherine", "kat": "katherine", "cathy": "catherine",
    "jen": "jennifer", "jenny": "jennifer", "matt": "matthew", "joe": "joseph", "joey": "joseph", "tony": "anthony", "andy": "andrew",
    "drew": "andrew", "steve": "steven", "stephen": "steven", "steph": "stephanie", "sam": "samuel", "ben": "benjamin", "benny": "benjamin",
    "alex": "alexander", "nick": "nicholas", "pat": "patrick", "ted": "theodore", "ed": "edward", "eddie": "edward", "ned": "edward",
    "jack": "john", "johnny": "john", "jon": "jonathan", "greg": "gregory", "jeff": "jeffrey", "geoff": "geoffrey", "ken": "kenneth", "kenny": "kenneth",
    "ron": "ronald", "ronnie": "ronald", "don": "donald", "doug": "douglas", "larry": "lawrence", "peggy": "margaret", "meg": "margaret",
    "maggie": "margaret", "sue": "susan", "suzy": "susan", "debbie": "deborah", "deb": "deborah", "becky": "rebecca", "chuck": "charles",
    "charlie": "charles", "hank": "henry", "harry": "henry", "abe": "abraham", "al": "albert", "fred": "frederick", "freddie": "frederick",
    "frank": "francis", "gene": "eugene", "gerry": "gerald", "jerry": "gerald", "kim": "kimberly", "lou": "louis", "max": "maximilian",
    "molly": "mary", "ray": "raymond", "sandy": "sandra", "terry": "terence", "tim": "timothy", "timmy": "timothy", "tina": "christina",
    "vicky": "victoria", "walt": "walter", "zach": "zachary", "zack": "zachary", "nate": "nathan", "nathaniel": "nathan", "josh": "joshua",
    "jake": "jacob", "toby": "tobias", "manny": "manuel", "pepe": "jose", "paco": "francisco", "pancho": "francisco", "sasha": "alexander",
    "misha": "mikhail", "dmitri": "dmitry", "seb": "sebastian", "vinny": "vincent", "vince": "vincent", "gus": "augustus", "pete": "peter",
    "mel": "melissa", "trish": "patricia", "pat": "patricia", "patty": "patricia", "trisha": "patricia", "abby": "abigail", "allie": "alison",
    "ellie": "eleanor", "nell": "eleanor", "franny": "frances", "fran": "frances", "annie": "ann", "nan": "ann", "nancy": "ann",
}


@dataclass
class ParsedName:
    raw: str
    family: str
    given: Optional[str]
    middle: List[str]
    honorifics: List[str]
    suffixes: List[str]
    layout: str                     # last_first | first_last | single
    family_key: str
    family_tokens: Set[str]
    given_key: Optional[str]        # canonical (nickname-resolved) lowercase given, or None
    given_initial: Optional[str]
    middle_initials: List[str]
    initials_only: bool
    quality: float = 1.0            # 1 full name, 0.6 initial, 0.3 family only


def _clean(s: str) -> str:
    s = strip_accents(s or "")
    s = re.sub(r"\(.*?\)", " ", s)              # (he/him), (Bill)
    s = re.sub(r"\"[^\"]*\"|'[^']*'", " ", s)   # "Bill"
    s = re.sub(r"[^\w\s,.\-']", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _tok(s: str) -> List[str]:
    return [t for t in re.split(r"[\s]+", s.strip()) if t]


def _norm(t: str) -> str:
    return t.replace(".", "").replace("'", "").lower()


def _is_initial(t: str) -> bool:
    return bool(re.fullmatch(r"(?:[A-Za-z]\.?){1,3}", t)) and len(t.replace(".", "")) <= 3 and (len(t.replace(".", "")) == 1 or "." in t or t.isupper())


def parse_name(raw: str) -> ParsedName:
    s = _clean(raw)
    honor, suffix = [], []
    layout = "first_last"
    if "," in s:
        parts = [p.strip() for p in s.split(",") if p.strip()]
        family_part = parts[0] if parts else ""
        rest_tokens: List[str] = []
        for extra in parts[1:]:
            toks = _tok(extra)
            if toks and all(_norm(t) in SUFFIXES for t in toks):
                suffix.extend(_norm(t) for t in toks)
            else:
                rest_tokens.extend(toks)
        # honorifics can lead the given part ('Smith, Dr. John')
        while rest_tokens and _norm(rest_tokens[0]) in HONORIFICS:
            honor.append(_norm(rest_tokens.pop(0)))
        fam_toks = _tok(family_part)
        while fam_toks and _norm(fam_toks[0]) in HONORIFICS:
            honor.append(_norm(fam_toks.pop(0)))
        layout = "last_first"
        family_toks, given_toks = fam_toks, rest_tokens
        if not given_toks and len(fam_toks) >= 2 and not any(_norm(t) in PARTICLES for t in fam_toks[:-1]):
            # 'John Smith,' with a trailing comma
            layout, family_toks, given_toks = "first_last", fam_toks[-1:], fam_toks[:-1]
    else:
        toks = _tok(s)
        while toks and _norm(toks[0]) in HONORIFICS:
            honor.append(_norm(toks.pop(0)))
        while toks and _norm(toks[-1]) in SUFFIXES and len(toks) > 1:
            suffix.append(_norm(toks.pop()))
        if len(toks) == 1:
            layout, family_toks, given_toks = "single", toks, []
        else:
            # family = last token plus preceding particles ('van der Berg')
            i = len(toks) - 1
            while i - 1 >= 1 and _norm(toks[i - 1]) in PARTICLES:
                i -= 1
            family_toks, given_toks = toks[i:], toks[:i]
    family = " ".join(family_toks)
    family_key = " ".join(_norm(t) for t in family_toks if _norm(t) not in PARTICLES) or _norm(family)
    family_tokens = {p for t in family_key.split() for p in t.split("-") if p}
    given, middle = (given_toks[0] if given_toks else None), given_toks[1:]
    initials_only = bool(given) and _is_initial(given)
    given_key = None
    given_initial = None
    if given:
        g = _norm(given)
        given_initial = g[0] if g else None
        if not initials_only:
            given_key = NICKNAMES.get(g, g)
    middle_initials = [_norm(m)[0] for m in middle if _norm(m)]
    quality = 1.0 if (given and not initials_only) else (0.6 if given else 0.3)
    return ParsedName(raw=raw, family=family, given=given, middle=middle, honorifics=honor, suffixes=suffix, layout=layout,
                      family_key=family_key, family_tokens=family_tokens, given_key=given_key, given_initial=given_initial,
                      middle_initials=middle_initials, initials_only=initials_only, quality=quality)


def compatibility(a: ParsedName, b: ParsedName) -> Tuple[str, str]:
    """Returns (level, reason). level in: exact | strong | weak | conflict.
    exact    same family, same canonical given (middle initials agree if both present)
    strong   same family; given differs only by nickname or one side is an initial matching the other
    weak     same family; one side has no given name at all
    conflict family differs, or both givens are full and different, or middle initials differ"""
    if a.family_key != b.family_key:
        if a.family_tokens & b.family_tokens and (len(a.family_tokens) > 1 or len(b.family_tokens) > 1):
            fam = "hyphenated family shares a component"
            fam_level = "weak"
        else:
            return "conflict", f"family differs ({a.family} vs {b.family})"
    else:
        fam, fam_level = "family matches", "ok"
    if a.middle_initials and b.middle_initials and a.middle_initials[0] != b.middle_initials[0]:
        return "conflict", f"middle initials differ ({a.middle_initials[0]} vs {b.middle_initials[0]})"
    if not a.given or not b.given:
        return "weak", fam + "; one side has no given name"
    if a.initials_only or b.initials_only:
        if a.given_initial == b.given_initial:
            lvl = "strong" if fam_level == "ok" else "weak"
            return lvl, fam + "; given initial matches"
        return "conflict", f"given initial differs ({a.given_initial} vs {b.given_initial})"
    if a.given_key == b.given_key:
        if _norm(a.given) == _norm(b.given):
            return ("exact" if fam_level == "ok" else "strong"), fam + "; given matches"
        return "strong", fam + f"; given matches via nickname ({a.given}/{b.given})"
    if a.given_initial == b.given_initial and (len(_norm(a.given)) <= 2 or len(_norm(b.given)) <= 2):
        return "strong", fam + "; short given form matches initial"
    return "conflict", f"given names differ ({a.given} vs {b.given})"


def email_key(email: Optional[str]) -> Optional[str]:
    if not email or "@" not in email:
        return None
    local, dom = email.strip().lower().split("@", 1)
    return f"{local}@{dom}"
