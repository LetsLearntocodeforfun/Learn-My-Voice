"""Autopilot: what the app does by itself while it is open, on a schedule, within what the creator chose.

This module is the core and nothing else. It keeps the list of tasks other modules register, decides when each
is due, runs them one at a time, writes down what every run did, counts the model calls they make against a
daily limit, and can reverse what a run changed. It has no task of its own, fetches nothing from anywhere and
calls no model: what a task does is the business of the module that registered it.

Switches. All of them have to be on before a task runs:

- ``Autopilot.on`` (``server.App.autopilot_on``): whether this start of the app may run tasks at all. It is off
  unless ``server.main`` turns it on: never for a server a test made, never for a trial of a package or of an
  installer, and not when the app is started with ``LEARN_MY_VOICE_NO_AUTOPILOT=1``.
- ``enabled`` in the creator's settings: off until the creator switches it on.
- the task's own switch in the settings (``tasks``): off until the creator switches that task on. A task a
  later version adds is therefore off until the creator has chosen it.

With any of them off the scheduler starts nothing, and a run asked for by hand (``run_now``) is refused with a
sentence in English and in Chinese. Reading the state, changing the settings and undoing a run work whatever
the switches say: a creator who switched autopilot off can still take back what it did.

A task (``Task``) has a name such as ``trends.refresh``, a cadence in seconds, whether it calls a model, an
optional further condition ``due(context)``, and ``run(context)``, which returns a small summary. Registering
one (``register_task``) only puts it on a list: nothing is imported, started or read.

When a task runs (``Autopilot.run_due``, called by the scheduler thread once a minute while the app is open):

- A task is due when its cadence has passed since its last run began, whoever started that run. One that never
  ran is due at once. So when the app is opened after a while, everything overdue is caught up on, the one that
  has waited longest first. Nothing runs while the app is closed: no service or login item is installed. A
  last run dated more than a cadence after now (the computer's clock was set back since) is not believed:
  the task is due, and is not left waiting until the clock has caught up.
- When a task has a ``due(context)`` condition it is asked once the cadence has passed, and again at every look
  until it says yes. A task that is not due leaves no line in the log.
- One task runs at a time, also when one was started by hand. The scheduler waits for it before it looks again.
  Nothing here cuts a task off: one that waits for ever keeps every other task waiting, and every restore
  refused, until the app is closed. A task has to put a limit on everything it waits for.
- A task that raises is logged as ``failed`` with what it said, cut to MAX_DETAIL_CHARS. The others still run.
  It is tried again after RETRY_AFTER_SEC, then after twice that, four times that and so on while it keeps
  failing, up to 128 times that (RETRY_LOOKBACK failures in a row are counted), and never later than its own
  cadence. A run the app was closed in the middle of is found at the next start, marked ``interrupted``, and
  tried again by the same rule.
- A task that calls a model is not started once the day's limit of automatic model calls is used up. It is
  logged as ``skipped``, once, and leaves no further line while the limit holds it back. It has its turn when
  calls are to be had again: when the next day begins, or as soon as the creator raises the limit. Nothing is
  queued behind the creator's back: a task held back for days then runs once, not once for each of them.
  Inside a run every model call is reserved first (``Context.spend``); a refusal there is counted on the run
  (``calls_refused``). The limit is ``daily_model_calls`` in the settings,
  DEFAULT_DAILY_CALLS unless the creator changed it, 0 to MAX_DAILY_CALLS. It counts the calls of runs the
  scheduler started, for the day (by this computer's clock) the run began. A run the creator started by hand
  is their own click, like every other button that calls a model: it is not held back and not counted.

The log is the table ``autopilot_runs``: which task, when it began and ended, how it ended, whether it was
started by hand, at which level, the model calls it made, its summary, how to reverse it, and when it was
reversed. Of each task the newest KEEP_RUNS_PER_TASK runs are kept and all of today's; of all tasks together
the newest MAX_RUNS. The oldest go first, and a run that is gone can no longer be undone. The table is part of
a backup, like the rest of the database.

Undo. While it runs, a task says for every change how to reverse it (``Context.record_undo(kind, payload)``);
that is written down at once, so it is there also when the run then fails or the app is closed. The module
that made the change registers what reverses a ``kind`` (``register_undo``). ``Autopilot.undo(run_id)``
reverses a run's changes, the last one first, once. What cannot be undone is refused in both languages; when
one change of several cannot be put back, those already put back are struck off, so a second try does not
reverse them twice.

Restoring a backup, and moving data in from an earlier folder, replace the database under everything. So a
run begins inside the gate every change passes (``backup.Backups.passage``): while data is being replaced it
waits there, and afterwards looks again at whether it is still due, in the log that is there then. The run
itself is a job of the app's ``Jobs`` (kind JOB_KIND), like a transcription: a restore or a migration is
refused while it is unfinished, and the page can follow it.

Settings are kept in ``config.json`` under ``autopilot`` and cleaned every time they are read, so a damaged
or hand-edited file cannot switch anything on: ``enabled``, ``level`` (``suggest`` or ``auto``; what the two
mean for a change is up to each task), ``daily_model_calls`` and ``tasks``. Other modules keep settings of
their own beside these (``save_other_setting``); saving these four leaves theirs as they are. ``clean_stored``
cleans the whole value at once, also one read from another folder's file. The settings belong to the
computer: a backup does not carry them and a restore does not change them.

Standard library only.
"""
from __future__ import annotations

import contextlib
import copy
import json
import math
import re
import sqlite3
import threading
import time
import traceback
import types
import uuid
from typing import TYPE_CHECKING, Any, Callable, Dict, Iterator, List, Mapping, Optional, Set, Tuple

from .jobs import JobError, Jobs
from .store import loads_stored

if TYPE_CHECKING:
    from .store import Store

# Set to 1 by anything that starts the app without a person in front of it: no task runs then (studio/server.py).
NO_AUTOPILOT_ENV = "LEARN_MY_VOICE_NO_AUTOPILOT"
KEY = "autopilot"                       # where the settings are kept in config.json
JOB_KIND = "autopilot"                  # the kind, and the lane, of the job a run is

SUGGEST, AUTO = "suggest", "auto"
LEVELS = (SUGGEST, AUTO)                # a change waits for the creator's yes, or is made within the task's own bounds
OWN_SETTINGS = ("enabled", "level", "daily_model_calls", "tasks")

RUNNING, DONE, FAILED, SKIPPED, INTERRUPTED = "running", "done", "failed", "skipped", "interrupted"
STATUSES = (RUNNING, DONE, FAILED, SKIPPED, INTERRUPTED)
DAILY_CAP = "daily_cap"                 # ``detail`` of a run that was skipped: the day's model calls were used up

