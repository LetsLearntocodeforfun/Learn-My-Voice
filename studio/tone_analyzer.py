"""Countable wording habits and measured sound delivery across the creator's material and in a script.

Nothing here is a model's opinion, and no personality score is computed. Round 1 combined word hits into five
0-to-100 "axes" (warmth, humour, restraint, naturalness, energy) with untuned weights; those were removed
because they presented arbitrary arithmetic as a measurement of character.

What is counted in text (``analyze_text_linguistics``), modeled on ``studio.voicematch``:

- breath length: spoken units per breath clause (5th, 50th and 95th nearest-rank percentiles);
- sentence-final particles: single-character particles from ``corpus.FINAL_PARTICLES`` and multi-character
  spoken tags at the end of a breath (``MULTI_PARTICLES``);
- spoken and formal connectors: conversational transitions (``COLLOQUIAL_CONNECTORS``) and formal written
  transitions (``BOOKISH_CONNECTORS``), matched longest-first without overlapping double counts or compound
  substring collisions (``自然而然`` is not ``然而``, ``不过敏`` is not ``不过``, ``检查结果`` is not ``结果``);
- reduplicated words: ``AABB`` forms (such as ``软软糯糯``) and ``AA的`` forms (such as ``水水的``, leaving out
  possessive kinship nouns such as ``妈妈的``);
- hedges and caveats (``HEDGE_PHRASES``) and hard-sell phrases (``HARD_SELL_PHRASES``), skipping negated uses
  such as ``不要闭眼入``, ``从来不按头安利`` or ``没有任何小缺点``;
- questions and exclamations: the share of spoken breaths that end with ``?`` / ``？`` or ``!`` / ``！`` (after
  stripping bracketed stage cues such as ``[笑!]``).

Every word list below (``MULTI_PARTICLES``, ``COLLOQUIAL_CONNECTORS``, ``BOOKISH_CONNECTORS``,
``HEDGE_PHRASES``, ``HARD_SELL_PHRASES``) is this project's own fixed list, chosen by hand. Counting matches
against them is a literal check on the words and says nothing about whether a script is good, honest,
persuasive, or likely to do well.

Wording reference rules (``corpus_tone_profile``, ``compare_script_tone``):

- Only confirmed non-sponsored material (``pipeline.confirmed_samples``, ``not is_sponsored``) is counted as
  the creator's own wording. Unconfirmed machine transcripts and unconfirmed scripts posted from this app are
  left out.
- Comparing a script against the creator's material requires at least ``MIN_REFERENCE_BREATHS`` (10) breaths in
  the confirmed non-sponsored corpus and ``MIN_SCRIPT_BREATHS`` (4, as in ``voicematch``) in the script. Once
  at least ``MIN_RANGE_SCRIPTS`` (3) confirmed non-sponsored items hold 4 or more breaths each, the creator's
  own per-script range (low, median, high) is reported beside every habit.

What is measured from timestamps and sound (``delivery_summary``):

- speaking rate (``timing.combined_rate``) and pauses per 100 spoken units (from ``timing["pauses"]`` and
  ``timing["units"]`` across non-sponsored timed items with at least 50 spoken units in total);
- loudness range (10th to 90th percentile of 1 s sound levels, in dB) and voice pitch estimate and span
  (autocorrelation on voiced stretches in Hz and semitones) from ``soundstats.measure`` / ``soundstats.across``
  on the 16 kHz mono WAV extracted during transcription. Each recording is bounded to
  ``soundstats.MAX_MEASURED_SEC`` (600 s, ten minutes; ``cut_short`` marks longer files honestly). Ten minutes
  of 16 kHz mono WAV (9.6 million samples) took 0.29 to 0.36 s on an Apple Silicon Mac (arm64, Python 3.9.6,
  21 November 2026). Sound measurements cannot separate background music or a second speaker from the creator.
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Any, Dict, Iterable, List, Optional, Tuple

from . import corpus, pipeline, soundstats, textstats, timing, voicematch
from .store import Store

MIN_REFERENCE_BREATHS = 10          # under 10 breaths of confirmed non-sponsored text, no script comparison is made
MIN_SCRIPT_BREATHS = voicematch.MIN_SCRIPT_BREATHS   # 4 breaths, aligned with voicematch
MIN_RANGE_SCRIPTS = voicematch.MIN_BAND_SCRIPTS      # 3 items of >= 4 breaths each before a per-script range is shown
MIN_TIMED_UNITS_FOR_PAUSE_RATE = 50                  # under 50 timed spoken units, pauses per 100 units is not reported
TOP_SHOWN = 8
MAX_SOUND_SAMPLES_SHOWN = 10

# Single-character sentence-final particles (shared with corpus.py and voicematch.py) and multi-character spoken
# tags at the end of a breath. Project's own list; longer tags are tried first so 对吧 is counted as 对吧, not 吧.
SINGLE_PARTICLES = tuple(corpus.FINAL_PARTICLES)
MULTI_PARTICLES = tuple(sorted((
    "对吧", "是吧", "好吧", "喔唷", "来着", "而已", "不是吗", "懂吧", "对不对", "好不好",
), key=len, reverse=True))

# Conversational spoken transitions vs. formal written transitions. Both are this project's own fixed lists,
# chosen by hand to show surface transition habits; neither says whether a line reads well.
COLLOQUIAL_CONNECTORS = tuple(sorted((
    "不过说实话", "最搞笑的是", "顺便提一嘴", "反正就是", "有一说一", "说实话", "老实讲", "说白了", "讲真",
    "然后呢", "所以说", "就是说", "比如说", "关键是", "重点是", "主要是",
    "其实", "结果", "本来", "后来", "然后", "不过", "反正", "而且", "所以", "但是", "可是", "因为", "顺便", "干脆", "索性",
), key=len, reverse=True))
BOOKISH_CONNECTORS = tuple(sorted((
    "让我们一起来看看", "值得一提的是", "总而言之", "综上所述", "不仅如此", "众所周知",
    "与此同时", "由此可见", "毫无疑问", "换句话说", "因此", "然而", "此外", "况且",
), key=len, reverse=True))

# Qualifying / softening expressions that narrow a claim or name a drawback. Project's own list; says nothing
# about whether a claim is truthful.
HEDGE_PHRASES = tuple(sorted((
    "唯一的小缺点", "美中不足的是", "不一定适合", "稍微有点", "有一点点", "但如果你", "不过如果",
    "因人而异", "不太适合", "不太方便", "实话实说", "说句实在话", "见仁见智", "看你自己",
    "唯一的问题", "小缺点", "美中不足", "客观讲", "看个人", "挑人",
), key=len, reverse=True))

# Common platform hype / hard-sell expressions. Project's own list; says nothing about compliance or quality.
HARD_SELL_PHRASES = tuple(sorted((
    "按头安利", "冲就完了", "万年回购", "绝绝子", "闭眼入", "无脑入", "天花板", "全网最",
    "必买", "疯抢", "封神", "炸裂", "逆天", "神仙", "买它",
), key=len, reverse=True))

WORDLIST_NOTE = {
    "zh": "连接词、复合语气词、留白限定词和硬广夸张词都是本项目自己整理的固定词表，只按字面计数（已排除「不闭眼入」「没有小缺点」这类否定说法），不是 AI 的评判，也不说明脚本好不好或会不会火。",
    "en": "The lists of connectors, multi-character tags, hedges and hard-sell phrases are this project's own fixed word lists. They count literal matches only (skipping negated uses) and say nothing about whether a script is good or how it will perform.",
}

_ALL_CONNECTORS: Tuple[Tuple[str, str], ...] = tuple(sorted(
    [(c, "colloquial") for c in COLLOQUIAL_CONNECTORS] + [(c, "bookish") for c in BOOKISH_CONNECTORS],
    key=lambda item: len(item[0]),
    reverse=True,
))
_ADVERB_AFTER_CONJ = frozenset(("其实", "本来", "反正", "干脆", "索性"))
_BUGUO_NOT_CONNECTOR_NEXT = frozenset("敏期夜关分度去来脑瘾秤审火")

# Kinship, polite and laughter characters whose AA+的 form (妈妈的, 宝宝的, 谢谢的, 哈哈的) is possessive or laughter,
# not a descriptive reduplication.
_NON_REDUPLICATION_AA = frozenset("妈爸姐哥弟妹奶爷叔伯姨姑舅婶婆公娃宝太谢哈")

_LAUGH_CUE_RE = re.compile(r"\[[^\[\]]*(?:笑|叹|气|哈|laugh|sigh|chuckle)[^\[\]]*\]", re.I)
_BREATH_SPLIT_RE = re.compile(r"([^/\n\uff0c,\u3002\uff01!\uff1f?\uff1b;\uff5e~\u2026]+)([/\n\uff0c,\u3002\uff01!\uff1f?\uff1b;\uff5e~\u2026]*)")
_AABB_RE = re.compile(r"([\u4e00-\u9fff])\1([\u4e00-\u9fff])\2")
_AA_DE_RE = re.compile(r"([\u4e00-\u9fff])\1的")
_NEGATED_WINDOW_RE = re.compile(
    r"(?:不是|不算|算不上|谈不上|称不上|不要|不用|不必|不会|不能|不搞|不说|不信|不至于|"
    r"没必要|没有|毫无|挑不出|找不到|从不|从来不|绝不|并非|并不是|真不是|拒绝|反对|讨厌|最烦|"
    r"别|勿|莫|不|没)"
    r"(?:太|很|那么|怎么|什么|任何|一点|半点|乱|瞎|盲目|随便|再|去|敢|想|会|能|要|说|是|算){0,3}$"
)
_START_SKIP = " \t\u3000\"'“”‘’「」『』（）()【】[]"
_TRAILING_WRAP = " \t\u3000\"'”’）)】]」』"

def _percentile_int(ordered: List[int], pct: float) -> int:
    """Nearest-rank percentile, matching ``corpus.voice_stats`` and ``voicematch.Counts.percentile``."""
    if not ordered:
        return 0
    rank = max(1, int(-(-pct * len(ordered) // 100)))
    return ordered[min(rank, len(ordered)) - 1]


def _percentile_float(ordered: List[float], pct: float) -> float:
    rank = max(1, int(-(-pct * len(ordered) // 100)))
    return ordered[min(rank, len(ordered)) - 1]


def _top_dict(counts: Counter, limit: int = TOP_SHOWN) -> Dict[str, int]:
    return dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:limit])


def _is_negated(clause: str, pos: int) -> bool:
    """True when ``clause[pos:]`` is immediately preceded by a negator within a bounded 10-character window."""
    if pos <= 0:
        return False
    window = clause[max(0, pos - 10):pos]
    return _NEGATED_WINDOW_RE.search(window) is not None


def _iter_breaths(raw: str) -> Iterable[Tuple[str, int, bool, bool]]:
    """Yield ``(clause, spoken_units, has_question, has_exclamation)`` for each spoken breath in ``raw``."""
    stripped = textstats.strip_cues(raw)
    for match in _BREATH_SPLIT_RE.finditer(stripped):
        clause = match.group(1).strip()
        if not clause:
            continue
        units = textstats.spoken_units(clause)
        if units <= 0:
            continue
        punct = match.group(2)
        yield (
            clause,
            units,
            ("?" in punct or "？" in punct),
            ("!" in punct or "！" in punct),
        )


def _match_connectors(clause: str) -> List[Tuple[str, str]]:
    """Non-overlapping ``(connector, kind)`` matches in one breath, longest first, without substring collisions."""
    head = clause.lstrip(_START_SKIP)
    if not head:
        return []
    found: List[Tuple[str, str]] = []
    pos = 0
    n = len(head)
    while pos < n:
        hit: Optional[Tuple[str, str]] = None
        for phrase, kind in _ALL_CONNECTORS:
            plen = len(phrase)
            if pos + plen > n or not head.startswith(phrase, pos):
                continue
            if plen <= 2:
                # Two-character connectors stand at the start of a breath (or right after 但/那/可/而 for adverbs
                # such as 但其实), so mid-breath words like 自然而然, 之所以, 名副其实, 检查结果 and 打不过 never match.
                allowed_start = (pos == 0) or (pos == 1 and head[0] in "但那可而" and phrase in _ADVERB_AFTER_CONJ)
                if not allowed_start:
                    continue
                after = head[pos + plen:pos + plen + 1]
                if phrase == "不过" and after in _BUGUO_NOT_CONNECTOR_NEXT:
                    continue
                if phrase == "干脆" and after == "面":
                    continue
            hit = (phrase, kind)
            break
        if hit is not None:
            found.append(hit)
            pos += len(hit[0])
        else:
            pos += 1
    return found


def _scan_phrases(clause: str, phrases: Tuple[str, ...]) -> List[str]:
    """Non-overlapping longest-first phrase matches in ``clause``, skipping negated occurrences."""
    found: List[str] = []
    pos = 0
    n = len(clause)
    while pos < n:
        matched: Optional[str] = None
        for phrase in phrases:
            if clause.startswith(phrase, pos):
                matched = phrase
                break
        if matched is not None:
            if not _is_negated(clause, pos):
                found.append(matched)
            pos += len(matched)
        else:
            pos += 1
    return found


def _scan_reduplications(clause: str) -> List[str]:
    """Reduplicated descriptive forms (``AABB`` and ``AA的``) in ``clause``, without double-counting ``AABB的``."""
    found: List[str] = []
    aabb_spans: List[Tuple[int, int]] = []
    for m in _AABB_RE.finditer(clause):
        if m.group(1) != m.group(2):
            found.append(m.group(0))
            aabb_spans.append((m.start(), m.end()))
    for m in _AA_DE_RE.finditer(clause):
        ch = m.group(1)
        if ch in _NON_REDUPLICATION_AA:
            continue
        if any(start <= m.start() < end for start, end in aabb_spans):
            continue
        found.append(m.group(0))
    return found


def analyze_text_linguistics(texts: Iterable[Any]) -> Dict[str, Any]:
    """Count inspectable speech and surface-wording habits across ``texts``. No composite or personality scores."""
    lengths: List[int] = []
    single_counts: Counter = Counter()
    multi_counts: Counter = Counter()
    particle_breaths = 0

    colloq_counts: Counter = Counter()
    bookish_counts: Counter = Counter()
    colloq_breaths = 0
    bookish_breaths = 0

    redup_counts: Counter = Counter()
    hedge_counts: Counter = Counter()
    hard_sell_counts: Counter = Counter()

    question_breaths = 0
    exclamation_breaths = 0
    laugh_cues = 0

    for raw in texts or []:
        if not isinstance(raw, str) or not raw.strip():
            continue
        laugh_cues += len(_LAUGH_CUE_RE.findall(raw))
        for clause, units, has_q, has_exc in _iter_breaths(raw):
            lengths.append(units)
            if has_q:
                question_breaths += 1
            if has_exc:
                exclamation_breaths += 1

            tail = clause.rstrip(_TRAILING_WRAP)
            multi = next((mp for mp in MULTI_PARTICLES if tail.endswith(mp)), None)
            if multi is not None:
                multi_counts[multi] += 1
                particle_breaths += 1
            elif tail and tail[-1] in SINGLE_PARTICLES:
                single_counts[tail[-1]] += 1
                particle_breaths += 1

            conns = _match_connectors(clause)
            if conns:
                has_c = False
                has_b = False
                for phrase, kind in conns:
                    if kind == "colloquial":
                        colloq_counts[phrase] += 1
                        has_c = True
                    else:
                        bookish_counts[phrase] += 1
                        has_b = True
                if has_c:
                    colloq_breaths += 1
                if has_b:
                    bookish_breaths += 1

            for tok in _scan_reduplications(clause):
                redup_counts[tok] += 1
            for phrase in _scan_phrases(clause, HEDGE_PHRASES):
                hedge_counts[phrase] += 1
            for phrase in _scan_phrases(clause, HARD_SELL_PHRASES):
                hard_sell_counts[phrase] += 1

    n_breaths = len(lengths)
    total_units = sum(lengths)
    if n_breaths == 0:
        return {
            "breaths": 0,
            "clauses": 0,
            "spoken_units": 0,
            "breath_length": {"p5": 0, "median": 0, "p95": 0},
            "particles": {"single": {}, "multi": {}, "particle_breaths": 0, "particle_rate_pct": 0.0},
            "connectors": {
                "colloquial": {}, "bookish": {},
                "colloquial_breaths": 0, "bookish_breaths": 0,
                "colloquial_rate_pct": 0.0, "bookish_rate_pct": 0.0,
            },
            "reduplications": {"words": {}, "count": 0, "per_100_breaths": 0.0},
            "hedges": {"phrases": {}, "count": 0, "per_100_breaths": 0.0},
            "hard_sell": {"phrases": {}, "count": 0, "per_100_breaths": 0.0},
            "punctuation": {
                "question_breaths": 0, "question_rate_pct": 0.0,
                "exclamation_breaths": 0, "exclamation_rate_pct": 0.0,
            },
            "laugh_cues": laugh_cues,
        }

    ordered_lengths = sorted(lengths)
    redup_total = sum(redup_counts.values())
    hedge_total = sum(hedge_counts.values())
    hard_sell_total = sum(hard_sell_counts.values())

    return {
        "breaths": n_breaths,
        "clauses": n_breaths,
        "spoken_units": total_units,
        "breath_length": {
            "p5": _percentile_int(ordered_lengths, 5),
            "median": _percentile_int(ordered_lengths, 50),
            "p95": _percentile_int(ordered_lengths, 95),
        },
        "particles": {
            "single": _top_dict(single_counts, len(SINGLE_PARTICLES)),
            "multi": _top_dict(multi_counts, len(MULTI_PARTICLES)),
            "particle_breaths": particle_breaths,
            "particle_rate_pct": round(100.0 * particle_breaths / n_breaths, 1),
        },
        "connectors": {
            "colloquial": _top_dict(colloq_counts),
            "bookish": _top_dict(bookish_counts),
            "colloquial_breaths": colloq_breaths,
            "bookish_breaths": bookish_breaths,
            "colloquial_rate_pct": round(100.0 * colloq_breaths / n_breaths, 1),
            "bookish_rate_pct": round(100.0 * bookish_breaths / n_breaths, 1),
        },
        "reduplications": {
            "words": _top_dict(redup_counts),
            "count": redup_total,
            "per_100_breaths": round(100.0 * redup_total / n_breaths, 1),
        },
        "hedges": {
            "phrases": _top_dict(hedge_counts),
            "count": hedge_total,
            "per_100_breaths": round(100.0 * hedge_total / n_breaths, 1),
        },
        "hard_sell": {
            "phrases": _top_dict(hard_sell_counts),
            "count": hard_sell_total,
            "per_100_breaths": round(100.0 * hard_sell_total / n_breaths, 1),
        },
        "punctuation": {
            "question_breaths": question_breaths,
            "question_rate_pct": round(100.0 * question_breaths / n_breaths, 1),
            "exclamation_breaths": exclamation_breaths,
            "exclamation_rate_pct": round(100.0 * exclamation_breaths / n_breaths, 1),
        },
        "laugh_cues": laugh_cues,
    }


def _script_metric_values(ling: Dict[str, Any]) -> Dict[str, float]:
    return {
        "breath_median_units": float(ling["breath_length"]["median"]),
        "particle_rate_pct": float(ling["particles"]["particle_rate_pct"]),
        "colloquial_rate_pct": float(ling["connectors"]["colloquial_rate_pct"]),
        "bookish_rate_pct": float(ling["connectors"]["bookish_rate_pct"]),
        "reduplications_per_100_breaths": float(ling["reduplications"]["per_100_breaths"]),
        "hedges_per_100_breaths": float(ling["hedges"]["per_100_breaths"]),
        "hard_sell_per_100_breaths": float(ling["hard_sell"]["per_100_breaths"]),
        "question_rate_pct": float(ling["punctuation"]["question_rate_pct"]),
        "exclamation_rate_pct": float(ling["punctuation"]["exclamation_rate_pct"]),
    }


def _reference_ranges(texts: Iterable[str]) -> Optional[Dict[str, Dict[str, Any]]]:
    """Per-script low, median and high across confirmed non-sponsored items of >= MIN_SCRIPT_BREATHS breaths."""
    per_script = [
        analyze_text_linguistics([text])
        for text in texts
        if isinstance(text, str) and text.strip()
    ]
    qualifying = [ling for ling in per_script if ling["breaths"] >= MIN_SCRIPT_BREATHS]
    if len(qualifying) < MIN_RANGE_SCRIPTS:
        return None
    extracted = [_script_metric_values(ling) for ling in qualifying]
    out: Dict[str, Dict[str, Any]] = {}
    for key in extracted[0]:
        vals = sorted(item[key] for item in extracted)
        is_int = (key == "breath_median_units")
        low, med, high = vals[0], _percentile_float(vals, 50), vals[-1]
        out[key] = {
            "low": int(round(low)) if is_int else round(low, 1),
            "median": int(round(med)) if is_int else round(med, 1),
            "high": int(round(high)) if is_int else round(high, 1),
            "scripts": len(vals),
        }
    return out


def _is_plain_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def delivery_summary(samples: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    """Measured speaking pace, timestamp pause density and WAV sound stats across non-sponsored items."""
    non_sponsored = [s for s in (samples or []) if isinstance(s, dict) and not s.get("is_sponsored")]
    timed_dicts: List[Dict[str, Any]] = []
    total_pauses = 0
    total_timed_units = 0
    sound_raw: List[Any] = []
    sound_samples: List[Dict[str, Any]] = []

    for sample in non_sponsored:
        t_obj = sample.get("timing")
        if not isinstance(t_obj, dict):
            continue
        timed_dicts.append(t_obj)
        pauses = t_obj.get("pauses")
        units = t_obj.get("units")
        if _is_plain_int(pauses) and _is_plain_int(units) and units > 0 and pauses >= 0:
            total_pauses += pauses
            total_timed_units += units
        cleaned_sound = soundstats.clean(t_obj.get("sound"))
        if cleaned_sound is not None:
            sound_raw.append(cleaned_sound)
            if cleaned_sound.get("measured") is True and len(sound_samples) < MAX_SOUND_SAMPLES_SHOWN:
                sound_samples.append(dict(cleaned_sound, title=str(sample.get("title") or "")))

    pause_per_100 = (
        round(100.0 * total_pauses / total_timed_units, 1)
        if total_timed_units >= MIN_TIMED_UNITS_FOR_PAUSE_RATE
        else None
    )
    measured_rate = timing.combined_rate(timed_dicts)
    sound_across = soundstats.across(sound_raw)
    return {
        "timed_items": len(timed_dicts),
        "timed_units": total_timed_units,
        "measured_speaking_rate": measured_rate,
        "pauses_per_100_units": pause_per_100,
        "sound": sound_across,
        "sound_samples": sound_samples,
    }


def delivery_metrics_for_prompt(samples: Iterable[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Measured pause density, loudness range and voice pitch for ``pipeline._build_voice``; None when none exist."""
    summary = delivery_summary(samples)
    sound = summary["sound"]
    out: Dict[str, Any] = {}
    if summary["pauses_per_100_units"] is not None:
        out["pauses_per_100_units"] = summary["pauses_per_100_units"]
    if sound["loudness_range_db"] is not None:
        out["loudness_range_db"] = sound["loudness_range_db"]
    if sound["pitch_hz"] is not None:
        out["pitch_hz"] = sound["pitch_hz"]
        out["pitch_span_semitones"] = sound["pitch_span_semitones"]
    if not out:
        return None
    if sound["cut_short"]:
        out["recordings_cut_short_at_10_min"] = sound["cut_short"]
    out["note"] = (
        "Measured from non-sponsored timed material, not inferred: `pauses_per_100_units` counts timestamp gaps "
        "of 0.3 s or more per 100 spoken units; `loudness_range_db` is the 10th-to-90th-percentile span of 1 s "
        "sound levels in dB; `pitch_hz` and `pitch_span_semitones` come from autocorrelation on voiced stretches "
        "(70-400 Hz) of the extracted 16 kHz mono WAV (up to 10 minutes per recording). Background music or "
        "another speaker in a recording is not separated out."
    )
    return out


