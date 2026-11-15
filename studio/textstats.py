"""Spoken-text statistics shared by ingestion metrics and script guardrails.

A "spoken unit" follows the definition in prompts/01 (``speaking_rate_cpm``):
one Chinese character, one English word, or one digit each count as 1.
Digits are an approximation (one syllable per digit).
"""
from __future__ import annotations

import re
from typing import List, Optional, Tuple

_CJK = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
_EN_WORD = re.compile(r"[A-Za-z][A-Za-z'\u2019\-]*")
_DIGIT = re.compile(r"\d")

# [轻笑] / [停顿0.5s] / [原声: "..."] and full-width variants are cues, not her voiceover.
# A cue runs from an opening bracket to the nearest closing one with no other opening bracket between. Stopping
# at an opening bracket matters: without it, text with many "[" and no "]" is searched to its end once for every
# "[", and one library item of 250,000 such characters made the library take 25 seconds to load while every
# other request waited.
_BRACKET_CUE = re.compile(r"\[[^\[\]]*\]|\uff3b[^\uff3b\uff3d]*\uff3d|\u3010[^\u3010\u3011]*\u3011")

# "/" is the explicit breath separator used by the prompts. Pause punctuation is
# treated as a breath break too unless punctuation_breaks=False.
_SLASH = re.compile(r"[/\n]")
_PAUSE_PUNCT = re.compile(r"[/\n\uff0c,\u3002\uff01!\uff1f?\uff1b;\uff5e~\u2026]")

_TIME_RANGE = re.compile(r"(\d{1,2}):(\d{2})\s*[-\u2013\u2014~]\s*(\d{1,2}):(\d{2})")


def strip_cues(text: str) -> str:
    """Remove bracketed delivery cues and inline live-clip quotes."""
    return _BRACKET_CUE.sub("", text or "").replace("`", "")


def english_words(text: str) -> List[str]:
    return _EN_WORD.findall(text or "")


def spoken_units(text: str) -> int:
    """Count spoken units in ``text`` (cues should be stripped by the caller)."""
    text = text or ""
    return len(_CJK.findall(text)) + len(_EN_WORD.findall(text)) + len(_DIGIT.findall(text))


def split_clauses(text: str, punctuation_breaks: bool = True) -> List[str]:
    """Split a spoken line into breath clauses, dropping empty pieces."""
    splitter = _PAUSE_PUNCT if punctuation_breaks else _SLASH
    return [c.strip() for c in splitter.split(strip_cues(text)) if c.strip()]


def parse_time_range(value: str) -> Optional[Tuple[int, int]]:
    """Parse ``"00:04-00:11"`` into ``(4, 11)`` seconds; None if unparseable."""
    m = _TIME_RANGE.search(value or "")
    if not m:
        return None
    start = int(m.group(1)) * 60 + int(m.group(2))
    end = int(m.group(3)) * 60 + int(m.group(4))
    return (start, end) if end >= start else None


def unit_budget(target_duration_sec: float, speaking_rate_cpm: float, live_clip_sec: float = 0.0) -> float:
    """Spoken-unit budget for a video, excluding time taken by live clips."""
    voiced_sec = max(target_duration_sec - live_clip_sec, 0.0)
    return voiced_sec / 60.0 * speaking_rate_cpm