DEFAULT_DAILY_CALLS = 12                # automatic model calls a day, unless the creator chose another number
MAX_DAILY_CALLS = 200                   # the most the creator can choose; 0 means no automatic model call at all
MAX_CALLS_AT_ONCE = 50                  # what one ``spend`` may ask for: more than that is a mistake in a task
# The shortest cadence a task may have. Each turn the scheduler gives a task writes at most one line in the log, so
# this is also what keeps those to 144 a day for one task. Runs started by hand come on top; MAX_RUNS bounds all.
MIN_EVERY_SEC = 600
MAX_EVERY_SEC = 30 * 86400              # the longest: the log is read to know when a task last ran
MAX_TASKS = 24                          # tasks one registry holds; with KEEP_RUNS_PER_TASK, fewer lines than MAX_RUNS
MAX_TASKS_KEPT = 64                     # task switches kept in the settings, also for tasks this version does not have
MAX_OTHER_SETTINGS = 40                 # settings of other modules that are handed to a task, or carried to another folder
LOOK_EVERY_SEC = 60.0                   # how often the scheduler looks for what is due
FIRST_LOOK_AFTER_SEC = 5.0              # after the app started: the page loads first
RETRY_AFTER_SEC = 3600                  # a failed task is tried again after this, doubled for each failure in a row
RETRY_LOOKBACK = 8                      # how many of a task's newest runs are read to count failures in a row: the wait stops doubling there
KEEP_RUNS_PER_TASK = 400                # lines of the log kept for one task (and all of today's)
MAX_RUNS = 10000                        # lines kept in all, whatever a database from elsewhere holds
RECENT_RUNS = 50                        # lines the page is given
MAX_SUMMARY_CHARS = 4000                # a run's summary as JSON; a longer one is not kept
MAX_DETAIL_CHARS = 300                  # what a failed task said
MAX_UNDO_RECORDS = 200                  # changes one run can say how to reverse
MAX_UNDO_CHARS = 20000                  # one of them as JSON: an id or a few, not a copy of what was changed

_NAME = re.compile(r"[a-z][a-z0-9_]{0,29}(?:\.[a-z][a-z0-9_]{0,29}){1,2}", re.ASCII)
_RUN_ID = re.compile(r"[0-9a-f]{12}", re.ASCII)
_OTHER_KEY = re.compile(r"[a-z][a-z0-9_]{0,39}", re.ASCII)

UndoHandler = Callable[["Store", Any], None]

# Refusals: code -> (HTTP status, English, Chinese). Both sentences are written to be shown as they are.
REFUSALS = {
    "autopilot_off_here": (
        409,
        "Autopilot does not run in this start of the app: it was started with LEARN_MY_VOICE_NO_AUTOPILOT=1, or "
        "by a script that tries the app. Nothing was run.",
        "这一次启动不运行自动任务：应用是用 LEARN_MY_VOICE_NO_AUTOPILOT=1 启动的，或者是由试运行的脚本启动的。没有运行任何任务。"),
    "autopilot_not_enabled": (
        409, "Autopilot is switched off in Settings. Switch it on there first. Nothing was run.",
        "自动任务在「设置」里是关着的，请先在那里打开。没有运行任何任务。"),
    "autopilot_task_off": (
        409, "This task is switched off in Settings, so it was not run. Switch the task on there first.",
        "这项任务在「设置」里是关着的，所以没有运行。请先在那里打开这项任务。"),
    "autopilot_unknown_task": (404, "There is no automatic task with that name.", "没有叫这个名字的自动任务。"),
    "autopilot_busy": (
        409, "An automatic task is running right now. Wait until it is done, then try again. Nothing was changed.",
        "现在有一项自动任务正在运行。请等它完成以后再试。没有改动任何内容。"),
    "autopilot_bad_setting": (
        400, "That was not saved: %(field)s %(rule)s. Nothing was changed.",
        "这一项没有保存：%(field)s %(rule_zh)s。没有改动任何内容。"),
    "autopilot_run_not_found": (
        404, "That run is no longer in the log, so it cannot be undone.", "记录里已经没有这一次运行了，所以没法撤销。"),
    "autopilot_nothing_to_undo": (
        409, "That run changed nothing that can be undone.", "这一次运行没有做可以撤销的改动。"),
    "autopilot_already_undone": (
        409, "That run was already undone. Nothing was changed this time.", "这一次运行已经撤销过了。这次没有改动任何内容。"),
    "autopilot_undo_unknown_kind": (
        409,
        "That run made a change this version of the app does not know how to put back, so nothing was undone.",
        "那一次运行做的改动里，有这个版本的应用不知道怎么恢复的内容，所以什么都没有撤销。"),
    "autopilot_undo_refused": (
        409,
        "Not everything was put back: %(undone)d of %(total)d changes were undone, then one was not. %(why)s",
        "没有全部恢复：%(total)d 处改动里撤销了 %(undone)d 处，接下来的一处没有撤销。%(why_zh)s"),
    "autopilot_undo_failed": (
        500,
        "Not everything could be put back: %(undone)d of %(total)d changes were undone, then one failed (%(why)s). "
        "Try again: what was put back already is not undone a second time.",
        "没能全部恢复：%(total)d 处改动里撤销了 %(undone)d 处，接下来的一处出错了（%(why)s）。可以再试一次：已经恢复的不会再撤销第二遍。"),
}
# What a setting has to be, for the refusal above: field -> (English, Chinese).
_RULES = {
    "settings": ("have to be a JSON object", "必须是一个 JSON 对象"),
    "enabled": ("has to be true or false", "只能是 true 或 false"),
    "level": ('has to be "suggest" or "auto"', "只能是 \"suggest\" 或 \"auto\""),
    "daily_model_calls": ("has to be a whole number from 0 to %d" % MAX_DAILY_CALLS, "必须是 0 到 %d 之间的整数" % MAX_DAILY_CALLS),
    "tasks": ("has to name tasks, each with true or false, and at most %d of them" % MAX_TASKS_KEPT,
              "必须是任务的名字，每个配 true 或 false，最多 %d 个" % MAX_TASKS_KEPT),
    "": ("is not a setting that is changed here", "不是在这里改的设置"),
}


class AutopilotError(Exception):
    """A request about autopilot that cannot be met. Unless the sentence says otherwise, nothing was changed.

    ``code`` is one of REFUSALS; ``status`` is the HTTP status for it; ``message`` (English) and ``message_zh``
    are written to be shown; ``extra`` holds further fields for the answer.
    """

    def __init__(self, code: str, extra: Optional[Dict[str, Any]] = None, **words: Any) -> None:
        self.code = code
        self.status, message, message_zh = REFUSALS[code]
        self.message = message % words if words else message
        self.message_zh = message_zh % words if words else message_zh
        self.extra = dict(extra or {})
        super().__init__(self.message)


class UndoRefused(Exception):
    """Raised by an undo handler that will not put a change back, with the reason in both languages.

    For what the handler can foresee: the creator has changed the same thing since, and putting the old state
    back would take their newer work away. The undo stops there; nothing after it is reversed.
    """

    def __init__(self, message: str, message_zh: str) -> None:
        super().__init__(message)
        self.message, self.message_zh = str(message), str(message_zh)


def _bad_setting(field: Any) -> AutopilotError:
    name = str(field)[:60]
    rule, rule_zh = _RULES[name if name in _RULES else ""]
    return AutopilotError("autopilot_bad_setting", extra={"field": name}, field=name, rule=rule, rule_zh=rule_zh)


