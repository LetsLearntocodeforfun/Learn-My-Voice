"""Rule-based linter for Stage 4 / Stage 5A script JSON.

Runs the checks that do not need an LLM, so they are cheap, repeatable and can
gate a draft before and after the Stage 5A critic:

- clause length against ``ideal_clause_length_chars.hard_max``
- phrases banned by the creator's ``voice_dna.json``
- generic clichés, translationese and compliance terms from ``lexicon.json``
- audience address terms and English tokens not attested in the Voice DNA
- total spoken units against the duration budget

Usage::

    python3 -m studio.guardrails draft.json --dna schemas/voice_dna.example.json

Exit status is 1 when any ``error`` finding exists, otherwise 0.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
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


def _phrase_to_regex(phrase: str) -> Optional["re.Pattern[str]"]:
    """Turn a blacklist entry into a regex.

    ``…`` / ``...`` become a short in-clause gap and ``XX`` a placeholder, so
    entries like ``它不仅…而且…`` or ``哈喽大家好我是XX`` match real text.
    A trailing parenthetical note (``质地非常轻盈 (prefer: …)``) is ignored.
    """
    phrase = re.split(r"\s*[(\uff08]", phrase.strip(), maxsplit=1)[0]
    phrase = phrase.replace("...", "\u2026").replace("XX", "\u2026")
    parts = [re.escape(p) for p in phrase.split("\u2026") if p]
    if not parts:
        return None
    return re.compile(r"[^/\uff0c\u3002\uff01\uff1f]{0,12}".join(parts))


def _attested_strings(dna: Dict[str, Any]) -> List[str]:
    """Positive evidence of how she really speaks (never the ban lists)."""
    out: List[str] = []
    out += _get(dna, "persona_summary.audience_address_terms.uses", []) or []
    out += _get(dna, "persona_summary.husband_and_family_references.verbatim_examples", []) or []
    out += _get(dna, "lexical_fingerprint.discourse_connectors.high_frequency", []) or []
    out += [o.get("verbatim_sample", "") for o in _get(dna, "lexical_fingerprint.signature_openers", []) or []]
    out += _get(dna, "code_switching_grammar.natural_english_categories", []) or []
    out += [e.get("authentic_her", "") for e in _get(dna, "code_switching_grammar.good_vs_bad_examples", []) or []]
    return [s for s in out if isinstance(s, str)]


def _script_lines(script: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Accept Stage 4 output or Stage 5A output wrapping it."""
    if "script_lines" not in script and isinstance(script.get("final_polished_script_json"), dict):
        script = script["final_polished_script_json"]
    return list(script.get("script_lines") or [])


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

    hard_max = _get(dna, "cadence_and_syntax.ideal_clause_length_chars.hard_max")
    cpm = _get(dna, "cadence_and_syntax.target_speaking_rate_cpm")
    attested = _attested_strings(dna)
    attested_text = "\n".join(attested)
    attested_english = {w.lower() for s in attested for w in textstats.english_words(s)}
    attested_english |= {w.lower() for s in extra_allowed_english for w in textstats.english_words(s)}

    # (regex, category, severity, detail) rules checked against every voiced line.
    rules: List[Tuple["re.Pattern[str]", str, str, str]] = []

    def add_phrases(phrases: Iterable[str], category: str, severity: str, detail: str, skip_attested: bool = False) -> None:
        for phrase in phrases or []:
            if skip_attested and phrase in attested_text:
                continue
            rx = _phrase_to_regex(phrase)
            if rx is not None:
                rules.append((rx, category, severity, detail))

    add_phrases(_get(dna, "anti_voice_blacklist.banned_phrases_zh", []), GENERIC_XHS_SLOP, ERROR, "Voice DNA anti_voice_blacklist")
    add_phrases(_get(dna, "anti_voice_blacklist.learned_from_her_edits", []), GENERIC_XHS_SLOP, ERROR, "Voice DNA learned_from_her_edits")
    add_phrases(_get(dna, "persona_summary.audience_address_terms.never_uses", []), GENERIC_XHS_SLOP, ERROR, "Voice DNA never_uses")
    add_phrases(_get(dna, "lexical_fingerprint.discourse_connectors.forbidden_bookish_connectors", []), BOOKISH_MANDARIN, ERROR, "Voice DNA forbidden_bookish_connectors")
    add_phrases(lexicon.get("generic_slop", []), GENERIC_XHS_SLOP, ERROR, "Lexicon generic cliché, not attested in Voice DNA", skip_attested=True)
    add_phrases(lexicon.get("audience_address_terms", []), GENERIC_XHS_SLOP, REVIEW, "Audience address term not in Voice DNA 'uses'", skip_attested=True)
    for entry in lexicon.get("translationese", []):
        rules.append((re.compile(entry["pattern"]), TRANSLATIONESE, entry.get("severity", ERROR), "Lexicon translationese / bookish pattern"))
    for entry in lexicon.get("compliance", []):
        rules.append((re.compile(entry["pattern"]), COMPLIANCE_RISK, entry.get("severity", REVIEW), entry.get("reason_en", "Lexicon compliance term")))

    rare_particles = [p for p in _get(dna, "lexical_fingerprint.modal_particles.rare_or_zero", []) or [] if p]

    total_units = 0
    live_clip_sec = 0.0
    last_end_sec: Optional[int] = None

    for line in lines:
        line_no = line.get("line_no")
        time_range = textstats.parse_time_range(str(line.get("time_range_sec", "")))
        if time_range:
            last_end_sec = max(last_end_sec or 0, time_range[1])
        if _is_pure_live_clip(line):
            if time_range:
                live_clip_sec += time_range[1] - time_range[0]
            continue

        raw = str(line.get("spoken_script_zh", ""))
        voiced = textstats.strip_cues(raw)
        total_units += textstats.spoken_units(voiced)

        for clause in textstats.split_clauses(raw, punctuation_breaks=punctuation_breaks):
            units = textstats.spoken_units(clause)
            if isinstance(hard_max, (int, float)) and units > hard_max:
                findings.append(Finding(line_no, CADENCE_TOO_LONG, ERROR, clause, "%d units > hard_max %d" % (units, hard_max)))
            tail = clause.rstrip(_CLAUSE_END_PUNCT)
            for particle in rare_particles:
                if tail.endswith(particle):
                    findings.append(Finding(line_no, GENERIC_XHS_SLOP, REVIEW, particle, "Clause-final particle listed as rare_or_zero in Voice DNA"))

        hits: List[Tuple[int, int, Finding]] = []
        for rx, category, severity, detail in rules:
            for m in rx.finditer(voiced):
                if m.end() > m.start():
                    hits.append((m.start(), m.end(), Finding(line_no, category, severity, m.group(0), detail)))
        findings.extend(_dedupe_overlaps(hits))

        seen = set()
        for word in textstats.english_words(voiced):
            key = word.lower()
            if key not in attested_english and key not in seen:
                seen.add(key)
                findings.append(Finding(line_no, UNNATURAL_CODESWITCH, REVIEW, word, "English token not attested in Voice DNA code-switching evidence"))

    duration = target_duration_sec if target_duration_sec is not None else last_end_sec
    if duration and isinstance(cpm, (int, float)) and cpm > 0 and lines:
        budget = textstats.unit_budget(float(duration), float(cpm), live_clip_sec)
        if budget > 0 and abs(total_units - budget) > duration_tolerance * budget:
            findings.append(Finding(
                None, DURATION_OFF_BUDGET, ERROR, "%d units" % total_units,
                "budget %.0f for %.0fs at %.0f units/min (%+.0f%%, tolerance %.0f%%)" % (
                    budget, float(duration), float(cpm), (total_units - budget) / budget * 100, duration_tolerance * 100),
            ))
    return findings


def script_stats(script: Dict[str, Any], dna: Dict[str, Any], target_duration_sec: Optional[float] = None) -> Dict[str, Any]:
    """Totals for the UI summary bar, computed the same way as the duration check."""
    lines = _script_lines(script)
    cpm = _get(dna, "cadence_and_syntax.target_speaking_rate_cpm")
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
        units += textstats.spoken_units(textstats.strip_cues(str(line.get("spoken_script_zh", ""))))
    duration = target_duration_sec if target_duration_sec is not None else last_end
    has_rate = isinstance(cpm, (int, float)) and cpm > 0
    budget = textstats.unit_budget(float(duration), float(cpm), live_clip_sec) if (duration and has_rate) else None
    return {
        "units": units,
        "budget": round(budget) if budget is not None else None,
        "speaking_rate_cpm": cpm if has_rate else None,
        "estimated_sec": round(units / cpm * 60 + live_clip_sec) if has_rate else None,
        "hard_max": _get(dna, "cadence_and_syntax.ideal_clause_length_chars.hard_max"),
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
