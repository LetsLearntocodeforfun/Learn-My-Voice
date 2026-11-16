"""Receipts for a proposed voice profile: look every claimed phrase up in the creator's own material.

A model proposes the profile. Nothing here calls a model: each line or habit the
profile attributes to the creator is searched for in their imported scripts and
transcripts, so the same profile and material always give the same report.

- ``shape_profile`` cuts a model's reply down to the fields a profile is known to have.
- ``verify_profile`` reports, per entry, whether it was found, how often and in
  which scripts, and flags phrases the profile says they never use but do. It
  also lists every piece of free text in the profile that nothing can be looked
  up for (``unverified``), so a page can show all of it.
- ``apply_decisions`` returns the profile the creator approved: without what
  they rejected, without quotes that were not found, without bans on things
  they really say, and without anything unconfirmed that they did not tick.

How an entry is read depends on what it claims:

- A quote is looked for whole and exactly as it is shown: nothing is cut off it
  first, not a bracket, not a note, not a leading "none". It is found when all of
  it stands in the material, in order.
- A habit or a ban is a phrase that may carry a note in brackets, which is not
  looked for (``…`` / ``...`` / ``XX`` stand for a short gap inside one breath, as
  in ``guardrails._phrase_parts``). The report gives the phrase that was looked
  for (``phrase``) and says when it differs from the entry as written.

Matching ignores spacing, punctuation, breath marks and everything
``guardrails.fold_char`` folds: width, letter case, accents, characters that draw
nothing, look-alike letters. The linter folds the same way, so a ban that would
flag the creator's own words is reported as a conflict here.
Sentence-final particles are counted as ``corpus.voice_stats`` counts them.
"""
from __future__ import annotations

import copy
import re
import unicodedata
from typing import Any, Callable, Dict, Iterator, List, NamedTuple, Optional, Tuple

from . import guardrails, textstats

QUOTE, TERM, NEVER = "quote", "term", "never"
FOUND, NOT_FOUND, ABSENT, CONFLICT, NOT_CHECKABLE = "found", "not_found", "absent", "conflict", "not_checkable"
ANYWHERE, BREATH_END = "anywhere", "breath_end"

MAX_TITLES = 3
GAP_MAX_CHARS = 12        # same length as the gap in guardrails._phrase_to_regex
LOOSE_MIN_UNITS = 4       # a phrase this long may be found across a pause; a shorter one must sit in one breath

# (list in the profile, field of each entry or None when entries are strings, kind, where it is looked for).
# A trailing ".*" means every list under that object.
_CHECKS: Tuple[Tuple[str, Optional[str], str, str], ...] = (
    ("persona_summary.audience_address_terms.uses", None, TERM, ANYWHERE),
    ("persona_summary.audience_address_terms.never_uses", None, NEVER, ANYWHERE),
    ("persona_summary.husband_and_family_references.spoken_nicknames_zh_en", None, TERM, ANYWHERE),
    ("persona_summary.husband_and_family_references.verbatim_examples", None, QUOTE, ANYWHERE),
    ("persona_summary.regional_and_phonetic_nuances.preserve_colloquialisms", None, TERM, ANYWHERE),
    ("lexical_fingerprint.signature_openers", "verbatim_sample", QUOTE, ANYWHERE),
    ("lexical_fingerprint.discourse_connectors.high_frequency", None, TERM, ANYWHERE),
    ("lexical_fingerprint.discourse_connectors.forbidden_bookish_connectors", None, NEVER, ANYWHERE),
    ("lexical_fingerprint.modal_particles.primary", "particle", TERM, BREATH_END),
    ("lexical_fingerprint.modal_particles.rare_or_zero", None, NEVER, BREATH_END),
    ("lexical_fingerprint.genuine_praise_lexicon.*", None, TERM, ANYWHERE),
    ("code_switching_grammar.good_vs_bad_examples", "authentic_her", QUOTE, ANYWHERE),
    ("anti_voice_blacklist.banned_phrases_zh", None, NEVER, ANYWHERE),
)

# Every value an item's "group" can take, in report order, with its kind.
GROUPS: Tuple[Tuple[str, str], ...] = tuple(
    (path[:-2] if path.endswith(".*") else path, kind) for path, _, kind, _ in _CHECKS)

_STATUSES_BY_KIND = {
    QUOTE: (FOUND, NOT_FOUND, NOT_CHECKABLE),
    TERM: (FOUND, NOT_FOUND, NOT_CHECKABLE),
    NEVER: (ABSENT, CONFLICT, NOT_CHECKABLE),
}

# Canonical text keeps letters and digits and reduces everything between them to one mark.
_SOFT, _MID, _STOP = " ", "\u00b7", "\n"          # spacing / other punctuation / end of a breath
_SEPARATORS = _SOFT + _MID + _STOP
_RANK = {"": 0, _SOFT: 1, _MID: 2, _STOP: 3}
# What a guardrails gap may not cross (/ ， 。 ！ ？), plus the full-width slash and line breaks.
_STOP_CHARS = "/\uff0f\uff0c\u3002\uff01\uff1f\n\r"

