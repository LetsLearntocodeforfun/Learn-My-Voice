"""The creator this installation writes for, as they described themselves during setup.

One creator per installation, kept in ``creator.json`` in the data folder
(``Store.load_creator`` / ``Store.save_creator``). Nothing here is inferred by
a model: every field was typed in by the creator.

- ``normalize``    validates what a form sends and returns the profile to store;
- ``is_set``       says whether setup has happened;
- ``for_page``     is the profile as the interface receives it, with ``is_set``;
- ``prompt_block`` is what the prompts receive, and works before setup;
- ``apply_never``  adds the phrases the creator refuses to say to a COPY of a
                   voice profile, so the linter enforces them by rule. They are
                   never written into the stored voice profile.

Stored shape::

    {"name": "小满", "pronoun": "she", "channel": "", "niches": ["护肤"], "about": "",
     "partner": {"called": "我老公", "language": "English"},      # or None
     "never": ["宝子们"], "created_at": 1763157600.0, "updated_at": 1763157600.0}
"""
from __future__ import annotations

import copy
import re
import time
from typing import Any, Dict, List, Optional

from . import guardrails

PRONOUNS = ("she", "he", "they")       # used for English text only
DEFAULT_PRONOUN = "they"
MAX_NAME_CHARS = 40
MAX_CHANNEL_CHARS = 60
MAX_ABOUT_CHARS = 500
MAX_NICHES = 8
MAX_NICHE_CHARS = 20
MAX_PARTNER_CHARS = 20
MAX_LANGUAGE_CHARS = 30
MAX_NEVER = 50                         # must match the number in err_too_many_never in studio/web/pages.js
MAX_NEVER_CHARS = 40

NOT_SET_NAME = "the creator"
NOT_SET_NOTE = (
    "No creator profile has been set up yet. Refer to \"the creator\", use \"they\" in English text, and assume "
    "nothing about gender, niche or who else appears on camera beyond what the voice profile and the creator's "
    "own scripts show."
)

_FIELDS = ("name", "pronoun", "channel", "niches", "about", "partner", "never")
_SPACE = re.compile(r"\s+")
_SEPARATORS = re.compile(r"[\n,\uff0c\u3001;\uff1b]")      # line break  ,  ，  、  ;  ；


def _line(value: Any, limit: int, code: str) -> str:
    """One line of text: None becomes "", runs of whitespace become one space, cut at ``limit``."""
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError(code)
    return _SPACE.sub(" ", value).strip()[:limit].strip()


def _entries(value: Any, limit: int, code: str, lead: str = "") -> List[str]:
    """Distinct entries from a list of strings or from one string, in the order given.

    Either way entries are split on line breaks, 、, commas and semicolons, so an
    entry cannot contain one of those. ``lead`` characters are removed from the
    start of each entry.
    """
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple)) or not all(isinstance(item, str) for item in value):
        raise ValueError(code)
    out: List[str] = []
    for item in value:
        for piece in _SEPARATORS.split(item):
            entry = _SPACE.sub(" ", piece).strip().lstrip(lead).strip()[:limit].strip()
            if entry and entry not in out:
                out.append(entry)
    return out


def _partner(value: Any) -> Optional[Dict[str, str]]:
    """``{"called", "language"}``, or None when nobody appears on camera with the creator."""
    if isinstance(value, str):
        value = {"called": value}
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("bad_partner")
    called = _line(value.get("called"), MAX_PARTNER_CHARS, "bad_partner")
    language = _line(value.get("language"), MAX_LANGUAGE_CHARS, "bad_partner")
    return {"called": called, "language": language} if called else None


def _slop_errors(script: Dict[str, Any], phrases: List[str]) -> List[guardrails.Finding]:
    """What the linter flags in ``script`` when ``phrases`` are the only banned phrases and nothing else is checked."""
    dna = {"anti_voice_blacklist": {"banned_phrases_zh": phrases}}
    return [f for f in guardrails.lint_script(script, dna, lexicon={})
            if f.category == guardrails.GENERIC_XHS_SLOP and f.severity == guardrails.ERROR]


