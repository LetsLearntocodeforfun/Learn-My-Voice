"""Trend keywords: reading what she pastes in, and scoring it against her own history.

No trend data is fetched from any platform. Keywords come from her (copied
from the app or a keyword tool) or from an AI that looked them up on the web,
which is stored as unverified together with its source link.

Scoring is deterministic:

    trend       = strength * freshness                              0..1
      strength  = log(1 + heat) / log(1 + highest heat)   when she entered a heat number
      freshness = 0.5 ** (days since last seen / HALF_LIFE_DAYS)
    performance = 0.5 + shrunk / 4, clamped                          0..1; 0.5 = like her usual video
      shrunk    = average log2(interactions / her median) over her on-topic
                  videos, each capped at +-LOG_CAP, then averaged together with
                  PRIOR_VIDEOS ordinary videos. Working in logs with a cap means
                  one viral video cannot dominate.
    score       = w_trend * trend + w_mine * performance             shown as 0..100

The constants and weights below are judgement calls. They have not been tuned
against real outcomes, because no outcome data exists yet.
"""
from __future__ import annotations

import math
import re
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

from . import corpus, guardrails, textstats
from .store import MAX_KEYWORD_CHARS, clean_keyword, keyword_key

HALF_LIFE_DAYS = 14.0    # a trend counts half as much two weeks after it was last seen
PRIOR_VIDEOS = 2.0       # her on-topic videos are averaged with this many "ordinary" ones
LOG_CAP = 3.0            # one video counts as at most 8x (or 1/8) of her median
RELEVANT_AT = 0.6        # share of a keyword's pieces a video must contain to count as on-topic
STALE_BELOW = 0.25       # freshness under this is shown as possibly out of date (after 28 days)
MAX_KEYWORDS_PER_PASTE = 200
WEIGHTS = {"trend": (0.7, 0.3), "balanced": (0.5, 0.5), "mine": (0.3, 0.7)}  # (trend, her own results)

_UNITS = {"万": 10_000, "w": 10_000, "k": 1_000, "亿": 100_000_000}
_EXPLICIT_SEPARATORS = "\t,，;；|｜"
_TRAILING_HEAT = re.compile(
    r"^(.+?)(\s*[\t,，;；|｜]\s*|\s+)(\d[\d,]*(?:\.\d+)?)\s*(万|亿|[wWkK])?\+?\s*$")
_BULLET = re.compile(r"^\s*(?:\d{1,3}\s*[.、)）:：](?!\d)\s*|[-•·*]\s+)")
_CJK_RUN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]+")
_LATIN = re.compile(r"[A-Za-z][A-Za-z'\-]*|\d+")


# ---- reading pasted keywords ------------------------------------------------
def _heat(number: str, unit: Optional[str]) -> Optional[int]:
    value = float(number.replace(",", "")) * _UNITS.get((unit or "").lower(), 1)
    return int(round(value)) if 0 < value < 1e13 else None


def parse_keywords(text: str) -> Tuple[List[Dict[str, Any]], int]:
    """Turn pasted lines into ``[{"keyword", "heat"}]`` plus a count of lines skipped.

    One keyword per line. An optional heat number may follow after a comma or
    tab (``秋冬护肤, 125000``), or after a space when it is clearly a count
    (``秋冬护肤 12.5万``, ``秋冬护肤 12,500``); ``iPhone 15`` stays a keyword.
    List numbering and a leading # are removed.
    """
    items: Dict[str, Dict[str, Any]] = {}
    skipped = 0
    for raw in (text or "").splitlines():
        line = _BULLET.sub("", raw).strip()
        if not line:
            continue
        heat = None
        match = _TRAILING_HEAT.match(line)
        if match:
            value = _heat(match.group(3), match.group(4))
            explicit = any(ch in match.group(2) for ch in _EXPLICIT_SEPARATORS)
            if explicit or match.group(4) or (value or 0) >= 1000:
                line, heat = match.group(1), value
        keyword = clean_keyword(line)
        key = keyword_key(keyword)
        if not key or len(keyword) > MAX_KEYWORD_CHARS or (key not in items and len(items) >= MAX_KEYWORDS_PER_PASTE):
            skipped += 1
            continue
        if heat is None and key in items:
            heat = items[key]["heat"]
        items[key] = {"keyword": keyword, "heat": heat}
    return list(items.values()), skipped