# ---- tasks, and who reverses what ------------------------------------------------------------------------------
class Task:
    """One thing the app may do by itself, as a module registers it.

    - ``name``: lower-case words joined by dots, ``area.what``: ``trends.refresh``, ``voice.ingest_folder``.
      It is what the log, the settings and the page call the task, so it is never changed once it shipped.
    - ``run(context)``: does the work and returns a summary, a small dict of plain JSON values (or None for an
      empty one). The summary is kept in the log and shown to the creator: counts and names, no key, nothing
      long (MAX_SUMMARY_CHARS as JSON; a longer one is not kept). The next run can read it
      (``Context.last_done``). Raising means the run failed; what was raised is logged, cut short.
    - ``every_sec``: the cadence, MIN_EVERY_SEC to MAX_EVERY_SEC. The task is not started sooner than this after
      its last run began.
    - ``calls_model``: whether a run may call a model. Such a task is not started when the day's limit is used
      up, and has to reserve every call with ``context.spend()`` before it makes it.
    - ``due(context)``: an optional further condition, asked once the cadence has passed and then at every look
      until it is true. It only reads and has to be quick: while it is asked no other task can start and no
      backup can be restored. Say here that there is nothing to do (no new file, no new trend): a task that is
      not due leaves no line in the log.
    """

    __slots__ = ("name", "run", "every_sec", "calls_model", "due")

    def __init__(self, name: str, run: Callable[["Context"], Optional[Dict[str, Any]]], *, every_sec: float,
                 calls_model: bool = False, due: Optional[Callable[["Context"], bool]] = None) -> None:
        if not isinstance(name, str) or not _NAME.fullmatch(name):
            raise ValueError("a task is named in lower-case words joined by dots, such as trends.refresh: %r" % (name,))
        if not callable(run) or (due is not None and not callable(due)):
            raise ValueError("run, and due when it is given, have to be functions that take the context")
        if isinstance(every_sec, bool) or not isinstance(every_sec, (int, float)) \
                or not MIN_EVERY_SEC <= every_sec <= MAX_EVERY_SEC:
            raise ValueError("every_sec has to be %d to %d seconds: %r" % (MIN_EVERY_SEC, MAX_EVERY_SEC, every_sec))
        if not isinstance(calls_model, bool):
            raise ValueError("calls_model has to be True or False")
        self.name, self.run, self.every_sec, self.calls_model, self.due = name, run, float(every_sec), calls_model, due

    def __repr__(self) -> str:
        return "Task(%r, every_sec=%r, calls_model=%r)" % (self.name, self.every_sec, self.calls_model)


class Registry:
    """The tasks there are, in the order they were registered, and what reverses each kind of change.

    The app uses one, ``REGISTRY``, which modules fill when they are imported (``register_task``,
    ``register_undo``). A test makes its own and hands it to ``Autopilot``, so it runs only what it put there.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._tasks: Dict[str, Task] = {}
        self._undo: Dict[str, UndoHandler] = {}

    def add(self, task: Task) -> Task:
        """Put a task on the list and return it. A name that is taken, or a task beyond MAX_TASKS, raises ``ValueError``."""
        if not isinstance(task, Task):
            raise ValueError("a registry holds Task objects")
        with self._lock:
            if task.name in self._tasks:
                raise ValueError("a task called %s is registered already" % task.name)
            if len(self._tasks) >= MAX_TASKS:
                raise ValueError("a registry holds at most %d tasks" % MAX_TASKS)
            self._tasks[task.name] = task
        return task

    def add_undo(self, kind: str, handler: UndoHandler) -> None:
        """Say what reverses a change of this ``kind``: ``handler(store, payload)``.

        ``kind`` is named like a task (``trends.added``) and, like a task's name, never changed once it shipped:
        it is written into the log with every change. ``payload`` is what ``Context.record_undo`` was given, as
        plain JSON. The handler puts that one change back and returns; it is called at most once for each
        recorded change. It raises ``UndoRefused`` when it will not (the creator changed the same thing since),
        and anything else when it cannot. A kind that is taken raises ``ValueError``.
        """
        if not isinstance(kind, str) or not _NAME.fullmatch(kind):
            raise ValueError("a kind of change is named like a task, such as trends.added: %r" % (kind,))
        if not callable(handler):
            raise ValueError("an undo handler is a function that takes the store and the payload")
        with self._lock:
            if kind in self._undo:
                raise ValueError("an undo handler for %s is registered already" % kind)
            self._undo[kind] = handler

    def tasks(self) -> List[Task]:
        with self._lock:
            return list(self._tasks.values())

    def task(self, name: Any) -> Optional[Task]:
        with self._lock:
            return self._tasks.get(name) if isinstance(name, str) else None

    def undo_handler(self, kind: Any) -> Optional[UndoHandler]:
        with self._lock:
            return self._undo.get(kind) if isinstance(kind, str) else None


REGISTRY = Registry()                   # the one the running app uses


def register_task(name: str, run: Callable[["Context"], Optional[Dict[str, Any]]], *, every_sec: float,
                  calls_model: bool = False, due: Optional[Callable[["Context"], bool]] = None) -> Task:
    """Register a task with the app (``REGISTRY``) and return it. See ``Task`` for what each argument means.

    Call it where the module is imported. It only puts the task on a list: nothing is started, read or
    imported, and the task stays off until the creator switches it on in the settings.
    """
    return REGISTRY.add(Task(name, run, every_sec=every_sec, calls_model=calls_model, due=due))


def register_undo(kind: str, handler: UndoHandler) -> None:
    """Register with the app (``REGISTRY``) what reverses a change of this ``kind``. See ``Registry.add_undo``."""
    REGISTRY.add_undo(kind, handler)


# ---- what a task is handed -------------------------------------------------------------------------------------
class Context:
    """What a task is handed when it runs, and when its ``due`` condition is asked.

    - ``store``: the app's ``Store``.
    - ``now``: the time by the app's clock, in seconds, read anew each time (in a test, the clock the test
      handed in). A task takes the time from here and not from ``time.time()``.
    - ``settings``: a copy of everything kept under ``autopilot`` in the settings that cannot be changed: this
      module's four values, cleaned, and beside them whatever other modules keep there, as it is stored. The
      module that owns a value checks it before it relies on it.
    - ``level``: ``suggest`` or ``auto``, as the creator chose. With ``suggest`` a task leaves what it found as
      a proposal; with ``auto`` it may make the change, within its own bounds, and says how to reverse it.
    - ``transport``: the model transport to hand to ``studio.llm`` (None for the real one; a stand-in in tests).
    - ``transcriber``: what the app transcribes with (``server.App.transcriber``: the ``studio.transcribe``
      module unless a test handed the app a stand-in), or None when this autopilot was handed none.
    - ``run_id``: the id of this run in the log; ``""`` while only ``due`` is asked.
    - ``task``: the task's name. ``forced``: whether the creator started this run by hand.
    - ``spend(calls=1)``, ``record_undo(kind, payload)``, ``progress(stage, fraction)``, ``last_done(task)``:
      see each.
    """

    __slots__ = ("store", "settings", "level", "transport", "transcriber", "run_id", "task", "forced", "_pilot", "_progress")

    def __init__(self, pilot: "Autopilot", task: str, run_id: str, forced: bool,
                 progress: Optional[Callable[[str, Optional[float]], None]]) -> None:
        self._pilot, self._progress = pilot, progress
        self.store = pilot.store
        self.settings: Mapping[str, Any] = _for_tasks(pilot.store)
        self.level: str = self.settings["level"]
        self.transport = pilot._transport()
        self.transcriber = pilot._transcriber()
        self.run_id, self.task, self.forced = run_id, task, bool(forced)

    @property
    def now(self) -> float:
        return float(self._pilot.clock())

    def spend(self, calls: int = 1) -> bool:
        """Reserve ``calls`` model calls before making them. False: the day's limit is used up, do not make them.

        Call it once before every model call, or once for several that belong together. True means they are
        counted on this run and against today's limit, also when the call then fails. False means nothing was
        reserved; the task leaves that part out, finishes what it can and says so in its summary. A run the
        creator started by hand is always told True: its calls are counted on the run and not against the limit.
        While only ``due`` is asked the answer is False: nothing is spent on deciding whether to run.
        """
        if isinstance(calls, bool) or not isinstance(calls, int) or not 1 <= calls <= MAX_CALLS_AT_ONCE:
            raise ValueError("spend takes a whole number of calls from 1 to %d: %r" % (MAX_CALLS_AT_ONCE, calls))
        if not self.run_id:
            return False
        return self._pilot._spend(self.run_id, self.forced, calls)

    def record_undo(self, kind: str, payload: Any) -> None:
        """Write down how to reverse a change this run makes: the handler registered for ``kind`` gets ``payload``.

        It is stored at once, so it is there also when the run fails afterwards or the app is closed. Record
        one for every change, in the order the changes are made; an undo reverses them last first. Record it
        before making the change or right after: a change with no record cannot be taken back.

        ``payload`` is plain JSON, at most MAX_UNDO_CHARS: the ids of what was added, the id of the earlier
        version to go back to. Not a copy of the creator's material. Raises ``ValueError`` when no handler is
        registered for ``kind``, when the payload is not plain JSON or too long, or when this run already has
        MAX_UNDO_RECORDS; ``RuntimeError`` when called while only ``due`` is asked.
        """
        if not self.run_id:
            raise RuntimeError("record_undo is for a run: a due condition changes nothing")
        self._pilot._record_undo(self.run_id, kind, payload)

    def progress(self, stage: str, fraction: Optional[float] = None) -> None:
        """Tell the page how far the run has come: a short word for the stage and, when known, a fraction from 0 to 1."""
        if self._progress is not None:
            self._progress(str(stage)[:80], fraction)

    def last_done(self, task: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """The newest earlier run of ``task`` (this task when left out) that ended ``done``, as the log has it, or None.

        ``{"id", "task", "status", "forced", "level", "started_at", "finished_at", "model_calls", "summary", ...}``.
        For a condition such as "seven days since the last one, or three new videos", and for comparing with
        what the last run's summary noted. None also when that run has dropped out of the log.
        """
        return self._pilot._last_done(task if task is not None else self.task, self.run_id)


# ---- the settings ----------------------------------------------------------------------------------------------
def _stored(store: "Store") -> Dict[str, Any]:
    held = store.load_config().get(KEY)
    return held if isinstance(held, dict) else {}


def _clean_tasks(raw: Any) -> Dict[str, bool]:
    """The task switches out of what is stored: names written like a task's, each with true or false, the first MAX_TASKS_KEPT."""
    kept: Dict[str, bool] = {}
    if isinstance(raw, dict):
        for name, value in raw.items():
            if len(kept) >= MAX_TASKS_KEPT:
                break
            if isinstance(name, str) and _NAME.fullmatch(name) and isinstance(value, bool):
                kept[name] = value
    return kept


