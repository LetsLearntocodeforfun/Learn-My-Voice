"""Suggestions made without the creator asking: trend keywords for the plans, and a fresh set of topic ideas.

Two tasks of autopilot are registered here (``studio/autopilot.py``). Both are off until the creator switches
them on, like every task. What they make are suggestions: something the creator takes or dismisses with a
click. Neither ever changes a plan or a script. A plan's title, notes, day, keywords and script are the same
after a run as before it, whichever level the creator chose; the one thing that puts a suggested keyword into
a plan is ``take``, which only a request of the creator's calls. Nothing here is offered through MCP.

``plans.suggest`` calls no model and sends nothing anywhere.

- It looks at each plan that is not posted and has no day, or a day from today to WITHIN_DAYS days from today.
  A plan whose day has passed is left alone until the creator moves it or takes its day away.
- For each it goes through the trend keywords that are usable and not stale, as ``trends.payload`` gives them,
  the best score first, the first MAX_TRENDS_TRIED of them. A keyword fits a plan when it stands in the plan's
  title, notes or viewers' questions as written, or when at least ``trends.RELEVANT_AT`` of its pieces do: the
  rule ``trends.py`` has for whether a video is on a keyword's topic (``fit``). It is a comparison of
  characters. Nothing here knows what a plan is about.
- A fitting keyword is kept as a row of ``plan_suggestions``: which plan, which keyword, what matched and
  where, the keyword's score and source at that moment, when, and which run. When the trend list says a
  keyword is ``rising`` or how often it was sighted, that is kept too; without those it works the same.
- A plan is offered a keyword once. One the creator dismissed, took, or that an undone run had offered is not
  offered for that plan again. A keyword the plan already has is not offered.
- Bounds: MAX_OPEN_PER_PLAN open suggestions for one plan, MAX_OFFERED_PER_PLAN ever made for one plan, and
  MAX_OPEN_TOTAL open in all. A plan that has as many keywords as a plan can hold gets none.
- It runs when something it reads has changed (a plan, the trend list, a decision of the creator's, the day)
  and there is something to offer; otherwise it leaves no line in the log (``plans_due``).
- Undoing a run withdraws the suggestions of that run that are still open. They are not offered for those
  plans again, so an undo is not taken back by the next run. A suggestion the creator took or dismissed in
  the meantime is the creator's decision and stays as it is, and so does the keyword in the plan.

``ideas.refresh`` calls a model: the AI service chosen in Settings gets the ideas prompt (``prompts/06``),
the one "Find ideas" sends, without a web lookup and without a request of the creator's. Every call is
reserved first (``context.spend``); over the day's limit none is made.

What a run of that prompt does to the ideas that are saved, as the app stood before this module: it replaces
all of them. The ideas file (``recommendations.json``) holds one set, the latest, and ``pipeline.apply_ideas``
writes the new one over it. For a click that is what was asked. A run nobody asked for must not do that to
ideas the creator has saved and may not have looked at. So:

- The set a run makes is kept in the table ``idea_sets`` (plain columns, so it is in every backup).
- It is put into the ideas file only when the creator chose the level ``auto`` AND what the file holds is a
  set an earlier automatic run made, or there is no ideas file. What it replaces is kept in the same row,
  byte for byte. Ideas that came from the creator's own click, a pasted reply or an AI app are therefore
  never replaced by a run: beside them the new set waits.
- Otherwise the set waits (``waiting``): the page offers it, and the creator takes it (``take_ideas``: it
  then replaces the saved ideas, as "Find ideas" does, because the creator said so) or dismisses it.
- A newer run takes the place of a set that is still waiting, and of the automatic set in the ideas file;
  the earlier one stays in the table. Of the sets that are neither waiting nor in the file the newest
  KEEP_IDEA_SETS are kept and the oldest go first.
- Undoing a run takes its set out again and puts back exactly what was there: the earlier bytes of the ideas
  file, or no file, and the set that was waiting before. It is refused, with the reason, when the creator
  took the set, when the ideas file was changed since, and when a later set took its place.

It runs when the top trend keywords differ from those of the last refresh, or IDEAS_AT_LATEST_SEC after it,
at most once in IDEAS_EVERY_SEC, and only when an AI service is set up and the library holds something
(``ideas_due``). No plan is made from an idea here: that stays a click on the plan page.

Not verified: no real AI service was used with this module. The tests stand a function in for the model.
Whether a model follows ``prompts/06`` when it is asked without a request, and how good its ideas are, was
never tried.

Standard library only.
"""
from __future__ import annotations

import datetime
import hashlib
import json
import threading
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

from . import autopilot, pipeline, planner, trends
from . import store as store_module
from .store import MANUAL_PROVIDER, MAX_KEYWORD_CHARS, MAX_KEYWORDS_PER_SCRIPT, Store, keyword_key, loads_stored, normalize_keywords

