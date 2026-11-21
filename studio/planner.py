"""The plan: the videos the creator means to make, each with a day or without one.

A plan is one video that is not made yet: a working title, the day it is meant to go out (or none), how long
it should be and which form it has, the creator's notes, why it is worth making (kept in Chinese and in
English, because the page shows either), the questions of viewers it answers, target keywords, and where it
came from (typed in, a saved idea, a viewer's question, something the numbers suggested, an AI app through
MCP). One script at most is made from a plan, and the plan then points at it (``script_id``).

The state of a plan, and what moves it:

- ``IDEA``: noted, not decided. ``PLANNED``: the creator means to make it. These two are the creator's to
  choose while the plan has no script.
- ``SCRIPTING``: a script was made from it (``make_script``) and that script exists.
- ``READY``: the same, and the creator said the script is finished.
- ``POSTED``: that script is marked as posted, which is to say it has its item in the library
  (``studio/posted.py``).

The state is WORKED OUT WHEN IT IS READ (``state_of``) and never stored. What is stored is only what the
creator said (``MARKS``: idea, planned, ready) and the link to the script. Two reasons:

- A read must change nothing. Round 1 brought the stored state up to date inside ``GET /api/plans``. The
  backup gate counts a request as a change by its method (``backup.Backups.passage``, ``Handler._gated_api``):
  a GET is let through while a restore keeps changes out, and is not refused when it comes from a page that
  was open before the restore. So that read could write into data that was being copied or had just been
  replaced. The MCP tool that lists the plan is declared read-only as well.
- The acts that move a plan happen in other places: a script is marked as posted on its own page or through
  MCP, its library item is deleted in the library, the script itself is deleted, a backup is restored. Writing
  the state at each of them means a line in every one of those places, and one that is forgotten leaves a
  plan saying "posted" for a video that is not. Worked out from the scripts and the library, it cannot be
  stale: deleting the script puts the plan back to planned, deleting the library item puts it back to writing.

What is not done here: nothing is fetched, nothing is sent anywhere except by ``make_script`` when the
creator asks for a draft from the AI service in Settings, and no plan is ever made, changed or removed
without a request that says so. The opportunities and the viewers' questions shown beside the plan are
``rednote_analytics.analytics_overview``'s; this module only leaves out what is planned already.
"""
from __future__ import annotations

import datetime
import math
import re
from typing import Any, Dict, List, Optional

from . import llm, pipeline, rednote_analytics
from .store import Store

IDEA, PLANNED, SCRIPTING, READY, POSTED = "IDEA", "PLANNED", "SCRIPTING", "READY", "POSTED"
PLAN_STATUSES = (IDEA, PLANNED, SCRIPTING, READY, POSTED)
MARKS = (IDEA, PLANNED, READY)      # what the creator may say about a plan: the only values the ``status`` column holds
OPPORTUNITY_BASES = ("word_lists", "numbers", "model")

MAX_TITLE_CHARS = 300       # as long as the title of a saved idea may be (pipeline.MAX_IDEA_TEXT_CHARS), so every idea fits
MAX_NOTES_CHARS = 4000      # a few paragraphs. They go, whole, into the notes of the script and so into a draft prompt
MAX_WHY_CHARS = 600         # why it is worth making, in each language: a sentence or two shown under the title
MAX_QUESTIONS = 12          # the questions of viewers one video answers
MAX_QUESTION_CHARS = 300    # one of them; a comment that is longer is cut, it is quoted and not edited
MIN_SEC, MAX_SEC = 15, 900  # a quarter of a minute to a quarter of an hour; the pages offer 30, 60 and 90
DEFAULT_SEC = 60
# Plans that have no script yet. What one person plans ahead is tens. The limit is there for a program that
# adds plans in a loop (an AI app through MCP). A plan with a script is not counted: there is one per script.
MAX_OPEN_PLANS = 300
MAX_AT_ONCE = 30            # plans added in one step: the page offers ten questions and twelve ideas to tick
MAX_SHOWN_OPPORTUNITIES = 8
MAX_SHOWN_QUESTIONS = 10
PROMPT_MAX_PLANS = 20       # what the ideas prompt is told is planned: the next weeks, not a year
_SOURCE_KIND = re.compile(r"[a-z0-9_]{1,32}")
_DAY = re.compile(r"([0-9]{4})-([0-9]{2})-([0-9]{2})")