def _clean(raw: Any) -> Dict[str, Any]:
    """This module's four settings out of whatever is stored. Anything that is not as this module writes it counts as never set."""
    held = raw if isinstance(raw, dict) else {}
    level, calls = held.get("level"), held.get("daily_model_calls")
    if isinstance(calls, bool) or not isinstance(calls, int) or not 0 <= calls <= MAX_DAILY_CALLS:
        calls = DEFAULT_DAILY_CALLS
    return {
        "enabled": held.get("enabled") is True,
        "level": level if isinstance(level, str) and level in LEVELS else SUGGEST,
        "daily_model_calls": calls,
        "tasks": _clean_tasks(held.get("tasks")),
    }


def clean_stored(value: Any) -> Dict[str, Any]:
    """Everything kept under ``autopilot`` in a settings file, made safe to use and to carry elsewhere.

    ``value`` is whatever a ``config.json`` holds under that name: this folder's, or another folder's when data
    is moved between versions. Returned: this module's four values, cleaned as ``settings`` cleans them (anything
    that is not as this module writes it counts as never set, so a damaged value switches nothing on), and
    beside them what other modules keep there, as it is stored. Of those, the ones with a name
    ``save_other_setting`` accepts are kept, at most MAX_OTHER_SETTINGS; what is inside them is not looked at
    and not copied: the module that owns a value checks it. A value that is no JSON object gives a new
    folder's settings and nothing else.
    """
    held = value if isinstance(value, dict) else {}
    kept: Dict[str, Any] = {}
    for key, stored in held.items():
        if len(kept) >= MAX_OTHER_SETTINGS:
            break
        if isinstance(key, str) and _OTHER_KEY.fullmatch(key) and key not in OWN_SETTINGS:
            kept[key] = stored
    kept.update(_clean(held))
    return kept


def settings(store: "Store") -> Dict[str, Any]:
    """The creator's settings for autopilot: ``{"enabled", "level", "daily_model_calls", "tasks": {name: bool}}``.

    Read from ``config.json`` and cleaned each time. A file that is damaged, or was edited by hand into
    something else, gives what a new folder gives: off, ``suggest``, DEFAULT_DAILY_CALLS, no task switched on.
    A task that is not in ``tasks`` is off.
    """
    return _clean(_stored(store))


def _for_tasks(store: "Store") -> Mapping[str, Any]:
    """What ``Context.settings`` is: ``clean_stored`` of what this folder holds, as a copy, with no way to change it."""
    try:
        held = copy.deepcopy(clean_stored(_stored(store)))
    except RecursionError:
        held = _clean(None)
    return types.MappingProxyType(held)


def save_settings(store: "Store", changes: Any) -> Dict[str, Any]:
    """Change some of the four settings and return all four as they then are (``settings``).

    ``changes`` names only what is to change: ``enabled`` (true or false), ``level`` (``suggest`` or ``auto``),
    ``daily_model_calls`` (a whole number, 0 to MAX_DAILY_CALLS), ``tasks`` (``{name: true or false}``, which
    sets those switches and leaves the other tasks' as they are). Whatever other modules keep beside these
    stays exactly as it is. Anything else in ``changes``, or a value of another kind, refuses the whole
    request: ``AutopilotError("autopilot_bad_setting")``, and nothing is saved.
    """
    if not isinstance(changes, dict):
        raise _bad_setting("settings")
    for field in changes:
        if field not in OWN_SETTINGS:
            raise _bad_setting(field)
    wanted: Dict[str, Any] = {}
    if "enabled" in changes:
        if not isinstance(changes["enabled"], bool):
            raise _bad_setting("enabled")
        wanted["enabled"] = changes["enabled"]
    if "level" in changes:
        if not isinstance(changes["level"], str) or changes["level"] not in LEVELS:
            raise _bad_setting("level")
        wanted["level"] = changes["level"]
    if "daily_model_calls" in changes:
        calls = changes["daily_model_calls"]
        if isinstance(calls, bool) or not isinstance(calls, int) or not 0 <= calls <= MAX_DAILY_CALLS:
            raise _bad_setting("daily_model_calls")
        wanted["daily_model_calls"] = calls
    switches = changes.get("tasks")
    if "tasks" in changes and (not isinstance(switches, dict) or len(switches) > MAX_TASKS_KEPT
                               or _clean_tasks(switches) != switches):
        raise _bad_setting("tasks")

    def change(config: Dict[str, Any]) -> None:
        held = dict(config[KEY]) if isinstance(config.get(KEY), dict) else {}
        held.update(wanted)
        if switches is not None:
            merged = _clean_tasks(held.get("tasks"))
            for name in switches:
                merged.pop(name, None)                       # one set again goes to the end: the newest are the ones kept
            merged.update(switches)
            held["tasks"] = dict(list(merged.items())[-MAX_TASKS_KEPT:])
        config[KEY] = held

    store.update_config(change)
    return settings(store)