# ---- matching a keyword to her videos ---------------------------------------
class _Hay:
    """A video's title and script, prepared for keyword matching."""

    __slots__ = ("compact", "words")

    def __init__(self, sample: Dict[str, Any]) -> None:
        text = ("%s\n%s" % (sample.get("title") or "", sample.get("text") or "")).replace("\u2019", "'").lower()
        self.compact = re.sub(r"\s+", "", text)
        self.words = set(_LATIN.findall(text))


def keyword_pieces(keyword: str) -> List[str]:
    """Chinese runs become overlapping two-character pieces; English words and numbers stay whole."""
    keyword = (keyword or "").replace("\u2019", "'").lower()
    pieces: List[str] = []
    for run in _CJK_RUN.findall(keyword):
        pieces += [run] if len(run) == 1 else [run[i:i + 2] for i in range(len(run) - 1)]
    return pieces + _LATIN.findall(keyword)


def relevance(keyword: str, hay: _Hay) -> float:
    """0..1: 1.0 when the keyword appears as written, else the share of its pieces present.

    English words must match whole words, so "gym" does not match "gymnastics".
    """
    key = keyword_key(keyword)
    if key and not key.isascii() and key in hay.compact:
        return 1.0
    pieces = keyword_pieces(keyword)
    if not pieces:
        return 0.0
    hits = sum(1 for p in pieces if (p in hay.words if p.isascii() else p in hay.compact))
    return hits / len(pieces)


def is_on_topic(sample: Dict[str, Any], keywords: Sequence[str]) -> bool:
    hay = _Hay(sample)
    return any(relevance(k, hay) >= RELEVANT_AT for k in keywords)


# ---- scoring ----------------------------------------------------------------
def _median(sorted_values: List[float]) -> Optional[float]:
    n = len(sorted_values)
    if not n:
        return None
    mid = n // 2
    return sorted_values[mid] if n % 2 else (sorted_values[mid - 1] + sorted_values[mid]) / 2


def _log_ratio(total: float, baseline: float) -> float:
    if total <= 0:
        return -LOG_CAP
    return max(-LOG_CAP, min(LOG_CAP, math.log2(total / baseline)))


def _flags(keyword: str, dna: Dict[str, Any], lexicon: Dict[str, Any]) -> List[str]:
    """"voice" if the keyword itself is something she never says; "compliance" if it is risky ad wording."""
    script = {"script_lines": [{"line_no": 1, "spoken_script_zh": keyword}]}
    flags = set()
    for finding in guardrails.lint_script(script, dna, lexicon=lexicon):
        if finding.severity != guardrails.ERROR:
            continue
        if finding.category == guardrails.COMPLIANCE_RISK:
            flags.add("compliance")
        elif finding.category in (guardrails.GENERIC_XHS_SLOP, guardrails.BOOKISH_MANDARIN, guardrails.TRANSLATIONESE):
            flags.add("voice")
    return sorted(flags)