# Refusals: code -> (HTTP status, English, Chinese). Both sentences are written to be shown as they are.
REFUSALS = {
    "not_found": (404, "There is no such plan.", "没有这条计划。"),
    "bad_request": (400, "A plan is sent as an object with a title.", "计划要写成一个带标题的对象。"),
    "title_required": (400, "Give the plan a title: what the video is about.", "请给这条计划写个标题：这条视频拍什么。"),
    "title_too_long": (400, "A plan's title can have at most %d characters." % MAX_TITLE_CHARS,
                       "计划的标题最多 %d 个字。" % MAX_TITLE_CHARS),
    "notes_too_long": (400, "A plan's notes can have at most %d characters. Longer notes belong in the script."
                       % MAX_NOTES_CHARS, "计划的备注最多 %d 个字。更长的内容请写进脚本里。" % MAX_NOTES_CHARS),
    "bad_day": (400, "The day has to be written as year-month-day, for example 2026-11-28, and be a day that "
                     "exists. Leave it empty for a plan without a day.",
                "日期要写成「年-月-日」，例如 2026-11-28，而且得是真有的一天。还没定日期就留空。"),
    "bad_length": (400, "The length has to be a whole number of seconds from %d to %d." % (MIN_SEC, MAX_SEC),
                   "时长要填 %d 到 %d 之间的整数秒。" % (MIN_SEC, MAX_SEC)),
    "bad_format": (400, "That is not one of the forms a video can have here.", "这里没有这种视频形式。"),
    "bad_status": (400, "A plan without a script can be an idea or planned; one with a script can be in writing or ready.",
                   "还没有脚本的计划可以是「想法」或「已计划」；有了脚本的可以是「写稿中」或「写好了」。"),
    "status_follows_script": (409, "This is not set by hand. A plan is in writing once a script was made from it, and "
                                   "posted once that script is marked as posted. Nothing was changed.",
                              "这个状态不是手动改的：从计划建了脚本就是「写稿中」，脚本标记为已发布就是「已发布」。这次没有改动任何内容。"),
    "needs_script": (409, "This plan has no script yet, so it cannot be in writing or ready. Make its script first.",
                     "这条计划还没有脚本，不能标成「写稿中」或「写好了」。请先建脚本。"),
    "has_script": (409, "A script was already made from this plan. Open that script. Nothing was changed.",
                   "这条计划已经建过脚本了，请打开那篇脚本。这次没有改动任何内容。"),
    "too_many_plans": (409, "There are already %d plans without a script. Make their scripts or delete some before "
                            "adding more. Nothing was added." % MAX_OPEN_PLANS,
                       "还没有脚本的计划已经有 %d 条了。请先给它们建脚本，或者删掉一些，再添加。这次什么都没有加。" % MAX_OPEN_PLANS),
    "too_many_at_once": (400, "At most %d plans can be added in one step. Nothing was added." % MAX_AT_ONCE,
                         "一次最多加 %d 条计划。这次什么都没有加。" % MAX_AT_ONCE),
    "nothing_chosen": (400, "Nothing was chosen to add.", "还没有勾选要加的内容。"),
    "reply_required": (400, "Paste the AI's reply first.", "请先把 AI 的回复粘贴进来。"),
}


class PlannerError(ValueError):
    """A request about the plan that cannot be met. Nothing was changed.

    ``code`` is one of REFUSALS; ``status`` is the HTTP status for it; ``message`` (English) and
    ``message_zh`` are written to be shown.
    """

    def __init__(self, code: str) -> None:
        self.code = code
        self.status, self.message, self.message_zh = REFUSALS[code]
        super().__init__(self.message)


def today() -> datetime.date:
    """Today by this computer's clock. The app and the creator's browser run on the same machine, so it is their today."""
    return datetime.date.today()


# ---- reading: nothing from here down to ``create_plan_item`` writes ---------------------------------------------
def state_of(plan: Dict[str, Any], scripts: Dict[str, str], posted: Dict[str, Optional[str]]) -> str:
    """The state of a stored plan, worked out from what is there now.

    ``scripts`` is ``Store.script_titles()`` (the scripts that exist) and ``posted`` is ``Store.posted_dates()``
    (the scripts that have their item in the library). A link to a script that is gone is no link.
    """
    script_id = plan.get("script_id")
    if script_id and script_id in scripts:
        if script_id in posted:
            return POSTED
        return READY if plan.get("status") == READY else SCRIPTING
    return IDEA if plan.get("status") == IDEA else PLANNED