_ANY_SEPARATOR = "[%s]" % _SEPARATORS
_LATIN = "[^\\W_\u2e80-\U0010ffff]"                # a letter or digit that is not CJK
_GAP = "[^%s]{0,%d}" % (_STOP, GAP_MAX_CHARS)

# The characters corpus.voice_stats strips from a breath before reading its final particle.
_TAIL_PUNCT = "\uff0c,\u3002\uff01!\uff1f?\uff1b;\uff5e~\u2026 \u3000"

_NOTE_START = re.compile(r"\s*[(\uff08]")          # as in guardrails._phrase_parts
_OPENING, _CLOSING = "(\uff08", ")\uff09"
_CUE_MARKS = "[]\uff3b\uff3d\u3010\u3011`"         # what textstats.strip_cues looks at
# "NONE — jumps straight in", "无", "N/A": the model saying there is nothing, not naming a phrase.
# Only a habit or a ban is read this way. A quote never is: a line that starts like this is still a line.
_NOTHING = re.compile(r"(?:none|n/a|null|\u65e0|\u6682\u65e0)\s*(?:$|[\u2014\u2013\-:\uff1a,\uff0c;\uff1b.\u3002(\uff08])", re.I)


class _Entry(NamedTuple):
    path: str
    group: str
    kind: str
    where: str
    text: str
    container: List[Any]      # the list to remove from when this entry is dropped
    index: int                # position in that list


class _Sample:
    """One item of the creator's material, read the ways the checks need.

    ``canon`` and ``tails`` are what was said aloud (cues left out). The other readings cost as much
    again, so each is made the first time something asks for it:

    - ``whole()``: the canonical form of everything written, cue brackets included;
    - ``written()`` and ``said()``: the text as the linter folds a line, with and without cue brackets.
    """

    __slots__ = ("title", "sponsored", "text", "canon", "tails", "_whole", "_written", "_said")

    def __init__(self, title: str, sponsored: bool, text: str) -> None:
        self.title, self.sponsored, self.text = title, sponsored, text
        self.canon = _canon(textstats.strip_cues(text))
        self.tails = _breath_tails(text)
        self._whole: Optional[str] = None
        self._written: Optional[Tuple[str, List[int]]] = None
        self._said: Optional[Tuple[str, List[int]]] = None

    def whole(self) -> str:
        if self._whole is None:
            self._whole = _canon(self.text)
        return self._whole

    def written(self) -> Tuple[str, List[int]]:
        if self._written is None:
            self._written = guardrails.fold(self.text)
        return self._written

    def said(self) -> Tuple[str, List[int]]:
        if self._said is None:
            self._said = guardrails.fold(self.text, guardrails._spoken_positions(self.text))
        return self._said


# ---- reading the profile ----------------------------------------------------
def _dig(node: Any, dotted: str) -> Any:
    for key in dotted.split("."):
        if not isinstance(node, dict):
            return None
        node = node.get(key)
    return node


def _entries(dna: Any) -> Iterator[_Entry]:
    """Every checkable entry of a profile. Anything missing or of the wrong type is skipped."""
    for list_path, field, kind, where in _CHECKS:
        if list_path.endswith(".*"):
            group = list_path[:-2]
            parent = _dig(dna, group)
            lists = [("%s.%s" % (group, key), value) for key, value in parent.items()] if isinstance(parent, dict) else []
        else:
            group = list_path
            lists = [(list_path, _dig(dna, list_path))]
        for path, values in lists:
            if not isinstance(values, list):
                continue
            for index, value in enumerate(values):
                if field is None:
                    if isinstance(value, str) and value.strip():
                        yield _Entry("%s[%d]" % (path, index), group, kind, where, value.strip(), values, index)
                elif isinstance(value, dict):
                    text = value.get(field)
                    text = text.strip() if isinstance(text, str) else ""
                    if text or kind == QUOTE:      # an opener with no line quoted is still a claim to show
                        yield _Entry("%s[%d].%s" % (path, index, field), group, kind, where, text, values, index)