def score_trends(trend_rows: List[Dict[str, Any]], samples: List[Dict[str, Any]], dna: Optional[Dict[str, Any]] = None,
                 weight: str = "balanced", now: Optional[float] = None) -> Dict[str, Any]:
    """Rank trend keywords by how hot they are and how her own videos on the topic performed.

    Only non-sponsored videos count as her evidence. Keywords that clash with
    her voice profile stay in the list but are marked ``usable: False``.
    """
    now = time.time() if now is None else now
    weight = weight if weight in WEIGHTS else "balanced"
    w_trend, w_mine = WEIGHTS[weight]
    hays = [(s, _Hay(s), corpus.interactions(s)) for s in samples if not s.get("is_sponsored")]
    totals = sorted(float(total) for _, _, total in hays if total is not None)
    baseline = _median(totals)
    if not baseline and totals:
        baseline = sum(totals) / len(totals)
    baseline = baseline or None

    heats = [t["heat"] for t in trend_rows if t.get("heat")]
    top_heat = max(heats) if heats else 0

    def strength_of(row: Dict[str, Any]) -> Optional[float]:
        return math.log1p(row["heat"]) / math.log1p(top_heat) if (row.get("heat") and top_heat) else None

    known = sorted(s for s in (strength_of(t) for t in trend_rows) if s is not None)
    default_strength = _median(known) if known else 1.0
    lexicon = guardrails.load_lexicon() if dna else None

    scored = []
    for row in trend_rows:
        keyword = row["keyword"]
        strength = strength_of(row)
        heat_known = strength is not None
        age_days = max(0.0, (now - (row.get("updated_at") or now)) / 86400.0)
        freshness = 0.5 ** (age_days / HALF_LIFE_DAYS)
        trend = (strength if heat_known else default_strength) * freshness

        matched = [(relevance(keyword, hay), sample, total) for sample, hay, total in hays]
        matched = [m for m in matched if m[0] >= RELEVANT_AT]
        numbered = [(rel, total) for rel, _, total in matched if total is not None]
        lift_raw, shrunk = None, 0.0
        if baseline and numbered:
            mass = sum(rel for rel, _ in numbered)
            mean_log = sum(rel * _log_ratio(total, baseline) for rel, total in numbered) / mass
            lift_raw = 2 ** mean_log
            shrunk = mass * mean_log / (mass + PRIOR_VIDEOS)
        performance = min(1.0, max(0.0, 0.5 + shrunk / 4.0))
        score = w_trend * trend + w_mine * performance

        matched.sort(key=lambda m: (m[0], m[2] if m[2] is not None else -1), reverse=True)
        flags = _flags(keyword, dna, lexicon) if dna else []
        scored.append((score, {
            "id": row.get("id"), "keyword": keyword, "heat": row.get("heat"), "heat_known": heat_known,
            "source": row.get("source") or "manual", "source_url": row.get("source_url") or "",
            "note_zh": row.get("note_zh") or "", "note_en": row.get("note_en") or "",
            "updated_at": row.get("updated_at"), "age_days": round(age_days, 1),
            "freshness": round(freshness, 3), "stale": freshness < STALE_BELOW, "trend": round(trend, 3),
            "her_videos": len(matched), "her_videos_with_numbers": len(numbered),
            "evidence_titles": [sample["title"] for _, sample, _ in matched[:3]],
            "lift_raw": round(lift_raw, 2) if lift_raw is not None else None, "lift": round(2 ** shrunk, 2),
            "performance": round(performance, 3), "score": int(round(score * 100)),
            "flags": flags, "usable": "voice" not in flags,
        }))
    scored.sort(key=lambda pair: (-pair[0], pair[1]["keyword"]))
    return {
        "trends": [item for _, item in scored], "weight": weight,
        "weights": {"trend": w_trend, "mine": w_mine},
        "baseline_interactions": baseline, "videos_with_numbers": len(totals),
        "half_life_days": HALF_LIFE_DAYS, "prior_videos": PRIOR_VIDEOS,
    }


def payload(store: Any, now: Optional[float] = None) -> Dict[str, Any]:
    """Scored trends for the active library, voice profile and weighting preference."""
    dna, _ = store.load_dna()
    weight = store.load_config().get("trend_weight") or "balanced"
    return score_trends(store.list_trends(), store.list_samples(), dna, weight, now)


# ---- did a script use its keywords? -----------------------------------------
def coverage(keywords: Sequence[str], lines: Any, post: Any) -> List[Dict[str, Any]]:
    """Where each target keyword appears as written: spoken lines, title options, caption, hashtags."""
    post = post if isinstance(post, dict) else {}
    spoken = keyword_key(" ".join(
        textstats.strip_cues(str(line.get("spoken_script_zh", "")))
        for line in (lines if isinstance(lines, list) else [])
        if isinstance(line, dict) and str(line.get("audio_mode", "")).strip().upper() != "LIVE_CLIP"))
    options = post.get("title_options") if isinstance(post.get("title_options"), list) else []
    titles = keyword_key(" ".join(str(o.get("title_zh", "")) for o in options if isinstance(o, dict)))
    caption = keyword_key(str(post.get("caption_body_zh") or ""))
    tags = post.get("hashtags_zh") if isinstance(post.get("hashtags_zh"), list) else []
    hashtags = keyword_key(" ".join(str(tag) for tag in tags).replace("#", " ").replace("\uff03", " "))
    out = []
    for keyword in keywords:
        key = keyword_key(keyword)
        found = {"spoken": key in spoken, "title": key in titles, "caption": key in caption, "hashtags": key in hashtags}
        out.append(dict(found, keyword=keyword, covered=any(found.values())))
    return out