def _shown(plan: Dict[str, Any], scripts: Dict[str, str], posted: Dict[str, Optional[str]]) -> Dict[str, Any]:
    """A stored plan as a page or an AI app gets it.

    ``status`` is the state (``state_of``). ``script_id`` is None unless that script exists, and
    ``script_title`` names it. ``posted_on`` is the day the script was posted, None unless the plan is posted.
    ``day`` is where the plan stands in a calendar: the day it was posted once it is, else the day it is
    planned for, "" when it has none.
    """
    state = state_of(plan, scripts, posted)
    linked = plan["script_id"] if state in (SCRIPTING, READY, POSTED) else None
    posted_on = posted.get(linked) if state == POSTED else None
    return dict(plan, status=state, script_id=linked, script_title=scripts.get(linked, "") if linked else "",
                posted_on=posted_on, day=posted_on or plan["scheduled_for"])


def plans(store: Store) -> List[Dict[str, Any]]:
    """Every plan as it is shown (``_shown``), in the order of ``Store.list_video_plans``. Reads only."""
    stored = store.list_video_plans()
    if not stored:
        return []
    scripts, posted = store.script_titles(), store.posted_dates()
    return [_shown(plan, scripts, posted) for plan in stored]


def get(store: Store, plan_id: Any) -> Dict[str, Any]:
    """One plan as it is shown. Raises ``PlannerError("not_found")``."""
    plan = store.get_video_plan(plan_id) if isinstance(plan_id, str) else None
    if plan is None:
        raise PlannerError("not_found")
    return _shown(plan, store.script_titles(), store.posted_dates())


def _line(value: Any, limit: int) -> str:
    """Text from outside as one line of at most ``limit`` characters; anything that is not text is no text."""
    return " ".join(value.split())[:limit].strip() if isinstance(value, str) else ""


def _key(title: Any) -> str:
    """How two titles are compared: without spaces, in lower case."""
    return "".join(str(title or "").split()).lower()


def opportunity_basis(opportunity: Dict[str, Any]) -> str:
    """How an opportunity was arrived at: ``word_lists``, ``numbers`` or ``model``.

    When ``rednote_analytics`` did not name one (or named something unknown), it is taken for
    ``word_lists``, the weakest of the three.
    """
    basis = opportunity.get("basis")
    return basis if basis in OPPORTUNITY_BASES else "word_lists"


def opportunity_title(opportunity: Dict[str, Any]) -> str:
    """The ready-made plan title of an opportunity, or "" when it has none.

    For ``word_lists`` and ``numbers``, ``title_zh`` and ``title_en`` describe what was counted — they are
    not video titles, so "" is returned and the creator writes a title before adding a plan. Only a
    ``model`` reading with a non-empty title carries a suggested video title, and nothing is fabricated when
    ``title_zh`` and ``title_en`` are empty.
    """
    if opportunity_basis(opportunity) != "model":
        return ""
    return _line(opportunity.get("title_zh") or opportunity.get("title_en"), MAX_TITLE_CHARS)


def _opportunity_has_substance(opportunity: Dict[str, Any]) -> bool:
    """Whether an opportunity has anything to show (a count description, a source video or a rationale)."""
    return any(_line(opportunity.get(field), MAX_WHY_CHARS)
               for field in ("title_zh", "title_en", "source_title", "rationale_zh", "rationale_en"))