def save_other_setting(store: "Store", key: str, value: Any) -> None:
    """Keep a setting of another module beside this module's four, under ``autopilot`` in ``config.json``.

    For the module that owns it: the watched folder, the way videos are transcribed, the sources that are
    switched on. A task reads it back as ``context.settings[key]``, as it was stored: the owner checks it on
    every read, because the file may have been edited by hand. ``None`` removes the key. Raises ``ValueError``
    for one of this module's own four names, a key that is not a short lower-case name, or a value that is not
    plain JSON; nothing is saved then.
    """
    if not isinstance(key, str) or not _OTHER_KEY.fullmatch(key) or key in OWN_SETTINGS:
        raise ValueError("a setting of another module has a short lower-case name of its own: %r" % (key,))
    if value is not None:
        try:
            json.dumps(value, ensure_ascii=False, allow_nan=False)
        except (TypeError, ValueError, RecursionError) as exc:
            raise ValueError("a setting has to be plain JSON: %s" % exc)

    def change(config: Dict[str, Any]) -> None:
        held = dict(config[KEY]) if isinstance(config.get(KEY), dict) else {}
        if value is None:
            held.pop(key, None)
        else:
            held[key] = value
        config[KEY] = held

    store.update_config(change)


# ---- the log ---------------------------------------------------------------------------------------------------
@contextlib.contextmanager
def _db(store: "Store") -> Iterator[sqlite3.Connection]:
    """A connection to the database that is closed when the block ends, whatever happened in it.

    One that is left open keeps the file from being replaced or removed on Windows.
    """
    with contextlib.closing(store._db()) as db:
        yield db


def _number(value: Any) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return float(value)


def _count(value: Any) -> int:
    """A stored count as a whole number from 0 to a thousand million, whatever is stored: a huge one stays huge, not nought."""
    number = _number(value)
    return int(min(max(number, 0.0), 1e9)) if number is not None else 0


def _undo_records(text: Any) -> List[Dict[str, Any]]:
    """The changes a run said how to reverse, in the order they were made; ``[]`` when the stored text is anything else."""
    try:
        listed = loads_stored(text) if isinstance(text, str) else None
    except ValueError:
        return []
    if not isinstance(listed, list) or len(listed) > MAX_UNDO_RECORDS:
        return []
    for record in listed:
        if not isinstance(record, dict) or not isinstance(record.get("kind"), str) or "payload" not in record:
            return []
    return listed


def _status_of(row: Any, current: Optional[str]) -> str:
    """How a run stands. One the log calls running that is not the run under way now was cut off: interrupted."""
    status = row["status"] if row["status"] in STATUSES else INTERRUPTED
    return INTERRUPTED if status == RUNNING and row["id"] != current else status


def _public(row: Any, current: Optional[str] = None) -> Dict[str, Any]:
    """One line of the log as it is handed out. Whatever is stored in it, this can be shown."""
    try:
        summary = loads_stored(row["summary_json"]) if isinstance(row["summary_json"], str) else {}
    except ValueError:
        summary = {}
    status = _status_of(row, current)
    changes = len(_undo_records(row["undo_json"]))
    undone_at = _number(row["undone_at"])
    return {
        "id": row["id"] if isinstance(row["id"], str) else "",
        "task": row["task"] if isinstance(row["task"], str) else "",
        "status": status,
        "forced": bool(_count(row["forced"])),
        "level": row["level"] if row["level"] in LEVELS else SUGGEST,
        "started_at": _number(row["started_at"]),
        "finished_at": _number(row["finished_at"]),
        "model_calls": _count(row["model_calls"]),
        "calls_refused": _count(row["calls_refused"]),
        "summary": summary if isinstance(summary, dict) else {},
        "detail": row["detail"][:MAX_DETAIL_CHARS] if isinstance(row["detail"], str) else "",
        "changes": changes,
        "can_undo": bool(changes) and undone_at is None and status != RUNNING,
        "undone_at": undone_at,
    }


def recent_runs(store: "Store", limit: int = RECENT_RUNS) -> List[Dict[str, Any]]:
    """The newest lines of the log, the newest first, as the page is given them. Reads only.

    Without the running app beside it this cannot know which run is under way, so a line the log calls running
    is given as ``interrupted``; ``Autopilot.status`` is what the page asks.
    """
    with _db(store) as db:
        rows = db.execute("SELECT * FROM autopilot_runs ORDER BY rowid DESC LIMIT ?", (max(1, min(int(limit), 500)),)).fetchall()
    return [_public(row) for row in rows]


def _newest(db: sqlite3.Connection, task: str, limit: int) -> List[Any]:
    return db.execute("SELECT * FROM autopilot_runs WHERE task = ? ORDER BY rowid DESC LIMIT ?", (task, limit)).fetchall()


def _day_bounds(now: float) -> Tuple[float, float]:
    """When the day ``now`` is in began and when it ends, by this computer's clock."""
    try:
        day = time.localtime(now)
        return (time.mktime((day.tm_year, day.tm_mon, day.tm_mday, 0, 0, 0, 0, 0, -1)),
                time.mktime((day.tm_year, day.tm_mon, day.tm_mday + 1, 0, 0, 0, 0, 0, -1)))
    except (OverflowError, OSError, ValueError):             # a time this system has no calendar for
        start = now - now % 86400
        return start, start + 86400


def _said(exc: BaseException) -> str:
    """What a task said when it failed, on one line and cut to MAX_DETAIL_CHARS."""
    return ("%s: %s" % (type(exc).__name__, " ".join(str(exc).split())))[:MAX_DETAIL_CHARS].rstrip(": ")


def _clean_summary(value: Any) -> Dict[str, Any]:
    """A run's summary as it is kept. One that is no dict of plain JSON, or too long, is left out and the log says which."""
    if value is None:
        return {}
    if not isinstance(value, dict):
        return {"summary_left_out": "not_an_object"}
    try:
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError, RecursionError):
        return {"summary_left_out": "not_json"}
    if len(encoded) > MAX_SUMMARY_CHARS:
        return {"summary_left_out": "too_long"}
    return json.loads(encoded)


