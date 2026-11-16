"""Rule-based linter for Stage 4 / Stage 5A script JSON.

Runs the checks that do not need an LLM, so they are cheap, repeatable and can
gate a draft before and after the Stage 5A critic:

- clause length against ``ideal_clause_length_chars.hard_max``
- phrases banned by the creator's ``voice_dna.json``
- generic clichés, translationese and compliance terms from ``lexicon.json``
- audience address terms and English tokens not attested in the Voice DNA
- total spoken units against the duration budget

How a phrase rule reads text. Before a phrase is looked for, the phrase and the
line are both folded (``fold``): full-width and other compatibility forms become
plain ones, letter case and accents are dropped, characters that draw nothing
(zero-width characters, soft hyphens, combining marks, blank fillers) are
removed, and Cyrillic or Greek letters drawn like Latin ones become those Latin
letters. A phrase from a ban list (the voice profile's lists, which is also where
the creator's own refused phrases arrive) is then matched on its letters and
digits alone: anything else standing between two of them in the line (a space, a
breath mark, punctuation, a bracket, a symbol) is ignored. Ban lists are checked
on every line whatever its ``audio_mode``, inside cue brackets, and across the
break between two neighbouring lines.

Folding only ever adds findings. What counts as the creator's own wording
(``_attested_strings``), which switches a built-in cliché rule off, is compared
as written.

Usage::

    python3 -m studio.guardrails draft.json --dna schemas/voice_dna.example.json

Exit status is 1 when any ``error`` finding exists, otherwise 0.
"""
from __future__ import annotations

import argparse
import bisect
import functools
import json
import math
import re
import sys
import unicodedata
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from . import textstats

LEXICON_PATH = Path(__file__).with_name("lexicon.json")

ERROR = "error"
REVIEW = "review"
_SEVERITY_RANK = {ERROR: 0, REVIEW: 1}

# Categories reuse the issue_category enum of prompts/05 (5A), plus DURATION_OFF_BUDGET.
CADENCE_TOO_LONG = "CADENCE_TOO_LONG"
GENERIC_XHS_SLOP = "GENERIC_XHS_SLOP"
BOOKISH_MANDARIN = "BOOKISH_MANDARIN"
TRANSLATIONESE = "TRANSLATIONESE"
UNNATURAL_CODESWITCH = "UNNATURAL_CODESWITCH"
COMPLIANCE_RISK = "COMPLIANCE_RISK"
DURATION_OFF_BUDGET = "DURATION_OFF_BUDGET"

_CLAUSE_END_PUNCT = "，,。！!？?；;～~… "

# Lists in a voice profile whose entries the linter reports as errors: (path in the profile, category, detail).
# Code that edits a profile's bans (pipeline.strip_refused) reads this, so it always works on what is enforced.
DNA_BAN_LISTS: Tuple[Tuple[str, str, str], ...] = (
    ("anti_voice_blacklist.banned_phrases_zh", GENERIC_XHS_SLOP, "Voice DNA anti_voice_blacklist"),
    ("anti_voice_blacklist.learned_from_her_edits", GENERIC_XHS_SLOP, "Voice DNA learned_from_her_edits"),
    ("persona_summary.audience_address_terms.never_uses", GENERIC_XHS_SLOP, "Voice DNA never_uses"),
    ("lexical_fingerprint.discourse_connectors.forbidden_bookish_connectors", BOOKISH_MANDARIN,
     "Voice DNA forbidden_bookish_connectors"),
)


@dataclass(frozen=True)
class Finding:
    line_no: Optional[int]
    category: str
    severity: str
    matched: str
    detail: str


def load_lexicon(path: Path = LEXICON_PATH) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _get(node: Any, path: str, default: Any = None) -> Any:
    for key in path.split("."):
        if not isinstance(node, dict) or key not in node:
            return default
        node = node[key]
    return node


def _phrase_parts(phrase: str) -> List[str]:
    """The literal pieces of a blacklist entry, in order; a short gap may stand between two of them.

    ``…`` / ``...`` become a short in-clause gap and ``XX`` a placeholder, so
    entries like ``它不仅…而且…`` or ``哈喽大家好我是XX`` match real text.
    A trailing parenthetical note (``质地非常轻盈 (prefer: …)``) is ignored.
    """
    phrase = re.split(r"\s*[(\uff08]", phrase.strip(), maxsplit=1)[0]
    phrase = phrase.replace("...", "\u2026").replace("XX", "\u2026")
    return [p for p in phrase.split("\u2026") if p]