# ---- the shape of a profile ---------------------------------------------------
# Everything a voice profile may hold from a model, as in prompts/02 and the bundled example. A model's reply
# is cut down to this (``shape_profile``): a field that is not here is not stored, shown, enforced or sent on.
_TEXT, _TEXTS, _NUMBER = "text", "texts", "number"    # one piece of text; a list of them; a measurement
_ANY = "*"                                            # lists under any plain name (the praise lexicon is split by niche)
_SHAPE: Dict[str, Any] = {
    "persona_summary": {
        "one_line_essence_en": _TEXT,
        "one_line_essence_zh": _TEXT,
        "audience_address_terms": {"uses": _TEXTS, "never_uses": _TEXTS},
        "husband_and_family_references": {
            "spoken_nicknames_zh_en": _TEXTS, "couple_dynamic_tone": _TEXT, "verbatim_examples": _TEXTS},
        "regional_and_phonetic_nuances": {"accent_region_notes": _TEXT, "preserve_colloquialisms": _TEXTS},
    },
    "cadence_and_syntax": {
        "target_speaking_rate_cpm": _NUMBER,
        "ideal_clause_length_chars": {"min": _NUMBER, "median": _NUMBER, "hard_max": _NUMBER},
        "sentence_structure_habits": _TEXTS,
        "punctuation_and_pause_style": _TEXT,
    },
    "lexical_fingerprint": {
        "signature_openers": [{"pattern_zh": _TEXT, "when_used_en": _TEXT, "verbatim_sample": _TEXT}],
        "discourse_connectors": {"high_frequency": _TEXTS, "forbidden_bookish_connectors": _TEXTS},
        "modal_particles": {"primary": [{"particle": _TEXT, "placement_rule": _TEXT}], "rare_or_zero": _TEXTS},
        "genuine_praise_lexicon": {_ANY: _TEXTS},
    },
    "code_switching_grammar": {
        "overall_density": _TEXT,
        "natural_english_categories": _TEXTS,
        "never_code_switch_rules": _TEXTS,
        "good_vs_bad_examples": [{"authentic_her": _TEXT, "fake_chinglish_to_avoid": _TEXT}],
    },
    "beat_playbooks": {
        "vlog_hook_formulas": _TEXTS, "product_share_structure": _TEXTS,
        "sponsor_soft_pivot_formulas": _TEXTS, "closing_formulas": _TEXTS,
    },
    "onscreen_flower_text_and_caption_dna": {
        "flower_text_style": _TEXT, "title_formulas": _TEXTS, "caption_and_emoji_habits": _TEXT},
    "anti_voice_blacklist": {"banned_phrases_zh": _TEXTS, "learned_from_her_edits": _TEXTS},
}
MAX_TEXT_CHARS = 400        # one piece of text; longer is cut, so that all of it can be shown and read
MAX_ENTRIES = 40            # entries in one list
MAX_NAMED_LISTS = 8         # lists under names the model chose (_ANY)
_PLAIN_NAME = re.compile(r"[a-z][a-z0-9_]{0,39}")
# What the evidence check looks up, as paths without positions: these are not free text.
_CHECKED_FIELDS = frozenset(path if field is None else "%s.%s" % (path, field) for path, field, _, _ in _CHECKS)


class _FreeText(NamedTuple):
    path: str                 # where it sits, with positions: beat_playbooks.closing_formulas[0]
    field: str                # the same without positions: beat_playbooks.closing_formulas
    text: str
    belongs_to: Optional[str]     # the checked entry it describes (an opener's pattern belongs to its quote), or None
    container: Any            # the list or object that holds it
    key: Any                  # its position or key there


def _one_line(value: Any) -> Optional[str]:
    """Text from a model as it is stored: one line, nothing invisible in it, cut at MAX_TEXT_CHARS. None if not text.

    Zero-width characters, direction marks and blank fillers are taken out, so what is stored is what a page shows.
    """
    if not isinstance(value, str):
        return None
    seen = "".join(ch for ch in value
                   if ch.isspace() or (ch not in guardrails._BLANK and unicodedata.category(ch) not in ("Cf", "Cc")))
    return " ".join(seen.split())[:MAX_TEXT_CHARS].strip()


def _shaped(value: Any, shape: Any) -> Any:
    """``value`` cut down to ``shape``; None when it is not the kind of thing the shape asks for."""
    if shape == _TEXT:
        return _one_line(value)
    if shape == _NUMBER:
        return guardrails._number(value)
    if shape == _TEXTS:
        if not isinstance(value, list):
            return None
        texts = [text for text in (_one_line(entry) for entry in value) if text]
        return texts[:MAX_ENTRIES]
    if isinstance(shape, list):
        if not isinstance(value, list):
            return None
        objects = [entry for entry in (_shaped(entry, shape[0]) for entry in value) if entry]
        return objects[:MAX_ENTRIES]
    if not isinstance(value, dict):
        return None
    out: Dict[str, Any] = {}
    named = 0
    for key, inner in value.items():
        if not isinstance(key, str):
            continue
        sub = shape.get(key)
        if sub is None and _ANY in shape and key != _ANY and _PLAIN_NAME.fullmatch(key) and named < MAX_NAMED_LISTS:
            sub, named = shape[_ANY], named + 1
        kept = _shaped(inner, sub) if sub is not None else None
        if kept is not None:
            out[key] = kept
    return out


def shape_profile(dna: Any) -> Dict[str, Any]:
    """A model's profile cut down to the known shape (``_SHAPE``), as a new dict.

    Kept: the known fields that are present and of the right kind. Text becomes one line with nothing
    invisible in it, at most MAX_TEXT_CHARS long; a list keeps its text entries, at most MAX_ENTRIES.
    Everything else goes: unknown keys at any depth, values of the wrong kind, entries of a list
    that are not text (or, in a list of objects, not an object). Nothing is added, so a field the
    reply left out stays out.
    """
    shaped = _shaped(dna, _SHAPE)
    return shaped if isinstance(shaped, dict) else {}