def payload(store: Store) -> Dict[str, Any]:
    """Everything the plan page shows (``GET /api/plans``), and what the MCP tool that lists the plan returns.

    - ``plans``: every plan as it is shown (``plans``). ``counts``: how many are in each state.
    - ``today``: today here, ``YYYY-MM-DD``, for the calendar.
    - ``opportunities`` and ``top_audience_questions``: from ``rednote_analytics.analytics_overview``, with
      their fields as that module gives them, without those that are planned already (a model opportunity
      whose ready-made title is some plan's title; a question some plan answers), at most
      MAX_SHOWN_OPPORTUNITIES and MAX_SHOWN_QUESTIONS. Each opportunity carries ``basis``
      (``opportunity_basis``: ``word_lists``, ``numbers`` or ``model``) and ``plan_title``
      (``opportunity_title``, "" for ``word_lists`` and ``numbers``).
    - ``ideas``: the saved ideas (``pipeline.apply_ideas``) that are not planned yet, as they are stored.
    - the three medians of the analytics, as they are.
    - ``limits``: the sizes above that a page has to know.

    It reads and works out. It writes nothing.
    """
    shown = plans(store)
    analytics = rednote_analytics.analytics_overview(store)
    titles = {_key(plan["title"]) for plan in shown}
    answered = {_key(question) for plan in shown for question in plan["questions"]}
    opportunities: List[Dict[str, Any]] = []
    for found in analytics.get("opportunities") or []:
        if not isinstance(found, dict) or not _opportunity_has_substance(found):
            continue
        basis = opportunity_basis(found)
        ready_title = opportunity_title(found)
        if ready_title and _key(ready_title) in titles:
            continue
        opportunities.append(dict(found, basis=basis, plan_title=ready_title))
    questions = [question for question in analytics.get("top_audience_questions") or []
                 if isinstance(question, dict) and _line(question.get("question_zh"), MAX_QUESTION_CHARS)
                 and _key(_line(question.get("question_zh"), MAX_QUESTION_CHARS)) not in answered]
    saved = store.load_recommendations()
    ideas = [idea for idea in (saved.get("ideas") if isinstance(saved, dict) else None) or []
             if isinstance(idea, dict) and _line(idea.get("title_zh"), MAX_TITLE_CHARS)
             and _key(_line(idea.get("title_zh"), MAX_TITLE_CHARS)) not in titles]
    return {
        "plans": shown,
        "counts": {state: sum(1 for plan in shown if plan["status"] == state) for state in PLAN_STATUSES},
        "today": today().isoformat(),
        "opportunities": opportunities[:MAX_SHOWN_OPPORTUNITIES],
        "top_audience_questions": questions[:MAX_SHOWN_QUESTIONS],
        "ideas": ideas[:pipeline.MAX_IDEAS],
        "median_views": analytics.get("median_views"),
        "median_interactions": analytics.get("median_interactions"),
        "median_save_rate_pct": analytics.get("median_save_rate_pct"),
        "limits": {"title": MAX_TITLE_CHARS, "notes": MAX_NOTES_CHARS, "min_sec": MIN_SEC, "max_sec": MAX_SEC,
                   "at_once": MAX_AT_ONCE},
    }


def prompt_context(store: Store) -> List[Dict[str, Any]]:
    """What is planned and not posted, as the ideas prompt is told it, so that it is not suggested again.

    At most PROMPT_MAX_PLANS plans, in the order of the plan (those with a day first, the earliest first):
    ``title``, ``status`` (the state), ``scheduled_for`` (None without a day), ``keywords``, and
    ``answers_viewer_questions`` where the plan answers any. Titles and questions can be a viewer's or a
    model's words: the caller encodes the list like every other block of a prompt (``pipeline._dump``).
    """
    out: List[Dict[str, Any]] = []
    for plan in plans(store):
        if plan["status"] == POSTED:
            continue
        entry: Dict[str, Any] = {"title": plan["title"], "status": plan["status"],
                                 "scheduled_for": plan["scheduled_for"] or None, "keywords": plan["keywords"]}
        if plan["questions"]:
            entry["answers_viewer_questions"] = plan["questions"]
        out.append(entry)
        if len(out) == PROMPT_MAX_PLANS:
            break
    return out


# ---- what a request may carry ---------------------------------------------------------------------------------
def _day(value: Any) -> str:
    """A plan's day from a request: ``YYYY-MM-DD`` for a day that exists, or "" for none (None, "").

    Any day may be planned for, also one that has passed: a plan is not a record of what happened.
    Raises ``PlannerError("bad_day")``.
    """
    if value is None or value == "":
        return ""
    found = _DAY.fullmatch(value) if isinstance(value, str) else None
    if not found:
        raise PlannerError("bad_day")
    try:
        return datetime.date(int(found.group(1)), int(found.group(2)), int(found.group(3))).isoformat()
    except ValueError:
        raise PlannerError("bad_day") from None


def _seconds(value: Any) -> Optional[int]:
    """A length in whole seconds from MIN_SEC to MAX_SEC, or None for anything else."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return int(value) if value == int(value) and MIN_SEC <= value <= MAX_SEC else None


def _title(value: Any) -> str:
    """A plan's title: one line. Raises ``PlannerError``: ``title_required``, ``title_too_long``."""
    title = " ".join(value.split()) if isinstance(value, str) else ""
    if not title:
        raise PlannerError("title_required")
    if len(title) > MAX_TITLE_CHARS:
        raise PlannerError("title_too_long")
    return title