# ---- folding: what a phrase rule reads ------------------------------------------
# What the gap in a ban entry ("…", "XX") may not cross. These five characters are kept as they are when text is folded.
_GAP_STOPS = "/\uff0c\u3002\uff01\uff1f"
_GAP = "[^%s]{0,12}" % _GAP_STOPS
_BETWEEN = r"[\W_]*"                               # between two letters of a ban entry: anything that is not a letter or digit
_UNSEEN_CATEGORIES = ("Mn", "Me", "Cf")            # combining marks and zero-width characters
# Characters that draw nothing but are classed as letters or symbols, so the categories above miss them:
# the Hangul fillers and the blank Braille pattern.
_BLANK = frozenset("\u115f\u1160\u3164\uffa0\u2800")
# Letters of other alphabets that are drawn like a Latin letter, and the Latin letter each is read as.
# This table is not complete. A look-alike that is missing here is not folded, and neither is a Chinese
# character written in another form (traditional for simplified) or a word that only sounds the same.
_LOOKALIKES = dict(zip(
    # Cyrillic capitals, then Cyrillic small letters
    "\u0410\u0412\u0415\u041a\u041c\u041d\u041e\u0420\u0421\u0422\u0423\u0425\u0405\u0406\u0408\u04ae\u04ba\u0500\u051a\u051c\u04c0"
    "\u0430\u0435\u043e\u0440\u0441\u0443\u0445\u0455\u0456\u0458\u04bb\u0501\u051b\u051d\u04cf\u04af\u043a\u043c\u0442\u043d\u0432\u0433\u043f"
    # Greek capitals, then Greek small letters
    "\u0391\u0392\u0395\u0396\u0397\u0399\u039a\u039c\u039d\u039f\u03a1\u03a4\u03a5\u03a7"
    "\u03b1\u03bf\u03c1\u03bd\u03b9\u03ba\u03c5\u03c7\u03b3\u03b5\u03c4\u03c9\u03b7\u03b2"
    # Latin letters of an unusual shape: dotless i, script a, script g
    "\u0131\u0251\u0261",
    "ABEKMHOPCTYXSIJYHDQWI"
    "aeopcyxsijhdqwlykmthbrn"
    "ABEZHIKMNOPTYX"
    "aopvikuxyetwnb"
    "iag"))


def draws_nothing(ch: str) -> bool:
    """Whether a character leaves no mark on the page: zero-width, a combining mark, a blank filler, a control code."""
    if ch in _BLANK:
        return True
    category = unicodedata.category(ch)
    return category in _UNSEEN_CATEGORIES or (category == "Cc" and ch not in "\t\n\r")


@functools.lru_cache(maxsize=8192)
def fold_char(ch: str) -> str:
    """One character as phrase rules compare it; ``""`` for a character that draws nothing.

    Width and other compatibility forms, letter case and accents are folded away, and a letter of
    another alphabet that is drawn like a Latin letter becomes that letter (``_LOOKALIKES``).
    """
    out: List[str] = []
    for piece in unicodedata.normalize("NFKD", ch):
        piece = _LOOKALIKES.get(piece, piece)
        for c in unicodedata.normalize("NFKD", piece.casefold()):
            if not draws_nothing(c):
                out.append(_LOOKALIKES.get(c, c))
    return "".join(out)


def fold(text: str, positions: Optional[Iterable[int]] = None) -> Tuple[str, List[int]]:
    """``text`` as phrase rules read it, and for every folded character the position in ``text`` it came from.

    ``positions`` limits the reading to those positions of ``text`` (in order); by default all of it is read.
    """
    chars: List[str] = []
    origin: List[int] = []
    for index in (range(len(text)) if positions is None else positions):
        ch = text[index]
        for c in (ch if ch in _GAP_STOPS else fold_char(ch)):
            chars.append(c)
            origin.append(index)
    return "".join(chars), origin


def _letters(part: str) -> str:
    """The letters and digits of one piece of a ban entry, folded: the only thing a ban is matched on."""
    return "".join(c for ch in part for c in fold_char(ch) if c.isalnum())


def _phrase_to_regex(phrase: str) -> Optional["re.Pattern[str]"]:
    """Turn a blacklist entry into a regex for folded text (see ``_phrase_parts`` for how an entry is read).

    The entry is matched on its letters and digits, folded (``fold_char``). Anything else may stand
    between two of them in the line, so ``宝子们`` is also found in ``宝子 们``, ``宝子/们`` and
    ``【宝子】们``, and ``yyds`` in ``YYDS`` and ``y.y.d.s``. None for an entry with no letter or digit.
    """
    pieces = [_letters(part) for part in _phrase_parts(phrase)]
    parts = [_BETWEEN.join(re.escape(c) for c in letters) for letters in pieces if letters]
    if not parts:
        return None
    return re.compile(_GAP.join(parts), re.IGNORECASE)