def _free_text(dna: Any) -> Iterator[_FreeText]:
    """Every piece of text in a profile that the evidence check does not look up. Wrong types are skipped."""
    def walk(node: Any, shape: Any, path: str, field: str, belongs_to: Optional[str]) -> Iterator[_FreeText]:
        if not isinstance(node, dict):
            return
        for key, value in node.items():
            if not isinstance(key, str):
                continue
            sub = shape.get(key)
            if sub is None and _ANY in shape and key != _ANY:
                sub = shape[_ANY]
            here = "%s.%s" % (path, key) if path else key
            name = "%s.%s" % (field, key) if field else key
            checked = name in _CHECKED_FIELDS or ("%s.*" % field) in _CHECKED_FIELDS
            if sub == _TEXT:
                if not checked and isinstance(value, str) and value.strip():
                    yield _FreeText(here, name, value.strip(), belongs_to, node, key)
            elif sub == _TEXTS:
                for index, entry in enumerate(value if isinstance(value, list) else []):
                    if not checked and isinstance(entry, str) and entry.strip():
                        yield _FreeText("%s[%d]" % (here, index), name, entry.strip(), None, value, index)
            elif isinstance(sub, list):
                owner = next((f for p, f, _, _ in _CHECKS if p == name and f is not None), None)
                for index, entry in enumerate(value if isinstance(value, list) else []):
                    item = "%s[%d]" % (here, index)
                    for found in walk(entry, sub[0], item, name, "%s.%s" % (item, owner) if owner else None):
                        yield found
            elif isinstance(sub, dict):
                for found in walk(value, sub, here, name, None):
                    yield found
    return walk(dna, _SHAPE, "", "", None)


def unverified_fields(dna: Any) -> List[Dict[str, Any]]:
    """All the free text in a profile that nothing can be looked up for, in the order of the profile.

    These are a model's descriptions and advice: the one-line summary, the sentence habits, the
    playbooks, the notes beside a quote. They are sent to the drafting model with every prompt once the
    profile is accepted, so a page has to show every one before the creator accepts. Each entry is
    ``{"path", "field", "text", "belongs_to"}``:

    - ``path``: where it sits, with positions (``beat_playbooks.closing_formulas[0]``). ``apply_decisions``
      keeps the text only when this path is among its ``keep_paths``.
    - ``field``: the same without positions (``beat_playbooks.closing_formulas``), for a heading.
    - ``text``: the text itself, in full.
    - ``belongs_to``: the ``path`` of the report item it describes, or None. An opener's pattern and note
      belong to the opener's quote and go when that quote goes.

    Never raises on a malformed profile.
    """
    return [{"path": f.path, "field": f.field, "text": f.text, "belongs_to": f.belongs_to} for f in _free_text(dna)]


def _limits(dna: Any) -> Dict[str, Any]:
    """The two numbers in a profile that the checks enforce, and where each came from.

    ``{"clause_length": {"min", "median", "hard_max", "source"}, "speaking_rate": {"units_per_minute", "source"}}``.
    A value that is not a real number is None. ``source`` is what the profile's ``source_corpus_summary``
    records: for the clause length ``measured`` | ``ai_bounded`` | ``default``, for the rate
    ``typed`` | ``measured`` | ``default``; None when the profile does not say.
    """
    lengths = _dig(dna, "cadence_and_syntax.ideal_clause_length_chars")
    summary = _dig(dna, "source_corpus_summary")
    summary = summary if isinstance(summary, dict) else {}
    lengths = lengths if isinstance(lengths, dict) else {}
    length_source = summary.get("clause_lengths_source")
    if not isinstance(length_source, str):
        length_source = "measured" if summary.get("clause_lengths_measured") is True else None
    rate_source = summary.get("speaking_rate_source")
    return {
        "clause_length": dict({key: guardrails._number(lengths.get(key)) for key in ("min", "median", "hard_max")},
                              source=length_source),
        "speaking_rate": {"units_per_minute": guardrails._number(_dig(dna, "cadence_and_syntax.target_speaking_rate_cpm")),
                          "source": rate_source if isinstance(rate_source, str) else None},
    }


# ---- normalising text -------------------------------------------------------
def _is_cjk(ch: str) -> bool:
    return ord(ch) >= 0x2E80 and ch.isalnum()


def _fold(text: str) -> str:
    """Text with every character folded as the linter folds it (``guardrails.fold_char``)."""
    return "".join(guardrails.fold_char(ch) for ch in text)


def _canon(text: str) -> str:
    """Letters and digits, folded, with one separator mark wherever anything else stood."""
    out: List[str] = []
    pending = ""
    for raw in unicodedata.normalize("NFC", text):
        if raw in _STOP_CHARS:
            pending = _STOP
            continue
        for ch in guardrails.fold_char(raw):         # nothing at all for a character that draws nothing
            if ch.isalnum():
                if pending and out:
                    out.append(pending)
                pending = ""
                out.append(ch)
            else:
                level = _SOFT if ch.isspace() else _MID
                if _RANK[level] > _RANK[pending]:
                    pending = level
    return "".join(out)


def _breath_tails(text: str) -> Tuple[str, ...]:
    """The end of every breath, read exactly as corpus.voice_stats reads it."""
    tails = (clause.rstrip(_TAIL_PUNCT) for clause in textstats.split_clauses(text))
    return tuple(_fold(tail) for tail in tails if tail)


