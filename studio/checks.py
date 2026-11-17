"""Guardrail results phrased for people, in Chinese and English."""
from __future__ import annotations

import re
import unicodedata
from typing import Any, Dict, List, Optional

from . import creator, guardrails, textstats, trends, voicematch, wordlist
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
    (guardrails.AI_TELL, "review"): ("「{m}」念出来可能像书面稿或机器写的，确认一下", "\"{m}\" may sound written or machine-made rather than spoken; please check"),
}
# ---- findings that come from the word list (studio/wordlist.py): first what was matched and why, then what it rests on ----
# The two COMPLIANCE_RISK messages above are only used for a finding whose entry cannot be found in the list.
# An advertising-rule finding. {why} is the entry's own ``why_zh`` / ``why_en``.
_RULE_LEAD = {
    "error": ("「{m}」：{why}，建议换个说法。", "\"{m}\": {why}. Better to say it another way."),
    "review": ("「{m}」：{why}，确认一下。", "\"{m}\": {why}; please check."),
}
# What an entry rests on: the text that was read, the place in it, and what that place says.
_BASIS = ("依据：{title}{where}：{says}", "Basis: {title}, {where}: {says}")
# The place does not name the wording that was matched: the entry is on the list by analogy with it.
_BY_ANALOGY = ("法条里没有点名这个说法，是比照列入的。", "The law does not name this wording; it is listed here by analogy.")
# An entry for which nothing was read. No text is cited for it.
_NO_BASIS = ("这一条没有核对过出处：它来自常见做法，没有对照过法规或平台规则。",
             "No source was checked for this entry: it rests on common practice and was not compared with a law or with platform rules.")
_REVIEWED = ("词表上次整理：{day}。", "The list was last reviewed on {day}.")
# A phrase the creator added to the word list themselves.
_OWN_LEAD = ("「{m}」在你自己加的词表里，确认一下。", "\"{m}\" is on {name}'s own word list; please check.")
_OWN_BASIS = ("这是你自己加的说法，不是自带词表里的条目。", "This phrase was added by {name}; it is not an entry of the shipped list.")
# A hint that a line may sound written. {source} is the name of the text the clue follows and {n} its number there.
_TELL_LEAD = ("「{m}」念出来可能像书面稿或机器写的，确认一下。", "\"{m}\" may sound written or machine-made rather than spoken; please check.")
_TELL_BASIS = ("这只是一条线索，不是规则：参照 {source} 的第 {n} 类写法。", "A clue, not a rule: after pattern {n} of {source}.")
_TELL_NO_SOURCE = ("这只是一条线索，不是规则：本项目自己加的，没有出处。", "A clue, not a rule: added by this project, with no source.")
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