def _plain_regex(phrase: str) -> Optional["re.Pattern[str]"]:
    """A phrase of the built-in lists as a regex for folded text: its characters side by side.

    Folding still applies, so an invisible character or a full-width letter does not hide a cliché,
    but nothing visible is skipped: these lists hold two-character words that neighbouring breaths
    would otherwise spell by accident.
    """
    parts = ["".join(re.escape(c) for ch in part for c in (ch if ch in _GAP_STOPS else fold_char(ch)))
             for part in _phrase_parts(phrase)]
    parts = [part for part in parts if part]
    if not parts:
        return None
    return re.compile(_GAP.join(parts), re.IGNORECASE)


def ban_key(phrase: Any) -> str:
    """Comparison form of a blacklist entry: two entries with the same key ban exactly the same text.

    Built from the same reading the linter uses (``_phrase_to_regex``), so a note in brackets, a
    trailing ellipsis, letter case, width, invisible characters and anything that is not a letter or
    digit make no difference. ``""`` for an entry the linter could never match.
    """
    if not isinstance(phrase, str):
        return ""
    return "\u2026".join(letters for letters in (_letters(part) for part in _phrase_parts(phrase)) if letters)


def _attested_strings(dna: Dict[str, Any]) -> List[str]:
    """Positive evidence of how she really speaks (never the ban lists).

    These are the lists the evidence check looks up in the creator's material: quotes, which are kept
    only when found whole, and habits. A habit counts without the note a model may add after it in
    brackets, because only the phrase in front of the note is what was looked up.
    ``code_switching_grammar.natural_english_categories`` is not here: it is a description written by
    a model, checked against nothing, and must not switch a rule off.

    A profile is written by a model, so a list may be missing, not a list, or
    hold the wrong kind of entry; anything that is not usable is skipped.
    """
    def items(path: str) -> List[Any]:
        value = _get(dna, path, [])
        return list(value) if isinstance(value, (list, tuple)) else []

    def habits(path: str) -> List[Any]:
        return [re.split(r"\s*[(\uff08]", value, maxsplit=1)[0] if isinstance(value, str) else value for value in items(path)]

    out: List[Any] = []
    out += habits("persona_summary.audience_address_terms.uses")
    out += items("persona_summary.husband_and_family_references.verbatim_examples")
    out += habits("lexical_fingerprint.discourse_connectors.high_frequency")
    out += [o.get("verbatim_sample", "") for o in items("lexical_fingerprint.signature_openers") if isinstance(o, dict)]
    out += [e.get("authentic_her", "") for e in items("code_switching_grammar.good_vs_bad_examples") if isinstance(e, dict)]
    return [s for s in out if isinstance(s, str)]