def _material(samples: Any) -> List[_Sample]:
    out: List[_Sample] = []
    for raw in samples if isinstance(samples, (list, tuple)) else []:
        if not isinstance(raw, dict):
            continue
        text = raw.get("text")
        if not isinstance(text, str) or not text.strip():
            continue
        title = raw.get("title")
        out.append(_Sample(title.strip() if isinstance(title, str) else "", bool(raw.get("is_sponsored")), text))
    return out


# ---- turning an entry into something to look for ----------------------------
def _without_note(text: str, kind: str, where: str = ANYWHERE) -> str:
    """The phrase a habit or a ban names, without the note a model may append in brackets.

    A quote never passes through here: it is looked for as written.

    A ban is read exactly as the linter reads the entry (everything before the first
    bracket; a cue bracket is text like any other), because the question for a ban is
    what the linter would flag. A habit, and a particle, lose cue brackets and a note
    that ends them, so a phrase is never shortened to its first half and then
    reported as found.
    """
    if kind == NEVER and where == ANYWHERE:
        return _NOTE_START.split(text.strip(), maxsplit=1)[0].strip()
    text = textstats.strip_cues(text).strip()
    if kind == NEVER:
        return _NOTE_START.split(text, maxsplit=1)[0].strip()
    while text and text[-1] in _CLOSING:
        depth = 0
        for pos in range(len(text) - 1, -1, -1):
            depth += (text[pos] in _CLOSING) - (text[pos] in _OPENING)
            if depth == 0:
                break
        else:
            break                                  # unbalanced bracket: not a note
        text = text[:pos].rstrip()
    return text


def _units(part: str) -> int:
    """Spoken units of a canonical phrase: one per CJK character, one per Latin word or number."""
    units, in_word = 0, False
    for ch in part:
        if ch in _SEPARATORS:
            in_word = False
        elif _is_cjk(ch):
            units, in_word = units + 1, False
        else:
            units, in_word = units + (not in_word), True
    return units


def _joint(before: str, after: str, pause_in_phrase: bool, loose: bool) -> str:
    """What may stand in the material between two neighbouring characters of the phrase."""
    if not _is_cjk(before) and not _is_cjk(after):
        if not pause_in_phrase:
            return ""                                                  # inside one word
        return _ANY_SEPARATOR if before.isdigit() and after.isdigit() else _ANY_SEPARATOR + "?"   # 3.5 is not 35
    if pause_in_phrase or loose:
        return _ANY_SEPARATOR + "?"
    return _SOFT + "?" if _is_cjk(before) != _is_cjk(after) else ""    # "去 Costco" and "去Costco" are the same


def _part_pattern(part: str) -> str:
    loose = _units(part) >= LOOSE_MIN_UNITS
    out: List[str] = []
    previous, pause = "", False
    for ch in part:
        if ch in _SEPARATORS:
            pause = True
            continue
        if previous:
            out.append(_joint(previous, ch, pause, loose))
        out.append(re.escape(ch))
        previous, pause = ch, False
    head = "" if _is_cjk(part[0]) else "(?<!%s)" % _LATIN              # "gym" is not found inside "gymnastics"
    tail = "" if _is_cjk(part[-1]) else "(?!%s)" % _LATIN
    return head + "".join(out) + tail


def _phrase_regex(phrase: str) -> Optional["re.Pattern[str]"]:
    phrase = phrase.replace("...", "\u2026").replace("XX", "\u2026")
    parts = [part for part in (_canon(piece) for piece in phrase.split("\u2026")) if part]
    if not parts:
        return None
    return re.compile(_GAP.join(_part_pattern(part) for part in parts))


def _count_endings(tails: Tuple[str, ...], particle: str) -> int:
    count = 0
    for tail in tails:
        if not tail.endswith(particle):
            continue
        before = tail[len(tail) - len(particle) - 1:len(tail) - len(particle)]
        if before.isalnum() and not _is_cjk(before) and not _is_cjk(particle[0]):
            continue                               # the end of a longer Latin word
        count += 1
    return count


def _quote_regex(quote: str) -> Optional["re.Pattern[str]"]:
    """A quote as one pattern: all of it, in order. Nothing stands in for a gap and nothing is left out."""
    canon = _canon(quote)
    return re.compile(_part_pattern(canon)) if canon else None


def _ban_matches(rx: "re.Pattern[str]", sample: _Sample) -> Tuple[int, List[str]]:
    """Where the linter would flag a ban in one item of the material: how often, and the text as written there.

    The item is read as the linter reads a line: everything written first, cue brackets included, then
    what is said aloud, for a phrase whose letters stand either side of a cue. A line break is a break
    between two neighbouring lines, which the linter reads across as well.
    """
    for folded, at in (sample.written(), sample.said()):
        spans = [(at[m.start()], at[m.end() - 1] + 1) for m in rx.finditer(folded)]
        if spans:
            shown = [" / ".join(piece.strip() for piece in sample.text[a:b].splitlines() if piece.strip()) for a, b in spans[:MAX_TITLES]]
            return len(spans), shown
    return 0, []