def _notes(value: Any) -> str:
    notes = value.strip() if isinstance(value, str) else ""
    if len(notes) > MAX_NOTES_CHARS:
        raise PlannerError("notes_too_long")
    return notes


def _questions(value: Any) -> List[str]:
    """The viewers' questions a plan answers: each one line, cut to MAX_QUESTION_CHARS, no repeats, at most MAX_QUESTIONS."""
    out: List[str] = []
    for raw in value if isinstance(value, list) else []:
        question = _line(raw, MAX_QUESTION_CHARS)
        if question and question not in out:
            out.append(question)
        if len(out) == MAX_QUESTIONS:
            break
    return out


def _new_plan(body: Any) -> Dict[str, Any]:
    """The columns of a new plan from what a request carries. Everything is checked before anything is stored.

    ``title`` is needed (``title_zh`` is read too, so a saved idea can be sent as it is). ``notes``
    (or ``script_notes_zh``), ``scheduled_for``, ``status`` (IDEA or PLANNED; PLANNED when left out),
    ``rationale_zh`` and ``rationale_en`` (or ``why_zh``, ``why_en``; cut to MAX_WHY_CHARS), ``keywords``
    (or ``trend_keywords``), ``questions`` and ``source_kind`` are optional.

    ``video_format`` and ``target_sec`` (or ``target_duration_sec``) often come from a model's reply or from
    the analytics, by way of an idea or an opportunity, and need not be values this app has: one that is
    missing or unknown becomes the usual form and DEFAULT_SEC, as ``pipeline.apply_ideas`` does it.

    Raises ``PlannerError``: ``bad_request``, ``title_required``, ``title_too_long``, ``notes_too_long``,
    ``bad_day``, ``bad_status``.
    """
    if not isinstance(body, dict):
        raise PlannerError("bad_request")
    status = body.get("status") or PLANNED
    if status not in (IDEA, PLANNED):
        raise PlannerError("bad_status")
    video_format = body.get("video_format")
    seconds = _seconds(body.get("target_sec") if body.get("target_sec") is not None else body.get("target_duration_sec"))
    kind = body.get("source_kind")
    return {
        "title": _title(body.get("title") or body.get("title_zh")),
        "video_format": video_format if video_format in pipeline.VIDEO_FORMATS else pipeline.VIDEO_FORMATS[0],
        "target_sec": seconds if seconds is not None else DEFAULT_SEC,
        "scheduled_for": _day(body.get("scheduled_for")),
        "status": status,
        "source_kind": kind if isinstance(kind, str) and _SOURCE_KIND.fullmatch(kind) else "manual",
        "notes": _notes(body.get("notes") or body.get("script_notes_zh")),
        "rationale_zh": _line(body.get("rationale_zh") or body.get("why_zh"), MAX_WHY_CHARS),
        "rationale_en": _line(body.get("rationale_en") or body.get("why_en"), MAX_WHY_CHARS),
        "keywords": body.get("keywords") if "keywords" in body else body.get("trend_keywords"),
        "questions": _questions(body.get("questions")),
    }


def _room_for(store: Store, count: int) -> None:
    if store.plans_without_script() + count > MAX_OPEN_PLANS:
        raise PlannerError("too_many_plans")


# ---- adding, changing, removing -------------------------------------------------------------------------------
def create_plan_item(store: Store, body: Any) -> Dict[str, Any]:
    """Add one plan and return it as it is shown. ``body`` is read by ``_new_plan``.

    Raises ``PlannerError`` (what ``_new_plan`` raises, and ``too_many_plans``). Nothing is stored then.
    """
    plan = _new_plan(body)
    _room_for(store, 1)
    return _shown(store.create_video_plans([plan])[0], store.script_titles(), store.posted_dates())