def _enforced_form(phrase: str) -> str:
    """The text the linter matches in a line that says exactly ``phrase``; "" when it would match nothing.

    Asked of the linter itself rather than re-implemented here, so the answer
    stays true if the linter's reading of ban lists changes. For a plain phrase
    this is the phrase. A trailing note in brackets, a trailing ellipsis or an
    ``XX`` placeholder is not part of what gets matched, so it is not kept.
    """
    hits = _slop_errors({"script_lines": [{"line_no": 1, "spoken_script_zh": phrase}]}, [phrase])
    return hits[0].matched if hits else ""


def normalize(body: Any, previous: Any = None, now: Optional[float] = None) -> Dict[str, Any]:
    """Validate a profile sent by a form and return the profile to store.

    Fields missing from ``body`` keep their value from ``previous`` (the stored
    profile), so a caller may send only what changed; a field sent as null or
    empty is cleared. Unknown keys and timestamps in ``body`` are ignored.

    Raises ``ValueError`` whose message is a short code: ``bad_profile``,
    ``name_required``, ``bad_pronoun``, ``bad_channel``, ``bad_niches``,
    ``bad_about``, ``bad_partner``, ``bad_never``, ``too_many_never`` (more
    than MAX_NEVER phrases), or ``never_uncheckable`` (a phrase the linter
    could never match: one with no letter or digit in it, such as brackets, an
    ellipsis, punctuation or an emoji on its own).

    Each ``never`` phrase is stored in the form the linter enforces (see
    ``_enforced_form``), so the stored list is exactly what is checked. Two
    phrases the linter reads as the same one (``guardrails.ban_key``: they differ
    only in width, letter case, an invisible character or what separates the
    characters) are stored once, as first typed. Text over a length limit is
    cut; a cut ``never`` phrase still matches everything the full phrase would.
    Niches beyond MAX_NICHES are dropped.
    """
    if not isinstance(body, dict):
        raise ValueError("bad_profile")
    kept = previous if is_set(previous) else {}
    given = {key: body[key] if key in body else kept.get(key) for key in _FIELDS}

    name = _line(given["name"] if isinstance(given["name"], str) else None, MAX_NAME_CHARS, "name_required")
    if not name:
        raise ValueError("name_required")
    pronoun = _line(given["pronoun"], 20, "bad_pronoun").lower() or DEFAULT_PRONOUN
    if pronoun not in PRONOUNS:
        raise ValueError("bad_pronoun")
    about = given["about"]
    if about is not None and not isinstance(about, str):
        raise ValueError("bad_about")
    about = (about or "").replace("\r\n", "\n").replace("\r", "\n").strip()[:MAX_ABOUT_CHARS].strip()
    never: List[str] = []
    seen = set()
    for phrase in _entries(given["never"], MAX_NEVER_CHARS, "bad_never"):
        enforced = _enforced_form(phrase)
        if not enforced:
            raise ValueError("never_uncheckable")
        key = guardrails.ban_key(enforced)
        if key not in seen:
            seen.add(key)
            never.append(enforced)
    if len(never) > MAX_NEVER:
        raise ValueError("too_many_never")

    now = time.time() if now is None else now
    created = kept.get("created_at")
    return {
        "name": name,
        "pronoun": pronoun,
        "channel": _line(given["channel"], MAX_CHANNEL_CHARS, "bad_channel"),
        "niches": _entries(given["niches"], MAX_NICHE_CHARS, "bad_niches", lead="#\uff03")[:MAX_NICHES],
        "about": about,
        "partner": _partner(given["partner"]),
        "never": never,
        "created_at": created if isinstance(created, (int, float)) and not isinstance(created, bool) else now,
        "updated_at": now,
    }