# ---- the running app's autopilot -------------------------------------------------------------------------------
class Autopilot:
    """Autopilot for one data folder, for the running app (``server.App.autopilot``).

    ``jobs`` is the app's ``Jobs``: a run is one of its jobs. ``backups`` is the app's ``backup.Backups``, or
    anything with its ``passage(changes, named)``: the gate a run begins inside. Left out, there is no gate,
    which is for a test of a task by itself. ``registry`` is ``REGISTRY`` unless a test hands in its own.
    ``transport`` is what tasks get as ``context.transport``; ``transport_of``, a function without arguments,
    is asked for it each time instead (the app hands that in, so a stand-in a test puts on ``App.transport``
    later is the one tasks get). ``transcriber_of``, likewise a function without arguments, is asked each time
    for what tasks get as ``context.transcriber``; left out, they get None. ``clock`` gives the time in seconds;
    a test hands in its own and never waits.

    ``on`` is the first switch (see the module's words): False until ``server.main`` sets it.
    """

    def __init__(self, store: "Store", jobs: Optional[Jobs] = None, backups: Any = None, *,
                 registry: Optional[Registry] = None, transport: Any = None,
                 transport_of: Optional[Callable[[], Any]] = None,
                 transcriber_of: Optional[Callable[[], Any]] = None, clock: Callable[[], float] = time.time) -> None:
        self.store = store
        self.jobs = jobs if jobs is not None else Jobs()
        self.backups = backups
        self.registry = registry if registry is not None else REGISTRY
        self.transport, self.transport_of = transport, transport_of
        self.transcriber_of = transcriber_of
        self.clock = clock
        self.on = False
        self._lock = threading.RLock()          # one start, and one undo, at a time
        self._current: Optional[str] = None     # the id of the run under way
        self._job_id: Optional[str] = None      # and of the job it is
        self._idle = threading.Event()          # set while no run is under way
        self._idle.set()
        self._thread: Optional[threading.Thread] = None
        self._wake = threading.Event()          # the scheduler looks now, and not when the minute is over
        self._stopping = False
        try:
            self.recover()
        except sqlite3.Error:
            pass                                # the next run tries again; the app starts all the same

    def _transport(self) -> Any:
        return self.transport_of() if self.transport_of is not None else self.transport

    def _transcriber(self) -> Any:
        return self.transcriber_of() if self.transcriber_of is not None else None

    @contextlib.contextmanager
    def _gate(self, changes: bool = True) -> Iterator[None]:
        """Inside the gate every request to the app passes: this waits while a backup is restored or data is moved in."""
        if self.backups is None:
            yield
            return
        with self.backups.passage(changes, None):
            yield

    # -- the log ------------------------------------------------------------
    def recover(self) -> int:
        """Mark every run the log calls running that is not under way as ``interrupted``. Returns how many there were.

        Run when the app starts: such a run was cut off when the app was last closed. And before each new run,
        because a restored backup may have been made while a task ran. Nothing is written when there is none.
        """
        current = self._current or ""
        with _db(self.store) as db:
            found = db.execute("SELECT COUNT(*) FROM autopilot_runs WHERE status = ? AND id != ?", (RUNNING, current)).fetchone()[0]
            if found:
                with db:
                    db.execute("UPDATE autopilot_runs SET status = ? WHERE status = ? AND id != ?", (INTERRUPTED, RUNNING, current))
        return int(found)

    def run(self, run_id: Any) -> Optional[Dict[str, Any]]:
        """One line of the log by its id, as the page is given it, or None."""
        if not isinstance(run_id, str) or not _RUN_ID.fullmatch(run_id):
            return None
        with _db(self.store) as db:
            row = db.execute("SELECT * FROM autopilot_runs WHERE id = ?", (run_id,)).fetchone()
        return _public(row, self._current) if row is not None else None

    def _insert(self, task: str, status: str, forced: bool, level: str, now: float, detail: str = "") -> str:
        """Write a new line and drop the lines that are over the bounds (see the module's words). Returns the new id."""
        run_id = uuid.uuid4().hex[:12]
        with _db(self.store) as db, db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("INSERT INTO autopilot_runs (id, task, status, forced, level, started_at, finished_at, detail)"
                       " VALUES (?,?,?,?,?,?,?,?)",
                       (run_id, task, status, 1 if forced else 0, level, now, None if status == RUNNING else now, detail))
            db.execute("DELETE FROM autopilot_runs WHERE task = ? AND started_at < ? AND id != ? AND id NOT IN"
                       " (SELECT id FROM autopilot_runs WHERE task = ? ORDER BY rowid DESC LIMIT ?)",
                       (task, _day_bounds(now)[0], run_id, task, KEEP_RUNS_PER_TASK))
            db.execute("DELETE FROM autopilot_runs WHERE id != ? AND id NOT IN"
                       " (SELECT id FROM autopilot_runs ORDER BY rowid DESC LIMIT ?)", (run_id, MAX_RUNS))
        return run_id

    def _finish(self, run_id: str, status: str, summary: Optional[Dict[str, Any]] = None, detail: str = "") -> None:
        with _db(self.store) as db, db:
            db.execute("UPDATE autopilot_runs SET status = ?, finished_at = ?, summary_json = ?, detail = ? WHERE id = ?",
                       (status, float(self.clock()), json.dumps(summary or {}, ensure_ascii=False, allow_nan=False),
                        detail[:MAX_DETAIL_CHARS], run_id))

    def _last_done(self, task: Any, before: str) -> Optional[Dict[str, Any]]:
        if not isinstance(task, str):
            return None
        with _db(self.store) as db:
            row = db.execute("SELECT * FROM autopilot_runs WHERE task = ? AND status = ? AND id != ? ORDER BY rowid DESC LIMIT 1",
                             (task, DONE, before)).fetchone()
        return _public(row, self._current) if row is not None else None

    # -- model calls --------------------------------------------------------
    @staticmethod
    def _spent_in(db: sqlite3.Connection, now: float) -> int:
        start, end = _day_bounds(now)
        return _count(db.execute(
            "SELECT COALESCE(SUM(CASE WHEN model_calls > 0 THEN model_calls ELSE 0 END), 0) FROM autopilot_runs"
            " WHERE forced = 0 AND started_at >= ? AND started_at < ?", (start, end)).fetchone()[0])

    def spent_today(self) -> int:
        """How many automatic model calls were reserved today: by runs the scheduler started, that began today."""
        with _db(self.store) as db:
            return self._spent_in(db, float(self.clock()))

    def _spend(self, run_id: str, forced: bool, calls: int) -> bool:
        limit = settings(self.store)["daily_model_calls"]       # read now: the creator may have lowered it meanwhile
        with _db(self.store) as db, db:
            db.execute("BEGIN IMMEDIATE")                       # counting and reserving are one step
            if not forced and self._spent_in(db, float(self.clock())) + calls > limit:
                db.execute("UPDATE autopilot_runs SET calls_refused = calls_refused + ? WHERE id = ?", (calls, run_id))
                return False
            db.execute("UPDATE autopilot_runs SET model_calls = model_calls + ? WHERE id = ?", (calls, run_id))
        return True

    def _record_undo(self, run_id: str, kind: Any, payload: Any) -> None:
        if self.registry.undo_handler(kind) is None:
            raise ValueError("no undo handler is registered for the kind %r" % (kind,))
        try:
            encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False)
        except (TypeError, ValueError, RecursionError) as exc:
            raise ValueError("what is recorded for an undo has to be plain JSON: %s" % exc)
        if len(encoded) > MAX_UNDO_CHARS:
            raise ValueError("what is recorded for an undo can be at most %d characters as JSON; this is %d"
                             % (MAX_UNDO_CHARS, len(encoded)))
        with _db(self.store) as db, db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT undo_json FROM autopilot_runs WHERE id = ?", (run_id,)).fetchone()
            records = _undo_records(row["undo_json"]) if row is not None else []
            if len(records) >= MAX_UNDO_RECORDS:
                raise ValueError("one run can say how to reverse at most %d changes" % MAX_UNDO_RECORDS)
            records.append({"kind": kind, "payload": json.loads(encoded)})
            db.execute("UPDATE autopilot_runs SET undo_json = ? WHERE id = ?",
                       (json.dumps(records, ensure_ascii=False, allow_nan=False), run_id))

    # -- when a task is due -------------------------------------------------
    def _due_at(self, task: Task, rows: List[Any], now: float, room: bool) -> float:
        """When ``task`` may next be started, from its newest lines in the log (the newest first). See the module's words.

        ``room``: whether any of today's automatic model calls are left.
        """
        if not rows:
            return 0.0
        status = _status_of(rows[0], self._current)
        if status == RUNNING:
            return math.inf
        began = _number(rows[0]["started_at"]) or 0.0
        if began > now + task.every_sec:
            # Dated more than a cadence after now: the clock was wrong then, or was set back since. Such a line
            # is not believed, or the task would wait until the clock has caught up. It is due.
            return 0.0
        if status in (FAILED, INTERRUPTED):
            failures = 0
            for row in rows:
                if _status_of(row, self._current) not in (FAILED, INTERRUPTED):
                    break
                failures += 1
            return began + min(task.every_sec, RETRY_AFTER_SEC * 2 ** (failures - 1))
        if status == SKIPPED:
            # Held back by the day's limit. It was due then and still is, as soon as calls are to be had again:
            # the creator raised the limit, or a new day began.
            return began if room else _day_bounds(began)[1]
        return began + task.every_sec

    def _wants(self, task: Task) -> bool:
        return task.due is None or bool(task.due(Context(self, task.name, "", False, None)))

    def _next_due(self, tried: Set[str]) -> Optional[Task]:
        """The task to start next: of those that are on and due, the one that has waited longest. None when there is none."""
        prefs = settings(self.store)
        if not (self.on and prefs["enabled"]):
            return None
        now = float(self.clock())
        waiting = []
        with _db(self.store) as db:
            room = self._spent_in(db, now) < prefs["daily_model_calls"]
            for index, task in enumerate(self.registry.tasks()):
                if task.name in tried or prefs["tasks"].get(task.name) is not True:
                    continue
                due_at = self._due_at(task, _newest(db, task.name, RETRY_LOOKBACK), now, room)
                if due_at <= now:
                    waiting.append((due_at, index, task))
        for _when, _index, task in sorted(waiting, key=lambda entry: entry[:2]):
            try:
                if self._wants(task):
                    return task
            except Exception:  # noqa: BLE001 - asked again where a run begins, and written down there
                return task
            tried.add(task.name)                # its condition said no: not asked again in this look
        return None

    # -- running ------------------------------------------------------------
    def _begin(self, task: Task, forced: bool) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
        """Start one run of ``task``, or write down why it was not started. Returns ``(the line in the log, the job)``.

        Inside the gate, so not while data is being replaced, and one at a time. Everything is looked at again
        here: the switches, and for the scheduler whether the task is still due in the log that is there now.
        ``(None, None)`` when the scheduler finds it is not due after all; a line and no job when it was skipped
        or its condition failed. Raises ``AutopilotError`` when a switch is off or another run is under way.
        """
        with self._gate(), self._lock:
            if self._current is not None:
                raise AutopilotError("autopilot_busy")
            prefs = settings(self.store)
            if not self.on:
                raise AutopilotError("autopilot_off_here")
            if not prefs["enabled"]:
                raise AutopilotError("autopilot_not_enabled")
            if prefs["tasks"].get(task.name) is not True:
                raise AutopilotError("autopilot_task_off")
            self.recover()
            now = float(self.clock())
            if not forced:
                with _db(self.store) as db:
                    rows = _newest(db, task.name, RETRY_LOOKBACK)
                    spent = self._spent_in(db, now)
                if self._due_at(task, rows, now, spent < prefs["daily_model_calls"]) > now:
                    return None, None
                try:
                    if not self._wants(task):
                        return None, None
                except Exception as exc:  # noqa: BLE001 - a task's condition must never take the server down either
                    traceback.print_exc()
                    return self.run(self._insert(task.name, FAILED, False, prefs["level"], now, detail=_said(exc))), None
                if task.calls_model and spent >= prefs["daily_model_calls"]:
                    return self.run(self._insert(task.name, SKIPPED, False, prefs["level"], now, detail=DAILY_CAP)), None
            run_id = self._insert(task.name, RUNNING, forced, prefs["level"], now)
            self._current = run_id
            self._idle.clear()
            try:
                job = self.jobs.start(JOB_KIND, lambda progress: self._work(task, run_id, forced, progress),
                                      label=task.name, lane=JOB_KIND)
            except BaseException:
                self._current = None
                self._idle.set()
                self._finish(run_id, FAILED, detail="the task could not be started")
                raise
            self._job_id = job["id"]
            return self.run(run_id), job

    def _work(self, task: Task, run_id: str, forced: bool, progress: Callable[[str, Optional[float]], None]) -> Any:
        """The job a run is: hand the task its context, and write down how it ended."""
        try:
            try:
                summary = task.run(Context(self, task.name, run_id, forced, progress))
            except Exception as exc:  # noqa: BLE001 - a task must never take the server down
                traceback.print_exc()
                said = _said(exc)
                shown = getattr(exc, "message_zh", None)
                kept = {"error": {"code": str(getattr(exc, "code", "") or "")[:60], "message": str(exc)[:MAX_DETAIL_CHARS],
                                  "message_zh": shown[:MAX_DETAIL_CHARS]}} if isinstance(shown, str) and shown else {}
                self._finish(run_id, FAILED, summary=kept, detail=said)
                raise JobError("autopilot_task_failed", "The automatic task %s failed: %s" % (task.name, said),
                               "自动任务 %s 没有完成：%s" % (task.name, said))
            self._finish(run_id, DONE, summary=_clean_summary(summary))
            return self.run(run_id)
        finally:
            with self._lock:
                self._current = None
                self._idle.set()

    def wait(self, timeout: float = 30.0) -> bool:
        """Block until no run is under way and its job has ended. False when one is still running after ``timeout``."""
        if not self._idle.wait(timeout):
            return False
        job_id = self._job_id
        if job_id is not None:
            self.jobs.wait(job_id, timeout)
        return True

    def run_due(self) -> List[Dict[str, Any]]:
        """Look once: start every task that is on and due, one after the other, and wait for each.

        Returns the lines written in this look, in the order the tasks were taken up: the one that has waited
        longest first, and of two that waited equally long the one registered first. Each task is taken up at
        most once in a look. Nothing is started, and ``[]`` returned, while either switch is off or a run
        started by hand is under way. The scheduler thread calls this; a test calls it itself.
        """
        ran: List[Dict[str, Any]] = []
        tried: Set[str] = set()
        while self.on and self._current is None:
            with self._gate(False):
                task = self._next_due(tried)
            if task is None:
                break
            tried.add(task.name)
            try:
                line, job = self._begin(task, forced=False)
            except AutopilotError as refused:
                if refused.code == "autopilot_task_off":
                    continue
                break
            if line is None:
                continue
            if job is not None:
                self._idle.wait()
                self.jobs.wait(job["id"], 60.0)
                line = self.run(line["id"]) or line
            ran.append(line)
        return ran

    def run_now(self, name: Any) -> Dict[str, Any]:
        """Run one task now, because the creator asked: ``{"run": its line in the log, "job": the job to follow}``.

        It answers at once; the task runs as a job. Refused (``AutopilotError``) when either switch is off, when
        the task is switched off or unknown, and while another run is under way. A run started this way is not
        held back by the day's limit and not counted against it, and it counts as the task's latest run: the
        scheduler waits one cadence from it.
        """
        task = self.registry.task(name)
        if task is None:
            raise AutopilotError("autopilot_unknown_task")
        line, job = self._begin(task, forced=True)
        return {"run": line, "job": job}

    # -- undo ---------------------------------------------------------------
    def undo(self, run_id: Any) -> Dict[str, Any]:
        """Reverse what one run changed, the last change first. Returns ``{"run": its line as it then is, "undone": how many}``.

        Works whatever the switches say. Refused (``AutopilotError``) while a task runs; when the run is not in
        the log, changed nothing that can be undone, or was undone already; and, before anything is touched,
        when one of its changes is of a kind no handler is registered for. When a handler will not or cannot
        put its change back, the undo stops there: the changes reversed so far are struck off the run, so a
        second try goes on from that one and reverses nothing twice.
        """
        with self._gate(), self._lock:
            if self._current is not None:
                raise AutopilotError("autopilot_busy")
            with _db(self.store) as db:
                row = db.execute("SELECT * FROM autopilot_runs WHERE id = ?", (run_id,)).fetchone() \
                    if isinstance(run_id, str) and _RUN_ID.fullmatch(run_id) else None
            if row is None:
                raise AutopilotError("autopilot_run_not_found")
            if row["undone_at"] is not None:
                raise AutopilotError("autopilot_already_undone")
            records = _undo_records(row["undo_json"])
            if not records:
                raise AutopilotError("autopilot_nothing_to_undo")
            handlers = [self.registry.undo_handler(record["kind"]) for record in records]
            if any(handler is None for handler in handlers):
                raise AutopilotError("autopilot_undo_unknown_kind")
            total, undone = len(records), 0
            try:
                while records:
                    try:
                        handlers[len(records) - 1](self.store, records[-1]["payload"])
                    except UndoRefused as refused:
                        raise AutopilotError("autopilot_undo_refused", extra={"undone": undone, "total": total},
                                             undone=undone, total=total, why=refused.message, why_zh=refused.message_zh)
                    except Exception as exc:  # noqa: BLE001 - said to the creator, with how far the undo came
                        traceback.print_exc()
                        raise AutopilotError("autopilot_undo_failed", extra={"undone": undone, "total": total},
                                             undone=undone, total=total, why=_said(exc))
                    records.pop()
                    undone += 1
            finally:
                with _db(self.store) as db, db:
                    db.execute("UPDATE autopilot_runs SET undo_json = ?, undone_at = ? WHERE id = ?",
                               (json.dumps(records, ensure_ascii=False, allow_nan=False),
                                None if records else float(self.clock()), run_id))
            return {"run": self.run(run_id), "undone": undone}

    # -- what the page is told, and what it changes ---------------------------
    def status(self) -> Dict[str, Any]:
        """What the page shows (``GET /api/autopilot``). Reads only, and asks no task anything.

        ``on`` and ``enabled`` are the two switches and ``active`` is both; ``level``; ``daily_model_calls``
        with today's ``spent`` and the ``limit``; ``tasks``, each with its switch, its cadence, whether it calls
        a model, whether it has a further condition, its ``last_run`` and ``next_run_at`` (None while it will
        not run by itself: a switch is off, or it is running now. Never earlier than ``now``: a task that is due
        has ``now`` there and is taken up at the scheduler's next look, within LOOK_EVERY_SEC. With a further
        condition it is the earliest time, not a promise);
        ``running``, the run under way; ``runs``, the newest RECENT_RUNS lines of the log; ``now``;
        ``look_every_sec``, how often the scheduler looks for what is due.
        """
        prefs = settings(self.store)
        now = float(self.clock())
        active = bool(self.on and prefs["enabled"])
        current = self._current
        tasks = []
        with _db(self.store) as db:
            spent = self._spent_in(db, now)
            room = spent < prefs["daily_model_calls"]
            for task in self.registry.tasks():
                rows = _newest(db, task.name, RETRY_LOOKBACK)
                switched_on = prefs["tasks"].get(task.name) is True
                due_at = self._due_at(task, rows, now, room) if active and switched_on else None
                tasks.append({
                    "name": task.name, "on": switched_on, "every_sec": task.every_sec, "calls_model": task.calls_model,
                    "has_condition": task.due is not None,
                    "last_run": _public(rows[0], current) if rows else None,
                    "next_run_at": max(due_at, now) if due_at is not None and math.isfinite(due_at) else None,
                })
            runs = [_public(row, current) for row in
                    db.execute("SELECT * FROM autopilot_runs ORDER BY rowid DESC LIMIT ?", (RECENT_RUNS,)).fetchall()]
            under_way = db.execute("SELECT * FROM autopilot_runs WHERE id = ?", (current,)).fetchone() if current else None
        return {
            "on": bool(self.on), "enabled": prefs["enabled"], "active": active,
            "level": prefs["level"], "levels": list(LEVELS),
            "daily_model_calls": {"limit": prefs["daily_model_calls"], "spent": spent, "max": MAX_DAILY_CALLS,
                                  "default": DEFAULT_DAILY_CALLS},
            "tasks": tasks,
            "running": _public(under_way, current) if under_way is not None else None,
            "runs": runs, "now": now, "look_every_sec": LOOK_EVERY_SEC,
        }

    def save_settings(self, changes: Any) -> Dict[str, Any]:
        """Change the creator's settings (``save_settings`` of this module) and return the state as it then is (``status``).

        A task switch for a name that is no task of this app is refused (``autopilot_unknown_task``): the page
        offers the tasks there are. Afterwards the scheduler looks at once, so a task just switched on does not
        wait for the minute to pass.
        """
        switches = changes.get("tasks") if isinstance(changes, dict) else None
        if isinstance(switches, dict) and any(self.registry.task(name) is None for name in switches):
            raise AutopilotError("autopilot_unknown_task")
        save_settings(self.store, changes)
        self._wake.set()
        return self.status()

    # -- the scheduler --------------------------------------------------------
    def start(self) -> bool:
        """Start the scheduler thread, once, and only when ``on`` is set. Returns whether one is running."""
        with self._lock:
            if not self.on:
                return False
            if self._thread is None or not self._thread.is_alive():
                self._stopping = False
                self._wake.clear()
                self._thread = threading.Thread(target=self._loop, name="autopilot", daemon=True)
                self._thread.start()
            return True

    def _loop(self) -> None:
        wait = FIRST_LOOK_AFTER_SEC
        while True:
            self._wake.wait(wait)
            self._wake.clear()
            if self._stopping:
                return
            try:
                self.run_due()
            except Exception:  # noqa: BLE001 - whatever a look trips over, the scheduler looks again
                traceback.print_exc()
            wait = LOOK_EVERY_SEC

    def stop(self, timeout: float = 2.0) -> None:
        """The app is closing: the scheduler starts nothing more. A task under way is not cut off here; it ends with the app."""
        self._stopping = True
        self._wake.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout)
