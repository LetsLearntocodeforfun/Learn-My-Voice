"""Dynamic RedNote (Xiaohongshu) view-count analytics, velocity, Creator Center import, and audience sentiment engine.

Capabilities (100% Python stdlib):
1. Mobile Share Snippet & URL Extractor: extracts clean ``xhslink.com`` / ``xiaohongshu.com`` URLs and title hints
   from pasted RedNote mobile share text.
2. Dynamic View Count & Velocity Engine: combines ``views``, ``interactions`` (likes + collects + comments),
   ``save_rate_pct`` (collects / views), ``comment_rate_pct``, and chronological ``metric_snapshots`` to compute
   daily view velocity (``views_per_day``), composite reach+resonance lift, and RedNote performance quadrants.
3. Creator Center Spreadsheet / Paste Batch Importer: reads ``.csv``, ``.tsv``, ``.txt`` or ``.xlsx`` post-analytics
   exports from Xiaohongshu Creator Center (创作服务平台) to batch-sync view counts and engagement snapshots.
4. Hybrid Audience Comment & Sentiment Analyzer:
   - Instant deterministic Chinese/English comment sentiment classifier + viewer-question detector (``求链接``,
     ``求教程``, ``适合...吗``, ``?``/``？``) that works offline with zero API key.
   - Optional deep LLM / copy-paste / MCP synthesis via ``prompts/09_sentiment_analyzer.md`` (``T=0.2``).
"""
from __future__ import annotations

import csv
import datetime
import io
import json
import math
import re
from typing import Any, Dict, List, Optional, Tuple

from . import corpus, creator, keyword_import, llm
from .store import ROOT, Store, _metric

STAGE9_PATH = ROOT / "prompts" / "09_sentiment_analyzer.md"

_URL_IN_TEXT = re.compile(r"(https?://[^\s<>\"'】\]）)]+)", re.I)
_SHARE_NOISE = re.compile(
    r"(复制后打开【小红书】查看笔记！?|打开小红书App查看精彩内容！?|小红书，标记我的生活|^\s*\d{1,3}\s+)"
)
_COUNT_TOKEN = re.compile(r"^\s*([0-9]+(?:\.[0-9]+)?)\s*([万亿wkWK]?)\s*$")
_COMMENT_META_TAIL = re.compile(
    r"(?:\s+(?:\d+\s*(?:分钟|小时|天|周|个月|年)前|昨天|前天|\d{2}-\d{2}|\d{4}-\d{2}-\d{2})"
    r"(?:\s+[\u4e00-\u9fff]{2,8})?(?:\s*(?:回复|赞\s*\d+|❤️\s*\d+))*\s*)$"
)
_TRAILING_LIKES = re.compile(r"(?:\s*(?:赞|❤️|👍|likes?:?)\s*(\d+)\s*)$", re.I)

POSITIVE_CUES = (
    "喜欢", "好看", "好美", "温柔", "舒服", "真实", "自然", "实用", "干货", "种草", "爱看", "太棒",
    "笑死", "好笑", "可爱", "甜", "治愈", "同感", "买了", "好用", "划算", "省心", "松弛", "宝藏",
    "绝了", "舒服了", "期待更新", "催更", "love", "helpful", "cozy", "great", "cute", "funny",
)
NEGATIVE_CUES = (
    "踩雷", "拔草", "不好用", "假滑", "搓泥", "闷痘", "过敏", "太贵", "智商税", "鸡肋", "广告",
    "硬广", "失望", "卡粉", "起球", "缩水", "翻车", "太沉", "不值", "难用", "太干", "太油",
    "disappointed", "expensive", "heavy", "broke out",
)
QUESTION_CUES = (
    "?", "？", "求链接", "求同款", "求牌子", "求货号", "求教程", "求分享", "求更新", "求下期",
    "请问", "想问", "问一下", "怎么买", "哪里买", "哪买的", "什么牌子", "哪个牌子", "多少钱",
    "重不重", "沉不沉", "干皮", "油皮", "敏感肌", "适合", "够不够", "会不会", "能不能", "可以吗",
    "尺码", "色号", "型号", "link", "where did you", "which brand", "how much",
)

TITLE_HEADERS = ("笔记标题", "标题", "作品名称", "视频标题", "笔记名称", "内容标题", "title", "note title", "post title", "video")
VIEWS_HEADERS = ("观看量", "播放量", "曝光量", "阅读量", "浏览量", "小眼睛", "views", "impressions", "plays", "reads")
LIKES_HEADERS = ("点赞量", "点赞数", "点赞", "赞", "likes", "like")
COLLECTS_HEADERS = ("收藏量", "收藏数", "收藏", "collects", "saves", "bookmarks", "favorites")
COMMENTS_HEADERS = ("评论量", "评论数", "评论", "comments", "replies")
DATE_HEADERS = ("发布时间", "发布日期", "日期", "统计日期", "时间", "posted_on", "date", "published")
LINK_HEADERS = ("笔记链接", "作品链接", "链接", "网址", "url", "link", "note_url")