def _text(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _short_title(title: Any) -> str:
    """The name a source's title begins with, without the note in brackets that follows it."""
    return re.split(r"\s*[(\uff08]", title, maxsplit=1)[0].strip() if isinstance(title, str) else ""


def _entries(lexicon: Dict[str, Any], group: str) -> List[Dict[str, Any]]:
    value = lexicon.get(group)
    return [entry for entry in value if isinstance(entry, dict)] if isinstance(value, list) else []


def _from_wordlist(finding: guardrails.Finding, lexicon: Any, who: Any) -> Optional[Dict[str, Any]]:
    """What a finding that comes from the word list says: ``{"lead", "basis", "entry_id", "own_phrase"}``, else None.

    ``lead`` and ``basis`` are ``(Chinese, English)``. ``lead`` is what was matched and why. ``basis`` is what
    the finding rests on, taken from the list and from nowhere else:

    - an advertising-rule entry with a basis: the text that was read, the article, what the article says, and
      whether the wording is named there or only listed by analogy; then the day the list was reviewed;
    - an advertising-rule entry without one: that no source was checked, and the day; nothing is cited;
    - a phrase of the creator's own list: that the creator added it;
    - a hint that a line may sound written: that it is a clue and not a rule, and what the clue follows.

    The entry is found through ``finding.detail`` (see ``guardrails.AI_TELL_DETAIL``). None when ``lexicon``
    is not given or holds no such entry; the finding is then phrased by its category alone.
    """
    if not isinstance(lexicon, dict) or finding.category not in (guardrails.COMPLIANCE_RISK, guardrails.AI_TELL):
        return None
    sources = lexicon.get("_sources") if isinstance(lexicon.get("_sources"), dict) else {}
    m = finding.matched
    if finding.category == guardrails.AI_TELL:
        entry = next((e for e in _entries(lexicon, "ai_tells") if guardrails.AI_TELL_DETAIL % e.get("id", "") == finding.detail), None)
        if entry is None:
            return None
        source = sources.get(entry.get("source")) if isinstance(entry.get("source"), str) else None
        if isinstance(source, dict) and entry.get("pattern_no") is not None:
            basis = (_TELL_BASIS[0].format(source=_short_title(source.get("title_zh")), n=entry["pattern_no"]),
                     _TELL_BASIS[1].format(source=_short_title(source.get("title_en")), n=entry["pattern_no"]))
        else:
            basis = _TELL_NO_SOURCE
        return {"lead": (_TELL_LEAD[0].format(m=m), _TELL_LEAD[1].format(m=m)), "basis": basis,
                "entry_id": entry.get("id"), "own_phrase": False}
    if finding.detail == guardrails.OWN_PHRASE_DETAIL:
        name = creator.english_name(who)
        return {"lead": (_OWN_LEAD[0].format(m=m), _OWN_LEAD[1].format(m=m, name=name)),
                "basis": (_OWN_BASIS[0], _OWN_BASIS[1].format(name=name)), "entry_id": None, "own_phrase": True}
    entry = next((e for e in _entries(lexicon, "compliance") if e.get("reason_en") == finding.detail), None)
    if entry is None or not (isinstance(entry.get("why_zh"), str) and isinstance(entry.get("why_en"), str)):
        return None
    lead_zh, lead_en = _RULE_LEAD.get(finding.severity, _RULE_LEAD["review"])
    rests_on = entry.get("basis")
    source = sources.get(rests_on.get("source")) if isinstance(rests_on, dict) and isinstance(rests_on.get("source"), str) else None
    if isinstance(rests_on, dict) and isinstance(source, dict):
        zh = _BASIS[0].format(title=_text(source.get("title_zh")), where=_text(rests_on.get("where_zh")), says=_text(rests_on.get("says_zh")))
        en = _BASIS[1].format(title=_text(source.get("title_en")), where=_text(rests_on.get("where_en")), says=_text(rests_on.get("says_en")))
        if rests_on.get("by_analogy"):
            zh, en = zh + _BY_ANALOGY[0], en + " " + _BY_ANALOGY[1]
    else:
        zh, en = _NO_BASIS
    day = lexicon.get("_reviewed_on")
    if isinstance(day, str) and day:
        zh, en = zh + _REVIEWED[0].format(day=day), en + " " + _REVIEWED[1].format(day=day)
    return {"lead": (lead_zh.format(m=m, why=entry["why_zh"]), lead_en.format(m=m, why=entry["why_en"])), "basis": (zh, en),
            "entry_id": entry.get("id"), "own_phrase": False}


def describe(finding: guardrails.Finding, stats: Dict[str, Any], who: Any = None, listed: bool = False,
             lexicon: Any = None) -> Dict[str, Any]:
    """One finding in both languages.

    ``who`` is the creator profile (None before setup). ``listed`` says the
    finding is a phrase from the creator's own never-say list. ``hidden_characters``
    in the answer is true when the flagged text holds a character that draws nothing.

    ``lexicon`` is the word list the finding was made with. A finding that comes from that list (an
    advertising-rule entry, a phrase the creator added to the list, a hint that a line may sound written)
    then also says what it rests on. Its message is two parts, which are given separately as well so that a
    page can set them apart: ``lead_zh`` / ``lead_en`` (what was matched and why) and ``basis_zh`` /
    ``basis_en`` (what the entry rests on, or that no source was checked, and the day the list was
    reviewed). ``entry_id`` names the entry of the shipped list (None for the creator's own phrase), and
    ``own_phrase`` is true for a phrase of the creator's own list. See ``_from_wordlist``.
    """
    hidden = any(_cannot_be_seen(ch) for ch in finding.matched)
    told = None
    if finding.category == guardrails.DURATION_OFF_BUDGET:
        over = stats["budget"] is not None and stats["units"] > stats["budget"]
        zh = "全篇 %s 字，这个时长大约念 %s 字，%s" % (stats["units"], stats["budget"], "偏长了" if over else "偏短了")
        en = "%s units against a budget of about %s: %s" % (stats["units"], stats["budget"], "too long" if over else "too short")
    else:
        told = None if listed else _from_wordlist(finding, lexicon, who)
        if told is not None:
            zh, en = told["lead"]
        else:
            zh, en = _NEVER_MESSAGE if listed else _MESSAGES.get((finding.category, finding.severity), ("「{m}」需要确认", "\"{m}\" needs a check"))
            n = textstats.spoken_units(finding.matched)
            zh, en = zh.format(m=finding.matched, n=n), en.format(m=finding.matched, n=n, name=creator.english_name(who))
        if hidden:
            zh, en = zh + _HIDDEN_NOTE[0], en + _HIDDEN_NOTE[1]
    out = {"line_no": finding.line_no, "category": finding.category, "severity": finding.severity,
           "matched": finding.matched, "message_zh": zh, "message_en": en, "hidden_characters": hidden}
    if told is not None:
        basis_zh, basis_en = told["basis"]
        out.update(message_zh=zh + basis_zh, message_en=en + " " + basis_en, lead_zh=zh, lead_en=en,
                   basis_zh=basis_zh, basis_en=basis_en, entry_id=told["entry_id"], own_phrase=told["own_phrase"])
    return out


# The most findings about lines that one check hands out. A real script has a few dozen at most. A line made to
# be flagged over and over (one flagged character 100,000 times) gave 200,000 of them, each saying in two
# languages what it rests on: a request of 300 KB was answered with 234 MB. Beyond this number the answer says
# how many were left out (``findings_left_out``), and the errors are the last to go.
MAX_FINDINGS = 500


def _within_limit(findings: List[guardrails.Finding]) -> List[guardrails.Finding]:
    """At most MAX_FINDINGS findings about lines, in the order given, and every finding about the script as a whole.

    When there are more, what must be fixed is kept before what asks for a second look, and that before hints
    that a line may sound written; within each, the earlier ones are kept.
    """
    about_lines = [at for at, finding in enumerate(findings) if finding.line_no is not None]
    if len(about_lines) <= MAX_FINDINGS:
        return findings
    about_lines.sort(key=lambda at: (findings[at].severity != guardrails.ERROR, findings[at].category == guardrails.AI_TELL, at))
    kept = set(about_lines[:MAX_FINDINGS])
    return [finding for at, finding in enumerate(findings) if finding.line_no is None or at in kept]


def lint_payload(store: Store, lines: Any, target_sec: Optional[float], keywords: Any = None,
                 post: Any = None) -> Dict[str, Any]:
    """Stats, bilingual findings and target-keyword coverage for script lines, against the active voice profile.

    Phrases the creator refuses to say are checked as part of that profile, also
    while it is still the bundled example. They are merged in here, per check,
    and never stored in the voice profile.

    ``voice_match`` compares the lines with the creator's own material, not with the voice profile: four
    habits counted in code on both sides (``voicematch.score``). It needs none of the AI steps.

    The word list is the one the creator keeps (``wordlist.effective``): the shipped list without the entries
    they switched off, plus their own phrases. ``wordlist`` in the answer is what has to be said once wherever
    these findings are listed (``wordlist.notice``): the disclaimer, the day the list was reviewed, how many
    shipped entries are switched off, and whether what was stored could not be read.

    ``findings`` holds at most MAX_FINDINGS findings about lines (``_within_limit``); ``findings_left_out`` is how
    many more there were, 0 for any script a person writes.
    """
    stored, is_example = store.load_dna()
    who = store.load_creator()
    dna = creator.apply_never(stored, who)
    script = {"script_lines": lines if isinstance(lines, list) else []}
    stats = guardrails.script_stats(script, dna, target_sec)
    lexicon = wordlist.effective(store)
    findings = guardrails.lint_script(script, dna, target_duration_sec=target_sec, lexicon=lexicon)
    own = creator.never_hits(script, who)
    shown = _within_limit(findings)
    described = [describe(f, stats, who, f.category == guardrails.GENERIC_XHS_SLOP and (f.line_no, f.matched) in own, lexicon)
                 for f in shown]
    return {"stats": stats, "findings": described, "findings_left_out": len(findings) - len(shown), "dna_is_example": is_example,
            "keyword_coverage": trends.coverage(normalize_keywords(keywords), script["script_lines"], post),
            "voice_match": voicematch.score(script["script_lines"], voicematch.reference_for(store)),
            "wordlist": wordlist.notice(lexicon)}