def create_many(store: Store, body: Any) -> List[Dict[str, Any]]:
    """Add several plans in one step, each a plan of its own: ``{"plans": [a plan as for create_plan_item, ...]}``.

    This is how several questions of viewers, or several saved ideas, are put into the plan at once. All of
    them are checked first, and they are stored together: when one is refused, none is added, and the
    refusal is that one's. At most MAX_AT_ONCE.

    Raises ``PlannerError``: ``bad_request``, ``nothing_chosen``, ``too_many_at_once``, ``too_many_plans`` and
    what ``_new_plan`` raises.
    """
    wanted = body.get("plans") if isinstance(body, dict) else None
    if not isinstance(wanted, list):
        raise PlannerError("bad_request")
    if not wanted:
        raise PlannerError("nothing_chosen")
    if len(wanted) > MAX_AT_ONCE:
        raise PlannerError("too_many_at_once")
    new = [_new_plan(one) for one in wanted]
    _room_for(store, len(new))
    scripts, posted = store.script_titles(), store.posted_dates()
    return [_shown(plan, scripts, posted) for plan in store.create_video_plans(new)]


def change(store: Store, plan_id: Any, body: Any) -> Dict[str, Any]:
    """Change a plan where it is shown, and return it as it is shown then. Only what ``body`` names is changed.

    ``title``, ``notes``, ``scheduled_for`` ("" takes the day away), ``target_sec``, ``video_format``,
    ``keywords``, ``questions``, and ``status``. Moving a plan to another day is a change of ``scheduled_for``.

    ``status`` is what the creator says about the plan, and only half of the state is theirs to say: a plan
    without a script may be IDEA or PLANNED, a plan with a script SCRIPTING or READY. Whether there is a
    script, and whether it was posted, is not set here (``status_follows_script``, ``needs_script``).

    Everything is checked before anything is written. Raises ``PlannerError``: ``not_found``, ``bad_request``,
    ``title_required``, ``title_too_long``, ``notes_too_long``, ``bad_day``, ``bad_length``, ``bad_format``,
    ``bad_status``, ``status_follows_script``, ``needs_script``.
    """
    plan = get(store, plan_id)
    if not isinstance(body, dict):
        raise PlannerError("bad_request")
    columns: Dict[str, Any] = {}
    if "title" in body:
        columns["title"] = _title(body["title"])
    if "notes" in body:
        columns["notes"] = _notes(body["notes"])
    if "scheduled_for" in body:
        columns["scheduled_for"] = _day(body["scheduled_for"])
    if "target_sec" in body:
        seconds = _seconds(body["target_sec"])
        if seconds is None:
            raise PlannerError("bad_length")
        columns["target_sec"] = seconds
    if "video_format" in body:
        if body["video_format"] not in pipeline.VIDEO_FORMATS:
            raise PlannerError("bad_format")
        columns["video_format"] = body["video_format"]
    if "keywords" in body:
        columns["keywords"] = body["keywords"]
    if "questions" in body:
        columns["questions"] = _questions(body["questions"])
    if "status" in body and body["status"] != plan["status"]:
        wanted, has_script = body["status"], plan["script_id"] is not None
        if wanted not in PLAN_STATUSES:
            raise PlannerError("bad_status")
        if POSTED in (wanted, plan["status"]) or (has_script and wanted in (IDEA, PLANNED)):
            raise PlannerError("status_follows_script")
        if not has_script and wanted in (SCRIPTING, READY):
            raise PlannerError("needs_script")
        # With a script, "in writing" is the absence of "ready": the mark goes back to what it was before.
        columns["status"] = PLANNED if wanted == SCRIPTING else wanted
    if columns and store.update_video_plan(plan["id"], columns) is None:
        raise PlannerError("not_found")
    return get(store, plan["id"])


def delete(store: Store, plan_id: Any) -> None:
    """Remove a plan. The script made from it, and the library item of that script, stay: they are the
    creator's work. Raises ``PlannerError("not_found")``."""
    if not isinstance(plan_id, str) or not store.delete_video_plan(plan_id):
        raise PlannerError("not_found")