def corpus_tone_profile(store: Store) -> Dict[str, Any]:
    """Counted wording habits over confirmed non-sponsored items, and measured sound over non-sponsored recordings."""
    all_samples = store.list_samples()
    confirmed_organic = [
        s for s in pipeline.confirmed_samples(all_samples)
        if not s.get("is_sponsored") and (s.get("text") or "").strip()
    ]
    dna, is_example = store.load_dna()
    delivery = delivery_summary(all_samples)
    measured = delivery["measured_speaking_rate"]
    target_cpm = (
        measured["speaking_rate_cpm"]
        if isinstance(measured, dict) and measured.get("speaking_rate_cpm")
        else (dna.get("cadence_and_syntax") or {}).get("target_speaking_rate_cpm", pipeline.DEFAULT_SPEAKING_RATE)
    )

    texts = [s["text"] for s in confirmed_organic]
    ling = analyze_text_linguistics(texts)
    ranges = _reference_ranges(texts)

    # Multimodal vocal observations come from an AI reading of a video, so only confirmed non-sponsored items count.
    vocal_notes: List[Dict[str, str]] = []
    for sample in confirmed_organic:
        t_obj = sample.get("timing")
        vis = t_obj.get("visual_ingest") if isinstance(t_obj, dict) else None
        if isinstance(vis, dict) and isinstance(vis.get("vocal_energy"), str) and vis["vocal_energy"].strip():
            vocal_notes.append({"title": sample["title"], "vocal_energy": vis["vocal_energy"].strip()[:240]})
            if len(vocal_notes) >= MAX_SOUND_SAMPLES_SHOWN:
                break

    return {
        "ready": ling["breaths"] >= MIN_REFERENCE_BREATHS,
        "needed_breaths": MIN_REFERENCE_BREATHS,
        "organic_items_analyzed": len(confirmed_organic),
        "timed_items_analyzed": delivery["timed_items"],
        "dna_is_example": is_example,
        "speaking_rate_cpm": target_cpm,
        "measured_speaking_rate": measured,
        "pauses_per_100_units": delivery["pauses_per_100_units"],
        "linguistics": ling,
        "reference_ranges": ranges,
        "sound": delivery["sound"],
        "sound_samples": delivery["sound_samples"],
        "multimodal_vocal_notes": vocal_notes,
        "wordlist_note": WORDLIST_NOTE,
    }


