"""Full-service deterministic & acoustic Voice and Tone Analyzer.

Extends ``corpus.py`` and ``voicematch.py`` into a deep multi-dimensional analyzer (100% Python stdlib):
1. Multi-character modal particles & spoken tags (``啦/嘛/哈/诶`` + ``对吧/是吧/好吧/喔唷/来着/而已``).
2. Both clause-initial AND mid-clause colloquial connectors (``不过说实话``, ``最搞笑的是``, ``顺便提一嘴``, ``说白了``).
3. Sensory & tactile reduplication patterns (``软软糯糯``, ``水水的``, ``黏糊糊``, AABB / AA的 spoken texture).
4. Five-axis normalized (``0..100``) Tone & Register Profile:
   - ``warmth_intimacy``: conversational warmth, direct viewer connection, cozy particles
   - ``playful_humor``: dry humor, self-roasts, ``哈`` tags, laugh cues
   - ``restraint_authenticity``: trust-building caveats vs. exaggerated hype / exclamation spam
   - ``spoken_naturalness``: breath-length comfort, colloquial connectors vs. stiff written connectors
   - ``pacing_energy``: speaking-rate tempo, punchy short-clause ratio, pause density
5. Acoustic Prosody Extraction from 16-bit PCM WAV audio (``analyze_wav_prosody``):
   - RMS vocal energy, dynamic range (p95 - p10 dB), voiced ratio, pause frequency & mean pause duration,
     and 3-part vocal energy arc (opening hook vs. body vs. closing sign-off).
"""
from __future__ import annotations

import array
import io
import math
import re
import sys
import wave
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from . import corpus, pipeline, textstats, timing, voicematch
from .store import Store

SINGLE_PARTICLES = tuple("啦嘛哈喔呐诶呀呗啊呢吧哦哇")
MULTI_PARTICLES = ("对吧", "是吧", "好吧", "喔唷", "来着", "而已", "不是吗", "懂吧", "对不对", "好不好")
COLLOQUIAL_CONNECTORS = (
    "不过说实话", "最搞笑的是", "顺便提一嘴", "反正就是", "说白了", "老实讲", "有一说一",
    "说实话", "讲真", "其实", "结果", "本来", "后来", "然后", "不过", "反正", "而且",
    "关键是", "重点是", "主要是", "顺便", "干脆", "索性",
)
BOOKISH_CONNECTORS = (
    "因此", "不仅如此", "总而言之", "众所周知", "与此同时", "综上所述", "由此可见",
    "毫无疑问", "值得一提的是", "让我们一起来看看",
)
WARMTH_MARKERS = (
    "你们看", "跟你们讲", "跟你们说", "有没有人", "姐妹", "闺蜜", "朋友", "我老公", "某人",
    "舒服", "喜欢", "安心", "温暖", "日常", "周末", "回家", "慢慢",
)
HUMOR_MARKERS = (
    "笑死", "服了", "离谱", "好笑", "最搞笑", "某人", "结果他", "结果我", "救命", "无语",
    "表面镇定", "狂喜", "吐槽",
)
CAVEAT_MARKERS = (
    "但如果你", "不过如果", "唯一的小缺点", "小缺点", "不太方便", "稍微有点", "不是那种",
    "因人而异", "实话实说", "客观讲", "不一定适合",
)
HYPE_MARKERS = (
    "绝绝子", "按头安利", "闭眼入", "天花板", "神仙", "逆天", "炸裂", "必买", "疯抢", "封神",
)

_LAUGH_CUE_RE = re.compile(r"\[[^\]]*(?:笑|叹|气|哈|laugh|sigh|chuckle)[^\]]*\]", re.I)
_AABB_RE = re.compile(r"([\u4e00-\u9fff])\1([\u4e00-\u9fff])\2")
_AA_DE_RE = re.compile(r"([\u4e00-\u9fff])\1的")
_TRAILING_PUNCT = " \t\"'”’）)】]~～。.！!？?…"


def _clamp100(val: float) -> int:
    return max(0, min(100, int(round(val))))