def _counter(kind: str, phrase: str, where: str) -> Optional[Callable[[_Sample], Tuple[int, List[str]]]]:
    """A function giving ``(how often, as written)`` for one sample, or None when there is nothing to look for.

    ``phrase`` is the whole entry for a quote or a ban, and the phrase without its note for a habit or a
    particle. ``as written`` is filled for a ban only.
    """
    if where == BREATH_END:
        particle = _fold(phrase).strip()
        if not any(ch.isalnum() for ch in particle):
            return None
        return lambda sample: (_count_endings(sample.tails, particle), [])
    if kind == NEVER:
        ban = guardrails._phrase_to_regex(phrase)      # the linter's own reading of the entry
        return None if ban is None else (lambda sample: _ban_matches(ban, sample))
    rx = _quote_regex(phrase) if kind == QUOTE else _phrase_regex(phrase)
    if rx is None:
        return None
    # A quote that itself contains a cue is also looked for in the text as written, cue brackets included.
    with_cue = kind == QUOTE and any(ch in _CUE_MARKS for ch in phrase)

    def count(sample: _Sample) -> Tuple[int, List[str]]:
        found = sum(1 for m in rx.finditer(sample.canon) if m.end() > m.start())
        if not found and with_cue:
            found = sum(1 for m in rx.finditer(sample.whole()) if m.end() > m.start())
        return found, []
    return count


def _reads_as_note(phrase: str) -> bool:
    """English prose such as "exact terms the creator uses for viewers": a description, not something they say."""
    words = len(textstats.english_words(phrase))
    return words >= 4 and sum(1 for ch in phrase if _is_cjk(ch)) < words


# ---- the report ---------------------------------------------------------------
_Hit = Tuple[_Sample, int, List[str]]       # an item of the material, how often the entry is in it, and as written there


def _titles(hits: List[_Hit]) -> List[str]:
    titles: List[str] = []
    for sample, _, _ in hits:
        if sample.title and sample.title not in titles:
            titles.append(sample.title)
            if len(titles) == MAX_TITLES:
                break
    return titles


def _decided(item: Dict[str, Any]) -> Dict[str, Any]:
    """Say what happens to the entry when the creator changes nothing, and whether a tick can change that.

    Kept without a tick: only what the material bears out (``found``, ``absent``). Never kept: a ban the
    material contradicts, and a quote that is not found. Everything else was not borne out and is kept
    only when the creator ticks it: a habit that was not found, and an entry that could not be checked.
    """
    status, kind = item["status"], item["kind"]
    borne_out = status in (FOUND, ABSENT)
    item["keep_by_default"] = borne_out
    item["can_keep"] = borne_out or (kind != QUOTE and status in (NOT_FOUND, NOT_CHECKABLE))
    return item


def _check(entry: _Entry, organic: List[_Sample], sponsored: List[_Sample],
           cache: Dict[Tuple[str, str], Any]) -> Dict[str, Any]:
    item: Dict[str, Any] = {
        "path": entry.path, "group": entry.group, "kind": entry.kind, "text": entry.text,
        "phrase": "", "searched_differs": False, "where": entry.where, "status": NOT_CHECKABLE,
        "count": 0, "scripts": 0, "titles": [], "written_as": [],
        "organic_count": 0, "sponsored_count": 0, "sponsored_only": False,
    }
    # Sponsored scripts carry brand wording, so they never turn a ban into a conflict.
    searched = organic if entry.kind == NEVER else organic + sponsored
    if not entry.text:
        item["status"] = NOT_FOUND                 # a pattern offered with no line quoted has no receipt
        return _decided(item)
    if entry.kind == QUOTE:
        phrase = entry.text                        # looked for whole, exactly as it is shown
    elif _NOTHING.match(entry.text):
        return _decided(item)
    else:
        phrase = _without_note(entry.text, entry.kind, entry.where)
        if entry.where == BREATH_END:
            phrase = phrase.rstrip(_TAIL_PUNCT)    # "啦～" is the particle 啦
    # A ban is handed to the linter's matcher as the whole entry: the linter decides what part of it counts.
    whole_ban = entry.kind == NEVER and entry.where == ANYWHERE
    key = (entry.where if entry.where == BREATH_END else entry.kind, entry.text if whole_ban else phrase)
    if key not in cache:
        count_in = _counter(entry.kind, key[1], entry.where)

        def hits(samples: List[_Sample]) -> List[_Hit]:
            counted = ((sample,) + count_in(sample) for sample in samples)      # type: ignore[misc]
            return [hit for hit in counted if hit[1]]
        cache[key] = None if count_in is None else (hits(organic), hits(sponsored))
    if cache[key] is None:
        if entry.kind == QUOTE and searched:
            item["status"] = NOT_FOUND             # nothing in it that anyone could have said
        return _decided(item)
    in_organic, in_sponsored = cache[key]
    item["phrase"] = phrase
    item["searched_differs"] = phrase != entry.text
    item["organic_count"] = sum(n for _, n, _ in in_organic)
    item["sponsored_count"] = sum(n for _, n, _ in in_sponsored)
    item["sponsored_only"] = bool(in_sponsored) and not in_organic

    deciding = in_organic if entry.kind == NEVER else in_organic + in_sponsored
    item["count"] = sum(n for _, n, _ in deciding)
    item["scripts"] = len(deciding)
    item["titles"] = _titles(deciding)
    for _, _, shown in deciding:
        item["written_as"] += [text for text in shown if text not in item["written_as"]]
    del item["written_as"][MAX_TITLES:]
    if not searched:
        return _decided(item)                      # nothing to look in: neither a match nor a miss
    if deciding:
        item["status"] = CONFLICT if entry.kind == NEVER else FOUND
    elif entry.kind == QUOTE or not _reads_as_note(phrase):    # English prose among habits or bans stays not_checkable
        item["status"] = ABSENT if entry.kind == NEVER else NOT_FOUND
    return _decided(item)


