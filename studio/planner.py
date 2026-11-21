"""Upcoming video content planner & seamless idea-to-script workflow.

Bridges RedNote view-count performance, audience sentiment / viewer questions, and trend keywords
into a persistent upcoming video plan (``IDEA -> PLANNED -> SCRIPTING -> READY -> POSTED``):
- 1-click plan creation from Stage 6 topic ideas, RedNote sentiment follow-up angles, viewer questions, or trends.
- Automatic status promotion when a linked script is created (``SCRIPTING``) or marked as posted (``POSTED``).
- Structured summary of upcoming planned topics fed into Stage 6 (``06_content_recommender.md``) so new
  recommendations complement what is already scheduled instead of repeating it.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from . import rednote_analytics
from .store import Store

PLAN_STATUSES = ("IDEA", "PLANNED", "SCRIPTING", "READY", "POSTED")


class PlannerError(Exception):
    """A planning operation was refused."""

    def __init__(self, code: str, message: str, message_zh: str, status: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.message_zh = message_zh
        self.status = status


def sync_plan_statuses(store: Store) -> List[Dict[str, Any]]:
    """Keep planned video cards synchronized with their linked scripts and posted status."""
    plans = store.list_video_plans()
    if not plans:
        return []
    posted_map = store.posted_dates()
    script_titles = store.script_titles()
    refreshed: List[Dict[str, Any]] = []
    for p in plans:
        sid = p.get("script_id")
        changed: Dict[str, Any] = {}
        if sid:
            if sid in posted_map and p["status"] != "POSTED":
                changed["status"] = "POSTED"
                if not p["scheduled_for"] and posted_map[sid]:
                    changed["scheduled_for"] = str(posted_map[sid])
            elif sid in script_titles and p["status"] in ("IDEA", "PLANNED"):
                changed["status"] = "SCRIPTING"
        if changed:
            updated = store.update_video_plan(p["id"], changed)
            if updated is not None:
                p = updated
        p = dict(p)
        p["script_title"] = script_titles.get(sid, "") if sid else ""
        p["posted_on"] = posted_map.get(sid) if sid else None
        refreshed.append(p)
    return refreshed


def payload(store: Store) -> Dict[str, Any]:
    """Full planner state for ``#/plan`` including RedNote content opportunities."""
    plans = sync_plan_statuses(store)
    analytics = rednote_analytics.analytics_overview(store)
    existing_titles = {p["title"].strip().lower() for p in plans}
    fresh_opps = [
        opp for opp in analytics.get("opportunities", [])
        if str(opp.get("title_zh") or "").strip().lower() not in existing_titles
    ]
    counts = {st: sum(1 for p in plans if p["status"] == st) for st in PLAN_STATUSES}
    return {
        "plans": plans,
        "counts": counts,
        "opportunities": fresh_opps[:8],
        "top_audience_questions": analytics.get("top_audience_questions", [])[:10],
        "median_views": analytics.get("median_views"),
        "median_interactions": analytics.get("median_interactions"),
        "median_save_rate_pct": analytics.get("median_save_rate_pct"),
    }


def create_plan_item(store: Store, body: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(body, dict):
        raise PlannerError("bad_request", "Invalid plan payload.", "无效的拍摄计划内容。", 400)
    title = str(body.get("title") or body.get("title_zh") or "").strip()
    if not title:
        raise PlannerError("title_required", "Enter a title or topic for the planned video.", "请填写新视频的标题或主题。", 400)
    fmt = str(body.get("video_format") or "VLOG_VOICEOVER").strip() or "VLOG_VOICEOVER"
    sec = body.get("target_sec") or body.get("target_duration_sec") or 60
    try:
        target_sec = int(sec)
    except (TypeError, ValueError):
        target_sec = 60
    priority = body.get("priority", 2)
    try:
        pri = int(priority)
    except (TypeError, ValueError):
        pri = 2
    notes = str(body.get("notes") or body.get("script_notes_zh") or "").strip()
    rationale_zh = str(body.get("rationale_zh") or body.get("why_zh") or "").strip()
    rationale_en = str(body.get("rationale_en") or body.get("why_en") or "").strip()
    keywords = body.get("keywords") if "keywords" in body else body.get("trend_keywords")
    questions = body.get("questions") if isinstance(body.get("questions"), list) else []
    return store.create_video_plan(
        title=title,
        video_format=fmt,
        target_sec=target_sec,
        scheduled_for=str(body.get("scheduled_for") or ""),
        status=str(body.get("status") or "PLANNED"),
        priority=pri,
        source_kind=str(body.get("source_kind") or "manual"),
        notes=notes,
        rationale_zh=rationale_zh,
        rationale_en=rationale_en,
        keywords=keywords,
        questions=questions,
        script_id=body.get("script_id") if isinstance(body.get("script_id"), str) else None,
    )


def draft_payload_for_plan(plan: Dict[str, Any]) -> Dict[str, Any]:
    """Assemble enriched Stage 4 script-generation parameters from a planned video card."""
    parts: List[str] = [plan["title"]]
    if plan.get("notes"):
        parts.append(plan["notes"])
    if plan.get("rationale_zh"):
        parts.append("策划切入点：" + plan["rationale_zh"])
    qs = [q for q in (plan.get("questions") or []) if isinstance(q, str) and q.strip()]
    if qs:
        parts.append("需在视频中回应的评论区真实提问：\n" + "\n".join("- " + q for q in qs))
    return {
        "title": plan["title"],
        "notes": "\n\n".join(parts),
        "target_sec": int(plan.get("target_sec") or 60),
        "video_format": plan.get("video_format") or "VLOG_VOICEOVER",
        "keywords": list(plan.get("keywords") or []),
    }


def prompt_context(store: Store) -> List[Dict[str, Any]]:
    """Compact list of active upcoming plans + drafts so Stage 6 avoids duplicate recommendations."""
    active_plans = [
        {
            "title": p["title"],
            "status": p["status"],
            "scheduled_for": p["scheduled_for"] or None,
            "keywords": p["keywords"],
        }
        for p in store.list_video_plans()
        if p["status"] != "POSTED"
    ][:20]
    posted_ids = set(store.posted_dates().keys())
    linked_ids = {p["script_id"] for p in store.list_video_plans() if p.get("script_id")}
    unposted_scripts = [
        {
            "title": s["title"],
            "status": "DRAFT_SCRIPT",
            "scheduled_for": None,
            "keywords": [],
        }
        for s in store.list_scripts()
        if s["id"] not in posted_ids and s["id"] not in linked_ids
    ][:15]
    return active_plans + unposted_scripts