def is_set(creator: Any) -> bool:
    """Whether setup has happened: a stored profile exists and has a name."""
    return isinstance(creator, dict) and isinstance(creator.get("name"), str) and bool(creator["name"].strip())


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _stored(creator: Any) -> Optional[Dict[str, Any]]:
    """The stored profile with every field in its expected type, or None before setup.

    ``normalize`` already guarantees the types; this keeps the readers below
    working if ``creator.json`` was edited by hand.
    """
    if not is_set(creator):
        return None
    partner = creator.get("partner") if isinstance(creator.get("partner"), dict) else {}
    niches = creator.get("niches") if isinstance(creator.get("niches"), list) else []
    never = creator.get("never") if isinstance(creator.get("never"), list) else []
    return {
        "name": creator["name"].strip(),
        "pronoun": creator.get("pronoun") if creator.get("pronoun") in PRONOUNS else DEFAULT_PRONOUN,
        "channel": _text(creator.get("channel")),
        "niches": [_text(n) for n in niches if _text(n)],
        "about": _text(creator.get("about")),
        "partner": ({"called": _text(partner.get("called")), "language": _text(partner.get("language"))}
                    if _text(partner.get("called")) else None),
        "never": [_text(p) for p in never if _text(p)],
    }


def for_page(creator: Any) -> Dict[str, Any]:
    """The profile as the interface receives it: every field present, plus ``is_set``.

    Before setup this is an empty profile with ``is_set`` false, so a form can
    read it without checking for a missing profile.
    """
    stored = _stored(creator)
    out: Dict[str, Any] = stored or {"name": "", "pronoun": DEFAULT_PRONOUN, "channel": "", "niches": [], "about": "",
                                     "partner": None, "never": []}
    if stored:
        out.update({key: creator[key] for key in ("created_at", "updated_at") if key in creator})
    out["is_set"] = stored is not None
    return out


def english_name(creator: Any) -> str:
    """How English text refers to the creator: by name, or as "the creator" before setup."""
    return creator["name"].strip() if is_set(creator) else NOT_SET_NAME


def never_phrases(creator: Any) -> List[str]:
    """The phrases the creator refuses to say; empty before setup."""
    stored = _stored(creator)
    return stored["never"] if stored else []


def never_hits(script: Dict[str, Any], creator: Any) -> List[Any]:
    """``(line_no, matched)`` for each place in ``script`` that the creator's own list flags.

    Lets a finding say that it comes from the creator's list rather than from the voice profile.
    """
    phrases = never_phrases(creator)
    return [(f.line_no, f.matched) for f in _slop_errors(script, phrases)] if phrases else []


def prompt_block(creator: Any) -> Dict[str, Any]:
    """What the prompts receive as ``{{creator_profile_json}}``. Usable when nothing is set yet.

    ``profile_set`` false tells the model that nothing is known, and ``note``
    says what to do then. ``on_camera_partner`` is None when nobody appears
    with the creator.
    """
    stored = _stored(creator)
    if stored is None:
        return {"profile_set": False, "name": None, "pronoun": DEFAULT_PRONOUN, "channel": None, "niches": [],
                "about": None, "on_camera_partner": None, "never_say": [], "note": NOT_SET_NOTE}
    partner = stored["partner"]
    return {
        "profile_set": True,
        "name": stored["name"],
        "pronoun": stored["pronoun"],
        "channel": stored["channel"] or None,
        "niches": stored["niches"],
        "about": stored["about"] or None,
        "on_camera_partner": ({"referred_to_as": partner["called"], "speaks": partner["language"] or None}
                              if partner else None),
        "never_say": stored["never"],
    }


def apply_never(dna: Any, creator: Any) -> Dict[str, Any]:
    """Return a copy of voice profile ``dna`` whose ban list also holds the creator's ``never`` phrases.

    Use the copy wherever a profile is handed to the linter or to a drafting
    prompt. ``dna`` itself is left untouched, and the copy must not be saved as
    the voice profile: the phrases belong to the creator profile, so that
    removing one there really stops it being enforced.
    """
    merged: Dict[str, Any] = copy.deepcopy(dna) if isinstance(dna, dict) else {}
    phrases = never_phrases(creator)
    if not phrases:
        return merged
    blacklist = merged.get("anti_voice_blacklist")
    if not isinstance(blacklist, dict):
        blacklist = merged["anti_voice_blacklist"] = {}
    banned = blacklist.get("banned_phrases_zh")
    banned = list(banned) if isinstance(banned, list) else []
    blacklist["banned_phrases_zh"] = banned + [p for p in phrases if p not in banned]
    return merged