def _summary(items: List[Dict[str, Any]], organic: List[_Sample], sponsored: List[_Sample]) -> Dict[str, Any]:
    totals = {status: 0 for status in (FOUND, NOT_FOUND, ABSENT, CONFLICT, NOT_CHECKABLE)}
    by_kind = {kind: {status: 0 for status in statuses} for kind, statuses in _STATUSES_BY_KIND.items()}
    for item in items:
        totals[item["status"]] += 1
        by_kind[item["kind"]][item["status"]] += 1
    summary: Dict[str, Any] = {
        "samples": len(organic) + len(sponsored), "organic_samples": len(organic), "sponsored_samples": len(sponsored),
        "items": len(items), "checked": len(items) - totals[NOT_CHECKABLE],
    }
    summary.update(totals)
    summary["sponsored_only"] = sum(1 for item in items if item["status"] == FOUND and item["sponsored_only"])
    summary["needs_tick"] = sum(1 for item in items if item["can_keep"] and not item["keep_by_default"])
    summary["by_kind"] = by_kind
    return summary


def verify_profile(dna: Any, samples: Any) -> Dict[str, Any]:
    """Look up every phrase a proposed profile attributes to the creator in their own material.

    ``samples`` are dicts with ``title``, ``text`` and ``is_sponsored``. Returns
    ``{"items": [...], "summary": {...}, "unverified": [...], "limits": {...}}``. Each item has:

    - ``path``: where the entry sits, e.g. ``lexical_fingerprint.signature_openers[0].verbatim_sample``
    - ``group``: the list it belongs to (one of ``GROUPS``), ``kind``: quote | term | never
    - ``text``: the entry as the model wrote it. ``phrase``: what was looked for: the whole entry for a
      quote, the phrase without its note for a habit or a ban, ``""`` when nothing could be looked for.
      ``searched_differs``: true when ``phrase`` is not the entry as written, so a page must show both.
    - ``where``: ``anywhere``, or ``breath_end`` for sentence-final particles
    - ``status``: found | not_found for a quote or term, absent | conflict for a never claim,
      not_checkable for a description or when there is no material to look in. A quote is never
      not_checkable while there is material: it is found whole or it is not found.
    - ``count``, ``scripts``, ``titles``: occurrences that decide the status, how many scripts they are in,
      and up to three of those scripts' titles (non-sponsored first). For a never claim only
      non-sponsored material counts.
    - ``written_as``: for a never claim that is contradicted, up to three of the places as they are
      written in the material (``到家 / 人们`` for ``家人们``), since the linter reads across what
      separates the characters.
    - ``organic_count``, ``sponsored_count``, ``sponsored_only``: the split, and whether the phrase
      appears in sponsored scripts only
    - ``keep_by_default``, ``can_keep``: what ``apply_decisions`` does with the entry (see ``_decided``)

    ``unverified`` is ``unverified_fields(dna)``: all the free text in the profile that nothing was looked
    up for. ``limits`` is the two numbers the checks enforce and where each came from (``_limits``).

    Never raises on a malformed profile or sample list.
    """
    material = _material(samples)
    organic = [s for s in material if not s.sponsored]
    sponsored = [s for s in material if s.sponsored]
    cache: Dict[Tuple[str, str], Any] = {}
    items = [_check(entry, organic, sponsored, cache) for entry in _entries(dna)]
    unverified = unverified_fields(dna)
    summary = _summary(items, organic, sponsored)
    summary["unverified"] = len(unverified)
    return {"items": items, "summary": summary, "unverified": unverified, "limits": _limits(dna)}


def _paths(value: Any) -> set:
    return {p for p in value if isinstance(p, str)} if isinstance(value, (list, tuple, set, frozenset)) else set()


