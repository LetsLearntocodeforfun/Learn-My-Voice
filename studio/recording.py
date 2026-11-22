"""Voiceover recording alignment, subtitle cue matching, and speech-pause island timing.

Timing modes (always stated plainly wherever cues or exports are shown):

- ``whisper_aligned``: spoken clauses matched against timestamped segments from on-device Whisper transcription.
- ``subtitle_aligned``: spoken clauses matched against timestamped cues from an uploaded ``.srt`` or ``.vtt`` file.
- ``pause_aligned``: spoken clauses distributed across detected speech/pause islands of a 16 kHz PCM ``.wav``
  proportionally to spoken unit weight. This mode measures sound level and pauses only; it does NOT recognize words.

Uploaded media files are written only to temporary paths inside ``store.uploads_dir`` and deleted in ``finally``
blocks before ``ingest_take`` returns, so raw audio or video bytes are never retained on disk or stored in SQLite.
Standard library only.
"""
from __future__ import annotations

import array
import difflib
import math
import os
import struct
import sys
import tempfile
import unicodedata
import uuid
from pathlib import Path
from typing import Any, BinaryIO, Dict, List, Optional, Sequence, Tuple, Union

from . import soundstats, textstats, timing, transcribe
from .store import MAX_LINE_CHARS, Store, clean_file_name

MIN_CUE_SEC = 0.35
SILENT_FLOWER_SEC = 2.0
PAUSE_ISLAND_GAP_SEC = 0.25
MIN_ISLAND_SEC = 0.06
FRAME_SEC = 0.02        # 20 ms RMS frames (320 samples at 16 kHz)
TARGET_RATE = 16000
MAX_TAKE_BYTES = 40 * 1024 * 1024
MAX_TAKE_JSON_BYTES = 56 * 1024 * 1024

SUBTITLE_EXTENSIONS = ("srt", "vtt")
AUDIO_VIDEO_EXTENSIONS = ("wav", "m4a", "mp3", "mp4", "mov", "webm")
ALLOWED_TAKE_EXTENSIONS = SUBTITLE_EXTENSIONS + AUDIO_VIDEO_EXTENSIONS


