"""RedNote cover, title & first-3s packaging lab, plus pre-filming readiness gate.

All checks here are deterministic layout width, word-list / never-phrase, and
local corpus comparisons. Nothing here predicts or guarantees Xiaohongshu
(RedNote) algorithm reach, click-through, or legal compliance.

Compliance and never-phrase checks run exclusively through ``checks.lint_payload``
so the creator's effective word list (switched-off rules + own phrases) and
``never_phrases`` are honoured with full source citations in one place.
"""
from __future__ import annotations

import json
import math
import re
import unicodedata
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

from . import checks, corpus, creator, guardrails, hooks, llm, pipeline, sponsored, textstats
from .store import MODE_SPONSORED, Store, keyword_key, normalize_keywords, normalize_lines

STAGE11 = "11_packaging_hook_lab.md"

TITLE_SAFE_UNITS = 20.0
TITLE_WRAP_UNITS = 28.0
COVER_TEXT_MAX_UNITS = 10.0
COVER_SUB_MAX_UNITS = 12.0
OPENING_HOOK_MAX_UNITS = 16
OPENING_HOOK_MAX_SEC = 4.0

MAX_TITLE_OPTIONS = 10
MAX_COVER_OPTIONS = 8
MAX_PINNED_OPTIONS = 8
MAX_HASHTAGS = 12
MAX_HOOK_TWEAKS = 4

MAX_TITLE_CHARS = 120
MAX_COVER_CHARS = 80
MAX_BODY_CHARS = 4000
MAX_PINNED_CHARS = 500
MAX_TAG_CHARS = 60
MAX_LOGIC_CHARS = 240

HONESTY_ZH = (
    "说明：以下字宽截断、词表与禁说词核对及历史样本对比均为本地确定性规则计算，"
    "不代表合规结论，也不预测小红书算法流量或点击表现。"
)
HONESTY_EN = (
    "Note: Display-width truncation, word-list / never-say checks, and past-note comparisons are "
    "deterministic local calculations—not legal advice and not a prediction of RedNote algorithm reach."
)

_PACKAGING_WRAPPERS = ("packaging", "rednote_post_package", "post_package", "post", "result", "data")
_BRACKET_TRANSLATE = str.maketrans({
    "[": "（", "]": "）",
    "［": "（", "］": "）",
    "【": "（", "】": "）",
})
_PATTERN_LABELS_ZH = {
    "question": "提问式",
    "number": "具体数字/量词",
    "time": "时间/场景词",
    "turn": "转折反差",
    "viewer": "直接对话读者",
    "plain": "平实直叙",
}
_PATTERN_LABELS_EN = {
    "question": "question",
    "number": "concrete number",
    "time": "time/place anchor",
    "turn": "unexpected turn",
    "viewer": "direct address",
    "plain": "plain statement",
}


