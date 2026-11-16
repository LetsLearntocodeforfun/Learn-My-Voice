"""Guardrail results phrased for people, in Chinese and English."""
from __future__ import annotations

import unicodedata
from typing import Any, Dict, Optional

from . import creator, guardrails, textstats, trends
from .store import Store, normalize_keywords

# English messages name the creator: {name} is their name, or "the creator" before setup. Chinese speaks to them as 你.
_MESSAGES = {
    (guardrails.CADENCE_TOO_LONG, "error"): ("这一句有 {n} 个字，一口气念不完，建议断开", "This clause is {n} units; too long for one breath"),
    (guardrails.GENERIC_XHS_SLOP, "error"): ("「{m}」是营销号常用说法，不像你的口吻", "\"{m}\" is generic influencer wording, not {name}'s voice"),
    (guardrails.GENERIC_XHS_SLOP, "review"): ("「{m}」你平时很少这么说，确认一下", "\"{m}\" is not something {name} usually says; please check"),
    (guardrails.BOOKISH_MANDARIN, "error"): ("「{m}」太书面了，口语里不这么说", "\"{m}\" is written-style Chinese, not spoken"),
    (guardrails.TRANSLATIONESE, "error"): ("「{m}」有点翻译腔", "\"{m}\" reads like translated English"),
    (guardrails.TRANSLATIONESE, "review"): ("「{m}」偏书面，确认一下", "\"{m}\" may be too formal; please check"),
    (guardrails.UNNATURAL_CODESWITCH, "review"): ("英文词「{m}」你平时不常夹在中文里，确认一下", "English word \"{m}\" is not in {name}'s usual code-switching; please check"),
    (guardrails.COMPLIANCE_RISK, "error"): ("「{m}」可能违规（极限词或功效词），建议换个说法", "\"{m}\" may break ad rules (absolute or efficacy claim)"),
    (guardrails.COMPLIANCE_RISK, "review"): ("「{m}」可能被当成极限词，确认一下", "\"{m}\" may be read as an absolute claim; please check"),
}
# A phrase the creator typed into their own list of things they refuse to say.
_NEVER_MESSAGE = ("「{m}」是你自己列的不说的话", "\"{m}\" is on the list of phrases {name} refuses to say")
# Added when the flagged text holds a character that draws nothing (a zero-width character, a soft hyphen):
# on the page it reads like any other text, and the creator should know why it was written that way.
_HIDDEN_NOTE = ("（这里夹了看不见的字符）", " (written with a character in it that cannot be seen)")


def _cannot_be_seen(ch: str) -> bool:
    """A character that draws nothing where a letter could stand: zero-width, a soft hyphen, a direction mark, a blank filler.

    Narrower than ``guardrails.draws_nothing``, which also folds accents and an emoji's variation mark away:
    those are ordinary writing and need no remark.
    """
    return ch in guardrails._BLANK or (unicodedata.category(ch) in ("Cf", "Cc") and not ch.isspace())


def describe(finding: guardrails.Finding, stats: Dict[str, Any], who: Any = None, listed: bool = False) -> Dict[str, Any]:
    """One finding in both languages.

    ``who`` is the creator profile (None before setup). ``listed`` says the
    finding is a phrase from the creator's own never-say list. ``hidden_characters``
    in the answer is true when the flagged text holds a character that draws nothing.
    """
    hidden = any(_cannot_be_seen(ch) for ch in finding.matched)
    if finding.category == guardrails.DURATION_OFF_BUDGET:
        over = stats["budget"] is not None and stats["units"] > stats["budget"]
        zh = "全篇 %s 字，这个时长大约念 %s 字，%s" % (stats["units"], stats["budget"], "偏长了" if over else "偏短了")
        en = "%s units against a budget of about %s: %s" % (stats["units"], stats["budget"], "too long" if over else "too short")
    else:
        zh, en = _NEVER_MESSAGE if listed else _MESSAGES.get((finding.category, finding.severity), ("「{m}」需要确认", "\"{m}\" needs a check"))
        n = textstats.spoken_units(finding.matched)
        zh, en = zh.format(m=finding.matched, n=n), en.format(m=finding.matched, n=n, name=creator.english_name(who))
        if hidden:
            zh, en = zh + _HIDDEN_NOTE[0], en + _HIDDEN_NOTE[1]
    return {"line_no": finding.line_no, "category": finding.category, "severity": finding.severity,
            "matched": finding.matched, "message_zh": zh, "message_en": en, "hidden_characters": hidden}


def lint_payload(store: Store, lines: Any, target_sec: Optional[float], keywords: Any = None,
                 post: Any = None) -> Dict[str, Any]:
    """Stats, bilingual findings and target-keyword coverage for script lines, against the active voice profile.

    Phrases the creator refuses to say are checked as part of that profile, also
    while it is still the bundled example. They are merged in here, per check,
    and never stored in the voice profile.
    """
    stored, is_example = store.load_dna()
    who = store.load_creator()
    dna = creator.apply_never(stored, who)
    script = {"script_lines": lines if isinstance(lines, list) else []}
    stats = guardrails.script_stats(script, dna, target_sec)
    findings = guardrails.lint_script(script, dna, target_duration_sec=target_sec)
    own = creator.never_hits(script, who)
    described = [describe(f, stats, who, f.category == guardrails.GENERIC_XHS_SLOP and (f.line_no, f.matched) in own)
                 for f in findings]
    return {"stats": stats, "findings": described, "dna_is_example": is_example,
            "keyword_coverage": trends.coverage(normalize_keywords(keywords), script["script_lines"], post)}