PLANS_TASK_NAME, IDEAS_TASK_NAME = "plans.suggest", "ideas.refresh"     # as the log and the settings name them: never renamed
SUGGESTED, REFRESHED = "plans.suggested", "ideas.refreshed"             # the kinds of change the two tasks record for an undo

OPEN, TAKEN, DISMISSED, WITHDRAWN = "open", "taken", "dismissed", "withdrawn"       # ``plan_suggestions.status``
WAITING, IN_USE, REPLACED = "waiting", "in_use", "replaced"                         # ``idea_sets.status``, with DISMISSED
BY_RUN, BY_CREATOR = "run", "creator"                                               # ``idea_sets.placed_by``
PARTS = ("title", "notes", "questions")     # where in a plan a keyword is looked for

# The shortest cadence autopilot allows. No model is called, and nothing runs unless something changed.
PLANS_EVERY_SEC = 600
WITHIN_DAYS = 14                # a plan with a day is looked at when that day is today or at most this many days ahead
MAX_PLANS_LOOKED_AT = 300       # as many as there can be plans without a script (planner.MAX_OPEN_PLANS)
MAX_TRENDS_TRIED = 200          # the highest-scoring usable keywords that are compared with each plan
MAX_OPEN_PER_PLAN = 3           # what fits beside one plan on its card
MAX_OFFERED_PER_PLAN = 24       # suggestions ever made for one plan, decided ones included: then it is left in peace
MAX_OPEN_TOTAL = 60             # open suggestions in all, so that one run records one short list for its undo
MAX_NAMED_IN_SUMMARY = 8        # suggestions a run names in its line of the log; the rest are counted

IDEAS_EVERY_SEC = 86400         # at most one automatic refresh a day: each is a model call
IDEAS_AT_LATEST_SEC = 3 * 86400     # with the same trends as last time, a refresh after this long
KEEP_IDEA_SETS = 12             # sets kept that are neither waiting nor in the ideas file; the oldest go first
# The ideas file as it is kept when a run puts its set in its place. The app's own file is some tens of
# thousands of characters; a larger one is not one this module wrote, and is then not replaced at all.
MAX_KEPT_IDEAS_BYTES = 1024 * 1024

# Refusals of the two steps about a waiting set of ideas: code -> (HTTP status, English, Chinese).
REFUSALS = {
    "ideas_set_not_found": (404, "Those ideas are no longer kept. Nothing was changed.", "这一组选题已经不在了。这次没有改动任何内容。"),
    "ideas_set_not_waiting": (
        409, "Those ideas are not waiting any more: they were taken, dismissed, or newer ones took their place. "
             "Nothing was changed.",
        "这一组选题已经不在等你决定了：已经采用过、忽略过，或者被更新的一组替换了。这次没有改动任何内容。"),
    "ideas_set_unreadable": (
        409, "What is kept for those ideas cannot be read as ideas, so it was not put in place. Nothing was changed.",
        "这一组选题存下来的内容读不出来，所以没有采用。这次没有改动任何内容。"),
}


class Refused(Exception):
    """A request about a waiting set of ideas that cannot be met. Nothing was changed.

    ``code`` is one of REFUSALS; ``status`` is the HTTP status for it; ``message`` (English) and
    ``message_zh`` are written to be shown.
    """

    def __init__(self, code: str) -> None:
        self.code = code
        self.status, self.message, self.message_zh = REFUSALS[code]
        super().__init__(self.message)


def _day_of(now: float) -> datetime.date:
    """The day ``now`` is in, by this computer's clock: the day the plan page calls today."""
    return datetime.date.fromtimestamp(now)