class AnalyticsError(Exception):
    """A RedNote analytics import or sentiment request could not be completed."""

    def __init__(self, code: str, message: str, message_zh: str, status: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.message_zh = message_zh
        self.status = status


def extract_share_link(raw: Any) -> Dict[str, str]:
    """Extract an ``http(s)`` URL and optional title hint from a RedNote share snippet or bare link."""
    if not isinstance(raw, str):
        return {"url": "", "title_hint": ""}
    text = raw.strip()
    if not text:
        return {"url": "", "title_hint": ""}
    m = _URL_IN_TEXT.search(text)
    if not m:
        return {"url": "", "title_hint": ""}
    url = m.group(1).rstrip(".,;!?，。；！？")
    rem = (text[:m.start()] + " " + text[m.end():]).strip()
    rem = _SHARE_NOISE.sub(" ", rem)
    rem = re.sub(r"\s+", " ", rem).strip(" -|｜:：，,。")
    return {"url": url[:1000], "title_hint": rem[:120]}


def parse_human_count(raw: Any) -> Optional[int]:
    """Parse Chinese/English shorthand numbers like ``1.2万``, ``3.5w``, ``12,400``, ``8k``."""
    if raw is None or isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return _metric(raw)
    if isinstance(raw, float):
        return _metric(int(round(raw))) if math.isfinite(raw) and raw >= 0 else None
    s = str(raw).strip().replace(",", "").replace("，", "").replace("+", "")
    if not s or s in ("-", "--", "N/A", "无"):
        return None
    m = _COUNT_TOKEN.match(s)
    if not m:
        return None
    num = float(m.group(1))
    unit = m.group(2).lower()
    mult = 1
    if unit in ("万", "w"):
        mult = 10_000
    elif unit == "亿":
        mult = 100_000_000
    elif unit == "k":
        mult = 1_000
    return _metric(int(round(num * mult)))


def _parse_day(raw: Any) -> str:
    if not isinstance(raw, str):
        return ""
    s = raw.strip()
    m = re.search(r"([0-9]{4})[-/.年]([0-9]{1,2})[-/.月]([0-9]{1,2})", s)
    if not m:
        return ""
    try:
        d = datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        return d.strftime("%Y-%m-%d")
    except ValueError:
        return ""


def _median(nums: List[float]) -> Optional[float]:
    if not nums:
        return None
    s = sorted(nums)
    mid = len(s) // 2
    return float(s[mid]) if len(s) % 2 else (s[mid - 1] + s[mid]) / 2.0


def _days_between(d1: Optional[str], d2: Optional[str]) -> Optional[int]:
    if not d1 or not d2:
        return None
    try:
        a = datetime.date.fromisoformat(d1[:10])
        b = datetime.date.fromisoformat(d2[:10])
        return max(0, (b - a).days)
    except ValueError:
        return None


def sample_velocity(sample: Dict[str, Any], snapshots: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Compute view & interaction growth velocity from chronological ``metric_snapshots``."""
    ordered = [s for s in snapshots if isinstance(s, dict)]
    latest_url = ""
    for snap in reversed(ordered):
        if snap.get("note_url"):
            latest_url = str(snap["note_url"])
            break
    if not latest_url and sample.get("note_url"):
        latest_url = str(sample.get("note_url") or "")

    views_per_day: Optional[float] = None
    delta_views: Optional[int] = None
    span_days: Optional[int] = None

    if len(ordered) >= 2:
        first, last = ordered[0], ordered[-1]
        v0, v1 = first.get("views"), last.get("views")
        days = _days_between(first.get("recorded_on"), last.get("recorded_on"))
        if v0 is not None and v1 is not None:
            delta_views = int(v1) - int(v0)
            eff_days = max(1, days) if days is not None else 1
            span_days = eff_days
            views_per_day = round(delta_views / float(eff_days), 1)
    elif sample.get("views") is not None and sample.get("posted_on"):
        end_day = sample.get("metrics_on") or (ordered[-1].get("recorded_on") if ordered else None)
        days = _days_between(sample.get("posted_on"), end_day)
        if days is not None:
            eff_days = max(1, days)
            span_days = eff_days
            views_per_day = round(float(sample["views"]) / float(eff_days), 1)

    return {
        "snapshot_count": len(ordered),
        "latest_recorded_on": ordered[-1]["recorded_on"] if ordered else (sample.get("metrics_on") or None),
        "note_url": latest_url,
        "delta_views": delta_views,
        "span_days": span_days,
        "views_per_day": views_per_day,
        "history": [
            {
                "recorded_on": s.get("recorded_on"),
                "views": s.get("views"),
                "likes": s.get("likes"),
                "collects": s.get("collects"),
                "comments": s.get("comments"),
            }
            for s in ordered[-12:]
        ],
    }


def _quadrant(view_lift: Optional[float], save_rate: Optional[float], comment_rate: Optional[float],
              median_save_rate: Optional[float], interaction_lift: Optional[float]) -> str:
    if view_lift is None and interaction_lift is None:
        return "UNMEASURED"
    reach_high = (view_lift is not None and view_lift >= 1.25) or (view_lift is None and (interaction_lift or 0) >= 1.35)
    save_high = (
        (save_rate is not None and median_save_rate is not None and save_rate >= max(1.5, median_save_rate * 1.2))
        or (save_rate is not None and median_save_rate is None and save_rate >= 3.0)
        or (save_rate is None and (interaction_lift or 0) >= 1.35)
    )
    if reach_high and save_high:
        return "BREAKOUT_HIT"
    if not reach_high and save_high:
        return "HIGH_SAVE_GEM"
    if comment_rate is not None and comment_rate >= 0.8:
        return "HIGH_DISCUSSION"
    if reach_high and not save_high:
        return "BROAD_LOW_CONVERT"
    if (view_lift is not None and view_lift < 0.65) and (interaction_lift is None or interaction_lift < 0.65):
        return "UNDERPERFORMING"
    return "STEADY"


def analytics_overview(store: Store) -> Dict[str, Any]:
    """Compute dynamic RedNote view-count, conversion, velocity, and sentiment insights across the library."""
    samples = store.list_samples()
    all_snaps = store.list_metric_snapshots()
    snaps_by_sample: Dict[str, List[Dict[str, Any]]] = {}
    for s in all_snaps:
        snaps_by_sample.setdefault(s["sample_id"], []).append(s)
    sentiments = store.list_sample_sentiments()

    organic = [s for s in samples if not s.get("is_sponsored")]
    org_views = [float(s["views"]) for s in organic if s.get("views") is not None and s["views"] > 0]
    org_inters = [float(c) for s in organic for c in (corpus.interactions(s),) if c is not None]
    org_save_rates = [
        100.0 * float(s["collects"]) / float(s["views"])
        for s in organic
        if s.get("views") and s["views"] > 0 and s.get("collects") is not None
    ]

    median_views = _median(org_views)
    median_inters = _median(org_inters)
    median_save_rate = _median(org_save_rates)

    videos: List[Dict[str, Any]] = []
    top_questions: List[Dict[str, Any]] = []
    opportunities: List[Dict[str, Any]] = []

    for sample in samples:
        sid = sample["id"]
        views = sample.get("views")
        likes = sample.get("likes")
        collects = sample.get("collects")
        comments_n = sample.get("comments")
        inter = corpus.interactions(sample)

        save_rate = round(100.0 * collects / views, 2) if (views and views > 0 and collects is not None) else None
        comment_rate = round(100.0 * comments_n / views, 2) if (views and views > 0 and comments_n is not None) else None
        inter_rate = round(100.0 * inter / views, 2) if (views and views > 0 and inter is not None) else None

        v_lift = round(float(views) / median_views, 2) if (views is not None and median_views and median_views > 0) else None
        i_lift = round(float(inter) / median_inters, 2) if (inter is not None and median_inters and median_inters > 0) else None

        if v_lift is not None and i_lift is not None:
            comp_lift = round(0.45 * v_lift + 0.55 * i_lift, 2)
        elif i_lift is not None:
            comp_lift = i_lift
        else:
            comp_lift = v_lift

        vel = sample_velocity(sample, snaps_by_sample.get(sid, []))
        quad = _quadrant(v_lift, save_rate, comment_rate, median_save_rate, i_lift)
        sent_rec = sentiments.get(sid)
        sent_summary = sent_rec["summary"] if sent_rec else None

        if isinstance(sent_summary, dict):
            for q in (sent_summary.get("audience_questions") or [])[:4]:
                if isinstance(q, dict) and q.get("question_zh"):
                    top_questions.append({
                        "sample_id": sid,
                        "video_title": sample["title"],
                        "question_zh": str(q["question_zh"]),
                        "question_en": str(q.get("question_en") or q["question_zh"]),
                        "demand_count": int(q.get("demand_count") or 1),
                    })
            for angle in (sent_summary.get("followup_video_angles") or [])[:2]:
                if isinstance(angle, dict) and angle.get("title_zh"):
                    opportunities.append({
                        "kind": "sentiment_followup",
                        "source_sample_id": sid,
                        "source_title": sample["title"],
                        "title_zh": str(angle["title_zh"]),
                        "title_en": str(angle.get("title_en") or angle["title_zh"]),
                        "video_format": str(angle.get("video_format") or "VLOG_VOICEOVER"),
                        "target_sec": int(angle.get("target_duration_sec") or 60),
                        "rationale_zh": str(angle.get("rationale_zh") or ""),
                        "rationale_en": str(angle.get("rationale_en") or ""),
                        "notes": str(angle.get("script_notes_zh") or ""),
                    })

        if quad in ("BREAKOUT_HIT", "HIGH_SAVE_GEM") and not sample.get("is_sponsored"):
            opportunities.append({
                "kind": "high_resonance_sequel",
                "source_sample_id": sid,
                "source_title": sample["title"],
                "title_zh": "%s｜续集与真实后续分享" % sample["title"][:28],
                "title_en": "Follow-up & deeper dive on \"%s\"" % sample["title"][:36],
                "video_format": "VLOG_VOICEOVER",
                "target_sec": 60,
                "rationale_zh": (
                    "该笔记综合表现为常值的 %.1f 倍%s，适合趁热做第二弹或回答细节。"
                    % (comp_lift or 1.5, ("（收藏率 %.1f%%）" % save_rate) if save_rate is not None else "")
                ),
                "rationale_en": (
                    "Performed at %.1fx usual baseline%s — prime candidate for a part-2 follow-up."
                    % (comp_lift or 1.5, (" (%.1f%% save rate)" % save_rate) if save_rate is not None else "")
                ),
                "notes": "延续《%s》里观众最感兴趣的具体细节，补充使用一段时间后的真实感受。" % sample["title"][:30],
            })

        videos.append({
            "id": sid,
            "title": sample["title"],
            "is_sponsored": bool(sample.get("is_sponsored")),
            "views": views,
            "likes": likes,
            "collects": collects,
            "comments": comments_n,
            "interactions": inter,
            "save_rate_pct": save_rate,
            "comment_rate_pct": comment_rate,
            "interaction_rate_pct": inter_rate,
            "view_lift": v_lift,
            "interaction_lift": i_lift,
            "composite_lift": comp_lift,
            "quadrant": quad,
            "velocity": vel,
            "sentiment": sent_summary,
            "sentiment_source": sent_rec["source"] if sent_rec else None,
        })

    videos.sort(
        key=lambda v: (
            v["composite_lift"] is not None,
            v["composite_lift"] if v["composite_lift"] is not None else -1.0,
            v["views"] or 0,
            v["interactions"] or 0,
        ),
        reverse=True,
    )
    top_questions.sort(key=lambda q: q["demand_count"], reverse=True)

    return {
        "total_videos": len(samples),
        "videos_with_views": len(org_views),
        "videos_with_interactions": len(org_inters),
        "videos_with_sentiment": len(sentiments),
        "median_views": round(median_views, 1) if median_views is not None else None,
        "median_interactions": round(median_inters, 1) if median_inters is not None else None,
        "median_save_rate_pct": round(median_save_rate, 2) if median_save_rate is not None else None,
        "videos": videos,
        "top_audience_questions": top_questions[:15],
        "opportunities": opportunities[:12],
    }


# ---- Creator Center batch CSV / TSV / XLSX analytics sync -------------------

def _match_header(header: str, candidates: Tuple[str, ...]) -> bool:
    norm = re.sub(r"[\s_()（）%％/-]+", "", header.strip().lower())
    for cand in candidates:
        c_norm = re.sub(r"[\s_()（）%％/-]+", "", cand.lower())
        if norm == c_norm or (len(c_norm) >= 2 and c_norm in norm):
            return True
    return False


def parse_creator_center_rows(raw_bytes: bytes, filename: str = "analytics.csv") -> List[Dict[str, Any]]:
    """Parse a RedNote Creator Center post-analytics ``.csv``, ``.tsv``, or ``.xlsx`` export into normalized rows."""
    if len(raw_bytes) > 8 * 1024 * 1024:
        raise AnalyticsError("file_too_large", "Analytics file exceeds 8 MB limit.", "数据文件超过 8 MB 上限。", 413)
    lower = (filename or "").lower()
    grid: List[List[str]] = []
    if lower.endswith(".xlsx") or raw_bytes[:4] == b"PK\x03\x04":
        try:
            sheets = keyword_import._xlsx_rows(raw_bytes)  # type: ignore[attr-defined]
            grid = [[str(c or "") for c in r] for r in sheets]
        except Exception:
            raise AnalyticsError("bad_spreadsheet", "Could not read .xlsx analytics workbook.", "无法读取该 .xlsx 数据表格。", 400)
    else:
        text = None
        for enc in ("utf-8-sig", "utf-16", "gb18030"):
            try:
                text = raw_bytes.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        if text is None:
            raise AnalyticsError("bad_encoding", "Could not decode analytics text file.", "无法解码该数据文件的字符编码。", 400)
        lines = [ln for ln in text.splitlines() if ln.strip()][:5000]
        if not lines:
            return []
        delim = "\t" if "\t" in lines[0] else ("," if "," in lines[0] else "｜")
        if delim == "｜":
            grid = [[c.strip() for c in ln.split("｜")] for ln in lines]
        else:
            reader = csv.reader(io.StringIO("\n".join(lines)), delimiter=delim)
            grid = [[c.strip() for c in row] for row in reader]

    if len(grid) < 2:
        return []

    # Locate header row within first 5 rows
    header_idx = 0
    col_map: Dict[str, int] = {}
    for r_idx in range(min(5, len(grid))):
        row = grid[r_idx]
        trial: Dict[str, int] = {}
        for c_idx, cell in enumerate(row[:40]):
            if "title" not in trial and _match_header(cell, TITLE_HEADERS):
                trial["title"] = c_idx
            elif "views" not in trial and _match_header(cell, VIEWS_HEADERS):
                trial["views"] = c_idx
            elif "likes" not in trial and _match_header(cell, LIKES_HEADERS):
                trial["likes"] = c_idx
            elif "collects" not in trial and _match_header(cell, COLLECTS_HEADERS):
                trial["collects"] = c_idx
            elif "comments" not in trial and _match_header(cell, COMMENTS_HEADERS):
                trial["comments"] = c_idx
            elif "date" not in trial and _match_header(cell, DATE_HEADERS):
                trial["date"] = c_idx
            elif "url" not in trial and _match_header(cell, LINK_HEADERS):
                trial["url"] = c_idx
        if "title" in trial and any(k in trial for k in ("views", "likes", "collects", "comments")):
            header_idx = r_idx
            col_map = trial
            break

    if "title" not in col_map:
        raise AnalyticsError(
            "missing_columns",
            "Could not find a note title column and metric columns (views/likes/collects/comments).",
            "未识别到「笔记标题」列及互动数据列（观看量/点赞/收藏/评论）。",
            400,
        )

    parsed_rows: List[Dict[str, Any]] = []
    for row in grid[header_idx + 1:header_idx + 501]:
        def cell(key: str) -> str:
            idx = col_map.get(key)
            return row[idx].strip() if idx is not None and idx < len(row) else ""

        title = cell("title")
        if not title:
            continue
        views = parse_human_count(cell("views"))
        likes = parse_human_count(cell("likes"))
        collects = parse_human_count(cell("collects"))
        comments = parse_human_count(cell("comments"))
        if all(x is None for x in (views, likes, collects, comments)):
            continue
        link_info = extract_share_link(cell("url"))
        parsed_rows.append({
            "title": title[:200],
            "views": views,
            "likes": likes,
            "collects": collects,
            "comments": comments,
            "recorded_on": _parse_day(cell("date")),
            "note_url": link_info["url"],
        })
    return parsed_rows


def sync_analytics_rows(store: Store, rows: List[Dict[str, Any]], *,
                        create_missing: bool = False) -> Dict[str, Any]:
    """Apply parsed RedNote Creator Center rows to matching library items and record time-series snapshots."""
    samples = store.list_samples()
    by_norm_title: Dict[str, Dict[str, Any]] = {}
    for s in samples:
        norm = re.sub(r"\s+", "", (s.get("title") or "").lower())
        if norm and norm not in by_norm_title:
            by_norm_title[norm] = s

    updated_count = 0
    created_count = 0
    unmatched: List[str] = []

    for row in rows[:300]:
        if not isinstance(row, dict):
            continue
        title = str(row.get("title") or "").strip()
        if not title:
            continue
        norm = re.sub(r"\s+", "", title.lower())
        target = by_norm_title.get(norm)
        if target is None:
            # Substring fallback if title >= 5 chars
            for cand_norm, cand_s in by_norm_title.items():
                if len(norm) >= 5 and (norm in cand_norm or cand_norm in norm):
                    target = cand_s
                    break

        metrics = {
            k: row.get(k)
            for k in ("views", "likes", "collects", "comments")
            if _metric(row.get(k)) is not None
        }
        if not metrics:
            continue

        rec_on = _parse_day(str(row.get("recorded_on") or ""))
        note_url = extract_share_link(str(row.get("note_url") or ""))["url"]

        if target is not None:
            store.update_sample(target["id"], metrics, record_snapshot=False)
            store.record_metric_snapshot(
                target["id"],
                views=metrics.get("views", target.get("views")),
                likes=metrics.get("likes", target.get("likes")),
                collects=metrics.get("collects", target.get("collects")),
                comments=metrics.get("comments", target.get("comments")),
                recorded_on=rec_on,
                note_url=note_url,
            )
            updated_count += 1
        elif create_missing:
            created = store.create_sample(
                title=title,
                text=title,
                metrics=metrics,
                kind="script",
                source_name="RedNote Creator Center",
            )
            if rec_on or note_url:
                store.record_metric_snapshot(
                    created["id"],
                    views=created.get("views"),
                    likes=created.get("likes"),
                    collects=created.get("collects"),
                    comments=created.get("comments"),
                    recorded_on=rec_on,
                    note_url=note_url,
                )
            by_norm_title[norm] = created
            created_count += 1
        else:
            unmatched.append(title)

    return {
        "updated": updated_count,
        "created": created_count,
        "unmatched": unmatched[:30],
    }


# ---- Hybrid Audience Comment & Sentiment Engine -----------------------------

def classify_comment_text(text: str) -> Tuple[str, bool]:
    """Return ``(sentiment, is_question)`` deterministically for one viewer comment."""
    lowered = text.lower()
    pos = sum(1 for cue in POSITIVE_CUES if cue in lowered)
    neg = sum(1 for cue in NEGATIVE_CUES if cue in lowered)
    is_q = any(cue in lowered for cue in QUESTION_CUES)
    if pos > 0 and neg > 0:
        sentiment = "mixed"
    elif neg > pos:
        sentiment = "negative"
    elif pos > 0:
        sentiment = "positive"
    else:
        sentiment = "neutral"
    return sentiment, is_q


def parse_pasted_comments(raw_text: str) -> List[Dict[str, Any]]:
    """Parse pasted RedNote comments into clean records with likes, sentiment, and question flags."""
    if not isinstance(raw_text, str) or not raw_text.strip():
        return []
    out: List[Dict[str, Any]] = []
    seen = set()
    for raw_line in raw_text.replace("\r\n", "\n").splitlines()[:600]:
        line = raw_line.strip()
        if not line or len(line) < 2:
            continue
        likes: Optional[int] = None
        m_likes = _TRAILING_LIKES.search(line)
        if m_likes:
            likes = _metric(int(m_likes.group(1)))
            line = line[:m_likes.start()].strip()
        line = _COMMENT_META_TAIL.sub("", line).strip()
        if not line:
            continue
        author = ""
        if "：" in line or ":" in line:
            sep = "：" if "：" in line else ":"
            left, right = line.split(sep, 1)
            if 1 <= len(left.strip()) <= 20 and len(right.strip()) >= 2 and not left.strip().startswith("http"):
                author = left.strip()
                line = right.strip()
        key = line.lower()
        if key in seen:
            continue
        seen.add(key)
        sentiment, is_q = classify_comment_text(line)
        out.append({
            "author": author,
            "text": line[:1000],
            "likes": likes,
            "sentiment": sentiment,
            "is_question": is_q,
        })
    return out


def build_local_sentiment_summary(sample: Dict[str, Any], comments: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Deterministic instant sentiment & viewer-question summary (works offline without any LLM)."""
    total = len(comments)
    if total == 0:
        return {
            "overall_sentiment": "neutral",
            "comment_count": 0,
            "sentiment_breakdown_pct": {"positive": 0, "neutral": 0, "negative": 0},
            "summary_zh": "暂无评论内容可供分析。",
            "summary_en": "No viewer comments added yet.",
            "praise_themes": [],
            "objections_or_pain_points": [],
            "audience_questions": [],
            "followup_video_angles": [],
        }

    pos_list = [c for c in comments if c.get("sentiment") == "positive"]
    neg_list = [c for c in comments if c.get("sentiment") in ("negative", "mixed")]
    neu_count = max(0, total - len(pos_list) - len(neg_list))

    pos_pct = int(round(100.0 * len(pos_list) / total))
    neg_pct = int(round(100.0 * len(neg_list) / total))
    neu_pct = max(0, 100 - pos_pct - neg_pct)

    if pos_pct >= 65 and neg_pct <= 15:
        overall = "positive"
    elif pos_pct >= 45 and neg_pct <= 25:
        overall = "mostly_positive"
    elif neg_pct >= 40:
        overall = "critical"
    elif pos_pct > 0 and neg_pct > 0:
        overall = "mixed"
    else:
        overall = "neutral"

    questions = [c for c in comments if c.get("is_question")]
    questions.sort(key=lambda c: (c.get("likes") or 0, len(c.get("text") or "")), reverse=True)

    audience_qs = [
        {
            "question_zh": q["text"][:140],
            "question_en": q["text"][:140],
            "demand_count": max(1, (q.get("likes") or 0) + 1),
        }
        for q in questions[:6]
    ]

    praise_themes = [
        {
            "theme_zh": p["text"][:60],
            "theme_en": "Viewer praise highlight",
            "sample_comment": p["text"][:140],
        }
        for p in sorted(pos_list, key=lambda c: c.get("likes") or 0, reverse=True)[:4]
    ]

    objections = [
        {
            "issue_zh": n["text"][:60],
            "issue_en": "Viewer concern / hesitation",
            "sample_comment": n["text"][:140],
        }
        for n in sorted(neg_list, key=lambda c: c.get("likes") or 0, reverse=True)[:4]
    ]

    followups: List[Dict[str, Any]] = []
    if audience_qs:
        top_q = audience_qs[0]["question_zh"]
        followups.append({
            "title_zh": "回应评论区高频提问｜%s" % top_q[:22].rstrip("？?！!。"),
            "title_en": "Answering top viewer question from \"%s\"" % sample["title"][:32],
            "video_format": "VLOG_VOICEOVER",
            "target_duration_sec": 60,
            "rationale_zh": "本条笔记有 %d 条观众提问，最高赞问题：「%s」" % (len(questions), top_q[:40]),
            "rationale_en": "%d viewer questions spotted; top question: \"%s\"" % (len(questions), top_q[:50]),
            "script_notes_zh": "开头直接引用上期评论区提问：「%s」，实拍演示并给出真实建议。" % top_q[:60],
        })
    if objections:
        top_obj = objections[0]["sample_comment"]
        followups.append({
            "title_zh": "%s｜真实优缺点与避坑实话" % sample["title"][:20],
            "title_en": "Honest pros, cons & caveats follow-up on \"%s\"" % sample["title"][:30],
            "video_format": "SINGLE_REVIEW",
            "target_duration_sec": 60,
            "rationale_zh": "评论区存在顾虑反馈（「%s」），适合拍一期实测解答。" % top_obj[:36],
            "rationale_en": "Addresses viewer concern spotted in comments.",
            "script_notes_zh": "针对观众担心的点（%s）做真实对比与适用人群边界说明。" % top_obj[:50],
        })

    summary_zh = "共分析 %d 条评论：正面 %d%%、中性 %d%%、顾虑/负面 %d%%，识别出 %d 条观众提问。" % (
        total, pos_pct, neu_pct, neg_pct, len(questions)
    )
    summary_en = "Analyzed %d comments: %d%% positive, %d%% neutral, %d%% critical/mixed, with %d viewer questions." % (
        total, pos_pct, neu_pct, neg_pct, len(questions)
    )

    return {
        "overall_sentiment": overall,
        "comment_count": total,
        "sentiment_breakdown_pct": {"positive": pos_pct, "neutral": neu_pct, "negative": neg_pct},
        "summary_zh": summary_zh,
        "summary_en": summary_en,
        "praise_themes": praise_themes,
        "objections_or_pain_points": objections,
        "audience_questions": audience_qs,
        "followup_video_angles": followups,
    }


def ingest_comments_for_sample(store: Store, sample_id: str, raw_text: str, *,
                               replace: bool = False) -> Dict[str, Any]:
    """Parse and save viewer comments for ``sample_id``, computing an immediate deterministic sentiment summary."""
    sample = store.get_sample(sample_id)
    if sample is None:
        raise AnalyticsError("not_found", "Library item not found.", "未找到该素材条目。", 404)
    parsed = parse_pasted_comments(raw_text)
    if not parsed:
        raise AnalyticsError("no_comments", "Paste at least one viewer comment to analyze.", "请至少粘贴一条评论。", 400)
    added = store.add_sample_comments(sample_id, parsed, replace=replace)
    all_comments = store.list_sample_comments(sample_id)
    # If sample.comments count was unset, sync it to at least len(all_comments)
    if sample.get("comments") is None:
        store.update_sample(sample_id, {"comments": len(all_comments)})
    local_summary = build_local_sentiment_summary(sample, all_comments)
    existing = store.get_sample_sentiment(sample_id)
    if existing is None or existing.get("source") == "local" or replace:
        store.save_sample_sentiment(sample_id, local_summary, source="local")
    return {
        "added": added,
        "total_comments": len(all_comments),
        "comments": all_comments[:100],
        "sentiment": (store.get_sample_sentiment(sample_id) or {}).get("summary", local_summary),
        "sentiment_source": (store.get_sample_sentiment(sample_id) or {}).get("source", "local"),
    }


def _load_stage9_sections() -> Tuple[str, str]:
    text = STAGE9_PATH.read_text(encoding="utf-8")
    sys_idx = text.index("## System Prompt") + len("## System Prompt")
    usr_idx = text.index("## User Prompt Template")
    system = text[sys_idx:usr_idx].strip().strip("-").strip()
    user_tmpl = text[usr_idx + len("## User Prompt Template"):].strip()
    return system, user_tmpl


def build_sentiment_prompt(store: Store, sample_id: str) -> Tuple[str, str, str]:
    """Build ``(system, user, brief_id)`` for Stage 9 AI sentiment synthesis on ``sample_id``."""
    sample = store.get_sample(sample_id)
    if sample is None:
        raise AnalyticsError("not_found", "Library item not found.", "未找到该素材条目。", 404)
    comments = store.list_sample_comments(sample_id)
    if not comments:
        raise AnalyticsError("no_comments", "Add viewer comments before running AI sentiment synthesis.",
                             "请先添加观众评论，再运行 AI 评论情绪分析。", 400)
    local_pre = build_local_sentiment_summary(sample, comments)
    system, user_tmpl = _load_stage9_sections()
    prof = creator.prompt_block(store.load_creator())
    meta = {
        "title": sample["title"],
        "is_sponsored": bool(sample.get("is_sponsored")),
        "views": sample.get("views"),
        "likes": sample.get("likes"),
        "collects": sample.get("collects"),
        "comments": sample.get("comments"),
    }
    compact_comments = [
        {"text": c["text"][:300], "likes": c.get("likes") or 0, "pre_flag": c.get("sentiment")}
        for c in comments[:80]
    ]
    user = (
        user_tmpl
        .replace("{{creator_profile_json}}", json.dumps(prof, ensure_ascii=False, indent=2))
        .replace("{{video_metadata_json}}", json.dumps(meta, ensure_ascii=False, indent=2))
        .replace("{{deterministic_counts_json}}", json.dumps(local_pre["sentiment_breakdown_pct"], ensure_ascii=False))
        .replace("{{viewer_comments_json}}", json.dumps(compact_comments, ensure_ascii=False, indent=2))
    )
    brief_id = store.save_brief("sentiment", {"sample_id": sample_id, "comment_count": len(comments)}, fingerprint=sample_id)
    return system, user, brief_id


def apply_sentiment_reply(store: Store, sample_id: str, reply: Dict[str, Any], *,
                          source: str = "ai") -> Dict[str, Any]:
    """Validate and store a Stage 9 JSON reply for ``sample_id``."""
    sample = store.get_sample(sample_id)
    if sample is None:
        raise AnalyticsError("not_found", "Library item not found.", "未找到该素材条目。", 404)
    comments = store.list_sample_comments(sample_id)
    fallback = build_local_sentiment_summary(sample, comments)
    if not isinstance(reply, dict):
        raise llm.LLMError("Sentiment reply must be a JSON object", "bad_json")

    overall = str(reply.get("overall_sentiment") or fallback["overall_sentiment"]).strip().lower()
    if overall not in ("positive", "mostly_positive", "mixed", "critical", "neutral"):
        overall = fallback["overall_sentiment"]

    raw_pct = reply.get("sentiment_breakdown_pct")
    if isinstance(raw_pct, dict):
        p = max(0, min(100, int(raw_pct.get("positive") or 0)))
        n = max(0, min(100, int(raw_pct.get("negative") or 0)))
        u = max(0, min(100, int(raw_pct.get("neutral") if raw_pct.get("neutral") is not None else (100 - p - n))))
        pct = {"positive": p, "neutral": u, "negative": n}
    else:
        pct = fallback["sentiment_breakdown_pct"]

    praise = []
    for item in (reply.get("praise_themes") if isinstance(reply.get("praise_themes"), list) else [])[:8]:
        if isinstance(item, dict) and item.get("theme_zh"):
            praise.append({
                "theme_zh": str(item["theme_zh"])[:120],
                "theme_en": str(item.get("theme_en") or item["theme_zh"])[:160],
                "sample_comment": str(item.get("sample_comment") or "")[:200],
            })

    objections = []
    for item in (reply.get("objections_or_pain_points") if isinstance(reply.get("objections_or_pain_points"), list) else [])[:8]:
        if isinstance(item, dict) and item.get("issue_zh"):
            objections.append({
                "issue_zh": str(item["issue_zh"])[:120],
                "issue_en": str(item.get("issue_en") or item["issue_zh"])[:160],
                "sample_comment": str(item.get("sample_comment") or "")[:200],
            })

    questions = []
    for item in (reply.get("audience_questions") if isinstance(reply.get("audience_questions"), list) else [])[:10]:
        if isinstance(item, dict) and item.get("question_zh"):
            questions.append({
                "question_zh": str(item["question_zh"])[:200],
                "question_en": str(item.get("question_en") or item["question_zh"])[:200],
                "demand_count": max(1, min(9999, int(item.get("demand_count") or 1)))
                if isinstance(item.get("demand_count"), (int, float)) else 1,
            })

    angles = []
    for item in (reply.get("followup_video_angles") if isinstance(reply.get("followup_video_angles"), list) else [])[:6]:
        if isinstance(item, dict) and item.get("title_zh"):
            angles.append({
                "title_zh": str(item["title_zh"])[:160],
                "title_en": str(item.get("title_en") or item["title_zh"])[:180],
                "video_format": str(item.get("video_format") or "VLOG_VOICEOVER")[:40],
                "target_duration_sec": max(15, min(300, int(item.get("target_duration_sec") or 60)))
                if isinstance(item.get("target_duration_sec"), (int, float)) else 60,
                "rationale_zh": str(item.get("rationale_zh") or "")[:400],
                "rationale_en": str(item.get("rationale_en") or "")[:400],
                "script_notes_zh": str(item.get("script_notes_zh") or "")[:800],
            })

    cleaned = {
        "overall_sentiment": overall,
        "comment_count": len(comments),
        "sentiment_breakdown_pct": pct,
        "summary_zh": str(reply.get("summary_zh") or fallback["summary_zh"])[:600],
        "summary_en": str(reply.get("summary_en") or fallback["summary_en"])[:600],
        "praise_themes": praise or fallback["praise_themes"],
        "objections_or_pain_points": objections or fallback["objections_or_pain_points"],
        "audience_questions": questions or fallback["audience_questions"],
        "followup_video_angles": angles or fallback["followup_video_angles"],
    }
    saved = store.save_sample_sentiment(sample_id, cleaned, source=source)
    return saved or {"sample_id": sample_id, "summary": cleaned, "source": source}


def analyze_sample_sentiment_ai(store: Store, sample_id: str,
                                transport: Optional[llm.Transport] = None) -> Dict[str, Any]:
    """Run Stage 9 deep audience sentiment synthesis via configured AI provider (``temperature=0.2``)."""
    cfg = store.load_config()
    provider = cfg.get("provider") or ""
    model = cfg.get("model") or ""
    if provider == "manual":
        raise llm.LLMError("Manual copy-paste mode is selected", "manual_mode")
    if not provider or not model:
        raise llm.LLMError("AI provider not configured", "not_configured")
    api_key = (cfg.get("keys") or {}).get(provider, "") if provider != "local" else ""
    if provider != "local" and not api_key:
        raise llm.LLMError("API key required", "not_configured")
    system, user, _ = build_sentiment_prompt(store, sample_id)
    reply = llm.complete_json(
        provider, api_key, model, system, user,
        transport=transport,
        base_url=cfg.get("local_base_url") or None,
        temperature=0.2,
    )
    return apply_sentiment_reply(store, sample_id, reply, source="ai")