def apply_decisions(dna: Any, report: Any, rejected_paths: Any, drop_not_found: bool = True,
                    keep_paths: Any = None) -> Dict[str, Any]:
    """The profile after the creator's review, as a new dict; ``dna`` and ``report`` are left untouched.

    What the report bears out (``found``, ``absent``) stays unless its path is in ``rejected_paths``.

    Removed whatever the creator says: never claims the report marks as ``conflict`` (a ban on
    something they say would make the linter flag their own words), and quotes it does not mark
    ``found`` (a line presented as theirs is kept only with its receipt).

    Removed unless its path is in ``keep_paths``: everything else, which is all that the material did
    not bear out. That is a habit that was ``not_found``, any entry that was ``not_checkable``, and every
    piece of free text nothing was looked up for (``unverified_fields``: a list entry goes, a single
    field is emptied). Keeping one of those is the creator's decision alone, so it has to be made, not
    assumed. It also means a field a page does not show cannot take effect: only a path the page was
    given, and sent back, is kept.

    A path in ``rejected_paths`` is removed whatever else is said about it. An entry that is an object,
    such as an opener, goes as a whole, with the notes that belong to it.

    ``drop_not_found=False`` switches the "not borne out" rules off: nothing is removed for lack of a
    receipt or of a tick, only conflicts and what the creator rejected. No route of the app uses it.

    ``evidence_review`` on the result records ``checked`` and ``found`` (what the report established
    about this profile, before removals), ``removed_unverified`` (looked-up entries removed for lack of
    a receipt), ``rejected_by_creator`` (removed on their say-so alone), ``kept_unverified`` (looked-up
    entries kept on their say-so alone), ``free_text_kept`` and ``free_text_removed`` (pieces of free
    text, counted where they remain in the profile) and ``conflicts_dropped`` (the entries, one per
    removal). Each removed entry is counted once, in this order of precedence: conflict, nothing vouches
    for it (the report does not describe it, or it is a quote without a receipt), rejected, not ticked.
    Removed quotes are not recorded by text, since this dict is later handed to the drafting model.

    An entry the report says nothing about is treated as not borne out, and a tick cannot keep it: there
    is no item at its path, or the item there is for other text (a report made from another version of
    the profile). The same holds for free text that is not in the report's ``unverified`` list with the
    same text. So a stale or partial report can neither remove an entry on the strength of another
    entry's status nor let one through. ``approve_voice`` always passes a report computed from the very
    profile it applies it to. Unknown paths are ignored.
    """
    out: Dict[str, Any] = copy.deepcopy(dna) if isinstance(dna, dict) else {}
    raw_items = report.get("items") if isinstance(report, dict) else None
    reported: Dict[str, Dict[str, Any]] = {}
    for item in raw_items if isinstance(raw_items, list) else []:
        if isinstance(item, dict) and isinstance(item.get("path"), str):
            reported[item["path"]] = item
    raw_fields = report.get("unverified") if isinstance(report, dict) else None
    shown = {(f.get("path"), f.get("text")) for f in (raw_fields if isinstance(raw_fields, list) else [])
             if isinstance(f, dict) and isinstance(f.get("path"), str)}
    rejected, kept = _paths(rejected_paths), _paths(keep_paths)

    review: Dict[str, Any] = {"checked": 0, "found": 0, "removed_unverified": 0, "rejected_by_creator": 0,
                              "kept_unverified": 0, "free_text_kept": 0, "free_text_removed": 0, "conflicts_dropped": []}
    doomed: Dict[int, Tuple[List[Any], set]] = {}
    for entry in _entries(out):
        item = reported.get(entry.path)
        if item is not None and item.get("text") != entry.text:
            item = None                            # the report describes other text at this place, not this entry
        status = item.get("status") if item is not None else None
        if status in (FOUND, NOT_FOUND, ABSENT, CONFLICT):
            review["checked"] += 1
            review["found"] += 1 if status == FOUND else 0
        borne_out = status == (ABSENT if entry.kind == NEVER else FOUND)
        if entry.kind == NEVER and status == CONFLICT:
            review["conflicts_dropped"].append(entry.text)
        elif drop_not_found and (item is None or (entry.kind == QUOTE and not borne_out)):
            review["removed_unverified"] += 1      # nothing vouches for it: no report on it at all, or a quote with no receipt
        elif entry.path in rejected:
            review["rejected_by_creator"] += 1
        elif drop_not_found and not borne_out and entry.path not in kept:
            review["removed_unverified"] += 1
        else:
            review["kept_unverified"] += 0 if borne_out else 1
            continue
        doomed.setdefault(id(entry.container), (entry.container, set()))[1].add(entry.index)
    # Free text: nothing was looked up for it, so there is no status to weigh, only whether the creator kept it.
    # A note that belongs to an entry which is already going (an opener's pattern) goes with it and is not counted.
    going = {id(container[index]) for container, indexes in doomed.values() for index in indexes}
    for field in _free_text(out):
        if id(field.container) in going:
            continue
        if field.path in rejected:
            review["rejected_by_creator"] += 1
        elif drop_not_found and (field.path not in kept or (field.path, field.text) not in shown):
            review["free_text_removed"] += 1
        else:
            review["free_text_kept"] += 1
            continue
        if isinstance(field.container, list):
            doomed.setdefault(id(field.container), (field.container, set()))[1].add(field.key)
        else:
            field.container[field.key] = ""
    # Rebuild each list once, by original position, so several removals cannot shift one another.
    for container, indexes in doomed.values():
        container[:] = [value for index, value in enumerate(container) if index not in indexes]
    out["evidence_review"] = review
    return out
