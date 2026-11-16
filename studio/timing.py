"""Subtitle files and speech timing measured from real timestamps.

A subtitle export (``.srt`` from 剪映 / CapCut auto-captions, ``.vtt``) or an
on-device transcript carries two kinds of evidence: the words as they were
written down, and when they were said. This module reads the first without
rewording it and measures the second, so speaking rate is counted, not assumed.
Auto-captions and transcripts are themselves a tool's hearing of the speech, so
the wording is only as faithful as the tool that produced the file.

A segment is ``{"start": float, "end": float, "text": str}`` with times in
seconds. Everything here is deterministic and standard library only.

What the numbers can and cannot see: timing is as fine as the cues. A pause
inside one cue is invisible, so ``pauses`` is a floor, and hand-timed
subtitles that linger on screen after the speaker stops will read as slower
speech than auto-captions of the same video.
"""
from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional, Union

from . import textstats

Segment = Dict[str, Any]

PAUSE_SEC = 0.3            # a gap this long between cues counts as a pause (prompts/01)
MIN_RATE_SEC = 10.0        # less speech than this in one video: no rate is reported
MIN_COMBINED_SEC = 60.0    # less speech than this across videos: no combined rate

# Hours are at most four digits. Unbounded, a "timestamp" whose hours field had 1,600,000 digits was accepted,
# and turning that field into a number held the server for 7 seconds.
_STAMP = r"(?:(\d{1,4}):)?(\d{1,2}):(\d{1,2})(?:[.,](\d{1,3}))?"
_TIMING = re.compile(r"^\s*%s\s*-->\s*%s(?:\s.*)?$" % (_STAMP, _STAMP))
_LINE_BREAK_TAG = re.compile(r"<br\s*/?>", re.I)
# <i>, </b>, <font color="..">, <c.yellow>, <v Name>, <00:00:01.500>, {\an8}
_TAG = re.compile(r"</?[A-Za-z][^<>]*>|<\d[\d:.]*>|\{\\[^{}]*\}")
_VTT_ENTITIES = (("&lt;", "<"), ("&gt;", ">"), ("&nbsp;", " "), ("&lrm;", ""), ("&rlm;", ""), ("&amp;", "&"))
_VTT_BLOCKS = ("NOTE", "STYLE", "REGION")
MAX_CUE_NUMBER_DIGITS = 9


def decode_text(data: bytes) -> str:
    """Decode an uploaded subtitle file: UTF-8, UTF-16 with a byte-order mark, or GB18030.

    Chinese subtitle tools on Windows still save GBK; reading that as UTF-8
    would turn the creator's words into garbage that then looks like evidence.
    """
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return data.decode("utf-16", "replace")
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", "replace")


def _millis(hours: Optional[str], minutes: str, seconds: str, fraction: Optional[str]) -> int:
    whole = (int(hours or 0) * 60 + int(minutes)) * 60 + int(seconds)
    return whole * 1000 + int((fraction or "0").ljust(3, "0"))


def _cue_number(text: str) -> Optional[int]:
    """The number on the line above a cue's timing, or None when ``text`` is not one.

    A cue number is a short run of digits. A longer run is left as text: it is not a cue number, and
    turning a line of a million digits into a number takes seconds.
    """
    return int(text) if len(text) <= MAX_CUE_NUMBER_DIGITS and text.isdecimal() else None


def _clean_line(line: str, is_vtt: bool) -> str:
    line = _TAG.sub("", _LINE_BREAK_TAG.sub(" ", line))
    if is_vtt:
        for entity, char in _VTT_ENTITIES:
            line = line.replace(entity, char)
    return line.strip()


def parse_subtitles(text: Union[str, bytes]) -> List[Segment]:
    """Cues of an ``.srt`` or WebVTT file, sorted by start time.

    Tolerates a byte-order mark, Windows line endings, ``,`` or ``.`` before
    the milliseconds, timestamps without hours, missing cue numbers, WebVTT
    headers / ``NOTE`` blocks / cue settings, and files with no blank lines
    between cues. Markup such as ``<i>`` or ``{\\an8}`` is removed; the wording
    is otherwise left exactly as written. A cue spread over several lines
    becomes one line joined by single spaces. Cues with no text are dropped.

    Raises ``ValueError("no_cues")`` when nothing in the text is a timed cue,
    which is how a caller tells a subtitle file from a plain pasted script.
    """
    if isinstance(text, (bytes, bytearray)):
        text = decode_text(bytes(text))
    lines = (text or "").lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    first = next((ln.strip() for ln in lines if ln.strip()), "")
    is_vtt = first.startswith("WEBVTT")

    cues: List[Dict[str, Any]] = []
    cue: Optional[Dict[str, Any]] = None    # the cue whose text is still being read
    loose: List[str] = []                   # lines of the current block that sit outside any cue
    skipping = False                        # inside a WebVTT header / NOTE / STYLE / REGION block

    def close_loose_block() -> None:
        # Text after a blank line with no timing of its own belongs to the cue above it
        # (some editors put blank lines inside a cue). A bare number is a stray cue number.
        if loose and cues and not all(ln.isdigit() for ln in loose):
            cues[-1]["lines"].extend(loose)
        del loose[:]

    for raw in lines:
        line = raw.strip()
        timing = _TIMING.match(raw)
        if timing:
            number = _cue_number(loose[-1]) if loose else None
            glued = _cue_number(cue["lines"][-1]) if (cue is not None and cue["lines"] and cue["number"] is not None) else None
            if glued is not None and glued > cue["number"]:
                cue["lines"].pop()
                number = glued                        # the next cue's number, glued on with no blank line
            del loose[:]                              # anything directly above a timing line is a cue identifier
            parts = timing.groups()
            start, end = _millis(*parts[:4]), _millis(*parts[4:])
            cue = {"start": start, "end": max(end, start), "lines": [], "number": number}
            cues.append(cue)
            skipping = False
        elif not line:
            cue = None
            skipping = False
            close_loose_block()
        elif skipping:
            continue
        elif cue is not None:
            cue["lines"].append(line)
        elif not loose and (line.startswith("WEBVTT") or line.split(None, 1)[0] in _VTT_BLOCKS):
            skipping = True
        else:
            loose.append(line)
    close_loose_block()

    segments: List[Segment] = []
    for item in cues:
        cleaned = " ".join(c for c in (_clean_line(ln, is_vtt) for ln in item["lines"]) if c)
        if cleaned:
            segments.append({"start": item["start"] / 1000.0, "end": item["end"] / 1000.0, "text": cleaned})
    if not segments:
        raise ValueError("no_cues")
    segments.sort(key=lambda s: (s["start"], s["end"]))
    return segments