class RecordingError(Exception):
    """A refused recording or subtitle take operation, with bilingual messages and HTTP status."""

    def __init__(self, code: str, message: str, message_zh: str = "", status: int = 400,
                 extra: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.message_zh = message_zh or message
        self.status = status
        self.extra = dict(extra or {})


CodedError = RecordingError


def _is_live(line: Dict[str, Any]) -> bool:
    return str(line.get("audio_mode", "")).strip().upper() == "LIVE_CLIP"


def _is_match_char(ch: str) -> bool:
    code = ord(ch)
    if 0x3400 <= code <= 0x4DBF or 0x4E00 <= code <= 0x9FFF:
        return True
    return ch.isalnum()


def _norm_with_spans(raw_text: str) -> Tuple[str, str, List[Tuple[int, int]]]:
    """Return ``(cleaned_text, norm_chars, [(start_in_cleaned, end_in_cleaned), ...])``."""
    cleaned = textstats.strip_cues(raw_text or "")
    norm_parts: List[str] = []
    spans: List[Tuple[int, int]] = []
    for idx, ch in enumerate(cleaned):
        folded = unicodedata.normalize("NFKC", ch).lower()
        for sub in folded:
            if _is_match_char(sub):
                norm_parts.append(sub)
                spans.append((idx, idx + 1))
    return cleaned, "".join(norm_parts), spans


def parse_subtitles(text: Union[str, bytes]) -> List[Tuple[float, float, str]]:
    """Parse standard ``.srt`` and WebVTT ``.vtt`` subtitle text into ``[(start_sec, end_sec, text), ...]``."""
    try:
        segments = timing.parse_subtitles(text)
    except ValueError:
        return []
    out: List[Tuple[float, float, str]] = []
    for seg in segments:
        start = float(seg["start"])
        end = max(start, float(seg["end"]))
        cue_text = str(seg.get("text") or "").strip()
        if cue_text:
            out.append((round(start, 3), round(end, 3), cue_text))
    return out


def _coerce_segments(segments: Sequence[Any]) -> List[Tuple[float, float, str]]:
    """Accept either ``(start, end, text)`` tuples or ``{"start", "end", "text"}`` dicts, sorted by start time."""
    out: List[Tuple[float, float, str]] = []
    for item in segments or ():
        if isinstance(item, (tuple, list)) and len(item) >= 3:
            raw_s, raw_e, raw_t = item[0], item[1], item[2]
        elif isinstance(item, dict):
            raw_s = item.get("start", item.get("start_sec"))
            raw_e = item.get("end", item.get("end_sec"))
            raw_t = item.get("text", "")
        else:
            continue
        if isinstance(raw_s, bool) or isinstance(raw_e, bool):
            continue
        if not isinstance(raw_s, (int, float)) or not isinstance(raw_e, (int, float)):
            continue
        s_val, e_val = float(raw_s), float(raw_e)
        if not (math.isfinite(s_val) and math.isfinite(e_val)):
            continue
        s_val = max(0.0, s_val)
        e_val = max(s_val, e_val)
        txt = " ".join(str(raw_t or "").splitlines()).strip()
        if txt:
            out.append((s_val, e_val, txt))
    out.sort(key=lambda r: (r[0], r[1]))
    return out


def _extract_clauses(lines: Sequence[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Extract ordered non-LIVE_CLIP spoken clauses and per-line metadata."""
    clauses: List[Dict[str, Any]] = []
    line_meta: List[Dict[str, Any]] = []
    norm_cursor = 0
    for idx, raw_line in enumerate(lines or ()):
        if not isinstance(raw_line, dict):
            continue
        line_no_raw = raw_line.get("line_no")
        line_no = int(line_no_raw) if isinstance(line_no_raw, int) and not isinstance(line_no_raw, bool) and line_no_raw >= 1 else (idx + 1)
        if _is_live(raw_line):
            continue
        spoken_raw = str(raw_line.get("spoken_script_zh") or "")
        line_clauses = textstats.split_clauses(spoken_raw, punctuation_breaks=False)
        if not line_clauses:
            continue
        line_start_norm = norm_cursor
        first_clause_pos = len(clauses)
        for c_idx, c_text in enumerate(line_clauses):
            _, c_norm, _ = _norm_with_spans(c_text)
            units = max(1, textstats.spoken_units(c_text))
            c_start = norm_cursor
            c_end = norm_cursor + len(c_norm)
            norm_cursor = c_end
            clauses.append({
                "line_no": line_no,
                "clause_idx": c_idx,
                "text": c_text,
                "units": units,
                "norm": c_norm,
                "norm_start": c_start,
                "norm_end": c_end,
            })
        scripted_clean = " / ".join(line_clauses)
        line_meta.append({
            "line_no": line_no,
            "scripted_zh": scripted_clean,
            "norm": "".join(c["norm"] for c in clauses[first_clause_pos:]),
            "norm_start": line_start_norm,
            "norm_end": norm_cursor,
            "clause_slice": (first_clause_pos, len(clauses)),
        })
    return clauses, line_meta


def _enforce_monotonic_cues(clauses: Sequence[Dict[str, Any]], raw_spans: Sequence[Tuple[float, float]]) -> List[Dict[str, Any]]:
    """Enforce monotonic non-overlapping ``(start_sec, end_sec)`` with ``MIN_CUE_SEC`` while keeping exact clause text."""
    cues: List[Dict[str, Any]] = []
    prev_end = 0.0
    for clause, (raw_s, raw_e) in zip(clauses, raw_spans):
        s_val = max(0.0, round(float(raw_s), 3), prev_end)
        e_val = max(round(float(raw_e), 3), round(s_val + MIN_CUE_SEC, 3))
        prev_end = e_val
        cues.append({
            "line_no": int(clause["line_no"]),
            "clause_idx": int(clause["clause_idx"]),
            "start_sec": s_val,
            "end_sec": e_val,
            "text": str(clause["text"]),
        })
    return cues


def build_aligned_flower_cues(
    lines: Sequence[Dict[str, Any]],
    clause_cues: Sequence[Dict[str, Any]],
    duration_sec: float = 0.0,
) -> Tuple[List[Dict[str, Any]], float]:
    """Build aligned ``onscreen_flower_text_zh`` cues spanning each line's aligned start..end (handling gaps cleanly)."""
    by_line: Dict[int, List[Dict[str, Any]]] = {}
    max_end = max(0.0, float(duration_sec or 0.0))
    for cue in clause_cues or ():
        if not isinstance(cue, dict):
            continue
        lno = int(cue.get("line_no") or 0)
        if lno >= 1:
            by_line.setdefault(lno, []).append(cue)
            max_end = max(max_end, float(cue.get("end_sec") or 0.0))

    indexed_lines: List[Tuple[int, Dict[str, Any]]] = []
    for idx, raw_line in enumerate(lines or ()):
        if not isinstance(raw_line, dict):
            continue
        lno_raw = raw_line.get("line_no")
        lno = int(lno_raw) if isinstance(lno_raw, int) and not isinstance(lno_raw, bool) and lno_raw >= 1 else (idx + 1)
        indexed_lines.append((lno, raw_line))

    # Precompute next voiced start for each position so unvoiced/LIVE_CLIP lines fit into real gaps.
    next_voiced_start: List[Optional[float]] = [None] * len(indexed_lines)
    upcoming: Optional[float] = None
    for pos in range(len(indexed_lines) - 1, -1, -1):
        lno, _ = indexed_lines[pos]
        if lno in by_line:
            upcoming = float(by_line[lno][0]["start_sec"])
        next_voiced_start[pos] = upcoming

    flowers: List[Dict[str, Any]] = []
    clock = 0.0
    pos = 0
    while pos < len(indexed_lines):
        lno, line = indexed_lines[pos]
        if lno in by_line:
            c_list = by_line[lno]
            l_start = round(float(c_list[0]["start_sec"]), 3)
            l_end = round(float(c_list[-1]["end_sec"]), 3)
            flower = str(line.get("onscreen_flower_text_zh") or "").strip()
            if flower:
                flowers.append({"line_no": lno, "start_sec": l_start, "end_sec": max(l_end, round(l_start + MIN_CUE_SEC, 3)), "text": flower})
            clock = max(clock, l_end)
            pos += 1
            continue

        # Contiguous run of lines without spoken clauses (LIVE_CLIP or silent flower-only lines).
        run_end = pos
        while run_end < len(indexed_lines) and indexed_lines[run_end][0] not in by_line:
            run_end += 1
        gap_target = next_voiced_start[pos]
        weights: List[float] = []
        for r_pos in range(pos, run_end):
            _, r_line = indexed_lines[r_pos]
            if _is_live(r_line):
                span = textstats.parse_time_range(str(r_line.get("time_range_sec") or ""))
                w = float(span[1] - span[0]) if span and span[1] > span[0] else 1.5
            elif str(r_line.get("onscreen_flower_text_zh") or "").strip():
                w = SILENT_FLOWER_SEC
            else:
                w = 0.0
            weights.append(w)
        total_w = sum(weights)
        if gap_target is not None and gap_target > clock and total_w > 0:
            avail = gap_target - clock
            cur_t = clock
            for offset, r_pos in enumerate(range(pos, run_end)):
                r_lno, r_line = indexed_lines[r_pos]
                w = weights[offset]
                dur = avail * (w / total_w) if w > 0 else 0.0
                n_t = cur_t + dur
                flower = str(r_line.get("onscreen_flower_text_zh") or "").strip()
                if flower:
                    f_s = round(cur_t, 3)
                    f_e = max(round(n_t, 3), round(f_s + MIN_CUE_SEC, 3))
                    flowers.append({"line_no": r_lno, "start_sec": f_s, "end_sec": f_e, "text": flower})
                    max_end = max(max_end, f_e)
                cur_t = n_t
            clock = max(clock, gap_target)
        else:
            cur_t = max(clock, max_end if gap_target is None else clock)
            for offset, r_pos in enumerate(range(pos, run_end)):
                r_lno, r_line = indexed_lines[r_pos]
                w = weights[offset]
                flower = str(r_line.get("onscreen_flower_text_zh") or "").strip()
                if flower:
                    dur = w if w > 0 else SILENT_FLOWER_SEC
                    f_s = round(cur_t, 3)
                    f_e = round(cur_t + max(dur, MIN_CUE_SEC), 3)
                    flowers.append({"line_no": r_lno, "start_sec": f_s, "end_sec": f_e, "text": flower})
                    cur_t = f_e
                    max_end = max(max_end, f_e)
                elif w > 0:
                    cur_t += w
            clock = max(clock, cur_t)
        pos = run_end

    return flowers, round(max_end, 3)


def _reconstruct_spoken_for_range(
    j_start: int,
    j_end: int,
    seg_cleaned: Sequence[str],
    spoken_seg_idx: Sequence[int],
    spoken_raw_spans: Sequence[Tuple[int, int]],
    seg_char_bounds: Sequence[Tuple[int, int]],
) -> str:
    """Slice exact spoken wording across segments for normalized spoken character span ``[j_start, j_end)``."""
    if j_end <= j_start:
        return ""
    by_seg: Dict[int, List[int]] = {}
    for j in range(j_start, j_end):
        s_idx = spoken_seg_idx[j]
        if s_idx not in by_seg:
            by_seg[s_idx] = [j, j]
        else:
            by_seg[s_idx][1] = j
    parts: List[str] = []
    for s_idx in sorted(by_seg):
        first_j, last_j = by_seg[s_idx]
        seg_first_j, seg_last_j = seg_char_bounds[s_idx]
        cleaned = seg_cleaned[s_idx]
        raw_lo = 0 if first_j == seg_first_j else spoken_raw_spans[first_j][0]
        raw_hi = len(cleaned) if last_j == seg_last_j - 1 else spoken_raw_spans[last_j][1]
        piece = cleaned[raw_lo:raw_hi].strip(" \t\r\n/，,。；;")
        if piece:
            parts.append(piece)
    return " / ".join(parts)


def align_timed_segments_to_script(
    lines: Sequence[Dict[str, Any]],
    segments: Sequence[Any],
    *,
    alignment_mode: str = "subtitle_aligned",
) -> Dict[str, Any]:
    """Align timestamped transcript/subtitle segments to scripted breath clauses via SequenceMatcher."""
    clean_segs = _coerce_segments(segments)
    clauses, line_meta = _extract_clauses(lines)
    if not clean_segs:
        flowers, total = build_aligned_flower_cues(lines, [], 0.0)
        return {
            "alignment_mode": alignment_mode,
            "duration_sec": total,
            "measured_cpm": 0.0,
            "cues": [],
            "flower_cues": flowers,
            "adlibs": [],
        }

    first_seg_start = clean_segs[0][0]
    last_seg_end = max(e for _, e, _ in clean_segs)

    # Build normalized spoken stream with per-character interpolated timestamps.
    spoken_norm_chars: List[str] = []
    spoken_starts: List[float] = []
    spoken_ends: List[float] = []
    spoken_seg_idx: List[int] = []
    spoken_raw_spans: List[Tuple[int, int]] = []
    seg_cleaned: List[str] = []
    seg_char_bounds: List[Tuple[int, int]] = []

    for s_idx, (seg_s, seg_e, seg_t) in enumerate(clean_segs):
        cleaned, s_norm, s_spans = _norm_with_spans(seg_t)
        seg_cleaned.append(cleaned)
        b_start = len(spoken_norm_chars)
        m_len = len(s_norm)
        dur = max(0.0, seg_e - seg_s)
        for k, ch in enumerate(s_norm):
            c_s = seg_s + dur * (k / float(m_len))
            c_e = seg_s + dur * ((k + 1) / float(m_len))
            spoken_norm_chars.append(ch)
            spoken_starts.append(c_s)
            spoken_ends.append(c_e)
            spoken_seg_idx.append(s_idx)
            spoken_raw_spans.append(s_spans[k])
        seg_char_bounds.append((b_start, len(spoken_norm_chars)))

    norm_spoken = "".join(spoken_norm_chars)
    norm_script = "".join(c["norm"] for c in clauses)
    n_script = len(norm_script)
    n_spoken = len(norm_spoken)

    seg_dicts = [{"start": s, "end": e, "text": t} for s, e, t in clean_segs]
    t_stats = timing.timing_stats(seg_dicts)
    spoken_sec = float(t_stats.get("spoken_sec") or 0.0)
    spoken_units = int(t_stats.get("units") or 0) or sum(int(c["units"]) for c in clauses)
    if spoken_sec <= 0.0 and last_seg_end > first_seg_start:
        spoken_sec = last_seg_end - first_seg_start
    measured_cpm = round(spoken_units * 60.0 / spoken_sec, 1) if spoken_sec >= 0.1 and spoken_units > 0 else 0.0

    if not clauses:
        flowers, total = build_aligned_flower_cues(lines, [], last_seg_end)
        return {
            "alignment_mode": alignment_mode,
            "duration_sec": max(round(last_seg_end, 3), total),
            "measured_cpm": measured_cpm,
            "cues": [],
            "flower_cues": flowers,
            "adlibs": [],
        }

    if n_script == 0 or n_spoken == 0:
        # Fallback proportional distribution across [first_seg_start, last_seg_end] when no alphanumeric/CJK chars exist.
        total_u = float(sum(c["units"] for c in clauses)) or 1.0
        span = max(last_seg_end - first_seg_start, len(clauses) * MIN_CUE_SEC)
        cur_t = first_seg_start
        raw_spans: List[Tuple[float, float]] = []
        for c in clauses:
            dur = span * (c["units"] / total_u)
            raw_spans.append((cur_t, cur_t + dur))
            cur_t += dur
        cues = _enforce_monotonic_cues(clauses, raw_spans)
        max_cue_end = max((c["end_sec"] for c in cues), default=0.0)
        flowers, total = build_aligned_flower_cues(lines, cues, max(last_seg_end, max_cue_end))
        return {
            "alignment_mode": alignment_mode,
            "duration_sec": total,
            "measured_cpm": measured_cpm,
            "cues": cues,
            "flower_cues": flowers,
            "adlibs": [],
        }

    matcher = difflib.SequenceMatcher(None, norm_script, norm_spoken, autojunk=False)
    opcodes = matcher.get_opcodes()

    script_char_times: List[Tuple[float, float]] = [(0.0, 0.0)] * n_script
    for tag, i1, i2, j1, j2 in opcodes:
        if tag == "equal":
            for k in range(i2 - i1):
                script_char_times[i1 + k] = (spoken_starts[j1 + k], spoken_ends[j1 + k])
        elif tag == "replace":
            m_len = i2 - i1
            t0 = spoken_starts[j1]
            t1 = spoken_ends[j2 - 1]
            span = max(0.0, t1 - t0)
            for k in range(m_len):
                script_char_times[i1 + k] = (t0 + span * (k / float(m_len)), t0 + span * ((k + 1) / float(m_len)))
        elif tag == "delete":
            m_len = i2 - i1
            if j1 == 0:
                t0 = t1 = first_seg_start
            elif j1 >= n_spoken:
                t0 = t1 = last_seg_end
            else:
                t0 = spoken_ends[j1 - 1]
                t1 = max(t0, spoken_starts[j1])
            span = max(0.0, t1 - t0)
            for k in range(m_len):
                script_char_times[i1 + k] = (t0 + span * (k / float(m_len)), t0 + span * ((k + 1) / float(m_len)))

    raw_clause_spans: List[Tuple[float, float]] = []
    for idx, c in enumerate(clauses):
        c_s, c_e = c["norm_start"], c["norm_end"]
        if c_e > c_s:
            raw_clause_spans.append((script_char_times[c_s][0], script_char_times[c_e - 1][1]))
        else:
            # Punctuation-only breath clause: place at neighbor boundary.
            if c_s > 0:
                anchor = script_char_times[c_s - 1][1]
            elif n_script > 0:
                anchor = script_char_times[0][0]
            else:
                anchor = first_seg_start
            raw_clause_spans.append((anchor, anchor + MIN_CUE_SEC))

    cues = _enforce_monotonic_cues(clauses, raw_clause_spans)

    # Detect per-line spoken ad-lib diffs when matched spoken text differs meaningfully from scripted wording.
    adlibs: List[Dict[str, Any]] = []
    # Map script boundary positions 0..n_script to spoken positions via opcodes so each line gets its spoken slice.
    for meta in line_meta:
        l_i1, l_i2 = meta["norm_start"], meta["norm_end"]
        if l_i2 <= l_i1:
            continue
        j_lo: Optional[int] = None
        j_hi: Optional[int] = None
        for tag, i1, i2, j1, j2 in opcodes:
            if tag in ("equal", "replace") and i2 > l_i1 and i1 < l_i2 and j2 > j1:
                ov_i1 = max(i1, l_i1)
                ov_i2 = min(i2, l_i2)
                if tag == "equal":
                    sub_j1 = j1 + (ov_i1 - i1)
                    sub_j2 = j1 + (ov_i2 - i1)
                else:
                    # Proportional slice of replaced spoken span when multiple lines share a replace block.
                    m_len = i2 - i1
                    n_len = j2 - j1
                    sub_j1 = j1 + int(round((ov_i1 - i1) * n_len / float(m_len)))
                    sub_j2 = j1 + int(round((ov_i2 - i1) * n_len / float(m_len)))
                if sub_j2 > sub_j1:
                    j_lo = sub_j1 if j_lo is None else min(j_lo, sub_j1)
                    j_hi = sub_j2 if j_hi is None else max(j_hi, sub_j2)
            elif tag == "insert" and j2 > j1:
                # Include inserted spoken words strictly inside this line's normalized character span.
                if l_i1 < i1 < l_i2:
                    j_lo = j1 if j_lo is None else min(j_lo, j1)
                    j_hi = j2 if j_hi is None else max(j_hi, j2)
                elif len(line_meta) == 1:
                    j_lo = j1 if j_lo is None else min(j_lo, j1)
                    j_hi = j2 if j_hi is None else max(j_hi, j2)
        if j_lo is None or j_hi is None or j_hi <= j_lo:
            continue
        spoken_norm_line = norm_spoken[j_lo:j_hi]
        script_norm_line = meta["norm"]
        if not spoken_norm_line or spoken_norm_line == script_norm_line:
            continue
        spoken_text = _reconstruct_spoken_for_range(
            j_lo, j_hi, seg_cleaned, spoken_seg_idx, spoken_raw_spans, seg_char_bounds
        )
        if not spoken_text:
            continue
        sim = round(difflib.SequenceMatcher(None, script_norm_line, spoken_norm_line, autojunk=False).ratio(), 3)
        adlibs.append({
            "line_no": int(meta["line_no"]),
            "scripted_zh": meta["scripted_zh"][:MAX_LINE_CHARS],
            "spoken_zh": spoken_text[:MAX_LINE_CHARS],
            "ratio": sim,
            "similarity": sim,
        })

    max_cue_end = max((c["end_sec"] for c in cues), default=0.0)
    flowers, total = build_aligned_flower_cues(lines, cues, max(last_seg_end, max_cue_end))
    return {
        "alignment_mode": alignment_mode,
        "duration_sec": total,
        "measured_cpm": measured_cpm,
        "cues": cues,
        "flower_cues": flowers,
        "adlibs": adlibs,
    }


def _percentile(sorted_vals: Sequence[float], pct: float) -> float:
    if not sorted_vals:
        return -120.0
    rank = max(1, int(-(-pct * len(sorted_vals) // 100)))
    return float(sorted_vals[min(rank, len(sorted_vals)) - 1])


def _read_pcm_wav_16k(path: Path) -> Tuple["array.array", float, bool]:
    """Read a PCM ``.wav`` file and return ``(mono_16k_int16_samples, duration_sec, already_16k_mono_16bit)``.

    Supports standard RIFF WAVE PCM as well as macOS ``afconvert`` extensible ``0xFFFE`` PCM headers,
    8/16/24/32-bit integer PCM, mono or multi-channel, and resamples in pure Python to 16 kHz mono 16-bit if needed.
    """
    try:
        with open(str(path), "rb") as handle:
            header = soundstats.wav_header(handle)
            if header is None or not header.get("pcm"):
                raise RecordingError(
                    "bad_wav",
                    "This .wav file is not uncompressed PCM audio. Export 16-bit PCM .wav, or use .srt / .vtt.",
                    "这个 .wav 文件不是未压缩的 PCM 音频。请导出 16-bit PCM .wav，或改用 .srt / .vtt 字幕。",
                )
            channels = int(header["channels"])
            rate = int(header["rate"])
            bits = int(header["bits"])
            if channels < 1 or channels > 8 or rate < 4000 or rate > 192000 or bits not in (8, 16, 24, 32):
                raise RecordingError(
                    "bad_wav",
                    "Unsupported WAV audio format (channels=%d, rate=%d, bits=%d)." % (channels, rate, bits),
                    "不支持的 WAV 音频参数（声道=%d，采样率=%d，位深=%d）。" % (channels, rate, bits),
                )
            data_at = int(header["data_at"])
            file_end = handle.seek(0, 2)
            avail_bytes = max(0, min(int(header["data_bytes"]), file_end - data_at))
            bytes_per_sample = bits // 8
            frame_bytes = channels * bytes_per_sample
            total_frames = avail_bytes // frame_bytes
            if total_frames <= 0:
                raise RecordingError("empty_audio", "This .wav file contains no audio samples.", "这个 .wav 文件里没有声音采样。")
            duration_sec = total_frames / float(rate)
            handle.seek(data_at)
            raw = handle.read(total_frames * frame_bytes)
    except RecordingError:
        raise
    except OSError:
        raise RecordingError("bad_wav", "Could not read the uploaded .wav file.", "无法读取上传的 .wav 文件。")

    if (channels, rate, bits) == (1, TARGET_RATE, 16):
        samples = array.array("h")
        samples.frombytes(raw)
        if sys.byteorder != "little":
            samples.byteswap()
        return samples, duration_sec, True

    # Pure-stdlib PCM decode to mono 16-bit at original rate, then resample to 16 kHz.
    mono_orig = array.array("h")
    if bits == 16:
        all_s = array.array("h")
        all_s.frombytes(raw)
        if sys.byteorder != "little":
            all_s.byteswap()
        if channels == 1:
            mono_orig = all_s
        else:
            mono_orig.extend(
                int(sum(all_s[at + c] for c in range(channels)) // channels)
                for at in range(0, len(all_s), channels)
            )
    elif bits == 8:
        for at in range(0, len(raw), channels):
            avg = sum((raw[at + c] - 128) * 256 for c in range(channels)) // channels
            mono_orig.append(max(-32768, min(32767, avg)))
    elif bits == 24:
        step = 3 * channels
        for at in range(0, len(raw), step):
            acc = 0
            for c in range(channels):
                pos = at + c * 3
                val = int.from_bytes(raw[pos:pos + 3], "little", signed=True)
                acc += val >> 8
            mono_orig.append(max(-32768, min(32767, acc // channels)))
    else:  # 32-bit signed PCM
        step = 4 * channels
        for at in range(0, len(raw), step):
            vals = struct.unpack_from("<%di" % channels, raw, at)
            mono_orig.append(max(-32768, min(32767, sum(v >> 16 for v in vals) // channels)))

    if rate == TARGET_RATE:
        return mono_orig, duration_sec, False

    out_len = max(1, int(round(len(mono_orig) * TARGET_RATE / float(rate))))
    resampled = array.array("h")
    ratio = len(mono_orig) / float(out_len)
    n_src = len(mono_orig)
    for i in range(out_len):
        src_pos = i * ratio
        idx = int(src_pos)
        if idx + 1 < n_src:
            frac = src_pos - idx
            val = int(round(mono_orig[idx] * (1.0 - frac) + mono_orig[idx + 1] * frac))
        else:
            val = mono_orig[min(idx, n_src - 1)]
        resampled.append(max(-32768, min(32767, val)))
    return resampled, duration_sec, False


def _write_pcm16_mono_wav(path: Path, samples: "array.array") -> None:
    raw = samples.tobytes() if sys.byteorder == "little" else _little_bytes(samples)
    head = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",
        36 + len(raw),
        b"WAVE",
        b"fmt ",
        16,
        1,
        1,
        TARGET_RATE,
        TARGET_RATE * 2,
        2,
        16,
        b"data",
        len(raw),
    )
    path.write_bytes(head + raw)


def _little_bytes(samples: "array.array") -> bytes:
    copy = array.array("h", samples)
    copy.byteswap()
    return copy.tobytes()


def _detect_speech_islands(samples: "array.array", duration_sec: float) -> List[Tuple[float, float]]:
    """Compute 20ms frame RMS energy, adaptive silence floor, and speech islands separated by >= 0.25s pauses."""
    frame_samples = int(round(FRAME_SEC * TARGET_RATE))     # 320 samples (20 ms)
    n_frames = len(samples) // frame_samples
    if n_frames <= 0:
        return [(0.0, max(duration_sec, MIN_CUE_SEC))] if duration_sec > 0 else []

    frame_db: List[float] = []
    for f_idx in range(n_frames):
        at = f_idx * frame_samples
        window = samples[at:at + frame_samples]
        energy = 0
        for s in window:
            energy += s * s
        if energy > 0:
            rms = math.sqrt(energy / float(frame_samples))
            db = 20.0 * math.log10(rms / 32768.0)
        else:
            db = -120.0
        frame_db.append(db)

    ordered = sorted(frame_db)
    p10 = _percentile(ordered, 10)
    p98 = _percentile(ordered, 98)
    if p98 <= -58.0:
        return []
    noise_gate = (p10 + 8.0) if (p98 - p10 >= 18.0) else -120.0
    threshold_db = max(-58.0, p98 - 28.0, noise_gate)
    voiced = [db > threshold_db for db in frame_db]

    # Bridge short silent dips (< 0.25s) inside speech so only pauses >= 0.25s split speech islands.
    gap_frames = int(math.ceil(PAUSE_ISLAND_GAP_SEC / FRAME_SEC - 1e-9))    # 13 frames = 0.26s (>= 0.25s)
    bridged = list(voiced)
    idx = 0
    while idx < n_frames:
        if bridged[idx]:
            idx += 1
            continue
        run_end = idx
        while run_end < n_frames and not bridged[run_end]:
            run_end += 1
        if idx > 0 and run_end < n_frames and (run_end - idx) < gap_frames:
            for k in range(idx, run_end):
                bridged[k] = True
        idx = run_end

    raw_islands: List[Tuple[float, float]] = []
    idx = 0
    while idx < n_frames:
        if not bridged[idx]:
            idx += 1
            continue
        run_end = idx
        while run_end < n_frames and bridged[run_end]:
            run_end += 1
        s_sec = idx * FRAME_SEC
        e_sec = min(duration_sec, run_end * FRAME_SEC)
        if e_sec - s_sec >= MIN_ISLAND_SEC:
            raw_islands.append((round(s_sec, 3), round(e_sec, 3)))
        idx = run_end

    return raw_islands


def _distribute_clauses_over_islands(
    clauses: Sequence[Dict[str, Any]],
    islands: Sequence[Tuple[float, float]],
    duration_sec: float,
) -> List[Tuple[float, float]]:
    """Distribute clauses across speech islands by spoken_units, snapping boundaries to real pauses."""
    m_clauses = len(clauses)
    if m_clauses == 0:
        return []
    total_units = float(sum(c["units"] for c in clauses)) or 1.0
    if not islands:
        span = max(duration_sec, m_clauses * MIN_CUE_SEC)
        cur = 0.0
        out: List[Tuple[float, float]] = []
        for c in clauses:
            dur = span * (c["units"] / total_units)
            out.append((cur, cur + dur))
            cur += dur
        return out

    k_islands = len(islands)
    island_durs = [max(0.001, e - s) for s, e in islands]
    voiced_total = sum(island_durs)

    if m_clauses == 1:
        return [(islands[0][0], islands[-1][1])]

    # Case 1: At least as many speech islands as clauses (k_islands >= m_clauses) — optimal monotonic DP
    # partitions islands into m_clauses contiguous groups so EVERY clause boundary snaps to an actual pause!
    if m_clauses <= k_islands <= 160:
        cum_voiced = [0.0]
        for d in island_durs:
            cum_voiced.append(cum_voiced[-1] + d)
        cum_units = [0.0]
        for c in clauses:
            cum_units.append(cum_units[-1] + c["units"])

        inf = 1e18
        # dp[m][k] = min squared error partitioning first k islands into m clauses
        dp = [[inf] * (k_islands + 1) for _ in range(m_clauses + 1)]
        parent = [[0] * (k_islands + 1) for _ in range(m_clauses + 1)]
        dp[0][0] = 0.0
        for m in range(1, m_clauses + 1):
            target_cum = voiced_total * (cum_units[m] / total_units)
            target_share = clauses[m - 1]["units"] / total_units
            # Leave enough islands for remaining clauses
            k_max = k_islands - (m_clauses - m)
            for k in range(m, k_max + 1):
                best_cost = inf
                best_prev = m - 1
                for prev_k in range(m - 1, k):
                    seg_share = (cum_voiced[k] - cum_voiced[prev_k]) / voiced_total
                    cum_err = ((cum_voiced[k] - target_cum) / voiced_total) ** 2
                    share_err = (seg_share - target_share) ** 2
                    cost = dp[m - 1][prev_k] + share_err + 0.5 * cum_err
                    if cost < best_cost:
                        best_cost = cost
                        best_prev = prev_k
                dp[m][k] = best_cost
                parent[m][k] = best_prev

        groups: List[Tuple[int, int]] = []
        cur_k = k_islands
        for m in range(m_clauses, 0, -1):
            prev_k = parent[m][cur_k]
            groups.append((prev_k, cur_k - 1))
            cur_k = prev_k
        groups.reverse()
        return [(islands[g0][0], islands[g1][1]) for g0, g1 in groups]

    # Case 2: More clauses than speech islands (or very many islands) — map cumulative voiced time and snap
    # boundaries near real pause edges so clause breaks align with pauses whenever available.
    cum_voiced = [0.0]
    for d in island_durs:
        cum_voiced.append(cum_voiced[-1] + d)
    avg_clause_v = voiced_total / float(m_clauses)
    snap_tol = min(0.45 * avg_clause_v, 0.45)

    # voiced_boundaries[m] in [0, voiced_total] for m = 0..m_clauses
    v_bounds = [0.0]
    acc_u = 0.0
    for m in range(1, m_clauses):
        acc_u += clauses[m - 1]["units"]
        raw_v = voiced_total * (acc_u / total_units)
        # Snap to nearest internal island boundary if within snap_tol and strictly monotonic.
        nearest = min(cum_voiced[1:-1], key=lambda bv: abs(bv - raw_v)) if k_islands > 1 else None
        if nearest is not None and abs(nearest - raw_v) <= snap_tol and nearest > v_bounds[-1]:
            v_bounds.append(nearest)
        else:
            v_bounds.append(max(v_bounds[-1], raw_v))
    v_bounds.append(voiced_total)

    def voiced_to_wall(v_pos: float, is_end: bool) -> float:
        if v_pos <= 0.0:
            return islands[0][0]
        if v_pos >= voiced_total:
            return islands[-1][1]
        for idx in range(k_islands):
            v0, v1 = cum_voiced[idx], cum_voiced[idx + 1]
            if abs(v_pos - v1) <= 1e-7:
                # At an exact pause boundary: an ending clause stops before the pause; a starting clause begins after!
                return islands[idx][1] if is_end else islands[min(idx + 1, k_islands - 1)][0]
            if v0 <= v_pos < v1:
                frac = (v_pos - v0) / max(1e-9, v1 - v0)
                s_i, e_i = islands[idx]
                return s_i + frac * (e_i - s_i)
        return islands[-1][1]

    spans: List[Tuple[float, float]] = []
    for m in range(m_clauses):
        w_s = voiced_to_wall(v_bounds[m], is_end=False)
        w_e = voiced_to_wall(v_bounds[m + 1], is_end=True)
        spans.append((w_s, max(w_s, w_e)))
    return spans


def align_wav_pauses_to_script(lines: Sequence[Dict[str, Any]], wav_path: Any) -> Dict[str, Any]:
    """Align non-LIVE_CLIP spoken clauses across detected speech/pause islands of a PCM ``.wav`` recording."""
    path = Path(str(wav_path))
    samples, duration_sec, is_exact_16k = _read_pcm_wav_16k(path)

    if is_exact_16k:
        measured_sound = soundstats.measure(path)
    else:
        with tempfile.TemporaryDirectory(prefix="lmv-wav16k-") as tmp_dir:
            norm_wav = Path(tmp_dir) / "norm16k.wav"
            _write_pcm16_mono_wav(norm_wav, samples)
            measured_sound = soundstats.measure(norm_wav)
    clean_sound = soundstats.clean(measured_sound) or (measured_sound if isinstance(measured_sound, dict) else {})

    islands = _detect_speech_islands(samples, duration_sec)
    clauses, _ = _extract_clauses(lines)
    raw_spans = _distribute_clauses_over_islands(clauses, islands, duration_sec)
    cues = _enforce_monotonic_cues(clauses, raw_spans)

    voiced_sec = sum(max(0.0, e - s) for s, e in islands)
    if voiced_sec <= 0.0:
        voiced_sec = duration_sec
    script_units = sum(textstats.spoken_units(c["text"]) for c in clauses)
    measured_cpm = round(script_units * 60.0 / voiced_sec, 1) if voiced_sec >= 0.1 and script_units > 0 else 0.0

    max_cue_end = max((c["end_sec"] for c in cues), default=0.0)
    flowers, total = build_aligned_flower_cues(lines, cues, max(round(duration_sec, 3), max_cue_end))
    return {
        "alignment_mode": "pause_aligned",
        "duration_sec": total,
        "measured_cpm": measured_cpm,
        "sound": clean_sound,
        "cues": cues,
        "flower_cues": flowers,
        "adlibs": [],
        "islands": [{"start_sec": s, "end_sec": e} for s, e in islands],
    }


def has_whisper(transcriber: Any = None) -> bool:
    """True when on-device Whisper transcription is supported, installed, and ready."""
    tr = transcriber if transcriber is not None else transcribe
    try:
        st = tr.status()
    except Exception:  # noqa: BLE001
        return False
    return bool(isinstance(st, dict) and st.get("supported") and st.get("installed") and st.get("model_ready"))


def has_afconvert() -> bool:
    """True when macOS sealed ``afconvert`` audio extraction is available."""
    if sys.platform != "darwin" or not os.path.exists(transcribe.AFCONVERT):
        return False
    return transcribe._seal_problem() is None


def capabilities(transcriber: Any = None) -> Dict[str, bool]:
    """Capabilities available on this computer for take ingestion."""
    return {
        "whisper": has_whisper(transcriber),
        "afconvert": has_afconvert(),
    }


def _unlink_quietly(path: Optional[Path]) -> None:
    if path is None:
        return
    try:
        path.unlink()
    except OSError:
        pass


def with_flower_cues(script: Dict[str, Any], take: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Attach deterministically computed ``flower_cues`` to a stored take dict."""
    if take is None:
        return None
    flowers, _ = build_aligned_flower_cues(script.get("lines", []), take.get("cues", []), float(take.get("duration_sec") or 0.0))
    return dict(take, flower_cues=flowers)


def ingest_take(
    store: Store,
    script_id: str,
    filename: str,
    raw_bytes: bytes,
    label: str = "",
    source_kind: str = "audio",
    *,
    transcriber: Any = None,
) -> Dict[str, Any]:
    """Ingest a subtitle (``.srt``/``.vtt``) or audio/video take, align it to ``script_id``, and save metadata only.

    Temporary media files are created inside ``store.uploads_dir`` and always removed in ``finally`` so raw bytes
    are never retained on disk or stored in ``studio.db``.
    """
    script = store.get_script(script_id)
    if script is None:
        raise RecordingError("not_found", "Script not found.", "找不到这篇脚本。", status=404)
    if not isinstance(raw_bytes, (bytes, bytearray)) or len(raw_bytes) == 0:
        raise RecordingError("empty_upload", "The uploaded file is empty.", "上传的文件是空的。", status=400)
    if len(raw_bytes) > MAX_TAKE_BYTES:
        mb = MAX_TAKE_BYTES // (1024 * 1024)
        raise RecordingError(
            "too_large",
            "Take file exceeds the %d MB limit." % mb,
            "录音或视频文件超过了 %d MB 的上限。" % mb,
            status=413,
            extra={"limit": MAX_TAKE_BYTES},
        )

    clean_name = clean_file_name(filename) or "take.wav"
    ext = clean_name.rsplit(".", 1)[-1].lower() if "." in clean_name else ""
    if ext not in ALLOWED_TAKE_EXTENSIONS:
        allowed = ", ".join("." + e for e in ALLOWED_TAKE_EXTENSIONS)
        raise RecordingError(
            "bad_media_type",
            "Unsupported file type. Choose one of: %s." % allowed,
            "不支持这个文件格式。请选择：%s。" % allowed,
            status=415,
        )

    store.uploads_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    temp_path = store.uploads_dir / ("%s.%s" % (uuid.uuid4().hex[:12], ext))
    extracted_wav: Optional[Path] = None
    try:
        fd = os.open(str(temp_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(bytes(raw_bytes))
        except BaseException:
            _unlink_quietly(temp_path)
            raise

        clean_label = " ".join(str(label or "").split()).strip() or clean_name
        if ext in SUBTITLE_EXTENSIONS:
            cues_raw = parse_subtitles(bytes(raw_bytes))
            if not cues_raw:
                raise RecordingError(
                    "bad_subtitles",
                    "No timed subtitle cues were found in this file. Check that it is a valid .srt or .vtt file.",
                    "这个文件里没有识别到带时间轴的字幕。请确认它是有效的 .srt 或 .vtt 文件。",
                    status=400,
                )
            aligned = align_timed_segments_to_script(script["lines"], cues_raw, alignment_mode="subtitle_aligned")
            src_kind = ext if source_kind not in ("srt", "vtt") else source_kind
            saved = store.save_script_take(
                script_id,
                label=clean_label,
                source_kind=src_kind,
                alignment_mode="subtitle_aligned",
                active=True,
                duration_sec=aligned["duration_sec"],
                measured_cpm=aligned["measured_cpm"],
                sound={},
                cues=aligned["cues"],
                adlibs=aligned["adlibs"],
            )
            return with_flower_cues(script, saved)  # type: ignore[return-value]

        # Audio / video take:
        src_kind = "mic" if source_kind == "mic" else "audio"
        tr = transcriber if transcriber is not None else transcribe
        whisper_aligned: Optional[Dict[str, Any]] = None
        measured_sound: Dict[str, Any] = {}

        # 1. Try Whisper word/segment alignment when available.
        if has_whisper(tr):
            try:
                tr_res = tr.transcribe_file(str(temp_path))
                if isinstance(tr_res, dict):
                    segs = tr_res.get("segments") if isinstance(tr_res.get("segments"), list) else []
                    measured_sound = soundstats.clean(tr_res.get("sound")) or {}
                    if segs and any(textstats.spoken_units(textstats.strip_cues(str(s.get("text") or ""))) > 0 for s in segs if isinstance(s, dict)):
                        whisper_aligned = align_timed_segments_to_script(
                            script["lines"], segs, alignment_mode="whisper_aligned"
                        )
                        dur_hint = tr_res.get("duration_sec")
                        if isinstance(dur_hint, (int, float)) and not isinstance(dur_hint, bool) and dur_hint > whisper_aligned["duration_sec"]:
                            whisper_aligned["duration_sec"] = round(float(dur_hint), 3)
            except Exception:  # noqa: BLE001 - fall back to WAV speech-pause alignment
                whisper_aligned = None

        if whisper_aligned is not None:
            if not measured_sound and ext == "wav":
                try:
                    wav_info = align_wav_pauses_to_script(script["lines"], temp_path)
                    measured_sound = wav_info.get("sound") or {}
                except RecordingError:
                    pass
            saved = store.save_script_take(
                script_id,
                label=clean_label,
                source_kind=src_kind,
                alignment_mode="whisper_aligned",
                active=True,
                duration_sec=whisper_aligned["duration_sec"],
                measured_cpm=whisper_aligned["measured_cpm"],
                sound=measured_sound,
                cues=whisper_aligned["cues"],
                adlibs=whisper_aligned["adlibs"],
            )
            return with_flower_cues(script, saved)  # type: ignore[return-value]

        # 2. Pure-stdlib 16 kHz PCM WAV speech-pause island alignment (directly for .wav, or via afconvert on macOS).
        if ext == "wav":
            try:
                wav_aligned = align_wav_pauses_to_script(script["lines"], temp_path)
            except RecordingError:
                if not has_afconvert():
                    raise
                extracted_wav = store.uploads_dir / ("%s.wav" % uuid.uuid4().hex[:12])
                try:
                    transcribe._extract_audio(temp_path.resolve(), extracted_wav, store.uploads_dir.resolve())
                except transcribe.TranscribeError:
                    raise RecordingError(
                        "recording_needs_wav_or_srt",
                        "This .wav encoding requires an external codec. Record directly in the browser mic booth, "
                        "export 16-bit PCM .wav, or attach .srt / .vtt subtitles (all work everywhere without extra tools).",
                        "这个 .wav 编码需要额外解码器。可以直接使用页面上的麦克风录音、导出 16-bit PCM .wav，或上传 .srt / .vtt 字幕（无需额外工具即可跨平台使用）。",
                        status=400,
                    )
                wav_aligned = align_wav_pauses_to_script(script["lines"], extracted_wav)
        elif has_afconvert():
            extracted_wav = store.uploads_dir / ("%s.wav" % uuid.uuid4().hex[:12])
            try:
                transcribe._extract_audio(temp_path.resolve(), extracted_wav, store.uploads_dir.resolve())
            except transcribe.TranscribeError:
                raise RecordingError(
                    "recording_needs_wav_or_srt",
                    "Could not extract audio from this .%s file on this computer. Record in the browser mic booth "
                    "(16 kHz .wav), upload a PCM .wav file, or attach .srt / .vtt subtitles — those work everywhere without extra tools." % ext,
                    "无法在这台电脑上读取这个 .%s 文件的音轨。请使用页面上的麦克风直接录音（16 kHz .wav）、上传 PCM .wav 音频，或附上 .srt / .vtt 字幕——这几种方式无需额外工具即可使用。" % ext,
                    status=400,
                )
            wav_aligned = align_wav_pauses_to_script(script["lines"], extracted_wav)
        else:
            raise RecordingError(
                "recording_needs_wav_or_srt",
                "Uploading .%s requires macOS afconvert or on-device Whisper. Use the browser mic recorder, "
                "a .wav file, or .srt / .vtt subtitles — they work on every system without extra tools." % ext,
                "直接解析 .%s 需要 macOS 的 afconvert 或本机 Whisper 转写。请改用页面麦克风录音、上传 .wav 音频，或上传 .srt / .vtt 字幕——它们在任何系统上都无需额外工具即可使用。" % ext,
                status=400,
            )

        saved = store.save_script_take(
            script_id,
            label=clean_label,
            source_kind=src_kind,
            alignment_mode="pause_aligned",
            active=True,
            duration_sec=wav_aligned["duration_sec"],
            measured_cpm=wav_aligned["measured_cpm"],
            sound=wav_aligned["sound"],
            cues=wav_aligned["cues"],
            adlibs=[],
        )
        return with_flower_cues(script, saved)  # type: ignore[return-value]
    finally:
        _unlink_quietly(temp_path)
        _unlink_quietly(extracted_wav)


def apply_adlib(store: Store, script_id: str, take_id: Any, line_no: Any) -> Dict[str, Any]:
    """Update ``line_no``'s ``spoken_script_zh`` in ``script_id`` to the spoken wording recorded on ``take_id``."""
    script = store.get_script(script_id)
    if script is None:
        raise RecordingError("not_found", "Script not found.", "找不到这篇脚本。", status=404)
    take = store.get_script_take(script_id, take_id)
    if take is None:
        raise RecordingError("not_found", "Take not found.", "找不到这条录音记录。", status=404)
    if isinstance(line_no, bool) or not isinstance(line_no, int) or line_no < 1:
        raise RecordingError("bad_line", "line_no must be a positive integer.", "line_no 必须是正整数。", status=400)

    matched = next((item for item in take.get("adlibs", []) if item.get("line_no") == line_no), None)
    if matched is None:
        raise RecordingError(
            "adlib_not_found",
            "No spoken wording difference is recorded for line %d on this take." % line_no,
            "这条录音在第 %d 句没有待应用的口播差异。" % line_no,
            status=404,
        )

    lines = [dict(line) for line in script.get("lines", [])]
    target_idx = next((idx for idx, ln in enumerate(lines) if int(ln.get("line_no") or (idx + 1)) == line_no), None)
    if target_idx is None:
        raise RecordingError("bad_line", "Line %d does not exist in this script." % line_no, "脚本里没有第 %d 句。" % line_no, status=400)

    lines[target_idx]["spoken_script_zh"] = str(matched["spoken_zh"])
    updated_script = store.update_script(script_id, {"lines": lines})
    remaining = [item for item in take.get("adlibs", []) if item.get("line_no") != line_no]
    updated_take = store.update_script_take_adlibs(script_id, take["id"], remaining)
    return {
        "script": updated_script,
        "take": with_flower_cues(updated_script or script, updated_take),
    }