def _short(said: List[Any]) -> str:
    """A short name for ``said``: it changes when anything in it does."""
    return hashlib.sha256(json.dumps(said, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:16]


# ---- trend keywords for a plan: what fits -----------------------------------------------------------------------
def _hay(text: str) -> Any:
    """``text`` prepared for keyword matching the way ``trends.py`` prepares a video's title and script."""
    return trends._Hay({"title": text})


def _has(piece: str, hay: Any) -> bool:
    """Whether one piece of a keyword is in the text, as ``trends.relevance`` counts it: an English word or a
    number as a whole word, anything else as it stands."""
    return piece in hay.words if piece.isascii() else piece in hay.compact


def fit(keyword: str, plan: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """How a trend keyword fits a plan, or None when it does not.

    The plan's title, its notes and the viewers' questions it answers are read as one text, each part kept
    apart from the next so that no match runs across two of them. The keyword fits when ``trends.relevance``
    of it in that text is at least ``trends.RELEVANT_AT``: it stands there as written, or that share of its
    pieces does (``trends.keyword_pieces``: two characters at a time for Chinese, whole words for English).

    Returns ``{"relevance", "matched", "matched_in"}``: the share from 0 to 1; what was found, the keyword
    itself when it stands there as written and else its pieces that were found, with a space between; and
    which of PARTS hold any of it. A plan's keywords are not read: one that is a keyword already is not offered.
    """
    texts = {"title": plan.get("title") or "", "notes": plan.get("notes") or "",
             "questions": "\n|\n".join(q for q in plan.get("questions") or [] if isinstance(q, str))}
    whole = _hay("\n|\n".join(str(texts[part]) for part in PARTS))
    relevance = trends.relevance(keyword, whole)
    if relevance < trends.RELEVANT_AT:
        return None
    pieces: List[str] = []
    for piece in trends.keyword_pieces(keyword):
        if piece not in pieces:
            pieces.append(piece)
    key = keyword_key(keyword)
    written = bool(key) and not key.isascii() and key in whole.compact
    found = [piece for piece in pieces if _has(piece, whole)]
    matched_in = []
    for part in PARTS:
        hay = _hay(str(texts[part]))
        if (written and key in hay.compact) or any(_has(piece, hay) for piece in pieces):
            matched_in.append(part)
    return {"relevance": round(float(relevance), 3), "matched": keyword if written else " ".join(found),
            "matched_in": matched_in}


def _eligible(plan: Dict[str, Any], today: datetime.date) -> bool:
    """Whether a plan, as it is shown, is one suggestions are made for: not posted, and without a day or with
    a day from today to WITHIN_DAYS days from today."""
    if plan.get("status") == planner.POSTED:
        return False
    day = plan.get("scheduled_for") or ""
    if not day:
        return True
    try:
        planned = datetime.date.fromisoformat(day)
    except (ValueError, TypeError):
        return False                    # a day nobody can read: the plan is left alone
    return today <= planned <= today + datetime.timedelta(days=WITHIN_DAYS)


def _carried(trend: Dict[str, Any]) -> Tuple[Optional[bool], Optional[int]]:
    """``(rising, sightings)`` when the trend list says them, None for each it does not say.

    They are another module's to work out. ``rising`` is taken when it is true or false, ``sightings`` when it
    is a count or a list of sightings (then its length). Anything else counts as not said.
    """
    rising = trend.get("rising") if isinstance(trend.get("rising"), bool) else None
    seen = trend.get("sightings")
    if isinstance(seen, list):
        seen = len(seen)
    sightings = seen if isinstance(seen, int) and not isinstance(seen, bool) and 0 <= seen < 2 ** 31 else None
    return rising, sightings


def _usable_trends(store: Store, now: float) -> List[Dict[str, Any]]:
    """The trend keywords that may be offered, the best score first: usable and not stale, as ``trends.payload`` says."""
    listed = trends.payload(store, now).get("trends")
    kept = [trend for trend in listed if isinstance(trend, dict) and isinstance(trend.get("keyword"), str)
            and trend.get("usable") is True and not trend.get("stale")] if isinstance(listed, list) else []
    return kept[:MAX_TRENDS_TRIED]


def _candidates(store: Store, now: float) -> Dict[str, Any]:
    """What a run would offer now: ``{"plans": how many plans were looked at, "rows": the new suggestions}``.

    Reads only. The rows are complete except for the run they belong to. See the module's words for which
    plans, which keywords and which bounds.
    """
    today = _day_of(now)
    shown = planner.plans(store)
    looked_at = [plan for plan in shown if _eligible(plan, today)][:MAX_PLANS_LOOKED_AT]
    if not looked_at:
        return {"plans": 0, "rows": []}
    not_posted = {plan["id"] for plan in shown if plan["status"] != planner.POSTED}
    offered: Dict[str, Dict[str, str]] = {}
    open_total = 0
    for row in store.list_plan_suggestions():
        offered.setdefault(row["plan_id"], {})[row["keyword_key"]] = row["status"]
        if row["status"] == OPEN and row["plan_id"] in not_posted:
            open_total += 1
    rows: List[Dict[str, Any]] = []
    current: Optional[List[Dict[str, Any]]] = None
    for plan in looked_at:
        if open_total >= MAX_OPEN_TOTAL:
            break
        before = offered.get(plan["id"], {})
        have = {keyword_key(keyword) for keyword in plan["keywords"]}
        open_here = sum(1 for status in before.values() if status == OPEN)
        room = min(MAX_OPEN_PER_PLAN - open_here, MAX_OFFERED_PER_PLAN - len(before),
                   MAX_KEYWORDS_PER_SCRIPT - len(plan["keywords"]) - open_here)
        if room <= 0:
            continue
        if current is None:
            current = _usable_trends(store, now)        # scored once, and only when some plan has room
        for trend in current:
            if room <= 0 or open_total >= MAX_OPEN_TOTAL:
                break
            keyword = trend["keyword"]
            key = keyword_key(keyword)
            if not key or len(keyword) > MAX_KEYWORD_CHARS or key in have or key in before:
                continue
            how = fit(keyword, plan)
            if how is None:
                continue
            rising, sightings = _carried(trend)
            score = trend.get("score")
            rows.append({
                "id": uuid.uuid4().hex[:12], "plan_id": plan["id"], "plan_title": plan["title"], "keyword": keyword,
                "keyword_key": key, "matched": how["matched"], "matched_in": how["matched_in"],
                "relevance": how["relevance"],
                "score": int(score) if isinstance(score, (int, float)) and not isinstance(score, bool) else None,
                "source": "ai" if trend.get("source") == "ai" else "manual",
                "rising": rising, "sightings": sightings, "run_id": "", "created_at": float(now)})
            have.add(key)
            room -= 1
            open_total += 1
    return {"plans": len(looked_at), "rows": rows}


def _seen(store: Store, now: float) -> str:
    """A short name for everything ``plans.suggest`` reads, apart from the scores.

    It changes when a plan is added, removed or posted, when a plan's title, notes, questions, keywords or
    day change, when the trend list changes, when a suggestion is decided, and when the day does. Reads only.
    """
    plans = [[plan["id"], plan["title"], plan["notes"], plan["questions"], plan["keywords"], plan["scheduled_for"],
              plan["status"] == planner.POSTED] for plan in planner.plans(store)]
    tracked = [[trend["id"], trend["keyword"], trend["heat"], trend["source"], trend["updated_at"]] for trend in store.list_trends()]
    decided = [[row["plan_id"], row["keyword_key"], row["status"]] for row in store.list_plan_suggestions()]
    return _short([_day_of(now).isoformat(), plans, tracked, decided])


_MEMO_LOCK = threading.Lock()
_NEW: Dict[str, Tuple[str, bool]] = {}          # data folder -> (what it looked like, whether there was anything to offer)
_TOP: Dict[str, Tuple[str, List[str]]] = {}     # data folder -> (what the trend list looked like, the top keywords then)


def _remembered(memo: Dict[str, Any], store: Store, look: str) -> Any:
    with _MEMO_LOCK:
        held = memo.get(str(store.db_path))
        return held[1] if held is not None and held[0] == look else None


def _remember(memo: Dict[str, Any], store: Store, look: str, value: Any) -> None:
    with _MEMO_LOCK:
        if len(memo) >= 8:              # one entry for each data folder this process has open: a handful, also in tests
            memo.clear()
        memo[str(store.db_path)] = (look, value)


def plans_due(context: autopilot.Context) -> bool:
    """Whether ``plans.suggest`` has anything to do: something it reads changed since its last run, and there
    is a suggestion to make. Reads only.

    Asked at every look of the scheduler. The scores are worked out once for each state of the plans and the
    trend list, and what came of it is remembered in memory until that state changes.
    """
    store, now = context.store, context.now
    look = _seen(store, now)
    last = context.last_done()
    if last is not None and last["summary"].get("seen") == look:
        return False
    found = _remembered(_NEW, store, look)
    if found is None:
        found = bool(_candidates(store, now)["rows"])
        _remember(_NEW, store, look, found)
    return bool(found)


def suggest_for_plans(context: autopilot.Context) -> Dict[str, Any]:
    """The task ``plans.suggest``: keep the trend keywords that fit the upcoming plans, as suggestions.

    The same at both levels: nothing but rows of ``plan_suggestions`` is written. How the run is reversed is
    written down before the rows are (one record with their ids). The summary says how many plans were
    looked at, how many suggestions were added and names the first MAX_NAMED_IN_SUMMARY of them.
    """
    store, now = context.store, context.now
    context.progress("matching", 0.2)
    found = _candidates(store, now)
    rows = found["rows"]
    kept: List[str] = []
    if rows:
        for row in rows:
            row["run_id"] = context.run_id
        context.record_undo(SUGGESTED, {"ids": [row["id"] for row in rows]})
        kept = store.add_plan_suggestions(rows)
    context.progress("done", 1.0)
    named = [{"plan": row["plan_title"][:40], "keyword": row["keyword"]} for row in rows if row["id"] in kept]
    return {"plans_looked_at": found["plans"], "added": len(kept), "suggested": named[:MAX_NAMED_IN_SUMMARY],
            "seen": _seen(store, now)}


def _undo_suggested(store: Store, payload: Any) -> None:
    """Reverse one run of ``plans.suggest``: withdraw the suggestions it added that are still open.

    A withdrawn suggestion is not shown and is not offered for that plan again. One the creator took or
    dismissed is left as it is, and no plan is touched: a keyword the creator took stays in the plan.
    """
    ids = payload.get("ids") if isinstance(payload, dict) else None
    store.withdraw_plan_suggestions([one for one in ids if isinstance(one, str)] if isinstance(ids, list) else [])


# ---- trend keywords for a plan: what the page is given, and the creator's two clicks ----------------------------
def _gone_stale(trend: Dict[str, Any], now: float) -> bool:
    """Whether a tracked keyword counts as possibly out of date now: the rule of ``trends.score_trends``."""
    seen = trend.get("updated_at")
    age_days = max(0.0, (now - seen) / 86400.0) if isinstance(seen, (int, float)) and not isinstance(seen, bool) else 0.0
    return 0.5 ** (age_days / trends.HALF_LIFE_DAYS) < trends.STALE_BELOW


def for_plans(store: Store, shown: List[Dict[str, Any]], now: Optional[float] = None) -> Dict[str, List[Dict[str, Any]]]:
    """``{plan id: its open suggestions}`` for the plans as they are shown (``planner.plans``). Reads only.

    Each suggestion: ``id``, ``keyword``, ``matched`` (what was found), ``matched_in`` (which of the title,
    the notes and the questions), ``relevance`` (the share of the keyword that was found, 0 to 1), ``score``
    and ``source`` (the keyword's, when it was suggested; ``ai`` means an AI found the keyword on the web and
    nobody verified it), ``rising`` and ``sightings`` (None unless the trend list said them), ``suggested_at``
    and ``run_id``. Left out, and not removed: a suggestion for a posted plan, one whose keyword the plan has
    by now, and one whose keyword is no longer tracked or has gone stale since. At most MAX_OPEN_PER_PLAN each.
    """
    waiting = store.list_plan_suggestions(OPEN)
    if not waiting:
        return {}
    now = time.time() if now is None else now
    tracked = {keyword_key(trend["keyword"]): trend for trend in store.list_trends()}
    plans = {plan["id"]: plan for plan in shown}
    out: Dict[str, List[Dict[str, Any]]] = {}
    for row in waiting:
        plan, trend = plans.get(row["plan_id"]), tracked.get(row["keyword_key"])
        if plan is None or plan["status"] == planner.POSTED or trend is None or _gone_stale(trend, now):
            continue
        if row["keyword_key"] in {keyword_key(keyword) for keyword in plan["keywords"]}:
            continue
        listed = out.setdefault(plan["id"], [])
        if len(listed) < MAX_OPEN_PER_PLAN:
            listed.append({"id": row["id"], "keyword": row["keyword"], "matched": row["matched"],
                           "matched_in": [part for part in row["matched_in"] if part in PARTS],
                           "relevance": row["relevance"], "score": row["score"],
                           "source": "ai" if row["source"] == "ai" else "manual", "rising": row["rising"],
                           "sightings": row["sightings"], "suggested_at": row["created_at"], "run_id": row["run_id"]})
    return out


def _open_suggestion(store: Store, plan: Dict[str, Any], suggestion_id: Any) -> Dict[str, Any]:
    row = store.get_plan_suggestion(suggestion_id)
    if row is None or row["plan_id"] != plan["id"]:
        raise planner.PlannerError("suggestion_not_found")
    if row["status"] != OPEN:
        raise planner.PlannerError("suggestion_decided")
    return row


def take(store: Store, plan_id: Any, suggestion_id: Any, now: Optional[float] = None) -> Dict[str, Any]:
    """The creator takes a suggested keyword: it becomes one of the plan's keywords. Returns the plan as shown.

    The keyword is added through ``planner.change``, after the keywords the plan has, and nothing else about
    the plan is changed. The suggestion is then marked as taken. When the plan holds the keyword already,
    only the mark is made.

    Raises ``planner.PlannerError``: ``not_found`` (no such plan), ``suggestion_not_found`` (no such
    suggestion for this plan), ``suggestion_decided`` (it was taken or dismissed before, or withdrawn),
    ``keywords_full`` (the plan holds as many keywords as a plan can; the suggestion stays open) and
    ``suggestion_unusable`` (the keyword cannot be a plan's keyword). In each case nothing was changed.
    """
    plan = planner.get(store, plan_id)
    row = _open_suggestion(store, plan, suggestion_id)
    key = keyword_key(row["keyword"])
    if key not in {keyword_key(keyword) for keyword in plan["keywords"]}:
        if len(plan["keywords"]) >= MAX_KEYWORDS_PER_SCRIPT:
            raise planner.PlannerError("keywords_full")
        wanted = plan["keywords"] + [row["keyword"]]
        if not key or key not in {keyword_key(keyword) for keyword in normalize_keywords(wanted)}:
            raise planner.PlannerError("suggestion_unusable")       # what the store would drop without a word
        planner.change(store, plan["id"], {"keywords": wanted})
    store.decide_plan_suggestion(row["id"], TAKEN, time.time() if now is None else now)
    return planner.get(store, plan["id"])


def dismiss(store: Store, plan_id: Any, suggestion_id: Any, now: Optional[float] = None) -> Dict[str, Any]:
    """The creator dismisses a suggested keyword: it is not shown, and not offered for this plan again.

    The plan is not changed. Returns it as shown. Raises ``planner.PlannerError``: ``not_found``,
    ``suggestion_not_found``, ``suggestion_decided``.
    """
    plan = planner.get(store, plan_id)
    row = _open_suggestion(store, plan, suggestion_id)
    if not store.decide_plan_suggestion(row["id"], DISMISSED, time.time() if now is None else now):
        raise planner.PlannerError("suggestion_decided")
    return plan


# ---- a fresh set of ideas ---------------------------------------------------------------------------------------
def _ai_ready(store: Store) -> bool:
    """Whether an AI service is set up that can be called directly: the condition ``pipeline.complete`` has."""
    config = store.load_config()
    provider, model = config.get("provider") or "", config.get("model") or ""
    if provider == MANUAL_PROVIDER or not model:
        return False
    if provider == "local":
        return bool(config.get("local_base_url"))
    keys = config.get("keys")
    return bool(isinstance(keys, dict) and keys.get(provider))


def _top_trends(store: Store, now: float) -> List[str]:
    """The trend keywords the ideas prompt carries now, the best score first (``pipeline._current_trends``)."""
    return [trend["keyword"] for trend in pipeline._current_trends(store, now)]


def _trends_look(store: Store, now: float) -> str:
    """A short name for what the ranking of the trend keywords is worked out from: the trend list, how the
    creator has it ranked, the library, and the day."""
    tracked = [[trend["id"], trend["keyword"], trend["heat"], trend["source"], trend["updated_at"]] for trend in store.list_trends()]
    return _short([_day_of(now).isoformat(), tracked, store.load_config().get("trend_weight"), list(store.samples_version())])


def ideas_due(context: autopilot.Context) -> bool:
    """Whether ``ideas.refresh`` has anything to do. Reads only, and spends no model call.

    Not while no AI service is set up that can be called directly (none, or copy and paste), and not while
    the library is empty: there is nothing a run could do. Otherwise: when it never made ideas; when its last
    run made none because the day's limit was used up; when IDEAS_AT_LATEST_SEC have passed since the last
    run began; or when the top trend keywords are not the ones the last run noted. The ranking is worked out
    once for each state of the trend list and the library and remembered in memory until that changes.
    """
    store, now = context.store, context.now
    if not _ai_ready(store) or store.samples_count() == 0:
        return False
    last = context.last_done()
    if last is None or last["summary"].get("refreshed") is not True:
        return True
    began = last.get("started_at")
    if isinstance(began, (int, float)) and now - began >= IDEAS_AT_LATEST_SEC:
        return True
    look = _trends_look(store, now)
    top = _remembered(_TOP, store, look)
    if top is None:
        top = _top_trends(store, now)
        _remember(_TOP, store, look, top)
    noted = last["summary"].get("trends")
    before = {keyword_key(keyword) for keyword in noted if isinstance(keyword, str)} if isinstance(noted, list) else set()
    return {keyword_key(keyword) for keyword in top} != before


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _keep(context: autopilot.Context, made: Dict[str, Any], top: List[str]) -> Dict[str, Any]:
    """Keep the set a run made, and put it into the ideas file where that is allowed (see the module's words).

    Returns ``{"set": its id, "in_place": whether it is in the ideas file, "waits_because": why not}``.
    ``waits_because`` is ``level`` (the creator chose suggestions only) or ``not_autopilots`` (what the ideas
    file holds is not a set an automatic run made, or cannot be kept as it is).

    One step under the lock every save of the ideas takes: what the file holds is read, kept in the new row,
    and only then replaced, so a save by the creator at the same moment is either seen here or comes after.
    How to reverse the run is written down before anything is.
    """
    store = context.store
    data = Store._encode(made)
    set_id = uuid.uuid4().hex[:12]
    with store_module._IDEAS_LOCK:
        current = store.read_recommendations_bytes()
        live = [row for row in store.list_idea_sets() if row["status"] in (WAITING, IN_USE)]
        ours = current is None or any(row["status"] == IN_USE and row["ideas_sha"] == _sha(current) for row in live)
        earlier: Optional[str] = None
        if current is not None and ours:
            try:
                earlier = current.decode("utf-8") if len(current) <= MAX_KEPT_IDEAS_BYTES else None
            except UnicodeDecodeError:
                earlier = None
            ours = earlier is not None      # what cannot be kept exactly is not replaced
        place = ours and context.level == autopilot.AUTO
        context.record_undo(REFRESHED, {"set": set_id})
        store.add_idea_set(
            {"id": set_id, "run_id": context.run_id, "status": IN_USE if place else WAITING,
             "placed_by": BY_RUN if place else "", "ideas_json": data.decode("utf-8"), "ideas_sha": _sha(data),
             "had_ideas": bool(place and current is not None), "earlier": earlier if place else None,
             "trends_json": json.dumps(top, ensure_ascii=False), "created_at": context.now},
            replaces=[row["id"] for row in live if place or row["status"] == WAITING], keep=KEEP_IDEA_SETS)
        if place:
            store.write_recommendations_bytes(data)
    return {"set": set_id, "in_place": place,
            "waits_because": "" if place else ("level" if context.level != autopilot.AUTO else "not_autopilots")}


def refresh_ideas(context: autopilot.Context) -> Dict[str, Any]:
    """The task ``ideas.refresh``: ask the AI service for a fresh set of ideas, and keep it without losing any.

    The prompt is built first, so that a library with nothing in it costs no call. Then one call is reserved
    (``context.spend``); refused, nothing is sent and the summary says ``{"refreshed": false, "reason":
    "daily_limit"}``. The reply is checked the way every ideas reply is (``pipeline.checked_ideas``) and
    marked with the run and the trend keywords the prompt carried (``automatic``). Where it is kept, and
    whether it goes into the ideas file, is ``_keep``'s.

    Raises ``llm.LLMError`` when the library holds no text, the service fails or the reply holds no idea;
    the run is then logged as failed and nothing was kept or replaced.
    """
    store, now = context.store, context.now
    context.progress("reading", 0.1)
    top = _top_trends(store, now)
    system, user = pipeline.ideas_prompt(store, now=now)
    if not context.spend():
        return {"refreshed": False, "reason": "daily_limit", "trends": top}
    context.progress("thinking", 0.3)
    reply = pipeline.complete(store, system, user, context.transport, temperature=pipeline.STAGE_TEMPERATURES["ideas"])
    made = pipeline.checked_ideas(store, reply, now=now)
    made["automatic"] = {"run_id": context.run_id, "trends": top}
    context.progress("keeping", 0.9)
    kept = _keep(context, made, top)
    return dict(kept, refreshed=True, ideas=len(made["ideas"]), trends=top)


def _undo_refreshed(store: Store, payload: Any) -> None:
    """Reverse one run of ``ideas.refresh``: take its set out and put back exactly what was there.

    - The set is still waiting: it is removed, and a set that waited before it waits again.
    - The run put the set into the ideas file and the file still holds it: the file gets back, byte for
      byte, what it held before (or is removed when there was none), the set is removed, and the sets it
      replaced are what they were.
    - Refused (``autopilot.UndoRefused``), with nothing changed: the creator took the set; the ideas file
      holds something else by now; a later set took this one's place in the file.
    - Nothing to do, and so done: the set was dismissed, a newer waiting set took its place before anybody
      took it, or it has dropped out as one of the oldest. None of its ideas is offered or in use then.
    """
    set_id = payload.get("set") if isinstance(payload, dict) else None
    with store_module._IDEAS_LOCK:
        row = store.get_idea_set(set_id)
        if row is None or row["status"] == DISMISSED or (row["status"] == REPLACED and row["was"] != IN_USE):
            return
        if row["status"] == WAITING:
            store.remove_idea_set(row["id"])
            return
        if row["status"] == REPLACED:
            raise autopilot.UndoRefused(
                "Newer ideas took the place of these since. Undo the later refresh first.",
                "这一组选题后来已经被更新的一组替换了，请先撤销后面那一次刷新。")
        if row["status"] != IN_USE:
            return                      # a state this version does not write: nothing of it is in use
        if row["placed_by"] != BY_RUN:
            raise autopilot.UndoRefused(
                "You took these ideas yourself, so they stay. Nothing was changed.",
                "这一组选题是你自己点了采用的，所以保留不动。这次没有改动任何内容。")
        current = store.read_recommendations_bytes()
        if current is None or _sha(current) != row["ideas_sha"]:
            raise autopilot.UndoRefused(
                "The saved ideas were changed after this refresh, so the earlier ones were not put back over them. "
                "Nothing was changed.",
                "这次刷新之后，保存的选题又变过了，所以没有用之前的那一组去覆盖。这次没有改动任何内容。")
        if row["had_ideas"] and row["earlier"] is None:
            raise autopilot.UndoRefused(
                "What the ideas file held before this refresh is not kept any more. Nothing was changed.",
                "这次刷新之前的选题内容已经没有留存了。这次没有改动任何内容。")
        store.write_recommendations_bytes(row["earlier"].encode("utf-8") if row["had_ideas"] else None)
        store.remove_idea_set(row["id"])


def _as_ideas(row: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """The set a row keeps, as the ideas page is given a set, or None when the stored text is not one."""
    if row is None:
        return None
    try:
        held = loads_stored(row["ideas_json"])
    except ValueError:
        return None
    return held if isinstance(held, dict) and isinstance(held.get("ideas"), list) else None


def _about(row: Dict[str, Any]) -> Dict[str, Any]:
    """What is said about a set beside its ideas: which run made it, when, and from which trend keywords."""
    try:
        listed = loads_stored(row["trends_json"])
    except ValueError:
        listed = []
    named = [keyword[:MAX_KEYWORD_CHARS] for keyword in listed if isinstance(keyword, str)] if isinstance(listed, list) else []
    return {"set_id": row["id"], "run_id": row["run_id"], "made_at": row["created_at"],
            "trends": named[:pipeline.IDEAS_MAX_TRENDS], "placed_by": row["placed_by"] if row["placed_by"] in (BY_RUN, BY_CREATOR) else ""}


def ideas_state(store: Store) -> Dict[str, Any]:
    """What the ideas page is told about automatic refreshes (``automatic`` in ``GET /api/recommendations``). Reads only.

    - ``in_use``: None, or, when the saved ideas the page shows ARE a set an automatic run made: ``set_id``,
      ``run_id``, ``made_at``, ``trends`` (the trend keywords the prompt carried) and ``placed_by`` (``run``:
      put there by the run; ``creator``: taken by the creator). Checked against the ideas file each time, so
      ideas saved since by a click are never called automatic.
    - ``waiting``: None, or the set that waits for the creator: the same fields and ``result``, the set as
      the page shows a set of ideas. Taking it replaces the saved ideas, as "Find ideas" does.
    - ``last``: None, or the newest automatic refresh that made ideas: the same fields and ``status``
      (``waiting``, ``in_use``, ``dismissed`` or ``replaced``). When ideas were last refreshed automatically,
      and from which trends, is this.
    """
    rows = store.list_idea_sets()
    try:
        current = store.read_recommendations_bytes()
    except OSError:
        current = None
    held = _sha(current) if current is not None else ""
    in_use = next((row for row in rows if row["status"] == IN_USE and row["ideas_sha"] == held), None)
    waiting = next((row for row in rows if row["status"] == WAITING and _as_ideas(row) is not None), None)
    last = rows[0] if rows else None

    def status_of(row: Dict[str, Any]) -> str:
        if row["status"] == IN_USE:
            return IN_USE if row is in_use else REPLACED      # the file holds other ideas by now
        return row["status"] if row["status"] in (WAITING, DISMISSED, REPLACED) else REPLACED

    return {
        "in_use": _about(in_use) if in_use is not None else None,
        "waiting": dict(_about(waiting), result=_as_ideas(waiting)) if waiting is not None else None,
        "last": dict(_about(last), status=status_of(last)) if last is not None else None,
    }


def take_ideas(store: Store, set_id: Any, now: Optional[float] = None) -> Dict[str, Any]:
    """The creator takes the waiting set: it becomes the saved ideas. Returns ``ideas_state`` as it then is.

    It replaces the ideas that are saved, as "Find ideas" does: the creator asked for it. What it replaces
    is not kept. The file is written first and the set marked afterwards, so a step cut off in between
    leaves the set waiting and can be taken again.

    Raises ``Refused``: ``ideas_set_not_found``, ``ideas_set_not_waiting``, ``ideas_set_unreadable``.
    """
    with store_module._IDEAS_LOCK:
        row = store.get_idea_set(set_id)
        if row is None:
            raise Refused("ideas_set_not_found")
        if row["status"] != WAITING:
            raise Refused("ideas_set_not_waiting")
        if _as_ideas(row) is None:
            raise Refused("ideas_set_unreadable")
        store.write_recommendations_bytes(row["ideas_json"].encode("utf-8"))
        others = [other["id"] for other in store.list_idea_sets() if other["status"] == IN_USE]
        store.decide_idea_set(row["id"], expect=WAITING, status=IN_USE, now=time.time() if now is None else now,
                              placed_by=BY_CREATOR, replaces=others)
    return ideas_state(store)


def dismiss_ideas(store: Store, set_id: Any, now: Optional[float] = None) -> Dict[str, Any]:
    """The creator dismisses the waiting set: it is not offered again. The saved ideas are not touched.

    Returns ``ideas_state`` as it then is. Raises ``Refused``: ``ideas_set_not_found``, ``ideas_set_not_waiting``.
    """
    row = store.get_idea_set(set_id)
    if row is None:
        raise Refused("ideas_set_not_found")
    if not store.decide_idea_set(row["id"], expect=WAITING, status=DISMISSED, now=time.time() if now is None else now):
        raise Refused("ideas_set_not_waiting")
    return ideas_state(store)


# ---- the two tasks, as autopilot knows them ---------------------------------------------------------------------
# Registering puts them on a list and nothing more (``autopilot.register_task``). Each stays off until the
# creator switches it on in the settings.
PLANS_TASK = autopilot.register_task(PLANS_TASK_NAME, suggest_for_plans, every_sec=PLANS_EVERY_SEC, due=plans_due)
IDEAS_TASK = autopilot.register_task(IDEAS_TASK_NAME, refresh_ideas, every_sec=IDEAS_EVERY_SEC, calls_model=True,
                                     due=ideas_due)
UNDO = {SUGGESTED: _undo_suggested, REFRESHED: _undo_refreshed}
for _kind, _handler in UNDO.items():
    autopilot.register_undo(_kind, _handler)


def register(registry: autopilot.Registry) -> None:
    """Put the two tasks and what reverses their changes into another registry: one a test made for itself."""
    registry.add(PLANS_TASK)
    registry.add(IDEAS_TASK)
    for kind, handler in UNDO.items():
        registry.add_undo(kind, handler)