def transcript_text(segments: Iterable[Segment]) -> str:
    """The words of a transcript, one cue per line, wording untouched."""
    out = []
    for seg in segments or []:
        line = " ".join(str(seg.get("text") or "").split("\n")).strip()
        if line:
            out.append(line)
    return "\n".join(out)


def _to_millis(value: Any) -> Optional[int]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(round(value * 1000))


def timing_stats(segments: Iterable[Segment], max_gap_sec: float = 1.2) -> Dict[str, Any]:
    """Speaking rate and pauses measured from cue timestamps.

    - ``cues``: cues that contain spoken words and have a real duration. Cues
      that are only a bracketed note such as ``[音乐]`` are not speech and are
      left out of every number here.
    - ``units``: spoken units in those cues (``textstats.spoken_units``: one
      Chinese character, one English word or one digit each count as 1).
    - ``spoken_sec``: time spent speaking. Cue durations, plus the gap between
      one cue and the next when it is at most ``max_gap_sec`` (a breath).
      Longer gaps are silence and are excluded. Overlapping cues count once.
    - ``span_sec``: first spoken cue start to last spoken cue end, silences included.
    - ``pauses``: gaps of at least 0.3 s between consecutive cues.
    - ``speaking_rate_cpm``: ``round(units / spoken minutes)``, or ``None``
      when there is under 10 seconds of speech to measure.
    """
    spans = []
    units = 0
    for seg in segments or []:
        if not isinstance(seg, dict):
            continue
        start, end = _to_millis(seg.get("start")), _to_millis(seg.get("end"))
        count = textstats.spoken_units(textstats.strip_cues(str(seg.get("text") or "")))
        if start is None or end is None or end <= start or count == 0:
            continue
        spans.append((start, end))
        units += count
    spans.sort()

    max_gap = int(round(max_gap_sec * 1000))
    pause = int(round(PAUSE_SEC * 1000))
    spoken = pauses = 0
    first = edge = None                     # edge: where speech has been accounted for up to
    for start, end in spans:
        if edge is None:
            first = edge = start
        gap = start - edge
        if gap >= pause:
            pauses += 1
        if 0 < gap <= max_gap:
            spoken += gap
        spoken += max(end - max(start, edge), 0)
        edge = max(edge, end)

    return {
        "cues": len(spans),
        "units": units,
        "spoken_sec": round(spoken / 1000.0, 3),
        "span_sec": round((edge - first) / 1000.0, 3) if spans else 0.0,
        "pauses": pauses,
        "speaking_rate_cpm": int(round(units * 60000.0 / spoken)) if spoken >= MIN_RATE_SEC * 1000 else None,
    }


def combined_rate(list_of_stats: Iterable[Optional[Dict[str, Any]]]) -> Optional[Dict[str, Any]]:
    """One speaking rate across several videos, pooled rather than averaged.

    Units and spoken time are summed over every video that has both, so a long
    video weighs more than a short one. Returns ``None`` while there is under
    60 seconds of measured speech in total: one short clip must not set the rate.
    """
    units = 0
    spoken = 0.0
    videos = 0
    for stats in list_of_stats or []:
        if not isinstance(stats, dict):
            continue
        u, t = stats.get("units"), stats.get("spoken_sec")
        if isinstance(u, bool) or isinstance(t, bool) or not isinstance(u, int) or not isinstance(t, (int, float)):
            continue
        if u <= 0 or t <= 0:
            continue
        units += u
        spoken += t
        videos += 1
    if spoken < MIN_COMBINED_SEC:
        return None
    return {"speaking_rate_cpm": int(round(units * 60.0 / spoken)), "spoken_sec": round(spoken, 3), "videos": videos}