class PackagingError(Exception):
    """A refused packaging request with bilingual messages and HTTP status."""

    def __init__(self, code: str, message: str, message_zh: str = "", status: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.message_zh = message_zh or message
        self.status = status


def _clean_str(value: Any, max_len: int) -> str:
    if not isinstance(value, str):
        return ""
    cleaned = "".join(ch for ch in value if ch in ("\n", "\t") or (ord(ch) >= 32 and ord(ch) != 127))
    return cleaned.strip()[:max_len]


def _char_units(ch: str) -> float:
    if ch in ("\r", "\n"):
        return 0.0
    cat = unicodedata.category(ch)
    if cat in ("Cf", "Cc", "Mn", "Me"):
        return 0.0
    code = ord(ch)
    if code < 128:
        return 0.55
    eaw = unicodedata.east_asian_width(ch)
    if eaw in ("W", "F") or code >= 0x2000 or (0x3400 <= code <= 0x9FFF):
        return 1.0
    return 0.55


def display_units(text: Any) -> float:
    """Calculate mobile feed display width: CJK/fullwidth = 1.0 unit, ASCII = 0.55 unit."""
    if not isinstance(text, str) or not text:
        return 0.0
    total = sum(_char_units(ch) for ch in text.replace("\r\n", " ").replace("\n", " "))
    return round(total, 2)


def truncate_for_feed(text: Any, max_units: float = TITLE_WRAP_UNITS) -> Tuple[str, bool]:
    """Return ``(preview_text, was_truncated)`` simulating a 2-column RedNote feed card."""
    if not isinstance(text, str) or not text.strip():
        return "", False
    flat = " ".join(text.strip().splitlines())
    used = 0.0
    out: List[str] = []
    for ch in flat:
        w = _char_units(ch)
        if used + w > max_units and out:
            return "".join(out).rstrip() + "…", True
        out.append(ch)
        used += w
    return "".join(out), False


def classify_title_width(text: Any) -> Dict[str, Any]:
    """Classify a title against RedNote composer (20 units) and 2-column feed (~28 units) bounds."""
    cleaned = _clean_str(text, MAX_TITLE_CHARS)
    units = display_units(cleaned)
    preview_zh, truncated = truncate_for_feed(cleaned, TITLE_WRAP_UNITS)
    if not cleaned:
        return {
            "units": 0.0,
            "status": "empty",
            "badge_zh": "未填写",
            "badge_en": "Empty",
            "preview_zh": "",
            "truncated": False,
        }
    if units <= TITLE_SAFE_UNITS:
        return {
            "units": units,
            "status": "ok",
            "badge_zh": "单行安全",
            "badge_en": "1-line safe",
            "preview_zh": preview_zh,
            "truncated": False,
        }
    if units <= TITLE_WRAP_UNITS:
        return {
            "units": units,
            "status": "wrap",
            "badge_zh": "双行折行（超20字上限预警）",
            "badge_en": "2-line wrap (>20 cap)",
            "preview_zh": preview_zh,
            "truncated": False,
        }
    return {
        "units": units,
        "status": "truncate",
        "badge_zh": "双列信息流截断",
        "badge_en": "Truncates in feed",
        "preview_zh": preview_zh,
        "truncated": True,
    }


def classify_cover_width(text: Any, limit: float = COVER_TEXT_MAX_UNITS) -> Dict[str, Any]:
    """Classify 3:4 mobile thumbnail callout density."""
    cleaned = _clean_str(text, MAX_COVER_CHARS)
    units = display_units(cleaned)
    if not cleaned:
        return {
            "units": 0.0,
            "status": "empty",
            "badge_zh": "未填写",
            "badge_en": "Empty",
        }
    if units <= limit:
        return {
            "units": units,
            "status": "ok",
            "badge_zh": "3:4 缩略图易读",
            "badge_en": "3:4 thumbnail clear",
        }
    return {
        "units": units,
        "status": "dense",
        "badge_zh": "3:4 缩略图字数偏密（建议 <=%g）" % limit,
        "badge_en": "Too dense for 3:4 mobile thumbnail (<=%g ideal)" % limit,
    }


def _normalize_hashtag(raw: Any) -> str:
    if not isinstance(raw, str):
        return ""
    cleaned = _clean_str(raw, MAX_TAG_CHARS).replace("\uff03", "#")
    cleaned = re.sub(r"\s+", "", cleaned).lstrip("#")
    return ("#" + cleaned) if cleaned else ""


def _normalize_title_options(raw_list: Any) -> List[Dict[str, str]]:
    if not isinstance(raw_list, list):
        return []
    out: List[Dict[str, str]] = []
    for item in raw_list:
        if len(out) >= MAX_TITLE_OPTIONS:
            break
        if isinstance(item, str):
            zh = _clean_str(item, MAX_TITLE_CHARS)
            if zh:
                out.append({"title_zh": zh, "title_en": "", "angle": "", "hook_logic": ""})
        elif isinstance(item, dict):
            zh = _clean_str(item.get("title_zh") or item.get("zh") or item.get("title") or item.get("text_zh"), MAX_TITLE_CHARS)
            if zh:
                out.append({
                    "title_zh": zh,
                    "title_en": _clean_str(item.get("title_en") or item.get("en") or item.get("text_en"), MAX_TITLE_CHARS * 2),
                    "angle": _clean_str(item.get("angle"), 40),
                    "hook_logic": _clean_str(item.get("hook_logic") or item.get("why_zh") or item.get("why_en"), MAX_LOGIC_CHARS),
                })
    return out


def _normalize_cover_options(raw_list: Any, legacy_list: Any = None) -> List[Dict[str, str]]:
    source = raw_list if isinstance(raw_list, list) and raw_list else (legacy_list if isinstance(legacy_list, list) else [])
    out: List[Dict[str, str]] = []
    for item in source:
        if len(out) >= MAX_COVER_OPTIONS:
            break
        if isinstance(item, str):
            zh = _clean_str(item, MAX_COVER_CHARS)
            if zh:
                out.append({"cover_text_zh": zh, "cover_sub_zh": "", "cover_text_en": "", "cover_sub_en": ""})
        elif isinstance(item, dict):
            zh = _clean_str(item.get("cover_text_zh") or item.get("headline_zh") or item.get("text_zh") or item.get("title_zh"), MAX_COVER_CHARS)
            sub = _clean_str(item.get("cover_sub_zh") or item.get("subtitle_zh") or item.get("sub_zh"), MAX_COVER_CHARS)
            if zh or sub:
                out.append({
                    "cover_text_zh": zh,
                    "cover_sub_zh": sub,
                    "cover_text_en": _clean_str(item.get("cover_text_en") or item.get("text_en"), MAX_COVER_CHARS * 2),
                    "cover_sub_en": _clean_str(item.get("cover_sub_en") or item.get("sub_en"), MAX_COVER_CHARS * 2),
                })
    return out


def _normalize_pinned_options(raw_list: Any) -> List[Dict[str, str]]:
    if not isinstance(raw_list, list):
        return []
    out: List[Dict[str, str]] = []
    for item in raw_list:
        if len(out) >= MAX_PINNED_OPTIONS:
            break
        if isinstance(item, str):
            zh = _clean_str(item, MAX_PINNED_CHARS)
            if zh:
                out.append({"text_zh": zh, "text_en": ""})
        elif isinstance(item, dict):
            zh = _clean_str(item.get("text_zh") or item.get("pinned_comment_zh") or item.get("comment_zh") or item.get("zh"), MAX_PINNED_CHARS)
            if zh:
                out.append({
                    "text_zh": zh,
                    "text_en": _clean_str(item.get("text_en") or item.get("en"), MAX_PINNED_CHARS * 2),
                })
    return out


def _normalize_hook_tweaks(raw_list: Any) -> List[Dict[str, str]]:
    if not isinstance(raw_list, list):
        return []
    out: List[Dict[str, str]] = []
    for item in raw_list:
        if len(out) >= MAX_HOOK_TWEAKS:
            break
        if isinstance(item, str):
            zh = _clean_str(item, 200)
            if zh:
                out.append({"spoken_script_zh": zh, "english_partner_translation": "", "why_zh": "", "why_en": ""})
        elif isinstance(item, dict):
            zh = _clean_str(item.get("spoken_script_zh") or item.get("line_zh") or item.get("text_zh"), 200)
            if zh:
                out.append({
                    "spoken_script_zh": zh,
                    "english_partner_translation": _clean_str(item.get("english_partner_translation") or item.get("text_en"), 300),
                    "why_zh": _clean_str(item.get("why_zh"), MAX_LOGIC_CHARS),
                    "why_en": _clean_str(item.get("why_en"), MAX_LOGIC_CHARS),
                })
    return out


def _normalize_hashtags_list(raw_tags: Any) -> List[str]:
    items: List[Any] = []
    if isinstance(raw_tags, str):
        items = [part for part in re.split(r"[\s,，]+", raw_tags) if part]
    elif isinstance(raw_tags, list):
        items = raw_tags
    out: List[str] = []
    seen = set()
    for raw in items:
        if len(out) >= MAX_HASHTAGS:
            break
        tag = _normalize_hashtag(raw)
        key = keyword_key(tag)
        if tag and key and key not in seen:
            seen.add(key)
            out.append(tag)
    return out


def normalize_post(raw_post: Any) -> Dict[str, Any]:
    """Return a backward-compatible normalized ``post`` dictionary with V3 packaging keys."""
    src = dict(raw_post) if isinstance(raw_post, dict) else {}
    title_options = _normalize_title_options(src.get("title_options"))
    raw_idx = src.get("selected_title_idx", 0)
    if isinstance(raw_idx, bool) or not isinstance(raw_idx, int):
        idx = 0
    else:
        idx = raw_idx
    if not title_options or idx < 0 or idx >= len(title_options):
        idx = 0

    cover_options = _normalize_cover_options(src.get("cover_options"), src.get("cover_thumbnail_text_options_zh"))
    if "cover_text_zh" in src:
        cover_text_zh = _clean_str(src.get("cover_text_zh"), MAX_COVER_CHARS)
    elif cover_options:
        cover_text_zh = cover_options[0]["cover_text_zh"]
    else:
        cover_text_zh = ""

    if "cover_sub_zh" in src:
        cover_sub_zh = _clean_str(src.get("cover_sub_zh"), MAX_COVER_CHARS)
    elif "cover_text_zh" not in src and cover_options:
        cover_sub_zh = cover_options[0]["cover_sub_zh"]
    else:
        cover_sub_zh = ""

    if "body_zh" in src and isinstance(src.get("body_zh"), str):
        body_zh = _clean_str(src.get("body_zh"), MAX_BODY_CHARS)
    else:
        body_zh = _clean_str(src.get("caption_body_zh"), MAX_BODY_CHARS)

    if "body_en" in src and isinstance(src.get("body_en"), str):
        body_en = _clean_str(src.get("body_en"), MAX_BODY_CHARS)
    else:
        body_en = _clean_str(src.get("caption_body_en"), MAX_BODY_CHARS)

    if "hashtags" in src and src.get("hashtags") is not None:
        hashtags = _normalize_hashtags_list(src.get("hashtags"))
    else:
        hashtags = _normalize_hashtags_list(src.get("hashtags_zh"))

    pinned_options = _normalize_pinned_options(src.get("pinned_options"))
    pinned_comment_zh = _clean_str(src.get("pinned_comment_zh"), MAX_PINNED_CHARS)
    hook_tweaks = _normalize_hook_tweaks(src.get("opening_hook_tweaks"))

    out = dict(src)
    out.update({
        "title_options": title_options,
        "selected_title_idx": idx,
        "cover_text_zh": cover_text_zh,
        "cover_sub_zh": cover_sub_zh,
        "cover_options": cover_options,
        "body_zh": body_zh,
        "body_en": body_en,
        "hashtags": hashtags,
        "pinned_comment_zh": pinned_comment_zh,
        "pinned_options": pinned_options,
        "opening_hook_tweaks": hook_tweaks,
        # Keep legacy Stage 4 keys synchronized so trends.coverage & sponsored._Post stay aligned:
        "caption_body_zh": body_zh,
        "caption_body_en": body_en,
        "hashtags_zh": list(hashtags),
    })
    if cover_options and "cover_thumbnail_text_options_zh" not in out:
        out["cover_thumbnail_text_options_zh"] = [c["cover_text_zh"] for c in cover_options if c["cover_text_zh"]]
    return out


def has_packaging_content(raw_post: Any) -> bool:
    """True only when ``raw_post`` has at least one non-empty user-facing packaging field."""
    if not isinstance(raw_post, dict) or not raw_post:
        return False
    norm = normalize_post(raw_post)
    return bool(
        norm["title_options"]
        or norm["cover_text_zh"]
        or norm["cover_sub_zh"]
        or norm["cover_options"]
        or norm["body_zh"]
        or norm["hashtags"]
        or norm["pinned_comment_zh"]
        or norm["pinned_options"]
    )


def active_title_of(post: Dict[str, Any]) -> str:
    options = post.get("title_options") if isinstance(post.get("title_options"), list) else []
    if not options:
        return ""
    idx = post.get("selected_title_idx", 0)
    if isinstance(idx, bool) or not isinstance(idx, int) or idx < 0 or idx >= len(options):
        idx = 0
    item = options[idx]
    return str(item.get("title_zh") or "") if isinstance(item, dict) else (item if isinstance(item, str) else "")


def _lint_packaging_fields(store: Store, field_texts: Sequence[Tuple[str, str]]) -> Tuple[Dict[str, List[Dict[str, Any]]], Dict[str, Any]]:
    """Run ``checks.lint_payload`` once across synthetic lines for all packaging fields.

    Brackets like ``【...】`` are mapped to ``（...）`` in the synthetic check text so RedNote bracketed
    title tags are never skipped as spoken ``[delivery cues]``. A ``LIVE_CLIP`` digit separator sits
    between fields so unrelated options never trigger cross-line ban joins.
    """
    synthetic: List[Dict[str, Any]] = []
    key_by_lineno: Dict[int, str] = {}
    next_no = 1
    for field_key, raw_text in field_texts:
        if not raw_text or not raw_text.strip():
            continue
        if synthetic:
            synthetic.append({"line_no": -1, "audio_mode": "LIVE_CLIP", "spoken_script_zh": "000000"})
        safe_text = raw_text.translate(_BRACKET_TRANSLATE)
        synthetic.append({"line_no": next_no, "audio_mode": "VOICEOVER", "spoken_script_zh": safe_text})
        key_by_lineno[next_no] = field_key
        next_no += 1

    lint_out = checks.lint_payload(store, synthetic, None)
    grouped: Dict[str, List[Dict[str, Any]]] = {field_key: [] for field_key, _ in field_texts}
    ignored_categories = {guardrails.CADENCE_TOO_LONG, guardrails.DURATION_OFF_BUDGET}
    for finding in lint_out.get("findings") or []:
        line_no = finding.get("line_no")
        cat = finding.get("category")
        if line_no not in key_by_lineno or cat in ignored_categories:
            continue
        field_key = key_by_lineno[line_no]
        item = dict(finding, field=field_key)
        grouped.setdefault(field_key, []).append(item)
    return grouped, lint_out.get("wordlist") or {}


def _median(nums: List[float]) -> Optional[float]:
    if not nums:
        return None
    ordered = sorted(nums)
    mid = len(ordered) // 2
    return float(ordered[mid]) if len(ordered) % 2 else (ordered[mid - 1] + ordered[mid]) / 2.0


def _describe_patterns(patterns: Sequence[str]) -> Tuple[str, str]:
    zh = "、".join(_PATTERN_LABELS_ZH.get(p, p) for p in patterns[:3]) or "平实直叙"
    en = ", ".join(_PATTERN_LABELS_EN.get(p, p) for p in patterns[:3]) or "plain statement"
    return zh, en


def corpus_insights(store: Store, active_title_zh: str = "") -> Dict[str, Any]:
    """Compute grounded historical packaging & hook observations from the creator's own organic corpus."""
    samples = [s for s in store.list_samples() if not s.get("is_sponsored")]
    snaps = store.list_metric_snapshots()
    latest_ctr_by_sample: Dict[str, float] = {}
    for snap in snaps:
        sid = snap.get("sample_id")
        ctr = snap.get("click_rate_pct")
        if isinstance(sid, str) and isinstance(ctr, (int, float)) and not isinstance(ctr, bool) and ctr > 0:
            latest_ctr_by_sample[sid] = float(ctr)

    hook_lib = hooks.library(samples)
    top_openers = hooks.best(samples, limit=5)

    observations: List[Dict[str, str]] = []
    ctr_notes = [
        (s, latest_ctr_by_sample[s["id"]])
        for s in samples
        if s["id"] in latest_ctr_by_sample and str(s.get("title") or "").strip()
    ]
    ctr_summary: Optional[Dict[str, Any]] = None
    if ctr_notes:
        med_ctr = _median([c for _, c in ctr_notes]) or 0.0
        high_ctr = [(s, c) for s, c in ctr_notes if c >= med_ctr]
        avg_units = round(sum(display_units(s["title"]) for s, _ in high_ctr) / len(high_ctr), 1)
        pattern_counts: Dict[str, int] = {}
        for s, _ in high_ctr:
            pats = set(hooks.describe(s["title"]).get("patterns") or []) | set(hooks.describe(s.get("text") or "").get("patterns") or [])
            for p in pats:
                if p != "plain":
                    pattern_counts[p] = pattern_counts.get(p, 0) + 1
        top_pats = [p for p, _ in sorted(pattern_counts.items(), key=lambda kv: (-kv[1], kv[0]))] or ["plain"]
        pats_zh, pats_en = _describe_patterns(top_pats)
        top_ctr_val = round(max(c for _, c in high_ctr), 1)
        ctr_summary = {
            "notes_with_ctr": len(ctr_notes),
            "median_ctr_pct": round(med_ctr, 2),
            "top_ctr_pct": top_ctr_val,
            "high_ctr_avg_title_units": avg_units,
            "top_patterns": top_pats[:3],
        }
        observations.append({
            "basis": "ctr",
            "zh": "你的 %d 篇有封面点击率记录的非商单笔记中，高点击率笔记标题平均约 %g 显示单位（中位点击率 %g%%），常见切入方式：%s。"
                  % (len(ctr_notes), avg_units, round(med_ctr, 1), pats_zh),
            "en": "Across %d non-sponsored notes with recorded cover CTR, higher-CTR titles average %g display units (median CTR %g%%), often opening with: %s."
                  % (len(ctr_notes), avg_units, round(med_ctr, 1), pats_en),
        })

    save_notes = [
        (s, 100.0 * float(s["collects"]) / float(s["views"]))
        for s in samples
        if s.get("views") and s["views"] > 0 and s.get("collects") is not None and str(s.get("title") or "").strip()
    ]
    save_summary: Optional[Dict[str, Any]] = None
    if save_notes:
        med_save = _median([r for _, r in save_notes]) or 0.0
        high_save = [(s, r) for s, r in save_notes if r >= med_save]
        avg_save_units = round(sum(display_units(s["title"]) for s, _ in high_save) / len(high_save), 1)
        save_summary = {
            "notes_with_saves": len(save_notes),
            "median_save_rate_pct": round(med_save, 2),
            "high_save_avg_title_units": avg_save_units,
        }
        observations.append({
            "basis": "saves",
            "zh": "你的 %d 篇有收藏率数据的非商单笔记中，高收藏笔记（中位收藏率 %g%%）标题平均约 %g 显示单位。"
                  % (len(save_notes), round(med_save, 1), avg_save_units),
            "en": "Across %d non-sponsored notes with save-rate numbers (median %g%%), higher-save titles average %g display units."
                  % (len(save_notes), round(med_save, 1), avg_save_units),
        })
    elif hook_lib.get("videos_with_numbers", 0) > 0 and hook_lib.get("openers"):
        usual = hook_lib.get("usual_interactions") or 0
        winners = [o for o in hook_lib["openers"] if (o.get("interactions") or 0) >= usual and o.get("title")]
        if winners:
            avg_win_units = round(sum(display_units(w["title"]) for w in winners) / len(winners), 1)
            pats_zh, pats_en = _describe_patterns(winners[0].get("patterns") or ["plain"])
            observations.append({
                "basis": "interactions",
                "zh": "互动表现高于你平时中位数的 %d 条非商单视频，标题平均约 %g 显示单位，代表开场类型：%s。"
                      % (len(winners), avg_win_units, pats_zh),
                "en": "Your %d above-median non-sponsored videos average %g display units in their titles, led by %s openings."
                      % (len(winners), avg_win_units, pats_en),
            })

    if active_title_zh.strip() and (ctr_summary or save_summary):
        ref_units = (ctr_summary["high_ctr_avg_title_units"] if ctr_summary else save_summary["high_save_avg_title_units"])  # type: ignore[index]
        cur_units = display_units(active_title_zh)
        diff = round(cur_units - ref_units, 1)
        if abs(diff) >= 5.0:
            observations.append({
                "basis": "comparison",
                "zh": "当前选定标题为 %g 显示单位，比你过往高表现笔记平均（%g 单位）%s。"
                      % (cur_units, ref_units, ("长 %g 单位" % diff) if diff > 0 else ("短 %g 单位" % abs(diff))),
                "en": "Selected title is %g display units—%g units %s than your top-note average (%g units)."
                      % (cur_units, abs(diff), "longer" if diff > 0 else "shorter", ref_units),
            })

    return {
        "organic_notes": len(samples),
        "ctr": ctr_summary,
        "saves": save_summary,
        "top_openers": top_openers,
        "hook_patterns": (hook_lib.get("patterns") or [])[:4],
        "observations": observations,
    }


def evaluate_packaging(store: Store, script: Dict[str, Any], post: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Deterministically evaluate RedNote display width, compliance/voice rules, target keywords, and corpus insights."""
    norm = normalize_post(post if post is not None else script.get("post"))
    title_options = norm["title_options"]
    selected_idx = norm["selected_title_idx"]
    active_title = active_title_of(norm)

    field_texts: List[Tuple[str, str]] = [
        ("cover_text", norm["cover_text_zh"]),
        ("cover_sub", norm["cover_sub_zh"]),
        ("body", norm["body_zh"]),
        ("pinned_comment", norm["pinned_comment_zh"]),
    ]
    for i, opt in enumerate(title_options):
        field_texts.append(("title_option:%d" % i, opt["title_zh"]))
    for i, copt in enumerate(norm["cover_options"]):
        joined_cover = " / ".join(p for p in (copt["cover_text_zh"], copt["cover_sub_zh"]) if p)
        field_texts.append(("cover_option:%d" % i, joined_cover))
    for i, popt in enumerate(norm["pinned_options"]):
        field_texts.append(("pinned_option:%d" % i, popt["text_zh"]))

    grouped_findings, wordlist_notice = _lint_packaging_fields(store, field_texts)

    title_checks: List[Dict[str, Any]] = []
    for i, opt in enumerate(title_options):
        width = classify_title_width(opt["title_zh"])
        f_list = grouped_findings.get("title_option:%d" % i, [])
        non_tell = [f for f in f_list if f.get("category") != guardrails.AI_TELL]
        title_checks.append({
            "index": i,
            "selected": i == selected_idx,
            "title_zh": opt["title_zh"],
            "title_en": opt["title_en"],
            "angle": opt["angle"],
            "hook_logic": opt["hook_logic"],
            "display_units": width["units"],
            "status": width["status"],
            "badge_zh": width["badge_zh"],
            "badge_en": width["badge_en"],
            "preview_zh": width["preview_zh"],
            "truncated": width["truncated"],
            "findings": f_list,
            "error_count": sum(1 for f in non_tell if f.get("severity") == guardrails.ERROR),
            "review_count": sum(1 for f in non_tell if f.get("severity") == guardrails.REVIEW),
        })

    active_title_width = classify_title_width(active_title)
    cover_head_width = classify_cover_width(norm["cover_text_zh"], COVER_TEXT_MAX_UNITS)
    cover_sub_width = classify_cover_width(norm["cover_sub_zh"], COVER_SUB_MAX_UNITS)
    cover_findings = grouped_findings.get("cover_text", []) + grouped_findings.get("cover_sub", [])

    cover_option_checks: List[Dict[str, Any]] = []
    for i, copt in enumerate(norm["cover_options"]):
        hw = classify_cover_width(copt["cover_text_zh"], COVER_TEXT_MAX_UNITS)
        sw = classify_cover_width(copt["cover_sub_zh"], COVER_SUB_MAX_UNITS)
        cover_option_checks.append({
            "index": i,
            "cover_text_zh": copt["cover_text_zh"],
            "cover_sub_zh": copt["cover_sub_zh"],
            "cover_text_en": copt["cover_text_en"],
            "cover_sub_en": copt["cover_sub_en"],
            "text_units": hw["units"],
            "sub_units": sw["units"],
            "text_status": hw["status"],
            "sub_status": sw["status"],
            "findings": grouped_findings.get("cover_option:%d" % i, []),
        })

    pinned_option_checks: List[Dict[str, Any]] = []
    for i, popt in enumerate(norm["pinned_options"]):
        pinned_option_checks.append({
            "index": i,
            "text_zh": popt["text_zh"],
            "text_en": popt["text_en"],
            "units": display_units(popt["text_zh"]),
            "findings": grouped_findings.get("pinned_option:%d" % i, []),
        })

    active_title_findings = [
        dict(f, field="title")
        for f in (grouped_findings.get("title_option:%d" % selected_idx, []) if title_options else [])
    ]
    active_findings: List[Dict[str, Any]] = (
        active_title_findings
        + grouped_findings.get("cover_text", [])
        + grouped_findings.get("cover_sub", [])
        + grouped_findings.get("body", [])
        + grouped_findings.get("pinned_comment", [])
    )
    all_findings: List[Dict[str, Any]] = []
    for _, items in grouped_findings.items():
        all_findings.extend(items)

    compliance_findings = [f for f in all_findings if f.get("category") == guardrails.COMPLIANCE_RISK]
    never_or_voice_findings = [
        f for f in all_findings
        if f.get("category") != guardrails.AI_TELL and f.get("category") != guardrails.COMPLIANCE_RISK
    ]

    keywords = normalize_keywords(script.get("keywords"))
    title_k = keyword_key(active_title)
    cover_k = keyword_key(norm["cover_text_zh"] + " " + norm["cover_sub_zh"])
    caption_k = keyword_key(norm["body_zh"])
    tags_k = keyword_key(" ".join(norm["hashtags"]).replace("#", " "))
    kw_items: List[Dict[str, Any]] = []
    for kw in keywords:
        k = keyword_key(kw)
        in_title = bool(k and k in title_k)
        in_cover = bool(k and k in cover_k)
        in_caption = bool(k and k in caption_k)
        in_hashtags = bool(k and k in tags_k)
        kw_items.append({
            "keyword": kw,
            "in_title": in_title,
            "in_cover": in_cover,
            "in_caption": in_caption,
            "in_hashtags": in_hashtags,
            "in_packaging": in_title or in_cover or in_caption,
            "covered": in_title or in_cover or in_caption or in_hashtags,
        })
    any_kw_in_packaging = (not keywords) or any(item["in_packaging"] for item in kw_items)

    who = store.load_creator()
    handle = (who.get("channel") or who.get("name") or "") if isinstance(who, dict) else ""

    return {
        "deterministic_only": True,
        "honesty_zh": HONESTY_ZH,
        "honesty_en": HONESTY_EN,
        "author_handle": handle or "创作者",
        "active_title": {
            "title_zh": active_title,
            "selected_title_idx": selected_idx,
            **active_title_width,
            "findings": active_title_findings,
        },
        "title_checks": title_checks,
        "cover_check": {
            "cover_text_zh": norm["cover_text_zh"],
            "cover_sub_zh": norm["cover_sub_zh"],
            "text_units": cover_head_width["units"],
            "text_status": cover_head_width["status"],
            "text_badge_zh": cover_head_width["badge_zh"],
            "text_badge_en": cover_head_width["badge_en"],
            "sub_units": cover_sub_width["units"],
            "sub_status": cover_sub_width["status"],
            "sub_badge_zh": cover_sub_width["badge_zh"],
            "sub_badge_en": cover_sub_width["badge_en"],
            "findings": cover_findings,
        },
        "cover_option_checks": cover_option_checks,
        "body_check": {
            "units": display_units(norm["body_zh"]),
            "findings": grouped_findings.get("body", []),
        },
        "pinned_check": {
            "pinned_comment_zh": norm["pinned_comment_zh"],
            "units": display_units(norm["pinned_comment_zh"]),
            "findings": grouped_findings.get("pinned_comment", []),
        },
        "pinned_option_checks": pinned_option_checks,
        "active_findings": active_findings,
        "all_findings": all_findings,
        "compliance_findings": compliance_findings,
        "voice_findings": never_or_voice_findings,
        "keyword_check": {
            "keywords": keywords,
            "items": kw_items,
            "any_in_packaging": any_kw_in_packaging,
            "missing_in_packaging": bool(keywords) and not any_kw_in_packaging,
        },
        "corpus_insights": corpus_insights(store, active_title),
        "wordlist": wordlist_notice,
    }


def _first_breath_stats(lines: Sequence[Dict[str, Any]], cpm: Optional[float]) -> Dict[str, Any]:
    rate = float(cpm) if isinstance(cpm, (int, float)) and not isinstance(cpm, bool) and cpm > 0 else 240.0
    for line in lines:
        if not isinstance(line, dict):
            continue
        raw = str(line.get("spoken_script_zh") or "")
        clauses = textstats.split_clauses(raw, punctuation_breaks=True)
        if not clauses:
            continue
        first_clause = clauses[0]
        units = textstats.spoken_units(first_clause)
        if units <= 0:
            continue
        sec = round(units / rate * 60.0, 1)
        return {
            "has_speech": True,
            "first_breath": first_clause,
            "units": units,
            "estimated_sec": sec,
            "ok": units <= OPENING_HOOK_MAX_UNITS and sec <= OPENING_HOOK_MAX_SEC,
        }
    return {"has_speech": False, "first_breath": "", "units": 0, "estimated_sec": 0.0, "ok": False}


def readiness_report(
    store: Store,
    script: Dict[str, Any],
    *,
    pkg_report: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Return the non-blocking Pre-Filming Readiness Gate checklist for ``script``."""
    lines = script.get("lines") if isinstance(script.get("lines"), list) else (
        script.get("script_lines") if isinstance(script.get("script_lines"), list) else []
    )
    target_sec = script.get("target_sec")
    norm_post = normalize_post(script.get("post"))
    report = pkg_report if pkg_report is not None else evaluate_packaging(store, script, norm_post)

    script_lint = checks.lint_payload(store, lines, target_sec, script.get("keywords"), norm_post)
    stats = script_lint.get("stats") or {}
    script_findings = script_lint.get("findings") or []

    # 1. Duration fit
    units = int(stats.get("units") or 0)
    budget = stats.get("budget")
    est_sec = stats.get("estimated_sec")
    off_budget = any(f.get("category") == guardrails.DURATION_OFF_BUDGET for f in script_findings)
    dur_ok = units > 0 and not off_budget
    if units == 0:
        dur_zh = "脚本尚未填写口播内容"
        dur_en = "Script has no spoken lines yet"
    elif dur_ok:
        dur_zh = "预计约 %ss / 目标 %ss（%d 字）" % (est_sec or 0, target_sec or 0, units)
        dur_en = "~%ss against %ss target (%d units)" % (est_sec or 0, target_sec or 0, units)
    else:
        dur_zh = "当前 %d 字（约 %ss），偏离目标 %ss 预算（约 %s 字）" % (units, est_sec or 0, target_sec or 0, budget or "?")
        dur_en = "%d units (~%ss) outside %ss target window (~%s units)" % (units, est_sec or 0, target_sec or 0, budget or "?")

    items: List[Dict[str, Any]] = [{
        "id": "duration",
        "ok": dur_ok,
        "status": "pass" if dur_ok else "warn",
        "label_zh": "时长匹配",
        "label_en": "Duration fit",
        "detail_zh": dur_zh,
        "detail_en": dur_en,
    }]

    # 2. Guardrail & word-list clean (script + active packaging)
    hard_script = [
        f for f in script_findings
        if f.get("line_no") is not None
        and f.get("category") not in (guardrails.CADENCE_TOO_LONG, guardrails.AI_TELL)
        and (f.get("severity") == guardrails.ERROR or f.get("category") == guardrails.COMPLIANCE_RISK)
    ]
    hard_pkg = [
        f for f in report.get("active_findings", [])
        if f.get("category") != guardrails.AI_TELL
        and (f.get("severity") == guardrails.ERROR or f.get("category") == guardrails.COMPLIANCE_RISK)
    ]
    total_flags = len(hard_script) + len(hard_pkg)
    comp_ok = total_flags == 0
    if comp_ok:
        comp_zh = "正文与当前包装均未命中词表风险或禁说词"
        comp_en = "0 compliance or never-say hits in script & active packaging"
    else:
        matched_preview = "、".join(
            dict.fromkeys(str(f.get("matched") or "") for f in (hard_script + hard_pkg) if f.get("matched"))
        )[:48]
        comp_zh = "脚本 %d 处 · 包装 %d 处需核对（%s）" % (len(hard_script), len(hard_pkg), matched_preview)
        comp_en = "%d script + %d packaging flag(s) (%s)" % (len(hard_script), len(hard_pkg), matched_preview)

    items.append({
        "id": "compliance",
        "ok": comp_ok,
        "status": "pass" if comp_ok else "warn",
        "label_zh": "词表与禁说词",
        "label_en": "Word list & voice clean",
        "detail_zh": comp_zh,
        "detail_en": comp_en,
    })

    # 3. Opening 3-second hook
    hook_info = _first_breath_stats(lines, stats.get("speaking_rate_cpm"))
    hook_ok = hook_info["ok"]
    if not hook_info["has_speech"]:
        hook_zh = "第 1 句还没有可念的开场词"
        hook_en = "Line 1 has no spoken hook yet"
    elif hook_ok:
        hook_zh = "首气口 %d 字（约 %gs）：「%s」" % (
            hook_info["units"], hook_info["estimated_sec"], hook_info["first_breath"][:18]
        )
        hook_en = "First breath %d units (~%gs)" % (hook_info["units"], hook_info["estimated_sec"])
    else:
        hook_zh = "首气口 %d 字（约 %gs），建议收紧到 <=%d 字 / 4s 内进场" % (
            hook_info["units"], hook_info["estimated_sec"], OPENING_HOOK_MAX_UNITS
        )
        hook_en = "First breath is %d units (~%gs); aim for <=%d units / ~4s" % (
            hook_info["units"], hook_info["estimated_sec"], OPENING_HOOK_MAX_UNITS
        )

    items.append({
        "id": "opening_hook",
        "ok": hook_ok,
        "status": "pass" if hook_ok else "warn",
        "label_zh": "前 3 秒开场",
        "label_en": "First-3s hook",
        "detail_zh": hook_zh,
        "detail_en": hook_en,
    })

    # 4. Packaging ready (active title + cover text selected and <= truncation limit)
    active_title_info = report["active_title"]
    cover_info = report["cover_check"]
    has_title = active_title_info["status"] != "empty"
    title_fits = active_title_info["status"] == "ok"
    has_cover = cover_info["text_status"] != "empty"
    cover_fits = cover_info["text_status"] == "ok" and cover_info["sub_status"] in ("ok", "empty")
    pkg_ok = has_title and title_fits and has_cover and cover_fits

    if pkg_ok:
        pkg_zh = "标题 %g 单位 · 封面 %g 单位，单行与 3:4 缩略图就绪" % (
            active_title_info["units"], cover_info["text_units"]
        )
        pkg_en = "Title %g u & cover %g u within feed limits" % (
            active_title_info["units"], cover_info["text_units"]
        )
    else:
        reasons_zh: List[str] = []
        reasons_en: List[str] = []
        if not has_title:
            reasons_zh.append("未选定标题")
            reasons_en.append("no active title")
        elif not title_fits:
            reasons_zh.append("标题 %g 单位（>20）" % active_title_info["units"])
            reasons_en.append("title %g u (>20)" % active_title_info["units"])
        if not has_cover:
            reasons_zh.append("未填 3:4 封面主标题")
            reasons_en.append("no cover headline")
        elif not cover_fits:
            reasons_zh.append("封面字数偏密")
            reasons_en.append("cover text too dense")
        pkg_zh = " · ".join(reasons_zh)
        pkg_en = "; ".join(reasons_en)

    items.append({
        "id": "packaging",
        "ok": pkg_ok,
        "status": "pass" if pkg_ok else "warn",
        "label_zh": "封面与标题就绪",
        "label_en": "Packaging ready",
        "detail_zh": pkg_zh,
        "detail_en": pkg_en,
    })

    # 5. Sponsored brief obligations (included ONLY when script["brief_id"] is set or script is linked to a brief)
    sp_check = sponsored.check_script_brief(store, script, script.get("brief_id"))
    if sp_check.get("sponsored"):
        if not sp_check.get("brief"):
            sp_ok = False
            sp_zh = "关联的品牌 Brief 已删除，请人工核对合作要求"
            sp_en = "Linked brand brief was deleted; verify obligations manually"
        else:
            counts = sp_check.get("counts") or {}
            not_said = int(counts.get("not_said") or 0)
            forbidden = int(counts.get("forbidden") or 0)
            tags_missing = int(counts.get("tags_missing") or 0)
            to_check = int(counts.get("to_check") or 0)
            sp_ok = (not_said + forbidden + tags_missing + to_check) == 0
            if sp_ok:
                sp_zh = "品牌 Brief 必提项、禁用词、话题与自查项已全部完成"
                sp_en = "All brand brief obligations satisfied"
            else:
                parts_zh: List[str] = []
                parts_en: List[str] = []
                if not_said:
                    parts_zh.append("待提口播 %d" % not_said)
                    parts_en.append("%d must-say missing" % not_said)
                if forbidden:
                    parts_zh.append("违禁词命中 %d" % forbidden)
                    parts_en.append("%d forbidden hit(s)" % forbidden)
                if tags_missing:
                    parts_zh.append("缺必带话题/@ %d" % tags_missing)
                    parts_en.append("%d required tag(s) missing" % tags_missing)
                if to_check:
                    parts_zh.append("待勾选自查 %d" % to_check)
                    parts_en.append("%d manual check(s) left" % to_check)
                sp_zh = " · ".join(parts_zh)
                sp_en = ", ".join(parts_en)
        items.append({
            "id": "sponsored_brief",
            "ok": sp_ok,
            "status": "pass" if sp_ok else "warn",
            "label_zh": "商单 Brief 核对",
            "label_en": "Sponsored brief",
            "detail_zh": sp_zh,
            "detail_en": sp_en,
        })

    passed = sum(1 for item in items if item["ok"])
    return {
        "ready": passed == len(items),
        "passed_count": passed,
        "total_count": len(items),
        "items": items,
        "honesty_zh": HONESTY_ZH,
        "honesty_en": HONESTY_EN,
    }


def _require_script(store: Store, script_or_id: Union[str, Dict[str, Any]]) -> Dict[str, Any]:
    if isinstance(script_or_id, dict):
        return script_or_id
    sid = script_or_id.strip() if isinstance(script_or_id, str) else ""
    script = store.get_script(sid) if sid else None
    if script is None:
        raise PackagingError("not_found", "Script not found.", "未找到这篇脚本。", 404)
    return script


def packaging_payload(store: Store, script_or_id: Union[str, Dict[str, Any]]) -> Dict[str, Any]:
    """Return ``{post, report, readiness, updated_at}`` for ``GET /api/scripts/<id>/packaging``."""
    script = _require_script(store, script_or_id)
    norm_post = normalize_post(script.get("post"))
    report = evaluate_packaging(store, script, norm_post)
    readiness = readiness_report(store, script, pkg_report=report)
    return {
        "post": norm_post,
        "report": report,
        "readiness": readiness,
        "updated_at": script.get("updated_at"),
    }


def build_packaging_prompt(store: Store, script_id: Any) -> Dict[str, Any]:
    """Build Stage 11 ``(system, user, prompt, brief_id)`` for API or Copy & Paste packaging generation."""
    script = _require_script(store, script_id)
    who = store.load_creator()
    stored_dna, _ = store.load_dna()
    dna = creator.apply_never(stored_dna, who)
    insights = corpus_insights(store)

    sp_constraints: Optional[Dict[str, Any]] = None
    sp_check = sponsored.check_script_brief(store, script, script.get("brief_id"))
    if sp_check.get("sponsored") and sp_check.get("brief"):
        sp_constraints = {
            "brief_title": sp_check["brief"].get("title", ""),
            "must_not_say": [
                {"text_zh": m.get("text", ""), "phrases": m.get("phrases", [])}
                for m in (sp_check.get("must_not_say") or [])
            ],
            "required_tags_and_mentions": [
                {"kind": t.get("kind", ""), "phrases": [w.get("phrase", "") for w in (t.get("wanted") or [])]}
                for t in (sp_check.get("tags") or [])
            ],
        }

    lines = script.get("lines") if isinstance(script.get("lines"), list) else []
    compact_lines = [
        {
            "line_no": idx + 1,
            "beat_type": str(line.get("beat_type") or ""),
            "audio_mode": str(line.get("audio_mode") or "VOICEOVER"),
            "spoken_script_zh": str(line.get("spoken_script_zh") or "")[:500],
            "onscreen_flower_text_zh": str(line.get("onscreen_flower_text_zh") or "")[:120],
        }
        for idx, line in enumerate(lines[:80])
        if isinstance(line, dict) and str(line.get("spoken_script_zh") or "").strip()
    ]
    first_breath = _first_breath_stats(lines, None)

    script_context = {
        "script_id": script.get("id"),
        "working_title": str(script.get("title") or "")[:200],
        "mode": script.get("mode") or "NORMAL_ORGANIC",
        "video_format": script.get("video_format") or "VLOG_VOICEOVER",
        "target_duration_sec": script.get("target_sec") or 60,
        "topic_notes": str(script.get("notes") or "")[:2000],
        "target_keywords": normalize_keywords(script.get("keywords")),
        "line_1_first_breath": {
            "text": first_breath["first_breath"],
            "spoken_units": first_breath["units"],
            "within_3s_budget": first_breath["ok"],
        },
        "script_lines": compact_lines,
        "sponsored_constraints": sp_constraints,
    }

    system, user = pipeline._blueprint(STAGE11, {
        "creator_profile_json": pipeline._dump(creator.prompt_block(who)),
        "voice_dna_json": pipeline._dump(dna),
        "historical_insights_json": pipeline._dump({
            "ctr_summary": insights["ctr"],
            "save_summary": insights["saves"],
            "top_openers": insights["top_openers"],
            "hook_patterns": insights["hook_patterns"],
        }),
        "script_context_json": pipeline._dump(script_context),
    })
    brief_id = store.save_brief(
        "packaging",
        {"script_id": script["id"], "keywords": script_context["target_keywords"]},
        fingerprint=str(script["id"]),
    )
    return {
        "system": system,
        "user": user,
        "prompt": pipeline.single_prompt(system, user),
        "brief_id": brief_id,
    }


def _unwrap_packaging_reply(raw_or_dict: Any) -> Dict[str, Any]:
    if isinstance(raw_or_dict, str):
        if not raw_or_dict.strip():
            raise PackagingError("reply_required", "Paste the JSON reply first.", "请先粘贴模型返回的 JSON 回复。", 400)
        parsed: Any = llm.parse_manual_reply(raw_or_dict, "packaging")
    elif isinstance(raw_or_dict, dict):
        parsed = raw_or_dict
    else:
        raise llm.LLMError("Model reply must be a JSON object", "bad_json", "回复必须是 JSON 对象格式。")

    unwrapped = parsed
    for _ in range(2):
        if not isinstance(unwrapped, dict):
            break
        if any(k in unwrapped for k in (
            "title_options", "cover_options", "cover_text_zh", "body_zh", "caption_body_zh",
            "hashtags", "hashtags_zh", "pinned_options", "pinned_comment_zh", "opening_hook_tweaks",
        )):
            break
        moved = False
        for wrapper in _PACKAGING_WRAPPERS:
            inner = unwrapped.get(wrapper)
            if isinstance(inner, dict):
                unwrapped = inner
                moved = True
                break
        if not moved:
            break
    if not isinstance(unwrapped, dict):
        raise llm.LLMError("Model reply must be a JSON object", "bad_json", "回复必须是 JSON 对象格式。")
    return unwrapped


def _merge_packaging(
    existing_raw: Any,
    incoming: Dict[str, Any],
    *,
    overwrite: bool = False,
) -> Tuple[Dict[str, Any], List[str]]:
    """Merge incoming AI/MCP packaging into ``existing_raw``.

    When ``overwrite=False`` (default), creator-customized ``body_zh``, ``cover_text_zh`` / ``cover_sub_zh``,
    and ``pinned_comment_zh`` are preserved if already non-empty, while new options are appended cleanly.
    Returns ``(merged_post, preserved_fields)``.
    """
    existing = normalize_post(existing_raw)
    preserved: List[str] = []

    new_titles = _normalize_title_options(incoming.get("title_options"))
    new_covers = _normalize_cover_options(incoming.get("cover_options"), incoming.get("cover_thumbnail_text_options_zh"))
    new_pinned = _normalize_pinned_options(incoming.get("pinned_options"))
    new_tweaks = _normalize_hook_tweaks(incoming.get("opening_hook_tweaks"))
    raw_tags = incoming.get("hashtags") if incoming.get("hashtags") is not None else incoming.get("hashtags_zh")
    new_tags = _normalize_hashtags_list(raw_tags) if raw_tags is not None else []

    in_cover_text = _clean_str(incoming.get("cover_text_zh"), MAX_COVER_CHARS) if "cover_text_zh" in incoming else ""
    in_cover_sub = _clean_str(incoming.get("cover_sub_zh"), MAX_COVER_CHARS) if "cover_sub_zh" in incoming else ""
    in_body_zh = _clean_str(
        incoming.get("body_zh") if isinstance(incoming.get("body_zh"), str) else incoming.get("caption_body_zh"),
        MAX_BODY_CHARS,
    )
    in_body_en = _clean_str(
        incoming.get("body_en") if isinstance(incoming.get("body_en"), str) else incoming.get("caption_body_en"),
        MAX_BODY_CHARS,
    )
    in_pinned_text = _clean_str(incoming.get("pinned_comment_zh"), MAX_PINNED_CHARS) if "pinned_comment_zh" in incoming else ""

    has_any = bool(
        new_titles or new_covers or new_pinned or new_tweaks or new_tags
        or in_cover_text or in_cover_sub or in_body_zh or in_pinned_text
    )
    if not has_any:
        raise llm.LLMError(
            "Model reply contained no packaging options",
            "empty_result",
            "回复里没有可用的标题、封面或文案内容。",
        )

    merged = dict(existing)

    # Title options merge
    if overwrite or not existing["title_options"]:
        if new_titles:
            merged["title_options"] = new_titles
            req_idx = incoming.get("selected_title_idx")
            merged["selected_title_idx"] = (
                req_idx if isinstance(req_idx, int) and not isinstance(req_idx, bool) and 0 <= req_idx < len(new_titles) else 0
            )
    elif new_titles:
        seen_titles = {keyword_key(t["title_zh"]) for t in existing["title_options"] if t.get("title_zh")}
        combined_titles = list(existing["title_options"])
        for t in new_titles:
            if len(combined_titles) >= MAX_TITLE_OPTIONS:
                break
            k = keyword_key(t["title_zh"])
            if k and k not in seen_titles:
                seen_titles.add(k)
                combined_titles.append(t)
        merged["title_options"] = combined_titles

    if overwrite and isinstance(incoming.get("selected_title_idx"), int) and not isinstance(incoming.get("selected_title_idx"), bool):
        idx = incoming["selected_title_idx"]
        if 0 <= idx < len(merged["title_options"]):
            merged["selected_title_idx"] = idx

    # Cover options & active cover text
    if overwrite and new_covers:
        merged["cover_options"] = new_covers
    elif new_covers:
        seen_covers = {keyword_key(c["cover_text_zh"] + "|" + c["cover_sub_zh"]) for c in existing["cover_options"]}
        combined_covers = list(existing["cover_options"])
        for c in new_covers:
            if len(combined_covers) >= MAX_COVER_OPTIONS:
                break
            k = keyword_key(c["cover_text_zh"] + "|" + c["cover_sub_zh"])
            if k and k not in seen_covers:
                seen_covers.add(k)
                combined_covers.append(c)
        merged["cover_options"] = combined_covers

    candidate_cover_text = in_cover_text or (new_covers[0]["cover_text_zh"] if new_covers else "")
    candidate_cover_sub = in_cover_sub if ("cover_text_zh" in incoming or "cover_sub_zh" in incoming) else (
        new_covers[0]["cover_sub_zh"] if new_covers else ""
    )
    if existing["cover_text_zh"] and not overwrite:
        if in_cover_text and in_cover_text != existing["cover_text_zh"]:
            preserved.append("cover_text_zh")
    elif candidate_cover_text or (overwrite and "cover_text_zh" in incoming):
        merged["cover_text_zh"] = candidate_cover_text
        if candidate_cover_sub or overwrite or not existing["cover_sub_zh"]:
            merged["cover_sub_zh"] = candidate_cover_sub

    if overwrite and "cover_sub_zh" in incoming:
        merged["cover_sub_zh"] = in_cover_sub

    # Body / caption merge (never silently overwrite non-empty creator-edited body_zh unless overwrite=True)
    if existing["body_zh"] and not overwrite:
        if in_body_zh and in_body_zh != existing["body_zh"]:
            preserved.append("body_zh")
    elif in_body_zh or (overwrite and ("body_zh" in incoming or "caption_body_zh" in incoming)):
        merged["body_zh"] = in_body_zh
        if in_body_en or overwrite:
            merged["body_en"] = in_body_en

    # Hashtags merge
    if overwrite and raw_tags is not None:
        merged["hashtags"] = new_tags
    elif new_tags:
        merged["hashtags"] = _normalize_hashtags_list(list(existing["hashtags"]) + new_tags)

    # Pinned comment options & active pinned comment
    if overwrite and new_pinned:
        merged["pinned_options"] = new_pinned
    elif new_pinned:
        seen_pin = {keyword_key(p["text_zh"]) for p in existing["pinned_options"]}
        combined_pin = list(existing["pinned_options"])
        for p in new_pinned:
            if len(combined_pin) >= MAX_PINNED_OPTIONS:
                break
            k = keyword_key(p["text_zh"])
            if k and k not in seen_pin:
                seen_pin.add(k)
                combined_pin.append(p)
        merged["pinned_options"] = combined_pin

    candidate_pinned = in_pinned_text or (new_pinned[0]["text_zh"] if new_pinned else "")
    if existing["pinned_comment_zh"] and not overwrite:
        if in_pinned_text and in_pinned_text != existing["pinned_comment_zh"]:
            preserved.append("pinned_comment_zh")
    elif candidate_pinned or (overwrite and "pinned_comment_zh" in incoming):
        merged["pinned_comment_zh"] = candidate_pinned

    if new_tweaks or (overwrite and "opening_hook_tweaks" in incoming):
        merged["opening_hook_tweaks"] = new_tweaks

    return normalize_post(merged), preserved


def apply_packaging_reply(
    store: Store,
    script_id: Any,
    raw_or_dict: Any,
    *,
    overwrite: bool = False,
) -> Dict[str, Any]:
    """Parse, validate, and merge a Stage 11 packaging reply into ``scripts.post_json``."""
    script = _require_script(store, script_id)
    incoming = _unwrap_packaging_reply(raw_or_dict)
    merged, preserved = _merge_packaging(script.get("post"), incoming, overwrite=overwrite)
    updated = store.update_script(script["id"], {"post": merged})
    if updated is None:
        raise PackagingError("not_found", "Script not found.", "未找到这篇脚本。", 404)
    out = packaging_payload(store, updated)
    out["preserved_fields"] = preserved
    return out


def generate_packaging(
    store: Store,
    script_id: Any,
    transport: Optional[llm.Transport] = None,
    *,
    overwrite: bool = False,
) -> Dict[str, Any]:
    """Generate Stage 11 packaging via the configured AI provider and merge into ``post_json``."""
    built = build_packaging_prompt(store, script_id)
    reply = pipeline.complete(
        store,
        built["system"],
        built["user"],
        transport,
        temperature=llm.STAGE_TEMPERATURES["packaging"],
    )
    return apply_packaging_reply(store, script_id, reply, overwrite=overwrite)


def update_packaging(
    store: Store,
    script_id: Any,
    patch: Any,
    *,
    base_updated_at: Any = None,
) -> Dict[str, Any]:
    """Apply direct creator edits or option selections to ``scripts.post_json``."""
    if not isinstance(patch, dict):
        raise PackagingError("bad_packaging", "Packaging update must be a JSON object.", "包装修改内容必须是 JSON 对象。", 400)
    script = _require_script(store, script_id)
    current = normalize_post(script.get("post"))

    if "title_options" in patch:
        current["title_options"] = _normalize_title_options(patch.get("title_options"))
    if "add_title_zh" in patch:
        added = _clean_str(patch.get("add_title_zh"), MAX_TITLE_CHARS)
        if added and len(current["title_options"]) < MAX_TITLE_OPTIONS:
            current["title_options"].append({
                "title_zh": added,
                "title_en": _clean_str(patch.get("add_title_en"), MAX_TITLE_CHARS * 2),
                "angle": _clean_str(patch.get("add_title_angle"), 40),
                "hook_logic": "",
            })
            current["selected_title_idx"] = len(current["title_options"]) - 1

    if "selected_title_idx" in patch:
        idx = patch.get("selected_title_idx")
        if isinstance(idx, bool) or not isinstance(idx, int):
            raise PackagingError("bad_title_index", "selected_title_idx must be an integer.", "selected_title_idx 必须是整数。", 400)
        if current["title_options"] and not (0 <= idx < len(current["title_options"])):
            raise PackagingError("bad_title_index", "selected_title_idx is out of range.", "所选标题序号超出范围。", 400)
        current["selected_title_idx"] = idx if current["title_options"] else 0

    if "cover_options" in patch:
        current["cover_options"] = _normalize_cover_options(patch.get("cover_options"))
    if "select_cover_idx" in patch:
        cidx = patch.get("select_cover_idx")
        if not isinstance(cidx, bool) and isinstance(cidx, int) and 0 <= cidx < len(current["cover_options"]):
            chosen_cover = current["cover_options"][cidx]
            current["cover_text_zh"] = chosen_cover["cover_text_zh"]
            current["cover_sub_zh"] = chosen_cover["cover_sub_zh"]
    if "cover_text_zh" in patch:
        current["cover_text_zh"] = _clean_str(patch.get("cover_text_zh"), MAX_COVER_CHARS)
    if "cover_sub_zh" in patch:
        current["cover_sub_zh"] = _clean_str(patch.get("cover_sub_zh"), MAX_COVER_CHARS)

    if "body_zh" in patch or "caption_body_zh" in patch:
        raw_body = patch.get("body_zh") if "body_zh" in patch else patch.get("caption_body_zh")
        current["body_zh"] = _clean_str(raw_body, MAX_BODY_CHARS)
    if "body_en" in patch or "caption_body_en" in patch:
        raw_en = patch.get("body_en") if "body_en" in patch else patch.get("caption_body_en")
        current["body_en"] = _clean_str(raw_en, MAX_BODY_CHARS)

    if "hashtags" in patch or "hashtags_zh" in patch:
        raw_tags = patch.get("hashtags") if "hashtags" in patch else patch.get("hashtags_zh")
        current["hashtags"] = _normalize_hashtags_list(raw_tags)

    if "pinned_options" in patch:
        current["pinned_options"] = _normalize_pinned_options(patch.get("pinned_options"))
    if "select_pinned_idx" in patch:
        pidx = patch.get("select_pinned_idx")
        if not isinstance(pidx, bool) and isinstance(pidx, int) and 0 <= pidx < len(current["pinned_options"]):
            current["pinned_comment_zh"] = current["pinned_options"][pidx]["text_zh"]
    if "pinned_comment_zh" in patch:
        current["pinned_comment_zh"] = _clean_str(patch.get("pinned_comment_zh"), MAX_PINNED_CHARS)

    normalized = normalize_post(current)
    changes: Dict[str, Any] = {"post": normalized}

    # Optional: creator clicks to apply a sharper opening hook tweak directly to line 1
    if "apply_hook_tweak_idx" in patch:
        hidx = patch.get("apply_hook_tweak_idx")
        tweaks = normalized.get("opening_hook_tweaks") or []
        if not isinstance(hidx, bool) and isinstance(hidx, int) and 0 <= hidx < len(tweaks):
            lines = [dict(l) for l in (script.get("lines") or []) if isinstance(l, dict)]
            chosen_tweak = tweaks[hidx]
            if not lines:
                lines = [{"line_no": 1, "audio_mode": "VOICEOVER", "spoken_script_zh": chosen_tweak["spoken_script_zh"]}]
            else:
                lines[0]["spoken_script_zh"] = chosen_tweak["spoken_script_zh"]
                if chosen_tweak.get("english_partner_translation"):
                    lines[0]["english_partner_translation"] = chosen_tweak["english_partner_translation"]
            changes["lines"] = normalize_lines(lines)

    if patch.get("sync_script_title") is True:
        chosen_title = active_title_of(normalized)
        if chosen_title:
            changes["title"] = chosen_title

    updated = store.update_script(script["id"], changes, base_updated_at=base_updated_at)
    if updated is None:
        raise PackagingError("not_found", "Script not found.", "未找到这篇脚本。", 404)
    out = packaging_payload(store, updated)
    if "lines" in changes:
        out["lines"] = updated["lines"]
    if "title" in changes:
        out["title"] = updated["title"]
    return out