def _all_keys(*dicts: Dict[str, int]) -> List[str]:
    merged: Counter = Counter()
    for d in dicts:
        merged.update(d)
    return [k for k, _ in sorted(merged.items(), key=lambda kv: (-kv[1], kv[0]))]


def compare_script_tone(store: Store, script: Dict[str, Any]) -> Dict[str, Any]:
    """Compare a script's counted wording habits side by side with the creator's confirmed non-sponsored material."""
    baseline = corpus_tone_profile(store)
    base_ling = baseline["linguistics"]
    ranges = baseline["reference_ranges"] or {}
    raw_lines = script.get("lines") if isinstance(script, dict) else []
    spoken_lines = voicematch.script_text_lines(raw_lines)
    script_ling = analyze_text_linguistics(spoken_lines)
    vm = voicematch.score(raw_lines, voicematch.reference_for(store))

    needed = {"reference_breaths": MIN_REFERENCE_BREATHS, "script_breaths": MIN_SCRIPT_BREATHS}
    ref_facts = {"scripts": baseline["organic_items_analyzed"], "breaths": base_ling["breaths"]}
    delivery_ref = {
        "speaking_rate_cpm": baseline["speaking_rate_cpm"],
        "measured_speaking_rate": baseline["measured_speaking_rate"],
        "pauses_per_100_units": baseline["pauses_per_100_units"],
        "sound": baseline["sound"],
    }

    if base_ling["breaths"] < MIN_REFERENCE_BREATHS:
        return {
            "ready": False,
            "reason": "too_little_material",
            "needed": needed,
            "reference": ref_facts,
            "baseline": base_ling,
            "reference_ranges": ranges or None,
            "script": script_ling,
            "voicematch": vm,
            "comparisons": {},
            "observations": [],
            "delivery": delivery_ref,
            "wordlist_note": WORDLIST_NOTE,
        }

    if script_ling["breaths"] < MIN_SCRIPT_BREATHS:
        return {
            "ready": False,
            "reason": "too_short",
            "needed": needed,
            "reference": ref_facts,
            "baseline": base_ling,
            "reference_ranges": ranges or None,
            "script": script_ling,
            "voicematch": vm,
            "comparisons": {},
            "observations": [],
            "delivery": delivery_ref,
            "wordlist_note": WORDLIST_NOTE,
        }

    low_len, high_len = base_ling["breath_length"]["p5"], base_ling["breath_length"]["p95"]
    script_clause_lens = [units for line in spoken_lines for _, units, _, _ in _iter_breaths(line)]
    inside_pct = round(100.0 * sum(1 for u in script_clause_lens if low_len <= u <= high_len) / len(script_clause_lens))

    own_particles = set(base_ling["particles"]["single"]) | set(base_ling["particles"]["multi"])
    script_particles = _all_keys(script_ling["particles"]["multi"], script_ling["particles"]["single"])
    unfamiliar_particles = [p for p in script_particles if p not in own_particles][:TOP_SHOWN]

    own_colloq = set(base_ling["connectors"]["colloquial"])
    script_colloq = list(script_ling["connectors"]["colloquial"])
    unfamiliar_colloq = [c for c in script_colloq if c not in own_colloq][:TOP_SHOWN]

    own_bookish = set(base_ling["connectors"]["bookish"])
    script_bookish = list(script_ling["connectors"]["bookish"])
    unfamiliar_bookish = [c for c in script_bookish if c not in own_bookish][:TOP_SHOWN]

    comparisons = {
        "breath_length": {
            "own": base_ling["breath_length"],
            "range": ranges.get("breath_median_units"),
            "script": {
                "median": script_ling["breath_length"]["median"],
                "p5": script_ling["breath_length"]["p5"],
                "p95": script_ling["breath_length"]["p95"],
                "inside_pct": inside_pct,
            },
        },
        "particles": {
            "own_rate_pct": base_ling["particles"]["particle_rate_pct"],
            "own_top": _all_keys(base_ling["particles"]["multi"], base_ling["particles"]["single"])[:TOP_SHOWN],
            "range": ranges.get("particle_rate_pct"),
            "script_rate_pct": script_ling["particles"]["particle_rate_pct"],
            "script_used": script_particles[:TOP_SHOWN],
            "unfamiliar": unfamiliar_particles,
        },
        "colloquial_connectors": {
            "own_rate_pct": base_ling["connectors"]["colloquial_rate_pct"],
            "own_top": list(base_ling["connectors"]["colloquial"])[:TOP_SHOWN],
            "range": ranges.get("colloquial_rate_pct"),
            "script_rate_pct": script_ling["connectors"]["colloquial_rate_pct"],
            "script_used": script_colloq[:TOP_SHOWN],
            "unfamiliar": unfamiliar_colloq,
        },
        "bookish_connectors": {
            "own_rate_pct": base_ling["connectors"]["bookish_rate_pct"],
            "own_top": list(base_ling["connectors"]["bookish"])[:TOP_SHOWN],
            "range": ranges.get("bookish_rate_pct"),
            "script_rate_pct": script_ling["connectors"]["bookish_rate_pct"],
            "script_used": script_bookish[:TOP_SHOWN],
            "unfamiliar": unfamiliar_bookish,
        },
        "reduplications": {
            "own_per_100": base_ling["reduplications"]["per_100_breaths"],
            "own_top": list(base_ling["reduplications"]["words"])[:TOP_SHOWN],
            "range": ranges.get("reduplications_per_100_breaths"),
            "script_per_100": script_ling["reduplications"]["per_100_breaths"],
            "script_used": list(script_ling["reduplications"]["words"])[:TOP_SHOWN],
        },
        "hedges": {
            "own_per_100": base_ling["hedges"]["per_100_breaths"],
            "own_top": list(base_ling["hedges"]["phrases"])[:TOP_SHOWN],
            "range": ranges.get("hedges_per_100_breaths"),
            "script_per_100": script_ling["hedges"]["per_100_breaths"],
            "script_used": list(script_ling["hedges"]["phrases"])[:TOP_SHOWN],
        },
        "hard_sell": {
            "own_per_100": base_ling["hard_sell"]["per_100_breaths"],
            "own_top": list(base_ling["hard_sell"]["phrases"])[:TOP_SHOWN],
            "range": ranges.get("hard_sell_per_100_breaths"),
            "script_per_100": script_ling["hard_sell"]["per_100_breaths"],
            "script_used": list(script_ling["hard_sell"]["phrases"])[:TOP_SHOWN],
        },
        "questions": {
            "own_rate_pct": base_ling["punctuation"]["question_rate_pct"],
            "range": ranges.get("question_rate_pct"),
            "script_rate_pct": script_ling["punctuation"]["question_rate_pct"],
        },
        "exclamations": {
            "own_rate_pct": base_ling["punctuation"]["exclamation_rate_pct"],
            "range": ranges.get("exclamation_rate_pct"),
            "script_rate_pct": script_ling["punctuation"]["exclamation_rate_pct"],
        },
    }

    # Plain factual observations when the script contains phrases or punctuation rates outside the creator's
    # confirmed non-sponsored material. Never framed as a personality score or a pass/fail verdict.
    observations: List[Dict[str, Any]] = []
    if script_ling["hard_sell"]["count"] > 0 and script_ling["hard_sell"]["per_100_breaths"] > base_ling["hard_sell"]["per_100_breaths"]:
        matched_hs = list(script_ling["hard_sell"]["phrases"])[:TOP_SHOWN]
        observations.append({
            "kind": "hard_sell",
            "matched": matched_hs,
            "message_zh": "这篇出现了词表里的硬广夸张词（%s）：%d 处，你的非商单素材里是每百口气 %.1f 处。" % (
                "、".join(matched_hs), script_ling["hard_sell"]["count"], base_ling["hard_sell"]["per_100_breaths"],
            ),
            "message_en": "Contains %d hard-sell phrase(s) from this project's list (%s), compared with %.1f per 100 breaths in confirmed non-sponsored material." % (
                script_ling["hard_sell"]["count"], ", ".join(matched_hs), base_ling["hard_sell"]["per_100_breaths"],
            ),
        })
    if script_ling["connectors"]["bookish_breaths"] > 0 and script_ling["connectors"]["bookish_rate_pct"] > base_ling["connectors"]["bookish_rate_pct"]:
        matched_bk = list(script_ling["connectors"]["bookish"])[:TOP_SHOWN]
        observations.append({
            "kind": "bookish_connectors",
            "matched": matched_bk,
            "message_zh": "这篇出现了书面连接词（%s）：占 %.1f%% 的气口，你的非商单素材里是 %.1f%%。" % (
                "、".join(matched_bk), script_ling["connectors"]["bookish_rate_pct"], base_ling["connectors"]["bookish_rate_pct"],
            ),
            "message_en": "Uses formal written connector(s) (%s) in %.1f%% of breaths, compared with %.1f%% in confirmed non-sponsored material." % (
                ", ".join(matched_bk), script_ling["connectors"]["bookish_rate_pct"], base_ling["connectors"]["bookish_rate_pct"],
            ),
        })
    exclam_ceiling = (
        ranges["exclamation_rate_pct"]["high"]
        if "exclamation_rate_pct" in ranges
        else base_ling["punctuation"]["exclamation_rate_pct"] + 20.0
    )
    if script_ling["punctuation"]["exclamation_breaths"] >= 2 and script_ling["punctuation"]["exclamation_rate_pct"] > exclam_ceiling:
        observations.append({
            "kind": "exclamations",
            "matched": [],
            "message_zh": "这篇有 %.1f%% 的气口带感叹号，你的非商单素材里是 %.1f%%。" % (
                script_ling["punctuation"]["exclamation_rate_pct"], base_ling["punctuation"]["exclamation_rate_pct"],
            ),
            "message_en": "%.1f%% of breaths in this script end with an exclamation mark, compared with %.1f%% in confirmed non-sponsored material." % (
                script_ling["punctuation"]["exclamation_rate_pct"], base_ling["punctuation"]["exclamation_rate_pct"],
            ),
        })

    return {
        "ready": True,
        "reason": None,
        "needed": needed,
        "reference": ref_facts,
        "baseline": base_ling,
        "reference_ranges": ranges or None,
        "script": script_ling,
        "voicematch": vm,
        "comparisons": comparisons,
        "observations": observations,
        "delivery": delivery_ref,
        "wordlist_note": WORDLIST_NOTE,
    }