def _script_lines(script: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Accept Stage 4 output or Stage 5A output wrapping it. Anything in the list that is not a line object is skipped."""
    if not isinstance(script, dict):
        return []
    if "script_lines" not in script and isinstance(script.get("final_polished_script_json"), dict):
        script = script["final_polished_script_json"]
    lines = script.get("script_lines")
    return [line for line in lines if isinstance(line, dict)] if isinstance(lines, (list, tuple)) else []


def _is_pure_live_clip(line: Dict[str, Any]) -> bool:
    return str(line.get("audio_mode", "")).strip().upper() == "LIVE_CLIP"


def _dedupe_overlaps(hits: List[Tuple[int, int, Finding]]) -> List[Finding]:
    """Keep the most severe / longest finding for each overlapping span."""
    hits.sort(key=lambda h: (_SEVERITY_RANK[h[2].severity], -(h[1] - h[0]), h[0]))
    kept: List[Tuple[int, int, Finding]] = []
    for start, end, finding in hits:
        if all(end <= ks or start >= ke for ks, ke, _ in kept):
            kept.append((start, end, finding))
    kept.sort(key=lambda h: h[0])
    return [f for _, _, f in kept]


_Rule = Tuple["re.Pattern[str]", str, str, str]     # (regex, category, severity, detail)
_HAS_LETTER = re.compile(r"[^\W_]")


def _number(value: Any) -> Optional[float]:
    """A measurement read from a profile, or None when it is not one: not a number, a bool, NaN, infinite, or not positive."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        return None
    return value


def _rounded(value: Optional[float]) -> Optional[int]:
    """``value`` to the nearest whole number; None when there is none or it is not finite."""
    return round(value) if value is not None and math.isfinite(value) else None


def _spoken_positions(raw: str) -> List[int]:
    """The positions of ``raw`` that are said aloud: exactly the characters ``textstats.strip_cues`` keeps."""
    keep: List[int] = []
    at = 0
    for cue in textstats._BRACKET_CUE.finditer(raw):
        keep.extend(range(at, cue.start()))
        at = cue.end()
    keep.extend(range(at, len(raw)))
    return [i for i in keep if raw[i] != "`"]


def _across_lines(bans: List[_Rule], before: Tuple[Any, str, List[int], str, List[int]],
                  after: Tuple[Any, str, List[int], str, List[int]]) -> List[Finding]:
    """Ban entries spelled across the break between two neighbouring lines: begun on one, finished on the next.

    Read aloud, the end of one line and the start of the next are one stretch of speech. ``before`` and
    ``after`` are ``(line_no, raw, spoken positions, folded spoken text, its positions)``. A finding is
    reported on the line where the phrase begins, and shows both pieces with ``/`` between them.
    """
    line_no, raw, spoken, said, said_at = before
    _, next_raw, next_spoken, next_said, next_at = after
    joined = said + "\n" + next_said
    edge = len(said)
    hits: List[Tuple[int, int, Finding]] = []
    for rx, category, severity, detail in bans:
        for m in rx.finditer(joined):
            if not m.start() < edge < m.end() - 1:
                continue
            head = "".join(raw[i] for i in spoken[bisect.bisect_left(spoken, said_at[m.start()]):])
            tail_end = next_at[m.end() - edge - 2] + 1
            tail = "".join(next_raw[i] for i in next_spoken[:bisect.bisect_left(next_spoken, tail_end)])
            hits.append((m.start(), m.end(), Finding(line_no, category, severity, "%s / %s" % (head.rstrip(), tail.lstrip()),
                                                     detail + ", across two lines")))
    return _dedupe_overlaps(hits)


def lint_script(
    script: Dict[str, Any],
    dna: Dict[str, Any],
    *,
    target_duration_sec: Optional[float] = None,
    lexicon: Optional[Dict[str, Any]] = None,
    punctuation_breaks: bool = True,
    duration_tolerance: float = 0.10,
    extra_allowed_english: Sequence[str] = (),
) -> List[Finding]:
    """Return all deterministic findings for ``script`` against ``dna``.

    ``punctuation_breaks`` treats commas and sentence punctuation as breath
    breaks in addition to ``/``. ``extra_allowed_english`` is for sponsor brand
    names that the Voice DNA cannot know about.
    """
    lexicon = lexicon if lexicon is not None else load_lexicon()
    lines = _script_lines(script)
    findings: List[Finding] = []

    hard_max = _number(_get(dna, "cadence_and_syntax.ideal_clause_length_chars.hard_max"))
    cpm = _number(_get(dna, "cadence_and_syntax.target_speaking_rate_cpm"))
    attested = _attested_strings(dna)
    attested_text = "\n".join(attested)
    attested_english = {w.lower() for s in attested for w in textstats.english_words(s)}
    attested_english |= {w.lower() for s in extra_allowed_english for w in textstats.english_words(s)}

    # Rules are (regex, category, severity, detail), in three groups that read a line differently:
    bans: List[_Rule] = []        # the profile's ban lists: folded, on every line, cue brackets included
    phrases: List[_Rule] = []     # the built-in phrase lists: folded, on what a voiced line says aloud
    patterns: List[_Rule] = []    # the built-in patterns: what a voiced line says aloud, as written
    # Compared as written apart from letter case. Folding is never used here: it must not make a cliché count as hers.
    attested_lower = attested_text.lower()

    def add_phrases(entries: Iterable[str], category: str, severity: str, detail: str, skip_attested: bool = False,
                    ban: bool = False) -> None:
        # A profile is written by a model: a list may arrive as one string or hold objects. Only strings in a list count.
        for phrase in entries if isinstance(entries, (list, tuple)) else []:
            if not isinstance(phrase, str):
                continue
            if skip_attested and phrase.lower() in attested_lower:    # matching ignores case, so this must too
                continue
            rx = _phrase_to_regex(phrase) if ban else _plain_regex(phrase)
            if rx is not None:
                (bans if ban else phrases).append((rx, category, severity, detail))

    for path, category, detail in DNA_BAN_LISTS:
        add_phrases(_get(dna, path, []), category, ERROR, detail, ban=True)
    add_phrases(lexicon.get("generic_slop", []), GENERIC_XHS_SLOP, ERROR, "Lexicon generic cliché, not attested in Voice DNA", skip_attested=True)
    add_phrases(lexicon.get("audience_address_terms", []), GENERIC_XHS_SLOP, REVIEW, "Audience address term not in Voice DNA 'uses'", skip_attested=True)
    for entry in lexicon.get("translationese", []):
        patterns.append((re.compile(entry["pattern"]), TRANSLATIONESE, entry.get("severity", ERROR), "Lexicon translationese / bookish pattern"))
    for entry in lexicon.get("compliance", []):
        patterns.append((re.compile(entry["pattern"]), COMPLIANCE_RISK, entry.get("severity", REVIEW), entry.get("reason_en", "Lexicon compliance term")))

    rare = _get(dna, "lexical_fingerprint.modal_particles.rare_or_zero", [])
    rare_particles = [p for p in (rare if isinstance(rare, (list, tuple)) else []) if isinstance(p, str) and p]

    total_units = 0
    live_clip_sec = 0.0
    last_end_sec: Optional[int] = None
    # The last line that said anything, for a ban spelled across the break between two lines.
    before: Optional[Tuple[Any, str, List[int], str, List[int]]] = None

    for line in lines:
        line_no = line.get("line_no")
        time_range = textstats.parse_time_range(str(line.get("time_range_sec", "")))
        if time_range:
            last_end_sec = max(last_end_sec or 0, time_range[1])

        value = line.get("spoken_script_zh", "")
        raw = "" if value is None else str(value)
        spoken = _spoken_positions(raw)                  # what is said aloud: cue brackets left out
        written, written_at = fold(raw)                  # everything on the line, cue brackets included
        said, said_at = fold(raw, spoken)

        def aloud(start: int, end: int) -> str:
            """The spoken characters of this line between two positions, as written."""
            return "".join(raw[i] for i in spoken[bisect.bisect_left(spoken, start):bisect.bisect_left(spoken, end)])

        # Ban lists apply to every line, whatever its audio_mode, and to what stands inside cue brackets.
        hits: List[Tuple[int, int, Finding]] = []
        for rx, category, severity, detail in bans:
            for m in rx.finditer(written):
                start, end = written_at[m.start()], written_at[m.end() - 1] + 1
                hits.append((start, end, Finding(line_no, category, severity, raw[start:end], detail)))
            for m in rx.finditer(said):                  # a phrase whose letters stand either side of a cue
                start, end = said_at[m.start()], said_at[m.end() - 1] + 1
                hits.append((start, end, Finding(line_no, category, severity, aloud(start, end), detail)))

        if before is not None and said:
            findings.extend(_across_lines(bans, before, (line_no, raw, spoken, said, said_at)))
        if _HAS_LETTER.search(said):
            before = (line_no, raw, spoken, said, said_at)

        if _is_pure_live_clip(line):
            if time_range:
                live_clip_sec += time_range[1] - time_range[0]
            findings.extend(_dedupe_overlaps(hits))      # somebody else's words: only the ban lists apply
            continue

        voiced = textstats.strip_cues(raw)
        total_units += textstats.spoken_units(voiced)

        for clause in textstats.split_clauses(raw, punctuation_breaks=punctuation_breaks):
            units = textstats.spoken_units(clause)
            if hard_max is not None and units > hard_max:
                findings.append(Finding(line_no, CADENCE_TOO_LONG, ERROR, clause, "%d units > hard_max %d" % (units, hard_max)))
            tail = clause.rstrip(_CLAUSE_END_PUNCT)
            for particle in rare_particles:
                if tail.endswith(particle):
                    findings.append(Finding(line_no, GENERIC_XHS_SLOP, REVIEW, particle, "Clause-final particle listed as rare_or_zero in Voice DNA"))

        for rx, category, severity, detail in phrases:
            for m in rx.finditer(said):
                if m.end() > m.start():
                    start, end = said_at[m.start()], said_at[m.end() - 1] + 1
                    hits.append((start, end, Finding(line_no, category, severity, aloud(start, end), detail)))
        # The built-in patterns were written for text as it is typed, so they read it unfolded. Characters
        # that draw nothing are still left out, so one of them cannot split a word a pattern looks for.
        seen_at = [i for i in spoken if not draws_nothing(raw[i])]
        visible = "".join(raw[i] for i in seen_at)
        for rx, category, severity, detail in patterns:
            for m in rx.finditer(visible):
                if m.end() > m.start():
                    start, end = seen_at[m.start()], seen_at[m.end() - 1] + 1
                    hits.append((start, end, Finding(line_no, category, severity, aloud(start, end), detail)))
        findings.extend(_dedupe_overlaps(hits))

        seen = set()
        for word in textstats.english_words(voiced):
            key = word.lower()
            if key not in attested_english and key not in seen:
                seen.add(key)
                findings.append(Finding(line_no, UNNATURAL_CODESWITCH, REVIEW, word, "English token not attested in Voice DNA code-switching evidence"))

    duration = _number(target_duration_sec if target_duration_sec is not None else last_end_sec)
    if duration is not None and cpm is not None and lines:
        budget = textstats.unit_budget(float(duration), float(cpm), live_clip_sec)
        if math.isfinite(budget) and budget > 0 and abs(total_units - budget) > duration_tolerance * budget:
            findings.append(Finding(
                None, DURATION_OFF_BUDGET, ERROR, "%d units" % total_units,
                "budget %.0f for %.0fs at %.0f units/min (%+.0f%%, tolerance %.0f%%)" % (
                    budget, float(duration), float(cpm), (total_units - budget) / budget * 100, duration_tolerance * 100),
            ))
    return findings


def script_stats(script: Dict[str, Any], dna: Dict[str, Any], target_duration_sec: Optional[float] = None) -> Dict[str, Any]:
    """Totals for the UI summary bar, computed the same way as the duration check.

    Every number in the answer is finite or None, whatever the profile holds, so the answer is always valid JSON.
    """
    lines = _script_lines(script)
    cpm = _number(_get(dna, "cadence_and_syntax.target_speaking_rate_cpm"))
    units = 0
    live_clip_sec = 0.0
    last_end: Optional[int] = None
    for line in lines:
        time_range = textstats.parse_time_range(str(line.get("time_range_sec", "")))
        if time_range:
            last_end = max(last_end or 0, time_range[1])
        if _is_pure_live_clip(line):
            if time_range:
                live_clip_sec += time_range[1] - time_range[0]
            continue
        value = line.get("spoken_script_zh", "")
        units += textstats.spoken_units(textstats.strip_cues("" if value is None else str(value)))
    duration = _number(target_duration_sec if target_duration_sec is not None else last_end)
    budget = textstats.unit_budget(float(duration), float(cpm), live_clip_sec) if (duration is not None and cpm is not None) else None
    return {
        "units": units,
        "budget": _rounded(budget),
        "speaking_rate_cpm": cpm,
        "estimated_sec": _rounded(units / cpm * 60 + live_clip_sec) if cpm is not None else None,
        "hard_max": _number(_get(dna, "cadence_and_syntax.ideal_clause_length_chars.hard_max")),
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Deterministic guardrail linter for RedNote script JSON.")
    parser.add_argument("script", type=Path, help="Stage 4 or Stage 5A script JSON")
    parser.add_argument("--dna", type=Path, required=True, help="voice_dna.json")
    parser.add_argument("--target-sec", type=float, default=None, help="Target duration; defaults to the last time_range end")
    parser.add_argument("--slash-only", action="store_true", help="Only '/' counts as a breath break")
    parser.add_argument("--allow-english", action="append", default=[], help="Extra allowed English (e.g. sponsor brand); repeatable")
    parser.add_argument("--json", action="store_true", help="Emit findings as JSON")
    args = parser.parse_args(argv)

    script = json.loads(args.script.read_text(encoding="utf-8"))
    dna = json.loads(args.dna.read_text(encoding="utf-8"))
    findings = lint_script(
        script, dna,
        target_duration_sec=args.target_sec,
        punctuation_breaks=not args.slash_only,
        extra_allowed_english=args.allow_english,
    )
    if args.json:
        print(json.dumps([asdict(f) for f in findings], ensure_ascii=False, indent=2))
    else:
        for f in findings:
            where = "L%s" % f.line_no if f.line_no is not None else "script"
            print("%-6s %-7s %-22s %s  (%s)" % (where, f.severity, f.category, f.matched, f.detail))
        errors = sum(1 for f in findings if f.severity == ERROR)
        print("%d error(s), %d to review" % (errors, len(findings) - errors))
    return 1 if any(f.severity == ERROR for f in findings) else 0


if __name__ == "__main__":
    sys.exit(main())