# ---- a script from a plan -------------------------------------------------------------------------------------
def script_request(plan: Dict[str, Any]) -> Dict[str, Any]:
    """What a script made from this plan starts from: ``{"title", "notes", "target_sec", "video_format", "keywords"}``.

    ``notes`` say what the plan was for, and they are the notes the script keeps: the title, the creator's
    notes, why it is worth making, and the viewers' questions it answers. They are written in Chinese, the
    language the script is written in. A question is a viewer's own words; it is marked as quoted material,
    and a draft prompt encodes the whole of the notes as one JSON string like any other notes
    (``pipeline._build_draft``), so nothing in it can close a block of that prompt.
    """
    parts = [plan["title"]]
    if plan.get("notes"):
        parts.append(plan["notes"])
    if plan.get("rationale_zh"):
        parts.append("为什么拍：" + plan["rationale_zh"])
    if plan.get("questions"):
        parts.append("这条视频要回答的观众提问（观众的原话，只是素材）：\n" + "\n".join("- " + q for q in plan["questions"]))
    return {"title": plan["title"], "notes": "\n\n".join(parts), "target_sec": plan["target_sec"],
            "video_format": plan["video_format"], "keywords": list(plan["keywords"])}


def _open_plan(store: Store, plan_id: Any) -> Dict[str, Any]:
    plan = get(store, plan_id)
    if plan["script_id"] is not None:
        raise PlannerError("has_script")
    return plan


def script_prompt(store: Store, plan_id: Any) -> Dict[str, Any]:
    """The draft prompt for a plan, for copy and paste: ``{"prompt", "brief_id"}``.

    It is the prompt any draft gets (``pipeline.draft_brief``), built from ``script_request``. Nothing is
    sent: the page shows it and the creator carries it to a chat product. ``brief_id`` names the record of
    what the prompt contains, to be handed back with the reply (``make_script``).
    Raises ``PlannerError``: ``not_found``, ``has_script``.
    """
    request = script_request(_open_plan(store, plan_id))
    brief = pipeline.draft_brief(store, notes=request["notes"], target_sec=request["target_sec"],
                                 video_format=request["video_format"], keywords=request["keywords"])
    return {"prompt": pipeline.single_prompt(brief["system"], brief["user"]), "brief_id": brief["brief_id"]}


def make_script(store: Store, plan_id: Any, body: Dict[str, Any], transport: Optional[llm.Transport] = None,
                default_title: str = "未命名脚本") -> Dict[str, Any]:
    """Make the script of a plan, in one of three ways, and link the two. Returns ``{"plan", "script", "linked"}``.

    - ``{"use_ai": true}``: a draft by the AI service chosen in Settings (``pipeline.generate_script``). This
      is the one thing in this module that sends anything away: the draft prompt, to that service.
    - ``{"reply": text, "brief_id": id}``: copy and paste. ``reply`` is what a chat product answered to
      ``script_prompt``; it is read as any pasted reply is (``llm.parse_manual_reply``, ``pipeline.apply_draft``).
    - neither: an empty script, to be written by hand.

    In all three the script gets the plan's title, length, form and keywords, and notes that say what the
    plan was for (``script_request``). The plan then points at the script, and its state is "in writing"
    from then on because the script exists, not because anything else was written down.

    ``linked`` is False in one case only: another window made a script for the same plan in the same moment.
    The script made here is kept, since the creator asked for it, and the plan keeps the other one.

    Raises ``PlannerError`` (``not_found``, ``has_script``, ``reply_required``), ``llm.LLMError`` when the
    service fails or the reply cannot be read, and ``TextTooLong``. In each case no script was made.
    """
    plan = _open_plan(store, plan_id)
    request = script_request(plan)
    if body.get("use_ai") is True:
        script = pipeline.generate_script(
            store, notes=request["notes"], target_sec=request["target_sec"], video_format=request["video_format"],
            transport=transport, default_title=default_title, keywords=request["keywords"])
    elif "reply" in body:
        reply = body.get("reply")
        if not isinstance(reply, str) or not reply.strip():
            raise PlannerError("reply_required")
        brief_id = body.get("brief_id")
        script = pipeline.apply_draft(
            store, llm.parse_manual_reply(reply, "draft"), notes=request["notes"], target_sec=request["target_sec"],
            video_format=request["video_format"], default_title=default_title, keywords=request["keywords"],
            brief_id=brief_id if isinstance(brief_id, str) else None)
    else:
        script = store.create_script(title=request["title"], notes=request["notes"], target_sec=request["target_sec"],
                                     video_format=request["video_format"], keywords=request["keywords"],
                                     default_title=default_title)
    if script["title"] != request["title"]:
        # A draft is named after the first words of its notes. A script made from a plan carries the plan's title.
        script = store.update_script(script["id"], {"title": request["title"]}) or script
    linked = store.link_plan_script(plan["id"], script["id"])
    return {"plan": get(store, plan["id"]), "script": script, "linked": linked}