def analyze_text_linguistics(texts: List[str], *, target_cpm: Optional[float] = None,
                             pause_per_100: Optional[float] = None) -> Dict[str, Any]:
    """Extract deep lexical, syntactic, particle, sensory, and 5-axis tone metrics from spoken Chinese/bilingual texts."""
    clauses: List[str] = []
    lengths: List[int] = []
    laugh_cues = 0
    exclamations = 0
    questions = 0

    for raw in texts:
        if not isinstance(raw, str) or not raw.strip():
            continue
        laugh_cues += len(_LAUGH_CUE_RE.findall(raw))
        exclamations += raw.count("！") + raw.count("!")
        questions += raw.count("？") + raw.count("?")
        for cl in textstats.split_clauses(raw):
            clean = textstats.strip_cues(cl).strip()
            if not clean:
                continue
            units = textstats.spoken_units(clean)
            if units > 0:
                clauses.append(clean)
                lengths.append(units)

    n_clauses = len(clauses)
    total_units = sum(lengths)
    if n_clauses == 0 or total_units == 0:
        return {
            "clauses": 0,
            "spoken_units": 0,
            "particles": {"single": {}, "multi": {}, "particle_rate_pct": 0.0},
            "connectors": {"colloquial": {}, "bookish": {}, "colloquial_rate_pct": 0.0},
            "sensory_reduplications": [],
            "tone_axes": {
                "warmth_intimacy": 50,
                "playful_humor": 50,
                "restraint_authenticity": 50,
                "spoken_naturalness": 50,
                "pacing_energy": 50,
            },
            "dominant_tone_zh": "样本不足",
            "dominant_tone_en": "Insufficient material",
        }

    single_counts: Dict[str, int] = {}
    multi_counts: Dict[str, int] = {}
    particle_clauses = 0

    colloq_counts: Dict[str, int] = {}
    bookish_counts: Dict[str, int] = {}
    sensory_found: Dict[str, int] = {}

    warmth_hits = 0
    humor_hits = laugh_cues
    caveat_hits = 0
    hype_hits = 0

    for cl in clauses:
        tail = cl.rstrip(_TRAILING_PUNCT)
        matched_multi = False
        for mp in MULTI_PARTICLES:
            if tail.endswith(mp):
                multi_counts[mp] = multi_counts.get(mp, 0) + 1
                particle_clauses += 1
                matched_multi = True
                break
        if not matched_multi and tail and tail[-1] in SINGLE_PARTICLES:
            ch = tail[-1]
            single_counts[ch] = single_counts.get(ch, 0) + 1
            particle_clauses += 1
            if ch == "哈":
                humor_hits += 1
            if ch in ("啦", "嘛", "喔", "呐", "呀"):
                warmth_hits += 1

        for conn in COLLOQUIAL_CONNECTORS:
            if conn in cl:
                colloq_counts[conn] = colloq_counts.get(conn, 0) + 1
        for bconn in BOOKISH_CONNECTORS:
            if bconn in cl:
                bookish_counts[bconn] = bookish_counts.get(bconn, 0) + 1

        for m in _AABB_RE.finditer(cl):
            tok = m.group(0)
            if tok[0] != tok[2]:
                sensory_found[tok] = sensory_found.get(tok, 0) + 1
        for m in _AA_DE_RE.finditer(cl):
            tok = m.group(0)
            sensory_found[tok] = sensory_found.get(tok, 0) + 1

        for w in WARMTH_MARKERS:
            if w in cl:
                warmth_hits += 1
        for h in HUMOR_MARKERS:
            if h in cl:
                humor_hits += 1
        for cv in CAVEAT_MARKERS:
            if cv in cl:
                caveat_hits += 1
        for hy in HYPE_MARKERS:
            if hy in cl:
                hype_hits += 1

    particle_rate = 100.0 * particle_clauses / n_clauses
    colloq_total = sum(colloq_counts.values())
    bookish_total = sum(bookish_counts.values())
    colloq_rate = 100.0 * colloq_total / n_clauses
    sensory_total = sum(sensory_found.values())

    sorted_lens = sorted(lengths)
    median_len = sorted_lens[n_clauses // 2]
    comfortable_share = sum(1 for ln in lengths if 4 <= ln <= 18) / float(n_clauses)
    short_punchy_share = sum(1 for ln in lengths if ln <= 10) / float(n_clauses)
    exclam_per_clause = exclamations / float(n_clauses)

    # 5-Axis Normalized Tone Profile (0..100)
    warmth = _clamp100(
        38.0
        + min(34.0, (warmth_hits / float(n_clauses)) * 65.0)
        + min(18.0, sensory_total * 6.0)
        + min(10.0, (questions / float(n_clauses)) * 40.0)
    )
    humor = _clamp100(
        25.0 + min(68.0, (humor_hits / float(n_clauses)) * 130.0)
    )
    restraint = _clamp100(
        72.0
        + min(20.0, caveat_hits * 8.0)
        - min(45.0, hype_hits * 15.0)
        - min(25.0, max(0.0, exclam_per_clause - 0.15) * 60.0)
    )
    naturalness = _clamp100(
        35.0
        + comfortable_share * 35.0
        + min(18.0, particle_rate * 0.55)
        + min(15.0, colloq_rate * 0.6)
        - min(40.0, bookish_total * 12.0)
    )
    cpm = float(target_cpm or 235.0)
    cpm_norm = max(0.0, min(1.0, (cpm - 170.0) / 130.0))
    pause_factor = 0.0 if pause_per_100 is None else min(12.0, pause_per_100 * 1.2)
    energy = _clamp100(
        30.0 + cpm_norm * 38.0 + short_punchy_share * 24.0 + min(12.0, exclam_per_clause * 30.0) - pause_factor * 0.5
    )

    axes = {
        "warmth_intimacy": warmth,
        "playful_humor": humor,
        "restraint_authenticity": restraint,
        "spoken_naturalness": naturalness,
        "pacing_energy": energy,
    }
    dom_zh, dom_en = _describe_dominant_tone(axes)

    return {
        "clauses": n_clauses,
        "spoken_units": total_units,
        "median_clause_units": median_len,
        "comfortable_clause_pct": round(comfortable_share * 100.0, 1),
        "particles": {
            "single": dict(sorted(single_counts.items(), key=lambda kv: (-kv[1], kv[0]))),
            "multi": dict(sorted(multi_counts.items(), key=lambda kv: (-kv[1], kv[0]))),
            "particle_rate_pct": round(particle_rate, 1),
        },
        "connectors": {
            "colloquial": dict(sorted(colloq_counts.items(), key=lambda kv: (-kv[1], kv[0]))[:15]),
            "bookish": dict(sorted(bookish_counts.items(), key=lambda kv: (-kv[1], kv[0]))),
            "colloquial_rate_pct": round(colloq_rate, 1),
        },
        "sensory_reduplications": [k for k, _ in sorted(sensory_found.items(), key=lambda kv: (-kv[1], kv[0]))[:12]],
        "caveat_count": caveat_hits,
        "laugh_cue_count": laugh_cues,
        "tone_axes": axes,
        "dominant_tone_zh": dom_zh,
        "dominant_tone_en": dom_en,
    }


def _describe_dominant_tone(axes: Dict[str, int]) -> Tuple[str, str]:
    tags_zh: List[str] = []
    tags_en: List[str] = []
    if axes["restraint_authenticity"] >= 75:
        tags_zh.append("克制真实")
        tags_en.append("restrained & authentic")
    if axes["warmth_intimacy"] >= 65:
        tags_zh.append("松弛熟人感")
        tags_en.append("warm close-friend intimacy")
    if axes["playful_humor"] >= 55:
        tags_zh.append("带冷幽默吐槽")
        tags_en.append("dry playful humor")
    if axes["spoken_naturalness"] >= 75:
        tags_zh.append("自然口语化")
        tags_en.append("highly natural spoken cadence")
    if axes["pacing_energy"] >= 68:
        tags_zh.append("节奏轻快")
        tags_en.append("brisk energetic pacing")
    elif axes["pacing_energy"] <= 45:
        tags_zh.append("慢速舒缓")
        tags_en.append("calm unhurried pacing")
    if not tags_zh:
        return "平稳日常口播", "Balanced everyday conversational tone"
    return " · ".join(tags_zh[:3]), ", ".join(tags_en[:3]).capitalize()


# ---- Pure-stdlib 16-bit PCM WAV Acoustic Prosody Analyzer -------------------

def analyze_wav_prosody(wav_source: Union[Path, bytes]) -> Optional[Dict[str, Any]]:
    """Compute vocal RMS energy, dynamic range (dB), pause statistics, and 3-stage energy arc from WAV audio."""
    try:
        fh: Any = io.BytesIO(wav_source) if isinstance(wav_source, bytes) else open(str(wav_source), "rb")
        with fh:
            with wave.open(fh, "rb") as wf:
                channels = wf.getnchannels()
                sampwidth = wf.getsampwidth()
                framerate = wf.getframerate()
                nframes = wf.getnframes()
                if framerate <= 0 or nframes <= 0 or sampwidth != 2 or channels < 1:
                    return None
                max_frames = framerate * 600  # analyze up to 10 minutes
                raw = wf.readframes(min(nframes, max_frames))
    except (OSError, wave.Error, EOFError):
        return None

    samples = array.array("h")
    samples.frombytes(raw[: len(raw) - (len(raw) % 2)])
    if sys.byteorder != "little":
        samples.byteswap()
    if channels > 1:
        samples = array.array("h", samples[0::channels])

    total_samples = len(samples)
    duration_sec = total_samples / float(framerate)
    if duration_sec < 0.8:
        return None

    win = max(1, int(framerate * 0.05))  # 50 ms frame
    frame_db: List[float] = []
    frame_rms: List[float] = []
    for start in range(0, total_samples - win + 1, win):
        acc = 0.0
        for val in samples[start:start + win]:
            acc += float(val) * float(val)
        rms = math.sqrt(acc / win) / 32768.0
        frame_rms.append(rms)
        db = 20.0 * math.log10(max(rms, 1e-6))
        frame_db.append(db)

    if not frame_db:
        return None

    sorted_db = sorted(frame_db)
    peak_db = sorted_db[-1]
    silence_thresh = max(-52.0, peak_db - 28.0)

    voiced_dbs = [d for d in frame_db if d > silence_thresh]
    voiced_ratio = len(voiced_dbs) / float(len(frame_db))

    # Count contiguous silent runs >= 0.30s (6 frames of 50ms) inside speech span
    pauses: List[float] = []
    run = 0
    seen_voice = False
    for d in frame_db:
        if d > silence_thresh:
            if seen_voice and run >= 6:
                dur = run * 0.05
                if dur <= 3.0:
                    pauses.append(dur)
            run = 0
            seen_voice = True
        elif seen_voice:
            run += 1

    if len(voiced_dbs) >= 4:
        vs = sorted(voiced_dbs)
        p10 = vs[int(0.10 * (len(vs) - 1))]
        p90 = vs[int(0.90 * (len(vs) - 1))]
        dyn_range = round(max(0.0, p90 - p10), 1)
        mean_db = round(sum(voiced_dbs) / len(voiced_dbs), 1)
    else:
        dyn_range = 0.0
        mean_db = round(sum(frame_db) / len(frame_db), 1)

    third = max(1, len(frame_db) // 3)
    arc = {
        "opening_db": round(sum(frame_db[:third]) / third, 1),
        "middle_db": round(sum(frame_db[third:2 * third]) / max(1, len(frame_db[third:2 * third])), 1),
        "closing_db": round(sum(frame_db[2 * third:]) / max(1, len(frame_db[2 * third:])), 1),
    }

    if dyn_range >= 12.0:
        style_zh, style_en = "起伏生动、重音明显", "Expressive vocal modulation with clear emphasis peaks"
    elif dyn_range >= 6.5:
        style_zh, style_en = "自然松弛、语调平稳微扬", "Natural conversational modulation"
    else:
        style_zh, style_en = "冷静克制、平缓叙述感", "Calm, understated even-toned delivery"

    return {
        "duration_sec": round(duration_sec, 2),
        "mean_voiced_db": mean_db,
        "dynamic_range_db": dyn_range,
        "voiced_ratio": round(voiced_ratio, 3),
        "pause_count": len(pauses),
        "mean_pause_sec": round(sum(pauses) / len(pauses), 2) if pauses else 0.0,
        "energy_arc_db": arc,
        "delivery_style_zh": style_zh,
        "delivery_style_en": style_en,
    }


def corpus_tone_profile(store: Store) -> Dict[str, Any]:
    """Full-service Voice & Tone Analyzer report over the creator's organic library and timed recordings."""
    all_samples = store.list_samples()
    confirmed_organic = [
        s for s in pipeline.confirmed_samples(all_samples)
        if not s.get("is_sponsored")
    ]
    dna, is_example = store.load_dna()

    # Timing & pause density across non-sponsored timed material
    timed_organic = [s for s in all_samples if not s.get("is_sponsored") and isinstance(s.get("timing"), dict)]
    total_pauses = 0
    total_timed_units = 0
    acoustic_notes: List[Dict[str, Any]] = []
    prosody_samples: List[Dict[str, Any]] = []

    for s in timed_organic:
        t_obj = s.get("timing") or {}
        if isinstance(t_obj.get("pauses"), int) and isinstance(t_obj.get("spoken_units"), int) and t_obj["spoken_units"] > 0:
            total_pauses += int(t_obj["pauses"])
            total_timed_units += int(t_obj["spoken_units"])
        vis = t_obj.get("visual_ingest")
        if isinstance(vis, dict) and vis.get("vocal_energy"):
            acoustic_notes.append({"title": s["title"], "vocal_energy": str(vis["vocal_energy"])})
        pros = t_obj.get("acoustic_prosody")
        if isinstance(pros, dict) and pros.get("dynamic_range_db") is not None:
            prosody_samples.append({"title": s["title"], **pros})

    pause_per_100 = round(100.0 * total_pauses / total_timed_units, 2) if total_timed_units >= 50 else None
    measured = timing.combined_rate([s.get("timing") for s in all_samples if not s.get("is_sponsored")])
    target_cpm = (
        measured.get("speaking_rate_cpm")
        if isinstance(measured, dict) and measured.get("speaking_rate_cpm")
        else (dna.get("cadence_and_syntax") or {}).get("target_speaking_rate_cpm", 238)
    )

    texts = [s["text"] for s in confirmed_organic]
    ling = analyze_text_linguistics(texts, target_cpm=target_cpm, pause_per_100=pause_per_100)

    return {
        "organic_items_analyzed": len(confirmed_organic),
        "timed_items_analyzed": len(timed_organic),
        "dna_is_example": is_example,
        "speaking_rate_cpm": target_cpm,
        "measured_speaking_rate": measured,
        "pauses_per_100_units": pause_per_100,
        "linguistics": ling,
        "acoustic_prosody_samples": prosody_samples[:10],
        "multimodal_vocal_notes": acoustic_notes[:10],
    }


def compare_script_tone(store: Store, script: Dict[str, Any]) -> Dict[str, Any]:
    """Compare a draft script's 5-axis tone profile and linguistic fingerprint against the creator's organic baseline."""
    baseline = corpus_tone_profile(store)
    base_ling = baseline["linguistics"]
    lines = [
        str(ln.get("spoken_script_zh") or "")
        for ln in (script.get("lines") or [])
        if isinstance(ln, dict) and ln.get("audio_mode") != "LIVE_CLIP"
    ]
    script_ling = analyze_text_linguistics(lines, target_cpm=baseline["speaking_rate_cpm"])
    vm = voicematch.score(script.get("lines") or [], voicematch.reference_for(store))

    if base_ling["clauses"] < 6 or script_ling["clauses"] < 3:
        return {
            "ready": False,
            "baseline": base_ling,
            "script": script_ling,
            "voicematch": vm,
            "tone_alignment_score": None,
            "diagnostics": [],
        }

    b_axes = base_ling["tone_axes"]
    s_axes = script_ling["tone_axes"]
    diffs: Dict[str, int] = {k: int(s_axes[k]) - int(b_axes[k]) for k in b_axes}
    mean_abs_diff = sum(abs(v) for v in diffs.values()) / float(len(diffs))
    tone_score = _clamp100(100.0 - mean_abs_diff * 1.4)

    diagnostics: List[Dict[str, str]] = []
    if diffs["restraint_authenticity"] < -15:
        diagnostics.append({
            "axis": "restraint_authenticity",
            "message_zh": "当前脚本感叹号或夸张词偏多，比你平时克制真实的语气更像推广文案。",
            "message_en": "Draft uses more hype/exclamation than your usual restrained, authentic tone.",
        })
    if diffs["spoken_naturalness"] < -15:
        diagnostics.append({
            "axis": "spoken_naturalness",
            "message_zh": "书面连接词或长句偏多，建议拆成短气口并补回自然的口头衔接词。",
            "message_en": "Sentences lean bookish or long compared with your natural spoken cadence.",
        })
    if diffs["warmth_intimacy"] < -18:
        diagnostics.append({
            "axis": "warmth_intimacy",
            "message_zh": "缺少熟人聊天的互动感（如日常小细节或柔和语气词）。",
            "message_en": "Feels slightly flat/impersonal compared to your usual warm friend-chat tone.",
        })

    return {
        "ready": True,
        "tone_alignment_score": tone_score,
        "axis_deltas": diffs,
        "baseline": base_ling,
        "script": script_ling,
        "voicematch": vm,
        "diagnostics": diagnostics,
    }
