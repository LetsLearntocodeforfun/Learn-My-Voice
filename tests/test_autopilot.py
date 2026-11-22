"""Tests for autopilot's core (studio/autopilot.py): what the app does by itself, within what the creator chose.

Each class covers one thing a creator, or a module that registers a task, can rely on:

- nothing runs unless this start of the app may run tasks AND the creator switched autopilot on AND switched
  that task on; with any of them off, a run asked for by hand is refused in English and in Chinese;
- registering a task or an undo handler only puts it on a list, and a name is checked when it is registered;
- a task is handed the store, the clock, a copy of the settings it cannot change, the level, the model
  transport and the transcriber the app has, its run's id, and ways to reserve model calls, to say how a change
  is reversed and how far it is;
- tasks that are due run in order, the one that has waited longest first, one at a time, and what is overdue
  when the app is opened is caught up on;
- a task that raises is logged with what it said, cut short; the next one still runs; it is tried again after
  an hour, then two, then four, and never later than its cadence;
- a task over the day's limit of automatic model calls is skipped and logged as skipped, once; it has its turn
  when the next day begins or the creator raises the limit; inside a run a call over the limit is refused;
- an undo reverses a run's changes last first, once, and refuses a second time;
- a run the app was closed in the middle of is marked as interrupted at the next start;
- the log is bounded, and a log from elsewhere with anything in it can still be shown;
- damaged settings switch nothing on, and what other modules keep beside these settings survives a save;
- a run begins inside the gate, waits there while data is being replaced, and looks again afterwards;
- a server a test makes, and an app a trial starts, run nothing: only ``server.main`` turns the first switch on
  and starts the scheduler, and not when the app is started with LEARN_MY_VOICE_NO_AUTOPILOT=1 or for a check of
  the window; both trials hand that variable to every app they start and ask the app whether it knows;
- the four requests (read the state, change the settings, run a task now, undo a run) hand over to the module
  and refuse with a code and a sentence in each language;
- no task starts while a backup is being restored or data is moved in from an earlier folder; a restore and a
  move are refused while a task runs; afterwards the schedule goes by the log that is there then;
- a backup made by data version 3 is restored by this version, and the lines of the log do not count as the
  creator's work.

No model is involved and nothing is asked of another computer, and no test waits for time to pass: the clock is
handed in. Where a test shows that a look is held back while data is replaced, it gives that look 0.3 seconds
to get through, which it would need a hundredth of.
"""
import contextlib
import http.client
import importlib.util
import io
import json
import os
import re
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from studio import autopilot, backup, buildinfo, server, updater
from studio import store as store_module
from studio.jobs import Jobs
from studio.store import ROOT, Store

HOUR, DAY = 3600, 86400
# Nine in the morning by this computer's clock, on a day with no change of the clocks near it.
BASE = time.mktime((2026, 11, 22, 9, 0, 0, 0, 0, -1))
CHINESE = "[\u4e00-\u9fff]"


class Clock:
    """The time, as a test says it is."""

    def __init__(self, now=BASE):
        self.now = now

    def __call__(self):
        return self.now


class Gate:
    """Stands in for ``backup.Backups``: writes down who passed, and can be closed the way a restore closes it."""

    def __init__(self):
        self.passed = []
        self.is_open = threading.Event()
        self.is_open.set()
        self.somebody_waits = threading.Event()

    @contextlib.contextmanager
    def passage(self, changes, named):
        if changes and not self.is_open.is_set():
            self.somebody_waits.set()
            self.is_open.wait(10)
        self.passed.append((changes, named))
        yield


@contextlib.contextmanager
def quiet():
    """What a failing task prints for whoever runs the app (the traceback) is not wanted in the test's output."""
    with contextlib.redirect_stderr(io.StringIO()) as said:
        yield said


class Case(unittest.TestCase):
    """A data folder of its own, a clock, a registry of its own, and the autopilot of an app on that folder."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="lmv-autopilot-test-"))
        self.addCleanup(shutil.rmtree, str(self.tmp), True)
        self.store = Store(self.tmp / "data")
        self.clock = Clock()
        self.registry = autopilot.Registry()
        self.ran = []                       # the names of the tasks that ran, in that order
        self.pilots, self.held = [], []
        self.addCleanup(self.settle)        # named after the folder's removal, so it happens before it
        self.pilot = self.make()

    def make(self, **more):
        pilot = autopilot.Autopilot(self.store, registry=self.registry, clock=self.clock, **more)
        self.pilots.append(pilot)
        return pilot

    def hold(self):
        """Something a task of this test waits for. It is let go at the end at the latest."""
        event = threading.Event()
        self.held.append(event)
        return event

    def settle(self):
        """Let every task that still waits go on, and wait until none runs: only then is the folder removed."""
        for event in self.held:
            event.set()
        for pilot in self.pilots:
            pilot.stop()
            pilot.wait(10)

    def task(self, name, every=HOUR, run=None, **more):
        def work(context):
            self.ran.append(name)
            return run(context) if run is not None else {"did": name}

        return self.registry.add(autopilot.Task(name, work, every_sec=every, **more))

    def switch_on(self, *names, **more):
        """Both switches, and the switch of each task named."""
        self.pilot.on = True
        return autopilot.save_settings(self.store, dict({"enabled": True, "tasks": {name: True for name in names}}, **more))

    def later(self, seconds):
        self.clock.now += seconds

    def look(self):
        """One look of the scheduler: ``[(task, how it ended)]`` for the lines it wrote."""
        return [(line["task"], line["status"]) for line in self.pilot.run_due()]

    def lines(self):
        """Every line of the log, the oldest first."""
        return list(reversed(self.pilot.status()["runs"]))

    def sql(self, statement, values=()):
        with contextlib.closing(sqlite3.connect(str(self.store.db_path))) as db:
            found = db.execute(statement, values).fetchall()
            db.commit()
        return found

    def refused(self, work, code):
        """``work`` is refused with this code and a sentence in each language."""
        with self.assertRaises(autopilot.AutopilotError) as caught:
            work()
        refusal = caught.exception
        self.assertEqual(refusal.code, code)
        self.assertRegex(refusal.message, "[A-Za-z]{4}")
        self.assertRegex(refusal.message_zh, CHINESE)
        self.assertNotEqual(refusal.message, refusal.message_zh)
        self.assertNotIn("%(", refusal.message + refusal.message_zh)
        return refusal


# ---------------------------------------------------------------------------------------------- 1. the switches
class SwitchesTest(Case):
    def setUp(self):
        super().setUp()
        self.task("trends.refresh")

    def nothing_happened(self):
        self.assertEqual((self.ran, self.lines()), ([], []))
        self.assertIsNone(self.pilot.jobs.latest(autopilot.JOB_KIND))
        self.assertEqual(self.pilot.jobs.unfinished(), 0)

    def test_while_this_start_of_the_app_may_not_run_tasks_nothing_runs_also_when_asked_by_hand(self):
        autopilot.save_settings(self.store, {"enabled": True, "tasks": {"trends.refresh": True}})   # the creator said yes to everything
        self.assertIs(self.pilot.on, False)                                  # and nobody turned the first switch on
        self.assertEqual(self.pilot.run_due(), [])
        refusal = self.refused(lambda: self.pilot.run_now("trends.refresh"), "autopilot_off_here")
        self.assertEqual(refusal.status, 409)
        self.assertIn("LEARN_MY_VOICE_NO_AUTOPILOT=1", refusal.message)
        self.assertIn("LEARN_MY_VOICE_NO_AUTOPILOT=1", refusal.message_zh)
        self.assertIs(self.pilot.start(), False)                             # nor is there a scheduler
        self.assertNotIn("autopilot", [thread.name for thread in threading.enumerate()])
        self.nothing_happened()
        state = self.pilot.status()
        self.assertEqual((state["on"], state["enabled"], state["active"]), (False, True, False))
        self.assertIsNone(state["tasks"][0]["next_run_at"])

    def test_while_the_creator_has_not_switched_it_on_nothing_runs_also_when_asked_by_hand(self):
        self.pilot.on = True
        autopilot.save_settings(self.store, {"tasks": {"trends.refresh": True}})
        self.assertIs(autopilot.settings(self.store)["enabled"], False)
        self.assertEqual(self.pilot.run_due(), [])
        refusal = self.refused(lambda: self.pilot.run_now("trends.refresh"), "autopilot_not_enabled")
        self.assertEqual(refusal.status, 409)
        self.nothing_happened()
        state = self.pilot.status()
        self.assertEqual((state["on"], state["enabled"], state["active"]), (True, False, False))
        self.assertIsNone(state["tasks"][0]["next_run_at"])

    def test_with_both_on_the_same_task_runs_by_itself_and_by_hand(self):
        """Without this the two tests above would also pass if nothing could ever run."""
        self.switch_on("trends.refresh")
        self.assertEqual(self.look(), [("trends.refresh", "done")])
        answer = self.pilot.run_now("trends.refresh")
        self.assertTrue(self.pilot.wait(10))
        self.assertEqual(self.ran, ["trends.refresh", "trends.refresh"])
        self.assertEqual([(line["status"], line["forced"]) for line in self.lines()], [("done", False), ("done", True)])
        self.assertEqual(self.pilot.jobs.get(answer["job"]["id"])["status"], "done")

    def test_a_task_the_creator_did_not_switch_on_does_not_run(self):
        self.task("ideas.refresh")
        self.switch_on("ideas.refresh")                                      # this one, and not trends.refresh
        self.assertEqual(self.look(), [("ideas.refresh", "done")])
        self.refused(lambda: self.pilot.run_now("trends.refresh"), "autopilot_task_off")
        autopilot.save_settings(self.store, {"tasks": {"trends.refresh": False}})     # said in so many words
        self.assertEqual(self.look(), [])
        self.refused(lambda: self.pilot.run_now("trends.refresh"), "autopilot_task_off")
        for name in ("no.such_task", "", None, 7, ["trends.refresh"]):
            self.assertEqual(self.refused(lambda: self.pilot.run_now(name), "autopilot_unknown_task").status, 404)
        self.assertEqual(self.ran, ["ideas.refresh"])
        self.assertEqual([task["on"] for task in self.pilot.status()["tasks"]], [False, True])

    def test_a_new_folder_has_everything_off(self):
        self.assertEqual(autopilot.settings(self.store),
                         {"enabled": False, "level": "suggest", "daily_model_calls": 12, "tasks": {}})
        self.assertFalse(self.store.config_path.exists())                    # and reading wrote nothing
        self.assertEqual(self.pilot.status()["daily_model_calls"], {"limit": 12, "spent": 0, "max": 200, "default": 12})
        self.assertEqual(self.pilot.status()["look_every_sec"], 60.0)        # the page can say how soon a due task is taken up


# ---------------------------------------------------------------------------------------------- 2. registering
class RegistryTest(Case):
    def test_registering_only_puts_a_task_on_a_list(self):
        asked = []
        task = self.registry.add(autopilot.Task("trends.refresh", lambda context: asked.append("run"), every_sec=12 * HOUR,
                                                calls_model=True, due=lambda context: asked.append("due")))
        self.registry.add_undo("trends.added", lambda store, payload: asked.append("undo"))
        self.assertEqual((task.name, task.every_sec, task.calls_model), ("trends.refresh", 12.0 * HOUR, True))
        self.assertEqual(self.registry.tasks(), [task])
        self.assertIs(self.registry.task("trends.refresh"), task)
        self.assertIsNone(self.registry.task("trends.other"))
        self.assertIsNotNone(self.registry.undo_handler("trends.added"))
        self.assertIsNone(self.registry.undo_handler("trends.removed"))
        self.assertEqual(asked, [])                                          # nothing was called
        self.assertEqual(self.lines(), [])                                   # nothing was written
        self.assertEqual(self.pilot.status()["tasks"][0]["on"], False)       # and the task is off until the creator says so

    def test_the_app_has_one_registry_that_modules_fill_when_they_are_imported(self):
        with mock.patch.object(autopilot, "REGISTRY", autopilot.Registry()):
            task = autopilot.register_task("voice.relearn", lambda context: {}, every_sec=7 * DAY, calls_model=True)
            autopilot.register_undo("voice.profile_applied", lambda store, payload: None)
            self.assertEqual(autopilot.REGISTRY.tasks(), [task])
            self.assertIsNotNone(autopilot.REGISTRY.undo_handler("voice.profile_applied"))
            self.assertIs(autopilot.Autopilot(self.store).registry, autopilot.REGISTRY)
        self.assertIsNone(autopilot.REGISTRY.task("voice.relearn"))          # this test left nothing behind

    def test_a_name_is_checked_when_it_is_registered(self):
        run = lambda context: {}                                             # noqa: E731
        for name in ("trends", "Trends.refresh", "trends.Refresh", "trends refresh", "trends.", ".refresh", "a.b.c.d",
                     "trends.refresh\n", "趋势.刷新", "x" * 31 + ".y", "", None, 7):
            with self.assertRaises(ValueError, msg=repr(name)):
                autopilot.Task(name, run, every_sec=HOUR)
            with self.assertRaises(ValueError, msg=repr(name)):
                self.registry.add_undo(name, lambda store, payload: None)
        for name in ("trends.refresh", "voice.ingest_folder", "lessons.accept_recurring", "a.b.c", "a1.b_2"):
            autopilot.Task(name, run, every_sec=HOUR)
        for every in (0, 599, -1, 30 * DAY + 1, float("inf"), float("nan"), "3600", None, True):
            with self.assertRaises(ValueError, msg=repr(every)):
                autopilot.Task("trends.refresh", run, every_sec=every)
        for every in (autopilot.MIN_EVERY_SEC, 600.5, autopilot.MAX_EVERY_SEC):
            autopilot.Task("trends.refresh", run, every_sec=every)
        for bad in ({"run": None}, {"run": "refresh"}, {"due": "always"}, {"calls_model": 1}):
            with self.assertRaises(ValueError, msg=repr(bad)):
                autopilot.Task("trends.refresh", bad.pop("run", run), every_sec=HOUR, **bad)

    def test_a_name_is_registered_once_and_a_registry_is_bounded(self):
        first = self.task("trends.refresh")
        with self.assertRaises(ValueError):
            self.task("trends.refresh")
        with self.assertRaises(ValueError):
            self.registry.add("trends.other")                                # a Task, not a name
        self.registry.add_undo("trends.added", lambda store, payload: None)
        with self.assertRaises(ValueError):
            self.registry.add_undo("trends.added", lambda store, payload: None)
        with self.assertRaises(ValueError):
            self.registry.add_undo("trends.removed", "not a function")
        self.assertEqual(self.registry.tasks(), [first])
        for number in range(autopilot.MAX_TASKS - 1):
            self.task("more.task_%d" % number)
        with self.assertRaises(ValueError):
            self.task("one.too_many")
        self.assertEqual(len(self.registry.tasks()), autopilot.MAX_TASKS)

    def test_importing_the_module_brings_in_nothing_that_talks_to_a_model_or_to_another_computer(self):
        heavy = ["studio.llm", "studio.pipeline", "studio.backup", "studio.server", "studio.updater", "studio.transcribe",
                 "urllib.request", "http.client", "ssl", "socket"]
        said = subprocess.run(
            [sys.executable, "-c", "import json, sys, studio.autopilot; print(json.dumps([name for name in %r if name in sys.modules]))" % heavy],
            cwd=str(ROOT), capture_output=True, timeout=120)
        self.assertEqual(said.returncode, 0, said.stderr.decode("utf-8", "replace"))
        self.assertEqual(json.loads(said.stdout.decode("utf-8")), [])


# ---------------------------------------------------------------------------------------------- 3. what a task is handed
class ContextTest(Case):
    def test_a_task_is_handed_the_store_the_clock_the_settings_the_level_the_transport_and_its_run(self):
        seen = {}
        stand_in = lambda *args: {"said": "nothing"}                         # noqa: E731

        def run(context):
            seen.update(store=context.store, first=context.now, settings=context.settings, level=context.level,
                        transport=context.transport, run_id=context.run_id, task=context.task, forced=context.forced)
            self.later(90)                                                   # the work took a minute and a half
            seen["then"] = context.now
            context.progress("halfway", 0.5)
            return {"kept": 2}

        self.task("trends.refresh", run=run)
        self.pilot = self.make(transport=stand_in)
        self.switch_on("trends.refresh", level="auto", daily_model_calls=7)
        autopilot.save_other_setting(self.store, "watch_folder", "/Users/somebody/Movies")
        line = self.pilot.run_due()[0]
        self.assertIs(seen["store"], self.store)
        self.assertEqual((seen["first"], seen["then"]), (BASE, BASE + 90))    # the clock that was handed in, read anew each time
        self.assertEqual((seen["level"], seen["task"], seen["forced"]), ("auto", "trends.refresh", False))
        self.assertIs(seen["transport"], stand_in)
        self.assertEqual(seen["run_id"], line["id"])
        self.assertRegex(line["id"], "^[0-9a-f]{12}$")
        self.assertEqual(dict(seen["settings"]), {"enabled": True, "level": "auto", "daily_model_calls": 7,
                                                  "tasks": {"trends.refresh": True}, "watch_folder": "/Users/somebody/Movies"})
        self.assertEqual((line["status"], line["level"], line["summary"], line["started_at"], line["finished_at"]),
                         ("done", "auto", {"kept": 2}, BASE, BASE + 90))
        job = self.pilot.jobs.latest(autopilot.JOB_KIND)
        self.assertEqual((job["label"], job["stage"], job["fraction"], job["status"]), ("trends.refresh", "halfway", 0.5, "done"))
        self.assertEqual(job["result"]["id"], line["id"])

    def test_the_settings_a_task_is_handed_cannot_be_changed_and_changing_the_copy_changes_nothing(self):
        seen = {}
        self.task("trends.refresh", run=lambda context: seen.update(settings=context.settings))
        self.store.update_config(lambda config: config.update(autopilot={"sources": {"lookup": {"on": True}}}))
        self.switch_on("trends.refresh")
        self.pilot.run_due()
        with self.assertRaises(TypeError):
            seen["settings"]["enabled"] = False
        with self.assertRaises(TypeError):
            del seen["settings"]["sources"]
        seen["settings"]["sources"]["lookup"]["on"] = False                  # what is inside is a copy
        seen["settings"]["tasks"]["trends.refresh"] = False
        self.assertEqual(self.store.load_config()["autopilot"]["sources"], {"lookup": {"on": True}})
        self.assertEqual(autopilot.settings(self.store)["tasks"], {"trends.refresh": True})

    def test_the_app_hands_over_the_transport_it_has_at_that_moment(self):
        seen = []
        held = {"transport": None}
        self.task("ideas.refresh", every=600, run=lambda context: seen.append(context.transport))
        self.pilot = self.make(transport="ignored", transport_of=lambda: held["transport"])
        self.switch_on("ideas.refresh")
        self.pilot.run_due()
        held["transport"] = stand_in = lambda *args: {}                      # put there later, as a test of the app does
        self.later(600)
        self.pilot.run_due()
        self.assertEqual(seen, [None, stand_in])

    def test_a_task_can_read_how_its_last_run_ended_and_another_tasks(self):
        seen = []
        outcome = {"fail": False}

        def refresh(context):
            seen.append(context.last_done())
            if outcome["fail"]:
                raise RuntimeError("no answer")
            return {"top": ["秋冬护肤"], "at": context.now}

        self.task("trends.refresh", every=600, run=refresh)
        self.task("plans.suggest", every=600, run=lambda context: seen.append(
            (context.last_done("trends.refresh"), context.last_done("no.such_task"), context.last_done())))
        self.switch_on("trends.refresh", "plans.suggest")
        first, _plans = self.pilot.run_due()
        self.assertIsNone(seen[0])                                           # it never ran before
        other, unknown, own = seen[1]
        self.assertEqual((other["id"], other["summary"], unknown, own), (first["id"], {"top": ["秋冬护肤"], "at": BASE}, None, None))
        outcome["fail"] = True
        self.later(600)
        with quiet():
            self.pilot.run_due()
        self.assertEqual(seen[2]["id"], first["id"])                         # the run before this one
        outcome["fail"] = False
        self.later(HOUR)
        self.pilot.run_due()
        self.assertEqual((seen[4]["id"], seen[4]["status"]), (first["id"], "done"))     # still the first: the failed one is not a run that was done

    def test_a_due_condition_gets_a_context_that_changes_and_spends_nothing(self):
        seen = {}

        def due(context):
            seen.update(run_id=context.run_id, task=context.task, spent=context.spend(), now=context.now,
                        level=context.level, last=context.last_done())
            context.progress("looking")                                      # goes nowhere, and is no mistake
            with self.assertRaises(RuntimeError):
                context.record_undo("trends.added", {})
            return True

        self.registry.add_undo("trends.added", lambda store, payload: None)
        self.task("trends.refresh", due=due, calls_model=True)
        self.switch_on("trends.refresh")
        self.assertEqual(self.look(), [("trends.refresh", "done")])
        self.assertEqual(seen, {"run_id": "", "task": "trends.refresh", "spent": False, "now": BASE, "level": "suggest", "last": None})
        self.assertEqual((self.lines()[0]["model_calls"], self.lines()[0]["calls_refused"], self.lines()[0]["changes"]), (0, 0, 0))


# ---------------------------------------------------------------------------------------------- 4. when tasks run
class ScheduleTest(Case):
    def test_a_task_that_never_ran_is_due_at_once_and_then_each_time_its_cadence_has_passed(self):
        self.task("trends.refresh", every=12 * HOUR)
        self.switch_on("trends.refresh")
        self.assertEqual(self.look(), [("trends.refresh", "done")])
        self.assertEqual(self.look(), [])                                    # looked again at once
        task = self.pilot.status()["tasks"][0]
        self.assertEqual((task["last_run"]["started_at"], task["next_run_at"], task["every_sec"], task["has_condition"]),
                         (BASE, BASE + 12 * HOUR, 12.0 * HOUR, False))
        self.later(12 * HOUR - 1)
        self.assertEqual(self.look(), [])                                    # a second too early
        self.later(1)
        self.assertEqual(self.look(), [("trends.refresh", "done")])
        self.assertEqual(self.ran, ["trends.refresh"] * 2)

    def test_what_is_overdue_when_the_app_is_opened_is_caught_up_on_the_longest_waiting_first(self):
        for name, every in (("plans.suggest", 24 * HOUR), ("trends.refresh", 6 * HOUR), ("voice.ingest_folder", HOUR)):
            self.task(name, every=every)
        self.switch_on("plans.suggest", "trends.refresh", "voice.ingest_folder")
        # None ever ran: all are due, and none has waited longer than another. The order they were registered in.
        self.assertEqual([name for name, _ended in self.look()], ["plans.suggest", "trends.refresh", "voice.ingest_folder"])
        self.later(30 * HOUR)                                                # the app was closed for more than a day
        self.pilot = self.make()                                             # and is opened again
        self.pilot.on = True
        # Due since: 1 hour, 6 hours and 24 hours after the first look.
        self.assertEqual([name for name, _ended in self.look()], ["voice.ingest_folder", "trends.refresh", "plans.suggest"])
        self.assertEqual(self.look(), [])
        self.later(HOUR)
        self.assertEqual(self.look(), [("voice.ingest_folder", "done")])     # only the one whose hour has passed

    def test_tasks_run_one_at_a_time(self):
        under_way, most = [], []

        def run(context):
            under_way.append(context.task)
            most.append(len(under_way))
            self.assertEqual(self.pilot.jobs.unfinished(), 1)
            self.assertEqual(self.pilot.status()["running"]["id"], context.run_id)
            under_way.remove(context.task)

        for name in ("trends.refresh", "plans.suggest", "ideas.refresh", "voice.relearn"):
            self.task(name, run=run)
        self.switch_on("trends.refresh", "plans.suggest", "ideas.refresh", "voice.relearn")
        self.assertEqual([ended for _name, ended in self.look()], ["done"] * 4)
        self.assertEqual(most, [1, 1, 1, 1])
        self.assertIsNone(self.pilot.status()["running"])
        self.assertEqual(self.pilot.jobs.unfinished(), 0)

    def test_while_a_task_runs_no_other_is_started_by_the_scheduler_or_by_hand(self):
        started, release = threading.Event(), self.hold()
        self.task("voice.relearn", run=lambda context: (started.set(), release.wait(10)) and {})
        self.task("ideas.refresh")
        self.switch_on("voice.relearn", "ideas.refresh")
        answer = self.pilot.run_now("voice.relearn")
        self.assertTrue(started.wait(10))
        state = self.pilot.status()
        self.assertEqual((state["running"]["id"], state["running"]["status"], state["runs"][0]["can_undo"]),
                         (answer["run"]["id"], "running", False))
        self.assertEqual(self.refused(lambda: self.pilot.run_now("ideas.refresh"), "autopilot_busy").status, 409)
        self.refused(lambda: self.pilot.undo(answer["run"]["id"]), "autopilot_busy")
        self.assertEqual(self.pilot.run_due(), [])                           # ideas.refresh is due, and waits
        self.assertEqual((self.pilot.jobs.unfinished(), self.ran), (1, ["voice.relearn"]))
        self.assertIs(self.pilot.wait(0.01), False)
        release.set()
        self.assertIs(self.pilot.wait(10), True)
        self.assertEqual(self.pilot.jobs.unfinished(), 0)
        self.assertEqual(self.look(), [("ideas.refresh", "done")])

    def test_a_task_with_a_further_condition_waits_until_it_is_true_and_leaves_no_line_meanwhile(self):
        arrived = []
        self.task("voice.ingest_folder", every=600, due=lambda context: bool(arrived))
        self.switch_on("voice.ingest_folder")
        for _ in range(5):
            self.assertEqual(self.look(), [])
            self.later(60)
        self.assertEqual((self.lines(), self.ran), ([], []))
        self.assertTrue(self.pilot.status()["tasks"][0]["has_condition"])
        arrived.append("clip.mp4")
        self.assertEqual(self.look(), [("voice.ingest_folder", "done")])
        self.later(599)
        self.assertEqual(self.look(), [])                                    # the condition holds, the cadence has not passed
        self.later(1)
        self.assertEqual(self.look(), [("voice.ingest_folder", "done")])

    def test_a_run_started_by_hand_counts_as_the_tasks_latest_run(self):
        self.task("trends.refresh", every=12 * HOUR)
        self.switch_on("trends.refresh")
        self.pilot.run_now("trends.refresh")
        self.assertTrue(self.pilot.wait(10))
        self.assertEqual(self.look(), [])                                    # it was just done
        self.later(12 * HOUR)
        self.assertEqual(self.look(), [("trends.refresh", "done")])

    def test_when_the_clock_was_set_back_a_task_waits_one_cadence_and_not_until_the_clock_has_caught_up(self):
        self.task("trends.refresh", every=6 * HOUR)
        self.switch_on("trends.refresh")
        self.later(40 * DAY)                                                 # the computer's clock was wrong when it ran
        self.assertEqual(self.look(), [("trends.refresh", "done")])
        self.later(-40 * DAY)                                                # and was then put right
        self.assertEqual(self.pilot.status()["tasks"][0]["next_run_at"], BASE)          # due: now, never a time gone by
        self.assertEqual(self.look(), [("trends.refresh", "done")])          # a run dated forty days from now is not believed
        self.assertEqual(self.look(), [])
        self.assertEqual(self.pilot.status()["tasks"][0]["next_run_at"], BASE + 6 * HOUR)
        self.later(6 * HOUR - 1)
        self.assertEqual(self.look(), [])
        self.later(1)
        self.assertEqual(self.look(), [("trends.refresh", "done")])

    def test_a_clock_put_back_by_a_little_does_not_make_a_task_run_twice(self):
        self.task("trends.refresh", every=6 * HOUR)
        self.switch_on("trends.refresh")
        self.assertEqual(self.look(), [("trends.refresh", "done")])
        self.later(-90)                                                      # the clock was corrected by a minute and a half
        self.assertEqual(self.look(), [])
        self.assertEqual(self.pilot.status()["tasks"][0]["next_run_at"], BASE + 6 * HOUR)
        self.later(6 * HOUR + 89)
        self.assertEqual(self.look(), [])
        self.later(1)
        self.assertEqual(self.look(), [("trends.refresh", "done")])


# ---------------------------------------------------------------------------------------------- 5. a task that fails
class FailureTest(Case):
    def test_a_task_that_raises_is_logged_with_what_it_said_and_the_next_still_runs(self):
        def refresh(context):
            raise ValueError("the service said no\n   and went on for a long time " + "x" * 1000)

        self.task("trends.refresh", run=refresh)
        self.task("plans.suggest")
        self.switch_on("trends.refresh", "plans.suggest")
        with quiet() as printed:
            lines = self.pilot.run_due()
        self.assertEqual([(line["task"], line["status"]) for line in lines], [("trends.refresh", "failed"), ("plans.suggest", "done")])
        failed = lines[0]
        self.assertTrue(failed["detail"].startswith("ValueError: the service said no and went on for a long time xxx"), failed["detail"])
        self.assertEqual(len(failed["detail"]), autopilot.MAX_DETAIL_CHARS)  # cut, and on one line
        self.assertEqual((failed["finished_at"], failed["summary"], failed["can_undo"]), (BASE, {}, False))
        self.assertIn("ValueError: the service said no", printed.getvalue())  # the whole of it, for whoever runs the app
        self.assertEqual(self.ran, ["trends.refresh", "plans.suggest"])
        self.assertEqual(self.pilot.jobs.unfinished(), 0)

    def test_the_job_of_a_failed_run_says_so_in_both_languages(self):
        self.task("trends.refresh", run=lambda context: 1 / 0)
        self.switch_on("trends.refresh")
        with quiet():
            answer = self.pilot.run_now("trends.refresh")
            self.assertTrue(self.pilot.wait(10))
        job = self.pilot.jobs.get(answer["job"]["id"])
        self.assertEqual((job["status"], job["error"]["code"]), ("failed", "autopilot_task_failed"))
        self.assertIn("ZeroDivisionError", job["error"]["message"])
        self.assertRegex(job["error"]["message_zh"], CHINESE)
        self.assertEqual(self.pilot.run(answer["run"]["id"])["status"], "failed")

    def test_a_failed_task_is_tried_again_after_an_hour_then_two_then_four_and_never_later_than_its_cadence(self):
        works = []

        def refresh(context):
            if not works:
                raise RuntimeError("no answer")

        self.task("trends.refresh", every=24 * HOUR, run=refresh)
        self.switch_on("trends.refresh")
        with quiet():
            self.assertEqual(self.look(), [("trends.refresh", "failed")])
            for wait in (1, 2, 4, 8, 16, 24, 24, 24, 24, 24):               # hours; 32 would be later than its cadence
                self.assertEqual(self.pilot.status()["tasks"][0]["next_run_at"], self.clock.now + wait * HOUR)
                self.later(wait * HOUR - 1)
                self.assertEqual(self.look(), [], wait)
                self.later(1)
                self.assertEqual(self.look(), [("trends.refresh", "failed")], wait)
        works.append(True)
        self.later(24 * HOUR)
        self.assertEqual(self.look(), [("trends.refresh", "done")])
        self.later(HOUR)
        self.assertEqual(self.look(), [])                                    # it worked: back to its cadence
        self.later(23 * HOUR)
        self.assertEqual(self.look(), [("trends.refresh", "done")])

    def test_for_a_task_with_a_long_cadence_the_wait_stops_doubling_at_128_hours(self):
        self.task("voice.relearn", every=30 * DAY, run=lambda context: [].pop())
        self.switch_on("voice.relearn")
        with quiet():
            self.assertEqual(self.look(), [("voice.relearn", "failed")])
            for wait in (1, 2, 4, 8, 16, 32, 64, 128, 128, 128):             # hours; eight failures in a row are counted
                self.later(wait * HOUR - 1)
                self.assertEqual(self.look(), [], wait)
                self.later(1)
                self.assertEqual(self.look(), [("voice.relearn", "failed")], wait)

    def test_a_task_with_a_cadence_shorter_than_an_hour_is_tried_again_at_its_cadence(self):
        self.task("voice.ingest_folder", every=600, run=lambda context: [].pop())
        self.switch_on("voice.ingest_folder")
        with quiet():
            for _ in range(4):
                self.assertEqual(self.look(), [("voice.ingest_folder", "failed")])
                self.later(599)
                self.assertEqual(self.look(), [])
                self.later(1)

    def test_a_condition_that_raises_is_logged_as_failed_and_the_task_is_not_run(self):
        self.task("voice.ingest_folder", every=600, due=lambda context: {}["folder"])
        self.task("plans.suggest")
        self.switch_on("voice.ingest_folder", "plans.suggest")
        with quiet():
            self.assertEqual(self.look(), [("voice.ingest_folder", "failed"), ("plans.suggest", "done")])
            self.assertEqual(self.look(), [])                                # not asked again at every look
        self.assertEqual(self.lines()[0]["detail"], "KeyError: 'folder'")
        self.assertEqual(self.ran, ["plans.suggest"])
        self.assertIsNone(self.pilot.jobs.get("0" * 12))
        self.assertEqual(self.pilot.jobs.unfinished(), 0)

    def test_what_a_failing_task_said_in_both_languages_is_kept_with_the_run(self):
        class NoService(Exception):
            code = "no_service"
            message_zh = "连接不上你选的 AI 服务。"

        def refresh(context):
            raise NoService("The AI service you chose could not be reached.")

        self.task("trends.refresh", run=refresh)
        self.switch_on("trends.refresh")
        with quiet():
            line = self.pilot.run_due()[0]
        self.assertEqual(line["summary"], {"error": {"code": "no_service", "message": "The AI service you chose could not be reached.",
                                                     "message_zh": "连接不上你选的 AI 服务。"}})
        self.assertEqual(line["detail"], "NoService: The AI service you chose could not be reached.")

    def test_a_summary_that_cannot_be_kept_is_left_out_and_the_run_still_counts_as_done(self):
        returned = [None, {}, ["a", "list"], "text", {"when": object()}, {"score": float("nan")}, {"long": "长" * 4000},
                    {"fits": "长" * 3900, "n": 3}]
        self.task("trends.refresh", every=600, run=lambda context: returned[len(self.ran) - 1])
        self.switch_on("trends.refresh")
        kept = []
        for _ in returned:
            line = self.pilot.run_due()[0]
            self.assertEqual(line["status"], "done")
            kept.append(line["summary"])
            self.later(600)
        self.assertEqual(kept, [{}, {}, {"summary_left_out": "not_an_object"}, {"summary_left_out": "not_an_object"},
                                {"summary_left_out": "not_json"}, {"summary_left_out": "not_json"},
                                {"summary_left_out": "too_long"}, {"fits": "长" * 3900, "n": 3}])


# ---------------------------------------------------------------------------------------------- 6. the day's limit of model calls
class LimitTest(Case):
    def calling(self, name, calls=2, **more):
        """A task that reserves ``calls`` model calls, one at a time, and reports what it was told."""
        return self.task(name, calls_model=True, run=lambda context: {"told": [context.spend() for _ in range(calls)]}, **more)

    def test_a_task_over_the_days_limit_is_skipped_and_logged_and_not_tried_again_that_day(self):
        self.calling("trends.refresh")
        self.calling("ideas.refresh")
        self.task("lessons.accept_recurring")                                # calls no model
        self.switch_on("trends.refresh", "ideas.refresh", "lessons.accept_recurring", daily_model_calls=3)
        first = self.pilot.run_due()
        self.assertEqual([(line["task"], line["status"], line["model_calls"], line["calls_refused"], line["summary"]) for line in first],
                         [("trends.refresh", "done", 2, 0, {"told": [True, True]}),
                          ("ideas.refresh", "done", 1, 1, {"told": [True, False]}),       # the fourth call of the day was refused
                          ("lessons.accept_recurring", "done", 0, 0, {"did": "lessons.accept_recurring"})])
        self.assertEqual(self.pilot.status()["daily_model_calls"], {"limit": 3, "spent": 3, "max": 200, "default": 12})
        self.later(HOUR)
        second = self.pilot.run_due()
        self.assertEqual([(line["task"], line["status"], line["detail"], line["model_calls"]) for line in second],
                         [("trends.refresh", "skipped", "daily_cap", 0), ("ideas.refresh", "skipped", "daily_cap", 0),
                          ("lessons.accept_recurring", "done", "", 0)])
        self.assertEqual(self.ran.count("trends.refresh"), 1)                # a skipped task did not run
        self.assertEqual([second[0]["started_at"], second[0]["finished_at"]], [BASE + HOUR, BASE + HOUR])
        for _ in range(5):                                                   # the rest of the day: no further line for the two
            self.later(HOUR)
            self.assertEqual(self.look(), [("lessons.accept_recurring", "done")])
        midnight = autopilot._day_bounds(BASE)[1]
        self.assertEqual(self.pilot.status()["tasks"][0]["next_run_at"], midnight)
        self.clock.now = midnight - 1
        self.assertNotIn("trends.refresh", [name for name, _ended in self.look()])
        self.clock.now = midnight                                            # a new day: nothing is spent yet
        self.assertEqual(self.pilot.status()["daily_model_calls"]["spent"], 0)
        self.assertEqual(self.look()[:2], [("trends.refresh", "done"), ("ideas.refresh", "done")])
        self.assertEqual(self.pilot.status()["daily_model_calls"]["spent"], 3)
        self.assertEqual([line["status"] for line in self.lines() if line["task"] == "trends.refresh"], ["done", "skipped", "done"])

    def test_a_task_held_back_today_has_its_turn_tomorrow_however_long_its_cadence_and_runs_once(self):
        self.calling("trends.refresh", every=600)
        self.calling("ideas.refresh", every=3 * DAY)
        self.switch_on("trends.refresh", "ideas.refresh", daily_model_calls=1)
        self.assertEqual(self.look(), [("trends.refresh", "done"), ("ideas.refresh", "skipped")])
        midnight = autopilot._day_bounds(BASE)[1]
        self.assertEqual([task["next_run_at"] for task in self.pilot.status()["tasks"]], [BASE + 600, midnight])
        self.later(600)
        self.assertEqual(self.look(), [("trends.refresh", "skipped")])       # the day's one call is spent
        self.clock.now = midnight + 2 * DAY                                  # the app was closed over the next two days
        # ideas.refresh, held back first, goes first, and runs once: not once for each day it was held back.
        self.assertEqual(self.look(), [("ideas.refresh", "done"), ("trends.refresh", "skipped")])
        self.assertEqual(self.look(), [])
        self.assertEqual(self.ran, ["trends.refresh", "ideas.refresh"])

    def test_when_the_creator_raises_the_limit_a_task_that_was_held_back_runs_at_the_next_look(self):
        self.calling("trends.refresh")
        self.calling("ideas.refresh", every=3 * DAY)
        self.switch_on("trends.refresh", "ideas.refresh", daily_model_calls=2)
        self.assertEqual(self.look(), [("trends.refresh", "done"), ("ideas.refresh", "skipped")])
        self.later(60)
        self.assertEqual(self.look(), [])                                    # held back: no line at every look
        state = self.pilot.save_settings({"daily_model_calls": 5})           # the creator, in Settings
        self.assertEqual((state["tasks"][1]["name"], state["tasks"][1]["next_run_at"]), ("ideas.refresh", state["now"]))
        line = self.pilot.run_due()[0]
        self.assertEqual((line["task"], line["status"], line["model_calls"], self.pilot.spent_today()), ("ideas.refresh", "done", 2, 4))
        self.assertEqual(self.look(), [])

    def test_reserving_says_yes_until_the_limit_and_then_no_and_several_are_reserved_together_or_not_at_all(self):
        told = []

        def run(context):
            told.extend([context.spend(2), context.spend(2), context.spend(), context.spend()])
            for wrong in (0, -1, 51, 1.0, "1", None, True):
                with self.assertRaises(ValueError, msg=repr(wrong)):
                    context.spend(wrong)

        self.task("trends.refresh", calls_model=True, run=run)
        self.switch_on("trends.refresh", daily_model_calls=3)
        line = self.pilot.run_due()[0]
        self.assertEqual(told, [True, False, True, False])                   # 2, then 2 more would be 4, then 1, then full
        self.assertEqual((line["model_calls"], line["calls_refused"], self.pilot.spent_today()), (3, 3, 3))

    def test_a_run_the_creator_started_by_hand_is_not_held_back_and_not_counted(self):
        self.calling("trends.refresh", calls=3)
        self.switch_on("trends.refresh", daily_model_calls=1)
        answer = self.pilot.run_now("trends.refresh")
        self.assertTrue(self.pilot.wait(10))
        line = self.pilot.run(answer["run"]["id"])
        self.assertEqual((line["forced"], line["model_calls"], line["calls_refused"], line["summary"]), (True, 3, 0, {"told": [True, True, True]}))
        self.assertEqual(self.pilot.spent_today(), 0)
        self.later(HOUR)
        line = self.pilot.run_due()[0]                                       # the scheduler's own run has its one call
        self.assertEqual((line["status"], line["model_calls"], line["calls_refused"]), ("done", 1, 2))
        self.pilot.run_now("trends.refresh")                                 # and with the limit used up, a click still works
        self.assertTrue(self.pilot.wait(10))
        self.assertEqual((self.lines()[-1]["status"], self.lines()[-1]["model_calls"], self.pilot.spent_today()), ("done", 3, 1))

    def test_with_a_limit_of_nought_no_task_that_calls_a_model_starts_by_itself(self):
        self.calling("trends.refresh")
        self.task("plans.suggest")
        self.switch_on("trends.refresh", "plans.suggest", daily_model_calls=0)
        self.assertEqual(self.look(), [("trends.refresh", "skipped"), ("plans.suggest", "done")])
        self.later(HOUR)
        self.assertEqual(self.look(), [("plans.suggest", "done")])           # no second line for the task that is held back
        self.clock.now = autopilot._day_bounds(BASE)[1]
        self.assertEqual(self.look(), [("plans.suggest", "done"), ("trends.refresh", "skipped")])     # one line a day says so
        self.assertEqual(self.ran, ["plans.suggest"] * 3)

    def test_a_limit_the_creator_lowers_while_a_task_runs_holds_from_then_on(self):
        told = []

        def run(context):
            told.append(context.spend())
            autopilot.save_settings(self.store, {"daily_model_calls": 1})    # the creator, in Settings, meanwhile
            told.append(context.spend())

        self.task("trends.refresh", calls_model=True, run=run)
        self.switch_on("trends.refresh", daily_model_calls=10)
        self.pilot.run_due()
        self.assertEqual(told, [True, False])


# ---------------------------------------------------------------------------------------------- 7. undo
class UndoTest(Case):
    def setUp(self):
        super().setUp()
        self.notes = []                     # stands in for what a task changes
        self.taken_back = []
        self.stuck = {}                     # word -> what the handler raises for it

        def take_back(store, payload):
            self.assertIs(store, self.store)
            if payload["word"] in self.stuck:
                raise self.stuck[payload["word"]]
            self.taken_back.append(payload["word"])
            self.notes.remove(payload["word"])

        self.registry.add_undo("notes.added", take_back)

        def add(context):
            for word in ("one", "two", "three"):
                self.notes.append(word)
                context.record_undo("notes.added", {"word": word})
            return {"added": 3}

        self.task("notes.add", run=add)
        self.switch_on("notes.add")

    def test_an_undo_reverses_a_runs_changes_the_last_first_once_and_is_refused_a_second_time(self):
        line = self.pilot.run_due()[0]
        self.assertEqual((line["changes"], line["can_undo"], line["undone_at"], self.notes), (3, True, None, ["one", "two", "three"]))
        self.later(5 * 60)
        answer = self.pilot.undo(line["id"])
        self.assertEqual((answer["undone"], self.taken_back, self.notes), (3, ["three", "two", "one"], []))
        after = answer["run"]
        self.assertEqual((after["status"], after["undone_at"], after["can_undo"], after["changes"]), ("done", BASE + 300, False, 0))
        refusal = self.refused(lambda: self.pilot.undo(line["id"]), "autopilot_already_undone")
        self.assertEqual(refusal.status, 409)
        self.assertEqual(self.taken_back, ["three", "two", "one"])           # nothing was reversed twice
        self.assertEqual(self.look(), [])                                    # and what was undone is not done again at once

    def test_what_cannot_be_undone_is_refused_in_both_languages_and_nothing_is_touched(self):
        for run_id in ("0" * 12, "nope", "", None, 7, "0" * 12 + "' OR 1=1 --"):
            self.assertEqual(self.refused(lambda: self.pilot.undo(run_id), "autopilot_run_not_found").status, 404)
        self.task("notes.count", run=lambda context: {"notes": len(self.notes)})
        autopilot.save_settings(self.store, {"tasks": {"notes.count": True}})
        added, counted = self.pilot.run_due()
        self.assertEqual((counted["changes"], counted["can_undo"]), (0, False))
        self.refused(lambda: self.pilot.undo(counted["id"]), "autopilot_nothing_to_undo")
        # An app that does not have the module that made the change: its registry has no handler for the kind.
        elsewhere = autopilot.Autopilot(self.store, registry=autopilot.Registry(), clock=self.clock)
        self.refused(lambda: elsewhere.undo(added["id"]), "autopilot_undo_unknown_kind")
        self.assertEqual((self.notes, self.taken_back), (["one", "two", "three"], []))
        self.assertEqual(self.pilot.run(added["id"])["changes"], 3)          # and it can still be undone where the handler is
        self.assertEqual(self.pilot.undo(added["id"])["undone"], 3)

    def test_an_undo_that_stops_halfway_says_how_far_it_came_and_a_second_try_reverses_nothing_twice(self):
        line = self.pilot.run_due()[0]
        self.stuck["two"] = autopilot.UndoRefused("You changed this note since, so it was left as it is.", "这一条你后来改过了，所以没有动它。")
        refusal = self.refused(lambda: self.pilot.undo(line["id"]), "autopilot_undo_refused")
        self.assertEqual((refusal.status, refusal.extra), (409, {"undone": 1, "total": 3}))
        self.assertIn("1 of 3", refusal.message)
        self.assertIn("You changed this note since", refusal.message)
        self.assertIn("这一条你后来改过了", refusal.message_zh)
        self.assertEqual((self.notes, self.taken_back), (["one", "two"], ["three"]))
        after = self.pilot.run(line["id"])
        self.assertEqual((after["changes"], after["can_undo"], after["undone_at"]), (2, True, None))
        self.stuck["two"] = RuntimeError("the disk is full")                 # something the handler did not foresee
        with quiet():
            refusal = self.refused(lambda: self.pilot.undo(line["id"]), "autopilot_undo_failed")
        self.assertEqual((refusal.status, refusal.extra), (500, {"undone": 0, "total": 2}))
        self.assertIn("RuntimeError: the disk is full", refusal.message)
        self.assertIn("RuntimeError: the disk is full", refusal.message_zh)
        self.assertEqual((self.notes, self.taken_back), (["one", "two"], ["three"]))
        self.stuck.clear()
        self.assertEqual(self.pilot.undo(line["id"])["undone"], 2)
        self.assertEqual((self.notes, self.taken_back), ([], ["three", "two", "one"]))     # "three" was taken back once
        self.refused(lambda: self.pilot.undo(line["id"]), "autopilot_already_undone")

    def test_how_to_reverse_a_change_is_written_down_at_once_so_a_run_that_then_fails_can_be_undone(self):
        seen = []

        def add_then_fail(context):
            self.notes.append("four")
            context.record_undo("notes.added", {"word": "four"})
            seen.append(self.pilot.run(context.run_id)["changes"])           # in the log while the task still runs
            raise RuntimeError("stopped in the middle")

        self.task("notes.add_more", run=add_then_fail)
        autopilot.save_settings(self.store, {"tasks": {"notes.add": False, "notes.add_more": True}})
        with quiet():
            line = self.pilot.run_due()[0]
        self.assertEqual((seen, line["status"], line["changes"], line["can_undo"]), ([1], "failed", 1, True))
        self.assertEqual(self.pilot.undo(line["id"])["undone"], 1)
        self.assertEqual(self.notes, [])

    def test_what_cannot_be_kept_for_an_undo_is_refused_when_the_task_records_it(self):
        refused = []

        def run(context):
            for kind, payload in (("notes.removed", {}), ("Not A Kind", {}), (None, {}), ("notes.added", {"when": object()}),
                                  ("notes.added", {"n": float("inf")}), ("notes.added", "长" * autopilot.MAX_UNDO_CHARS)):
                try:
                    context.record_undo(kind, payload)
                except ValueError as exc:
                    refused.append(str(exc)[:40])
            for number in range(autopilot.MAX_UNDO_RECORDS):
                context.record_undo("notes.added", {"word": number})
            try:
                context.record_undo("notes.added", {"word": "one too many"})
            except ValueError:
                refused.append("full")

        self.task("notes.fill", run=run)
        autopilot.save_settings(self.store, {"tasks": {"notes.add": False, "notes.fill": True}})
        line = self.pilot.run_due()[0]
        self.assertEqual((len(refused), refused[-1], line["status"], line["changes"]), (7, "full", "done", autopilot.MAX_UNDO_RECORDS))

    def test_an_undo_works_whatever_the_switches_say(self):
        line = self.pilot.run_due()[0]
        autopilot.save_settings(self.store, {"enabled": False, "tasks": {"notes.add": False}})
        self.pilot.on = False
        self.assertEqual(self.pilot.undo(line["id"])["undone"], 3)
        self.assertEqual(self.notes, [])


# ---------------------------------------------------------------------------------------------- 8. closed in the middle
class InterruptedTest(Case):
    def test_a_run_the_app_was_closed_in_the_middle_of_is_marked_at_the_next_start_and_tried_again_after_an_hour(self):
        started, never = threading.Event(), self.hold()
        closed = []
        self.registry.add_undo("notes.added", lambda store, payload: closed.append(payload))

        def relearn(context):
            if not closed:
                context.record_undo("notes.added", "half done")
                started.set()
                never.wait(30)                                               # the app is closed while this is under way

        self.task("voice.relearn", every=7 * DAY, run=relearn)
        self.switch_on("voice.relearn")
        answer = self.pilot.run_now("voice.relearn")
        self.assertTrue(started.wait(10))
        self.assertEqual(self.sql("SELECT status FROM autopilot_runs"), [("running",)])
        # The next start of the app: what was running then is not running now.
        closed.append("the app was closed")
        self.later(3 * HOUR)
        opened = self.make()
        self.assertEqual(self.sql("SELECT status, finished_at FROM autopilot_runs"), [("interrupted", None)])
        line = opened.status()["runs"][0]
        self.assertEqual((line["id"], line["status"], line["finished_at"], line["can_undo"]), (answer["run"]["id"], "interrupted", None, True))
        self.assertIsNone(opened.status()["running"])
        opened.on = True
        self.assertEqual([line["status"] for line in opened.run_due()], ["done"])     # an hour has passed: tried again
        self.assertEqual(opened.undo(answer["run"]["id"])["undone"], 1)      # and what the cut-off run changed can be taken back
        self.assertEqual(closed, ["the app was closed", "half done"])

    def test_within_the_hour_it_is_not_tried_again(self):
        self.task("voice.relearn", every=7 * DAY)
        self.sql("INSERT INTO autopilot_runs (id, task, status, started_at) VALUES ('aaaaaaaaaaaa', 'voice.relearn', 'running', ?)", (BASE - 60,))
        opened = self.make()
        self.assertEqual(self.sql("SELECT status FROM autopilot_runs"), [("interrupted",)])
        self.pilot = opened
        self.switch_on("voice.relearn")
        self.assertEqual(self.look(), [])
        self.later(HOUR - 60)
        self.assertEqual(self.look(), [("voice.relearn", "done")])

    def test_a_start_with_nothing_cut_off_writes_nothing(self):
        self.task("trends.refresh")
        self.switch_on("trends.refresh")
        self.pilot.run_due()
        before = (self.store.db_path.stat().st_mtime_ns, self.sql("SELECT * FROM autopilot_runs"))
        self.assertEqual(self.make().recover(), 0)
        self.assertEqual((self.store.db_path.stat().st_mtime_ns, self.sql("SELECT * FROM autopilot_runs")), before)


# ---------------------------------------------------------------------------------------------- 9. the log
class LogTest(Case):
    def test_of_each_task_the_newest_runs_are_kept_and_the_oldest_go_first(self):
        self.task("trends.refresh", every=DAY)
        self.task("plans.suggest", every=30 * DAY)
        self.switch_on("trends.refresh", "plans.suggest")
        with mock.patch.object(autopilot, "KEEP_RUNS_PER_TASK", 3):
            for _ in range(6):
                self.pilot.run_due()
                self.later(DAY)
        kept = self.lines()
        self.assertEqual([(line["task"], line["started_at"]) for line in kept],
                         [("plans.suggest", BASE)] + [("trends.refresh", BASE + day * DAY) for day in (3, 4, 5)])
        # The one run of the task that runs once a month is still there: the other's many runs did not push it out.
        self.assertEqual(self.look(), [("trends.refresh", "done")])

    def test_all_of_todays_runs_are_kept_whatever_their_number(self):
        self.task("voice.ingest_folder", every=600)
        self.switch_on("voice.ingest_folder")
        with mock.patch.object(autopilot, "KEEP_RUNS_PER_TASK", 3):
            for _ in range(8):
                self.pilot.run_due()
                self.later(600)
            self.assertEqual(len(self.lines()), 8)                           # today's spending is counted from these
            self.later(DAY)
            self.pilot.run_due()
        self.assertEqual([line["started_at"] for line in self.lines()], [BASE + 6 * 600, BASE + 7 * 600, BASE + DAY + 8 * 600])

    def test_of_all_tasks_together_the_newest_are_kept(self):
        for name in ("trends.refresh", "plans.suggest"):
            self.task(name, every=600)
        self.switch_on("trends.refresh", "plans.suggest")
        with mock.patch.object(autopilot, "MAX_RUNS", 5):
            for _ in range(4):
                self.pilot.run_due()
                self.later(600)
        self.assertEqual([(line["task"], line["started_at"]) for line in self.lines()],
                         [("plans.suggest", BASE + 600), ("trends.refresh", BASE + 1200), ("plans.suggest", BASE + 1200),
                          ("trends.refresh", BASE + 1800), ("plans.suggest", BASE + 1800)])

    def test_a_log_from_elsewhere_with_anything_in_it_can_still_be_shown_and_switches_nothing_on(self):
        self.task("trends.refresh", calls_model=True)
        rows = [
            ("aaaaaaaaaaaa", "trends.refresh", "weird", "yes", "loud", BASE, "never", -5, "many", "[1, 2", "x" * 1000, '{"kind": "a"}', "never"),
            ("bbbbbbbbbbbb", "not a task\n<b>", "done", 7, 7, "yesterday", None, 10 ** 12, "x", '"text"', 5, "[[]]", None),
            ("cccccccccccc", "trends.refresh", "done", 0, "auto", BASE, BASE, 3.0, -1, "{" * 200 + "}" * 200, "", '[{"kind": "a.b"}]', 4.5),
        ]
        for row in rows:
            self.sql("INSERT INTO autopilot_runs (id, task, status, forced, level, started_at, finished_at, model_calls, calls_refused,"
                     " summary_json, detail, undo_json, undone_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", row)
        state = self.pilot.status()
        json.dumps(state, allow_nan=False)                                   # as it would be sent
        shown = {line["id"]: line for line in state["runs"]}
        first, second, third = shown["aaaaaaaaaaaa"], shown["bbbbbbbbbbbb"], shown["cccccccccccc"]
        self.assertEqual((first["status"], first["forced"], first["level"], first["model_calls"], first["calls_refused"], first["summary"],
                          len(first["detail"]), first["changes"], first["can_undo"], first["finished_at"], first["undone_at"]),
                         ("interrupted", False, "suggest", 0, 0, {}, autopilot.MAX_DETAIL_CHARS, 0, False, None, None))
        self.assertEqual((second["task"], second["forced"], second["started_at"], second["model_calls"], second["summary"], second["changes"]),
                         ("not a task\n<b>", True, None, 10 ** 9, {}, 0))
        self.assertEqual((second["level"], second["calls_refused"], second["detail"]), ("suggest", 0, "5"))
        self.assertEqual((third["summary"], third["changes"], third["undone_at"], third["can_undo"], third["model_calls"]), ({}, 0, 4.5, False, 3))
        self.assertEqual(autopilot.recent_runs(self.store), state["runs"])
        self.assertEqual(state["daily_model_calls"]["spent"], 3)             # the count below nought takes nothing off what was spent
        self.assertEqual(self.pilot.run_due(), [])                           # and nothing in a log switches anything on
        for run_id in shown:
            with self.assertRaises(autopilot.AutopilotError):
                self.pilot.undo(run_id)

    def test_a_huge_count_in_a_log_from_elsewhere_counts_as_spent_not_as_nothing(self):
        self.task("trends.refresh", calls_model=True)
        self.sql("INSERT INTO autopilot_runs (id, task, status, started_at, model_calls) VALUES ('aaaaaaaaaaaa', 'other.task', 'done', ?, ?)",
                 (BASE - HOUR, 10 ** 15))
        self.switch_on("trends.refresh")
        self.assertEqual(self.pilot.spent_today(), 10 ** 9)
        self.assertEqual(self.look(), [("trends.refresh", "skipped")])


# ---------------------------------------------------------------------------------------------- 10. the settings
class SettingsTest(Case):
    NEW = {"enabled": False, "level": "suggest", "daily_model_calls": 12, "tasks": {}}

    def test_damaged_settings_switch_nothing_on(self):
        self.task("trends.refresh")
        self.pilot.on = True
        damaged = [
            "on", 1, True, None, [], ["enabled"], {"enabled": "yes"}, {"enabled": 1}, {"enabled": [True]}, {"enabled": None},
            {"level": 5, "daily_model_calls": 10 ** 9, "tasks": ["trends.refresh"]},
            {"level": "AUTO", "daily_model_calls": -1, "tasks": "trends.refresh"},
            {"level": ["auto"], "daily_model_calls": 12.5, "tasks": {"trends.refresh": "true", "Trends Refresh": True, "a.b.c.d": True, "": True}},
            {"daily_model_calls": True, "tasks": {"trends.refresh": 1}},
        ]
        for held in damaged:
            self.store.update_config(lambda config: config.update(autopilot=held))
            self.assertEqual(autopilot.settings(self.store), self.NEW, held)
            self.assertEqual(self.pilot.run_due(), [], held)
            state = self.pilot.status()
            self.assertEqual((state["enabled"], state["active"], state["level"], state["tasks"][0]["on"]), (False, False, "suggest", False), held)
        for raw in (b"{cut short", b"", b"\xff\xfe not text", b"[1, 2, 3]", b'{"autopilot": {"enabled": true', b"null"):
            self.store.config_path.write_bytes(raw)
            self.assertEqual(autopilot.settings(self.store), self.NEW, raw)
            self.assertEqual(self.pilot.run_due(), [], raw)
        self.assertEqual(self.ran, [])

    def test_what_is_usable_in_a_file_edited_by_hand_is_used_and_the_rest_counts_as_never_set(self):
        self.store.update_config(lambda config: config.update(autopilot={
            "enabled": True, "level": "auto", "daily_model_calls": 0,
            "tasks": {"trends.refresh": True, "plans.suggest": False, "ideas.refresh": "yes", "not a name": True}}))
        self.assertEqual(autopilot.settings(self.store), {"enabled": True, "level": "auto", "daily_model_calls": 0,
                                                          "tasks": {"trends.refresh": True, "plans.suggest": False}})

    def test_saving_these_settings_leaves_what_other_modules_keep_beside_them_exactly_as_it_is(self):
        others = {"watch_folder": "/Users/somebody/Movies/小红书", "transcribe_with": "on_device",
                  "sources": {"lookup": {"on": True, "every_hours": 12}, "files": {"on": False}}, "seen": [1, 2.5, None, "x"]}
        self.store.update_config(lambda config: config.update(provider="manual", autopilot=dict(others)))
        saved = autopilot.save_settings(self.store, {"enabled": True, "level": "auto", "daily_model_calls": 30,
                                                     "tasks": {"trends.refresh": True}})
        self.assertEqual(saved, {"enabled": True, "level": "auto", "daily_model_calls": 30, "tasks": {"trends.refresh": True}})
        held = self.store.load_config()["autopilot"]
        self.assertEqual({key: held[key] for key in others}, others)
        self.assertEqual(sorted(held), sorted(list(others) + ["enabled", "level", "daily_model_calls", "tasks"]))
        self.assertEqual(self.store.load_config()["provider"], "manual")     # and the rest of the settings too
        autopilot.save_settings(self.store, {"enabled": False})
        autopilot.save_settings(self.store, {"tasks": {"plans.suggest": True}})
        self.assertEqual({key: self.store.load_config()["autopilot"][key] for key in others}, others)
        self.assertEqual(autopilot.settings(self.store), {"enabled": False, "level": "auto", "daily_model_calls": 30,
                                                          "tasks": {"trends.refresh": True, "plans.suggest": True}})

    def test_another_module_keeps_a_setting_of_its_own_and_these_stay_as_they_are(self):
        autopilot.save_settings(self.store, {"enabled": True, "tasks": {"trends.refresh": True}})
        autopilot.save_other_setting(self.store, "watch_folder", "/Users/somebody/Movies")
        autopilot.save_other_setting(self.store, "sources", {"lookup": True})
        self.assertEqual(autopilot.settings(self.store), {"enabled": True, "level": "suggest", "daily_model_calls": 12,
                                                          "tasks": {"trends.refresh": True}})
        self.assertEqual(dict(autopilot._for_tasks(self.store)),
                         {"enabled": True, "level": "suggest", "daily_model_calls": 12, "tasks": {"trends.refresh": True},
                          "watch_folder": "/Users/somebody/Movies", "sources": {"lookup": True}})
        autopilot.save_other_setting(self.store, "watch_folder", None)
        self.assertNotIn("watch_folder", self.store.load_config()["autopilot"])
        before = self.store.config_path.read_bytes()
        for key, value in (("enabled", True), ("level", "auto"), ("daily_model_calls", 99), ("tasks", {}), ("Watch Folder", "x"),
                           ("", "x"), (None, "x"), ("x" * 41, 1), ("when", object()), ("score", float("nan"))):
            with self.assertRaises(ValueError, msg=repr(key)):
                autopilot.save_other_setting(self.store, key, value)
        self.assertEqual(self.store.config_path.read_bytes(), before)

    def test_one_function_cleans_everything_kept_there_also_what_was_read_from_another_folders_file(self):
        new = dict(self.NEW)
        for value in (None, "on", 7, True, [], [{"enabled": True}]):
            self.assertEqual(autopilot.clean_stored(value), new, value)
        theirs = {"lookup": {"on": True}, "deep": [[["as", "stored"]]]}
        value = {"enabled": True, "level": "auto", "daily_model_calls": 30, "tasks": {"trends.refresh": True, "Not A Task": True},
                 "watch_folder": "/Users/somebody/Movies", "sources": theirs, "Bad Key": 1, "": 2, "x" * 41: 3, "UPPER": 4}
        cleaned = autopilot.clean_stored(value)
        self.assertEqual(cleaned, {"enabled": True, "level": "auto", "daily_model_calls": 30, "tasks": {"trends.refresh": True},
                                   "watch_folder": "/Users/somebody/Movies", "sources": theirs})
        self.assertIs(cleaned["sources"], theirs)                            # theirs is kept as it is stored, not looked into
        self.assertEqual(autopilot.clean_stored(cleaned), cleaned)           # cleaning what is clean changes nothing
        damaged = autopilot.clean_stored({"enabled": "yes", "level": 5, "daily_model_calls": 10 ** 9, "tasks": ["trends.refresh"],
                                          "watch_folder": "/kept"})
        self.assertEqual(damaged, dict(new, watch_folder="/kept"))           # what is damaged switches nothing on
        many = autopilot.clean_stored({"key_%d" % n: n for n in range(100)})
        self.assertEqual(len(many), autopilot.MAX_OTHER_SETTINGS + 4)
        self.assertEqual(value["tasks"], {"trends.refresh": True, "Not A Task": True})     # and what was handed in is not changed
        # What a task is handed is this, for the folder in use.
        self.store.update_config(lambda config: config.update(autopilot=value))
        self.assertEqual(dict(autopilot._for_tasks(self.store)), cleaned)
        self.assertEqual(autopilot.settings(self.store), {key: cleaned[key] for key in autopilot.OWN_SETTINGS})

    def test_a_value_of_another_kind_refuses_the_whole_request_and_saves_nothing(self):
        autopilot.save_settings(self.store, {"enabled": True, "level": "auto", "daily_model_calls": 5, "tasks": {"trends.refresh": True}})
        before = self.store.config_path.read_bytes()
        wrong = [
            ("settings", None), ("settings", []), ("settings", "enabled"), ("settings", 1),
            ("enabled", {"enabled": "yes"}), ("enabled", {"enabled": 1}), ("enabled", {"enabled": None}),
            ("level", {"level": "AUTO"}), ("level", {"level": "automatic"}), ("level", {"level": ["auto"]}), ("level", {"level": None}),
            ("daily_model_calls", {"daily_model_calls": -1}), ("daily_model_calls", {"daily_model_calls": 201}),
            ("daily_model_calls", {"daily_model_calls": 12.0}), ("daily_model_calls", {"daily_model_calls": "12"}),
            ("daily_model_calls", {"daily_model_calls": True}),
            ("tasks", {"tasks": ["trends.refresh"]}), ("tasks", {"tasks": {"trends.refresh": "on"}}), ("tasks", {"tasks": {"Not A Task": True}}),
            ("tasks", {"tasks": None}), ("tasks", {"tasks": {"task.number_%d" % n: True for n in range(autopilot.MAX_TASKS_KEPT + 1)}}),
            ("watch_folder", {"watch_folder": "/somewhere"}), ("Enabled", {"Enabled": True}),
            ("enabled", {"level": "suggest", "enabled": "no"}),              # one that is right beside one that is not
        ]
        for field, changes in wrong:
            refusal = self.refused(lambda: autopilot.save_settings(self.store, changes), "autopilot_bad_setting")
            self.assertEqual((refusal.status, refusal.extra), (400, {"field": field}), changes)
            self.assertIn(field, refusal.message)
            self.assertEqual(self.store.config_path.read_bytes(), before, changes)
        self.assertEqual(self.refused(lambda: autopilot.save_settings(self.store, {"k" * 500: 1}), "autopilot_bad_setting").extra,
                         {"field": "k" * 60})

    def test_task_switches_are_set_one_at_a_time_and_only_so_many_are_kept(self):
        autopilot.save_settings(self.store, {"tasks": {"trends.refresh": True, "plans.suggest": True}})
        self.assertEqual(autopilot.save_settings(self.store, {"tasks": {"plans.suggest": False}})["tasks"],
                         {"trends.refresh": True, "plans.suggest": False})
        self.assertEqual(autopilot.save_settings(self.store, {"tasks": {}})["tasks"], {"trends.refresh": True, "plans.suggest": False})
        for start in (0, 40, 80):
            autopilot.save_settings(self.store, {"tasks": {"old.task_%d" % n: True for n in range(start, start + 40)}})
        kept = autopilot.settings(self.store)["tasks"]
        self.assertEqual(len(kept), autopilot.MAX_TASKS_KEPT)                # the ones set last
        self.assertEqual(list(kept)[-1], "old.task_119")
        self.assertNotIn("trends.refresh", kept)
        self.assertEqual(len(self.store.load_config()["autopilot"]["tasks"]), autopilot.MAX_TASKS_KEPT)

    def test_the_app_refuses_a_switch_for_a_task_it_does_not_have_and_answers_with_the_state(self):
        self.task("trends.refresh")
        self.refused(lambda: self.pilot.save_settings({"enabled": True, "tasks": {"no.such_task": True}}), "autopilot_unknown_task")
        self.assertEqual(autopilot.settings(self.store), self.NEW)           # nothing of that request was saved
        state = self.pilot.save_settings({"enabled": True, "level": "auto", "daily_model_calls": 20, "tasks": {"trends.refresh": True}})
        self.assertEqual((state["enabled"], state["level"], state["daily_model_calls"]["limit"], state["tasks"][0]["on"]), (True, "auto", 20, True))
        self.assertEqual(state["active"], False)                             # the first switch is not the creator's to set
        self.refused(lambda: self.pilot.save_settings("enabled"), "autopilot_bad_setting")


# ---------------------------------------------------------------------------------------------- 11. the gate
class GateTest(Case):
    def setUp(self):
        super().setUp()
        self.gate = Gate()
        self.pilot = self.make(backups=self.gate)
        self.task("trends.refresh")
        self.switch_on("trends.refresh")

    def test_a_run_begins_inside_the_gate_every_change_passes_and_so_does_an_undo(self):
        self.registry.add_undo("notes.added", lambda store, payload: None)
        self.task("notes.add", run=lambda context: context.record_undo("notes.added", 1))
        autopilot.save_settings(self.store, {"tasks": {"notes.add": True}})
        self.assertEqual(self.look(), [("trends.refresh", "done"), ("notes.add", "done")])
        self.assertEqual(self.gate.passed.count((True, None)), 2)            # once for each run that began
        self.assertTrue(set(self.gate.passed) <= {(True, None), (False, None)})     # and it never names data: it is no page
        del self.gate.passed[:]
        self.pilot.run_now("trends.refresh")
        self.assertTrue(self.pilot.wait(10))
        self.assertEqual(self.gate.passed, [(True, None)])
        del self.gate.passed[:]
        self.pilot.undo(self.lines()[1]["id"])
        self.assertEqual(self.gate.passed, [(True, None)])

    def test_while_the_gate_is_closed_nothing_begins_and_afterwards_the_task_runs(self):
        self.gate.is_open.clear()                                            # a backup is being restored
        self.addCleanup(self.gate.is_open.set)
        written = []
        look = threading.Thread(target=lambda: written.extend(self.pilot.run_due()), daemon=True)
        look.start()
        self.assertTrue(self.gate.somebody_waits.wait(10))                   # the scheduler has come to the gate
        self.assertEqual((self.ran, self.sql("SELECT COUNT(*) FROM autopilot_runs"), self.pilot.jobs.unfinished()), ([], [(0,)], 0))
        self.gate.is_open.set()
        look.join(10)
        self.assertFalse(look.is_alive())
        self.assertEqual(([line["status"] for line in written], self.ran), (["done"], ["trends.refresh"]))

    def test_after_waiting_at_the_gate_it_looks_again_in_the_log_that_is_there_then(self):
        self.gate.is_open.clear()
        self.addCleanup(self.gate.is_open.set)
        written = []
        look = threading.Thread(target=lambda: written.extend(self.pilot.run_due()), daemon=True)
        look.start()
        self.assertTrue(self.gate.somebody_waits.wait(10))
        # The restored data says the task ran ten minutes ago.
        self.sql("INSERT INTO autopilot_runs (id, task, status, started_at, finished_at) VALUES ('aaaaaaaaaaaa', 'trends.refresh', 'done', ?, ?)",
                 (BASE - 600, BASE - 590))
        self.gate.is_open.set()
        look.join(10)
        self.assertFalse(look.is_alive())
        self.assertEqual((written, self.ran), ([], []))                      # so it is not due after all
        self.assertEqual(self.sql("SELECT COUNT(*) FROM autopilot_runs"), [(1,)])


# ---------------------------------------------------------------------------------------------- 12. the scheduler
class SchedulerTest(Case):
    def test_the_scheduler_is_one_thread_started_only_when_the_first_switch_is_on(self):
        ran = threading.Event()
        self.task("trends.refresh", run=lambda context: ran.set())
        autopilot.save_settings(self.store, {"enabled": True, "tasks": {"trends.refresh": True}})
        with mock.patch.object(autopilot, "FIRST_LOOK_AFTER_SEC", 0):
            self.assertIs(self.pilot.start(), False)
            self.assertNotIn("autopilot", [thread.name for thread in threading.enumerate()])
            self.pilot.on = True
            self.addCleanup(self.pilot.stop)
            self.assertIs(self.pilot.start(), True)
            self.assertIs(self.pilot.start(), True)                          # asked twice: still one
            self.assertEqual([thread.name for thread in threading.enumerate()].count("autopilot"), 1)
            self.assertTrue(ran.wait(10))                                    # it looked, and caught up on what was due
            self.assertTrue(self.pilot.wait(10))
        self.pilot.stop()
        self.assertNotIn("autopilot", [thread.name for thread in threading.enumerate()])
        self.assertEqual(self.ran, ["trends.refresh"])

    def test_a_look_that_trips_over_something_does_not_end_the_scheduler_and_saving_settings_makes_it_look_at_once(self):
        looked = threading.Semaphore(0)
        trips = [RuntimeError("the database was busy")]

        def look():
            looked.release()
            if trips:
                raise trips.pop()
            return []

        self.pilot.on = True
        self.addCleanup(self.pilot.stop)
        with mock.patch.object(autopilot, "FIRST_LOOK_AFTER_SEC", 0), mock.patch.object(self.pilot, "run_due", look), quiet() as printed:
            self.pilot.start()
            self.assertTrue(looked.acquire(timeout=10))                      # the first look, which trips
            self.pilot.save_settings({"enabled": True})                      # a minute has not passed
            self.assertTrue(looked.acquire(timeout=10))                      # and it looks again all the same
            self.pilot.stop()
        self.assertIn("the database was busy", printed.getvalue())
        self.assertEqual(trips, [])


# ---- what the classes below need --------------------------------------------------------------------------------
# The fingerprint data version 3 stood for (tests/test_backup.py, LAYOUT_OF_DATA_VERSION), and the tables as the code
# at 309d9f4 creates them: the commit that version was published from, and the one autopilot was built on.
LAYOUT_OF_DATA_VERSION_3 = "d5c2a68c0ed818d4"
SCHEMA_AT_309D9F4 = """
CREATE TABLE IF NOT EXISTS scripts (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    mode TEXT NOT NULL DEFAULT 'NORMAL_ORGANIC',
    video_format TEXT NOT NULL DEFAULT 'VLOG_VOICEOVER',
    target_sec INTEGER NOT NULL DEFAULT 60,
    notes TEXT NOT NULL DEFAULT '',
    lines_json TEXT NOT NULL DEFAULT '[]',
    line_count INTEGER,
    preview TEXT,
    post_json TEXT NOT NULL DEFAULT '{}',
    ai_lines_json TEXT,
    status TEXT NOT NULL DEFAULT 'DRAFT',
    keywords_json TEXT NOT NULL DEFAULT '[]',
    basis_json TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS samples (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    text TEXT NOT NULL,
    is_sponsored INTEGER NOT NULL DEFAULT 0,
    views INTEGER,
    likes INTEGER,
    collects INTEGER,
    comments INTEGER,
    kind TEXT NOT NULL DEFAULT 'script',
    timing_json TEXT,
    source_name TEXT NOT NULL DEFAULT '',
    needs_review INTEGER NOT NULL DEFAULT 0,
    reviewed_at REAL,
    origin TEXT NOT NULL DEFAULT '',
    script_id TEXT,
    posted_on TEXT,
    note_url TEXT NOT NULL DEFAULT '',
    metrics_on TEXT,
    ai_json TEXT,
    snapshot TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS trends (
    id TEXT PRIMARY KEY,
    key TEXT NOT NULL UNIQUE,
    keyword TEXT NOT NULL,
    heat INTEGER,
    source TEXT NOT NULL DEFAULT 'manual',
    source_url TEXT NOT NULL DEFAULT '',
    note_zh TEXT NOT NULL DEFAULT '',
    note_en TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS lessons (
    id TEXT PRIMARY KEY,
    script_id TEXT NOT NULL DEFAULT '',
    ai TEXT NOT NULL,
    final TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL,
    UNIQUE (ai, final)
);
CREATE TABLE IF NOT EXISTS briefs (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    fingerprint TEXT NOT NULL DEFAULT '',
    payload_json TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS ai_suggestions (
    script_id TEXT NOT NULL,
    text TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS ai_suggestions_by_script ON ai_suggestions (script_id);
CREATE TABLE IF NOT EXISTS script_pins (
    script_id TEXT NOT NULL,
    sample_id TEXT NOT NULL,
    created_at REAL NOT NULL,
    PRIMARY KEY (script_id, sample_id)
);
CREATE TABLE IF NOT EXISTS metric_snapshots (
    id TEXT PRIMARY KEY,
    sample_id TEXT NOT NULL,
    read_on TEXT NOT NULL,
    views INTEGER,
    likes INTEGER,
    collects INTEGER,
    comments INTEGER,
    shares INTEGER,
    new_followers INTEGER,
    click_rate_pct REAL,
    avg_watch_sec REAL,
    completion_rate_pct REAL,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS metric_snapshots_by_sample ON metric_snapshots (sample_id);
CREATE TABLE IF NOT EXISTS sample_comments (
    id TEXT PRIMARY KEY,
    sample_id TEXT NOT NULL,
    text TEXT NOT NULL,
    likes INTEGER,
    sentiment TEXT NOT NULL DEFAULT 'neutral',
    is_question INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS sample_comments_by_sample ON sample_comments (sample_id);
CREATE TABLE IF NOT EXISTS sample_sentiments (
    sample_id TEXT PRIMARY KEY,
    summary_json TEXT NOT NULL DEFAULT '{}',
    source TEXT NOT NULL DEFAULT 'local',
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS sample_beats (
    id TEXT PRIMARY KEY,
    sample_id TEXT NOT NULL,
    beat_index INTEGER NOT NULL DEFAULT 1,
    beat_type TEXT NOT NULL,
    audio_mode TEXT NOT NULL DEFAULT 'VOICEOVER',
    delivery TEXT NOT NULL DEFAULT '',
    start_sec REAL,
    end_sec REAL,
    spoken_text TEXT NOT NULL,
    english_meaning TEXT NOT NULL DEFAULT '',
    visual_summary TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS sample_beats_by_sample ON sample_beats (sample_id);
CREATE TABLE IF NOT EXISTS video_plans (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    video_format TEXT NOT NULL DEFAULT 'VLOG_VOICEOVER',
    target_sec INTEGER NOT NULL DEFAULT 60,
    scheduled_for TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'PLANNED',
    priority INTEGER NOT NULL DEFAULT 2,
    source_kind TEXT NOT NULL DEFAULT 'manual',
    notes TEXT NOT NULL DEFAULT '',
    rationale_zh TEXT NOT NULL DEFAULT '',
    rationale_en TEXT NOT NULL DEFAULT '',
    keywords_json TEXT NOT NULL DEFAULT '[]',
    questions_json TEXT NOT NULL DEFAULT '[]',
    script_id TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS sponsor_briefs (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL DEFAULT '',
    source_name TEXT NOT NULL DEFAULT '',
    brief TEXT NOT NULL,
    reading_json TEXT,
    choice_json TEXT,
    ticks_json TEXT NOT NULL DEFAULT '{}',
    script_id TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
"""


def load(folder, name):
    """A script of this repository that is not part of a package (``tools/``, ``desktop/``), loaded by its path."""
    spec = importlib.util.spec_from_file_location(name, str(ROOT / folder / (name + ".py")))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


try_release = load("tools", "try_release")
try_installer = load("desktop", "try_installer")


@contextlib.contextmanager
def loopback_only():
    """Nothing may be asked of another computer: a connection or a lookup that leaves this one is written down and refused."""
    blocked = []
    real_connect, real_lookup = socket.socket.connect, socket.getaddrinfo

    def is_local(host):
        host = str(host)
        return host in ("localhost", "::1") or host.startswith("127.")

    def connect(sock, address):
        host = address[0] if isinstance(address, tuple) else address
        if not is_local(host):
            blocked.append(("connect", address))
            raise OSError("a test tried to reach another computer")
        return real_connect(sock, address)

    def lookup(host, *args, **kwargs):
        if host is not None and not is_local(host):
            blocked.append(("lookup", host))
            raise socket.gaierror("a test tried to look up another computer")
        return real_lookup(host, *args, **kwargs)

    with mock.patch.object(socket.socket, "connect", connect), mock.patch.object(socket, "getaddrinfo", lookup):
        yield blocked


def rows(path):
    """Every row of every table of a database, with its place in the table."""
    with contextlib.closing(sqlite3.connect(str(path))) as db:
        tables = sorted(row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'"))
        return {table: db.execute("SELECT rowid, * FROM %s ORDER BY rowid" % table).fetchall() for table in tables}


class ServerCase(Case):
    """The app on that folder as a test makes it, answering on this computer. Its autopilot has this test's tasks and clock."""

    def setUp(self):
        super().setUp()
        guard = loopback_only()
        self.blocked = guard.__enter__()
        self.addCleanup(guard.__exit__, None, None, None)
        self.httpd = server.make_server(0, self.store, lambda *args: {})
        self.app = self.httpd.app
        self.pilot = self.app.autopilot
        self.pilot.registry, self.pilot.clock = self.registry, self.clock
        self.pilots.append(self.pilot)
        self.port = self.httpd.server_address[1]
        thread = threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
        thread.start()
        self.addCleanup(lambda: (self.httpd.shutdown(), self.httpd.server_close(), thread.join(5)))

    def send(self, method, path, payload=None, headers=None, body=None):
        head = dict(headers or {})
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            head["Content-Type"] = "application/json"
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=60)
        try:
            conn.request(method, path, body=body, headers=head)
            res = conn.getresponse()
            raw = res.read()
        finally:
            conn.close()
        return res.status, json.loads(raw.decode("utf-8"))

    def said(self, answer):
        """A refusal has a code and a sentence in each language, and is never the last-resort answer."""
        self.assertNotEqual(answer.get("error"), "internal")
        self.assertRegex(answer.get("message_zh", ""), CHINESE)
        self.assertRegex(answer["message"], "[A-Za-z]{4}")
        return answer["error"]

    def packaged(self, version):
        """The app runs as a release package of ``version`` for as long as the test lasts."""
        path = self.tmp / "build.json"
        path.write_text(json.dumps({"version": version, "commit": "a" * 40, "built_on": "2026-11-22"}), encoding="utf-8")
        for patch in (mock.patch.object(buildinfo, "BUILD_FILE", path), mock.patch.object(buildinfo, "desktop", return_value=None)):
            patch.start()
            self.addCleanup(patch.stop)


# ---------------------------------------------------------------------------------------------- 13. the four requests
class RequestsTest(ServerCase):
    def test_a_server_a_test_makes_runs_nothing_whatever_is_requested(self):
        self.task("trends.refresh")
        self.assertIs(self.app.autopilot_on, False)
        self.assertNotIn("autopilot", [thread.name for thread in threading.enumerate()])
        status, state = self.send("POST", "/api/autopilot/settings", {"enabled": True, "level": "auto", "tasks": {"trends.refresh": True}})
        self.assertEqual((status, state["on"], state["enabled"], state["active"], state["level"]), (200, False, True, False, "auto"))
        status, answer = self.send("POST", "/api/autopilot/run", {"task": "trends.refresh"})
        self.assertEqual((status, self.said(answer)), (409, "autopilot_off_here"))
        self.assertIn("LEARN_MY_VOICE_NO_AUTOPILOT=1", answer["message"])
        self.assertIn("LEARN_MY_VOICE_NO_AUTOPILOT=1", answer["message_zh"])
        self.assertEqual(self.pilot.run_due(), [])                           # and what the scheduler would do does nothing either
        status, state = self.send("GET", "/api/autopilot")
        self.assertEqual((status, state["runs"], state["running"], state["tasks"][0]["next_run_at"]), (200, [], None, None))
        self.assertEqual((self.ran, self.app.jobs.unfinished(), self.blocked), ([], 0, []))

    def test_once_the_first_switch_is_on_the_same_requests_run_the_task_and_take_back_what_it_did(self):
        """Without this the test above would also pass if the requests could never run anything."""
        notes = []
        self.registry.add_undo("notes.added", lambda store, payload: notes.remove(payload))
        self.task("notes.add", run=lambda context: (notes.append("one"), context.record_undo("notes.added", "one"), {"added": 1})[2])
        self.app.autopilot_on = True
        status, state = self.send("POST", "/api/autopilot/settings", {"enabled": True, "tasks": {"notes.add": True}})
        self.assertEqual((status, state["active"], state["tasks"][0]["on"], state["tasks"][0]["next_run_at"], state["now"]),
                         (200, True, True, BASE, BASE))                      # it never ran: it is due now
        status, answer = self.send("POST", "/api/autopilot/run", {"task": "notes.add"})
        self.assertEqual((status, answer["run"]["task"], answer["run"]["forced"], answer["job"]["kind"]), (202, "notes.add", True, "autopilot"))
        self.assertTrue(self.pilot.wait(10))
        status, followed = self.send("GET", "/api/jobs/" + answer["job"]["id"])
        self.assertEqual((status, followed["job"]["status"], followed["job"]["label"], followed["job"]["result"]["summary"]),
                         (200, "done", "notes.add", {"added": 1}))
        status, state = self.send("GET", "/api/autopilot")
        line = state["runs"][0]
        self.assertEqual((line["id"], line["status"], line["changes"], line["can_undo"], notes), (answer["run"]["id"], "done", 1, True, ["one"]))
        self.assertEqual((state["tasks"][0]["last_run"]["id"], state["tasks"][0]["next_run_at"]), (line["id"], BASE + HOUR))
        status, undone = self.send("POST", "/api/autopilot/runs/%s/undo" % line["id"], {})
        self.assertEqual((status, undone["undone"], undone["run"]["can_undo"], undone["run"]["undone_at"], notes), (200, 1, False, BASE, []))
        status, again = self.send("POST", "/api/autopilot/runs/%s/undo" % line["id"], {})
        self.assertEqual((status, self.said(again)), (409, "autopilot_already_undone"))
        self.assertEqual(self.blocked, [])

    def test_every_refusal_has_a_code_and_a_sentence_in_each_language(self):
        self.task("trends.refresh")
        refused = [
            ("/api/autopilot/run", {"task": "trends.refresh"}, 409, "autopilot_off_here"),
            ("/api/autopilot/run", {"task": "no.such_task"}, 404, "autopilot_unknown_task"),
            ("/api/autopilot/run", {}, 404, "autopilot_unknown_task"),
            ("/api/autopilot/settings", {"enabled": "yes"}, 400, "autopilot_bad_setting"),
            ("/api/autopilot/settings", {"daily_model_calls": 500}, 400, "autopilot_bad_setting"),
            ("/api/autopilot/settings", {"watch_folder": "/somewhere"}, 400, "autopilot_bad_setting"),
            ("/api/autopilot/settings", {"tasks": {"no.such_task": True}}, 404, "autopilot_unknown_task"),
            ("/api/autopilot/runs/000000000000/undo", {}, 404, "autopilot_run_not_found"),
        ]
        for path, payload, expected, code in refused:
            status, answer = self.send("POST", path, payload)
            self.assertEqual((status, self.said(answer)), (expected, code), (path, payload))
        self.assertEqual(autopilot.settings(self.store), SettingsTest.NEW)   # none of it saved anything
        self.app.autopilot_on = True
        status, answer = self.send("POST", "/api/autopilot/run", {"task": "trends.refresh"})
        self.assertEqual((status, self.said(answer)), (409, "autopilot_not_enabled"))
        self.send("POST", "/api/autopilot/settings", {"enabled": True})
        status, answer = self.send("POST", "/api/autopilot/run", {"task": "trends.refresh"})
        self.assertEqual((status, self.said(answer)), (409, "autopilot_task_off"))
        for method, path in (("GET", "/api/autopilot/runs"), ("GET", "/api/autopilot/run"), ("DELETE", "/api/autopilot"),
                             ("PUT", "/api/autopilot/settings"), ("POST", "/api/autopilot/runs/not-an-id/undo"),
                             ("POST", "/api/autopilot/runs/000000000000/redo"), ("GET", "/api/autopilotx")):
            status, answer = self.send(method, path, {} if method == "POST" else None)
            self.assertEqual((status, answer["error"]), (404, "not_found"), (method, path))
        status, answer = self.send("POST", "/api/autopilot/settings", body=b"enabled=true",
                                   headers={"Content-Type": "application/x-www-form-urlencoded"})
        self.assertGreaterEqual(status, 400)                                 # a form another site could send is not taken
        self.assertIs(autopilot.settings(self.store)["level"], "suggest")
        self.assertEqual((self.ran, self.blocked), ([], []))

    def test_a_page_that_was_open_before_a_restore_can_neither_change_the_settings_nor_run_a_task(self):
        self.task("trends.refresh")
        self.switch_on("trends.refresh")
        stale = {backup.EPOCH_HEADER: "abcdefabcdef"}                        # the data that page means is no longer there
        for path, payload in (("/api/autopilot/settings", {"enabled": False}), ("/api/autopilot/run", {"task": "trends.refresh"}),
                              ("/api/autopilot/runs/000000000000/undo", {})):
            status, answer = self.send("POST", path, payload, headers=stale)
            self.assertEqual((status, self.said(answer)), (409, "stale_data"), path)
        self.assertEqual((autopilot.settings(self.store)["enabled"], self.ran), (True, []))
        status, _state = self.send("GET", "/api/autopilot", headers=stale)   # reading goes on
        self.assertEqual(status, 200)

    def test_a_task_gets_the_transport_the_app_has_when_it_runs(self):
        seen = []
        self.task("ideas.refresh", run=lambda context: seen.append(context.transport))
        self.switch_on("ideas.refresh")
        self.app.transport = stand_in = lambda *args: {"said": "nothing"}    # put there after the app was made, as tests do
        self.assertEqual(self.look(), [("ideas.refresh", "done")])
        self.assertEqual(seen, [stand_in])

    def test_a_task_gets_the_transcriber_the_app_has_when_it_runs_and_none_where_none_was_handed_in(self):
        seen = []
        self.task("voice.ingest_folder", every=600, due=lambda context: seen.append(("due", context.transcriber)) or True,
                  run=lambda context: seen.append(("run", context.transcriber)))
        self.switch_on("voice.ingest_folder")
        self.assertEqual(self.look(), [("voice.ingest_folder", "done")])
        self.assertIs(self.app.transcriber, server.transcribe)               # what the app transcribes with unless told otherwise
        self.assertEqual(seen[-1], ("run", server.transcribe))
        self.assertEqual({what for _when, what in seen}, {server.transcribe})    # its condition is handed the same
        self.app.transcriber = stand_in = object()                           # put there after the app was made, as tests do
        self.later(600)
        self.look()
        self.assertEqual(seen[-1], ("run", stand_in))
        alone = self.make()                                                  # an autopilot nobody handed a transcriber
        alone.on = True
        self.later(600)
        self.assertEqual([line["status"] for line in alone.run_due()], ["done"])
        self.assertEqual(seen[-1], ("run", None))


# ---------------------------------------------------------------------------------------------- 14. who turns the first switch on
class StartedTest(Case):
    """Only ``server.main`` turns the first switch on and starts the scheduler, and only for a run a person started."""

    def started(self, **variables):
        seen = {}

        def serve(httpd, *args, **kwargs):
            pilot = httpd.app.autopilot
            seen.update(on=httpd.app.autopilot_on, scheduler=pilot._thread is not None and pilot._thread.is_alive(),
                        window_check=httpd.app.window_check is not None, looked=pilot.run_due(), pilot=pilot)

        variables.setdefault(server.NO_UPDATE_CHECK_ENV, "1")
        environment = dict(variables, STUDIO_DATA_DIR=str(self.tmp / ("started-%d" % len(os.listdir(str(self.tmp)))) / "data"))
        with mock.patch.dict(os.environ, environment), mock.patch.object(server.StudioServer, "serve_forever", serve), \
                contextlib.redirect_stdout(io.StringIO()):
            for name in (server.NO_AUTOPILOT_ENV, server.WINDOW_CHECK_ENV):
                if name not in variables:
                    os.environ.pop(name, None)
            self.assertEqual(server.main(["--port", "0", "--no-browser"]), 0)
        pilot = seen.pop("pilot")
        seen["scheduler_after"] = pilot._thread is not None and pilot._thread.is_alive()
        return seen

    def test_started_by_a_person_the_app_may_run_tasks_and_has_its_scheduler_until_it_closes(self):
        self.assertEqual(self.started(), {"on": True, "scheduler": True, "window_check": False, "looked": [], "scheduler_after": False})
        self.assertNotIn("autopilot", [thread.name for thread in threading.enumerate()])

    def test_started_with_the_variable_it_may_not(self):
        self.assertEqual(server.NO_AUTOPILOT_ENV, "LEARN_MY_VOICE_NO_AUTOPILOT")
        seen = self.started(LEARN_MY_VOICE_NO_AUTOPILOT="1")
        self.assertEqual((seen["on"], seen["scheduler"]), (False, False))
        for other in ("0", "", "yes", "true"):
            seen = self.started(LEARN_MY_VOICE_NO_AUTOPILOT=other)
            self.assertEqual((seen["on"], seen["scheduler"]), (True, True), other)

    def test_started_for_a_check_of_the_window_it_may_not(self):
        seen = self.started(LEARN_MY_VOICE_WINDOW_CHECK="1")
        self.assertEqual((seen["on"], seen["scheduler"], seen["window_check"]), (False, False, True))

    def test_a_server_made_any_other_way_may_not_and_has_no_scheduler(self):
        httpd = server.make_server(0, self.store)
        self.addCleanup(httpd.server_close)
        self.assertIs(httpd.app.autopilot_on, False)
        self.assertIsNone(httpd.app.autopilot._thread)
        self.assertIs(httpd.app.autopilot.start(), False)
        source = (ROOT / "studio" / "server.py").read_text(encoding="utf-8")
        main = source[source.index("\ndef main("):]
        # One place sets the switch and one starts the scheduler: both in main. The other assignment is the property itself.
        self.assertEqual((len(re.findall(r"\.autopilot_on = ", source)), len(re.findall(r"\.autopilot_on = ", main))), (1, 1))
        self.assertEqual((len(re.findall(r"autopilot\.start\(", source)), len(re.findall(r"autopilot\.start\(", main))), (1, 1))
        self.assertEqual(len(re.findall(r"autopilot\.on = ", source)), 1)
        for name in ("backup.py", "updater.py", "mcp_server.py", "pipeline.py"):
            other = (ROOT / "studio" / name).read_text(encoding="utf-8")
            self.assertIsNone(re.search(r"autopilot_on\s*=|\.on = True|\.start\(\)", other), name)


# ---------------------------------------------------------------------------------------------- 15. the trials
class Ended:
    """Stands in for a started app that ended at once."""
    pid, returncode = 4242, 1

    def poll(self):
        return 1

    def wait(self, timeout=None):
        return 1

    def kill(self):
        pass


class TrialsTest(ServerCase):
    def test_the_three_places_name_the_same_variable(self):
        self.assertEqual({autopilot.NO_AUTOPILOT_ENV, server.NO_AUTOPILOT_ENV, try_release.NO_AUTOPILOT_ENV, try_installer.NO_AUTOPILOT_ENV},
                         {"LEARN_MY_VOICE_NO_AUTOPILOT"})

    def test_the_trial_of_a_package_hands_it_to_every_app_it_starts(self):
        for given in ({}, {"LEARN_MY_VOICE_NO_AUTOPILOT": "0"}, {"LEARN_MY_VOICE_NO_AUTOPILOT": "1"}):
            started = []

            def start(command, **more):
                started.append((command, more["env"]))
                return Ended()

            app = self.tmp / "unpacked" / "learn-my-voice-0.2.0"
            app.mkdir(parents=True, exist_ok=True)
            with mock.patch.dict(os.environ, given), mock.patch.object(try_release.subprocess, "Popen", start):
                if not given:
                    os.environ.pop("LEARN_MY_VOICE_NO_AUTOPILOT", None)
                with self.assertRaises(try_release.Failed) as stopped:
                    try_release.run_once(app, ["sh", "start-learn-my-voice.sh"], "0.2.0", "the launcher", [], chinese=True)
            self.assertIn("ended at once", str(stopped.exception))
            self.assertEqual(len(started), 1)
            self.assertEqual(started[0][1]["LEARN_MY_VOICE_NO_AUTOPILOT"], "1", given)
        source = (ROOT / "tools" / "try_release.py").read_text(encoding="utf-8")
        self.assertEqual(source.count("subprocess.Popen("), 1)               # and there is no other place it starts an app

    def test_the_trial_of_an_installer_hands_it_to_both_starts_of_the_app(self):
        started = []
        with mock.patch.object(try_installer.subprocess, "Popen", lambda command, **more: started.append(more["env"])), \
                mock.patch.dict(os.environ, {"LEARN_MY_VOICE_NO_AUTOPILOT": "0"}):
            try_installer.start(self.tmp / "app", self.tmp / "data-of-the-trial", self.tmp / "log.txt", window_check=True)
            try_installer.start(self.tmp / "app", None, self.tmp / "log.txt")
        self.assertEqual([env["LEARN_MY_VOICE_NO_AUTOPILOT"] for env in started], ["1", "1"])
        source = (ROOT / "desktop" / "try_installer.py").read_text(encoding="utf-8")
        self.assertEqual(source.count("subprocess.Popen("), 1)

    def test_the_trial_asks_the_app_whether_it_knows_and_fails_when_it_would_run_tasks(self):
        self.packaged("0.2.0-beta.rc.99")
        base = "http://127.0.0.1:%d" % self.port
        checks = []
        try_release.use(base, "0.2.0-beta.rc.99", checks)
        self.assertIn("started by a trial, it runs no task by itself and says so", checks)
        self.app.autopilot_on = True                                         # the variable did not reach the app
        with self.assertRaises(try_release.Failed) as stopped:
            try_release.use(base, "0.2.0-beta.rc.99", [])
        self.assertIn("it runs no task by itself", str(stopped.exception))
        # The trial of an installer asks the same of the app it started the way a person starts it. That trial needs
        # an installer and is not run here: only that it asks, and what it does with the answer, is read off its text.
        for name, folder in (("try_release", "tools"), ("try_installer", "desktop")):
            source = (ROOT / folder / (name + ".py")).read_text(encoding="utf-8")
            self.assertEqual(re.findall(r'"/api/autopilot[^"]*"', source), ['"/api/autopilot"'], name)
        installer = (ROOT / "desktop" / "try_installer.py").read_text(encoding="utf-8")
        self.assertIn('pilot.get("on") is not False', installer)
        self.assertEqual(self.blocked, [])


# ---------------------------------------------------------------------------------------------- 16. restoring a backup, moving data in
class RestoreCase(Case):
    """The gate and the jobs of a running app (``backup.Backups``, ``Jobs``), and its autopilot beside them."""

    def setUp(self):
        super().setUp()
        self.jobs = Jobs()
        self.backups = backup.Backups(self.store, self.jobs)
        self.pilot = self.make(jobs=self.jobs, backups=self.backups)
        self.count = 0

    def backup_of(self, store):
        self.count += 1
        path = self.tmp / ("made-%d.%s" % (self.count, backup.EXTENSION))
        backup.create(store, path)
        return path

    def staged(self, path):
        """Hand a copy of the file to the app the way an upload arrives."""
        self.count += 1
        copy = self.tmp / ("arrived-%d.part" % self.count)
        shutil.copyfile(str(path), str(copy))
        return self.backups.stage(copy)

    def elsewhere(self):
        """Another data folder, with a script in it."""
        there = Store(self.tmp / ("elsewhere-%d" % self.count) / "data")
        there.create_script(title="那边的一篇")
        return there

    def look_while(self, replace):
        """Have the scheduler look in the middle of ``replace(at_the_moment)``, which calls ``at_the_moment()`` once the
        data is being replaced. Returns ``(what was seen at that moment, the lines the look wrote in the end)``."""
        seen, written = {}, []
        look = threading.Thread(target=lambda: written.extend(self.pilot.run_due()), daemon=True)

        def at_the_moment():
            if not seen:
                look.start()
                look.join(0.3)              # far longer than a look takes that nothing holds back
                seen.update(still_waiting=look.is_alive(), ran=list(self.ran), jobs=self.jobs.unfinished())

        replace(at_the_moment)
        look.join(10)
        self.assertFalse(look.is_alive())
        return seen, written


class RestoreTest(RestoreCase):
    def test_no_task_starts_while_a_backup_is_being_restored_and_it_runs_afterwards_in_the_restored_data(self):
        self.task("trends.refresh")
        self.switch_on("trends.refresh")
        preview = self.staged(self.backup_of(self.elsewhere()))

        def restore(at_the_moment):
            self.backups._before_step = lambda name: at_the_moment()         # the restore has shut everybody out and replaces the data
            self.backups.restore(preview["restore_id"])

        seen, written = self.look_while(restore)
        self.assertEqual(seen, {"still_waiting": True, "ran": [], "jobs": 0})
        # The log that was restored has no run of the task: it is due, and runs on what is there now.
        self.assertEqual(([line["status"] for line in written], self.ran), (["done"], ["trends.refresh"]))
        self.assertEqual([script["title"] for script in self.store.list_scripts()], ["那边的一篇"])
        self.assertEqual(self.sql("SELECT task, status FROM autopilot_runs"), [("trends.refresh", "done")])

    def test_after_a_restore_the_schedule_goes_by_the_log_that_was_restored(self):
        self.task("trends.refresh", every=12 * HOUR)
        self.switch_on("trends.refresh")
        there = self.elsewhere()
        autopilot.save_settings(there, {"enabled": True, "tasks": {"trends.refresh": True}})
        over_there = autopilot.Autopilot(there, registry=self.registry, clock=self.clock)
        self.pilots.append(over_there)
        over_there.on = True
        self.assertEqual([line["status"] for line in over_there.run_due()], ["done"])      # it ran there, just now
        del self.ran[:]
        preview = self.staged(self.backup_of(there))

        def restore(at_the_moment):
            self.backups._before_step = lambda name: at_the_moment()
            self.backups.restore(preview["restore_id"])

        seen, written = self.look_while(restore)                             # here it never ran: before the restore it was due
        self.assertEqual(seen, {"still_waiting": True, "ran": [], "jobs": 0})
        self.assertEqual((written, self.ran), ([], []))                      # by the restored log it ran just now: not due
        self.later(12 * HOUR)
        self.assertEqual(self.look(), [("trends.refresh", "done")])
        self.assertEqual(len(self.sql("SELECT id FROM autopilot_runs")), 2)

    def test_a_restore_is_refused_while_a_task_runs_and_goes_through_once_it_is_done(self):
        started, release = threading.Event(), self.hold()
        self.task("voice.relearn", run=lambda context: (started.set(), release.wait(10)) and {})
        self.switch_on("voice.relearn")
        preview = self.staged(self.backup_of(self.elsewhere()))
        self.assertEqual(self.jobs.unfinished(), 0)
        look = threading.Thread(target=self.pilot.run_due, daemon=True)      # the scheduler's own run, not one started by hand
        look.start()
        self.assertTrue(started.wait(10))
        self.assertEqual((self.jobs.unfinished(), self.backups.info()["busy"]), (1, True))
        with self.assertRaises(backup.Refused) as refused:
            self.backups.restore(preview["restore_id"])
        self.assertEqual((refused.exception.code, refused.exception.status), ("restore_busy", 409))
        self.assertRegex(refused.exception.message_zh, CHINESE)
        self.assertEqual(self.store.list_scripts(), [])                      # nothing was replaced
        release.set()
        look.join(10)
        self.assertTrue(self.pilot.wait(10))
        self.assertEqual((self.jobs.unfinished(), self.backups.info()["busy"]), (0, False))
        self.assertTrue(self.backups.restore(preview["restore_id"])["restored"])
        self.assertEqual([script["title"] for script in self.store.list_scripts()], ["那边的一篇"])

    def test_a_backup_made_while_a_task_ran_holds_that_run_and_after_a_restore_it_counts_as_cut_off(self):
        started, release = threading.Event(), self.hold()
        self.task("voice.relearn", every=7 * DAY, run=lambda context: (started.set(), release.wait(10)) and {})
        self.switch_on("voice.relearn")
        self.pilot.run_now("voice.relearn")
        self.assertTrue(started.wait(10))
        path = self.backup_of(self.store)                                    # a backup can be made while a task runs
        release.set()
        self.assertTrue(self.pilot.wait(10))
        self.assertEqual(self.lines()[0]["status"], "done")
        self.later(2 * HOUR)
        self.backups.restore(self.staged(path)["restore_id"])
        state = self.pilot.status()
        self.assertEqual((state["runs"][0]["status"], state["running"], state["tasks"][0]["next_run_at"]),
                         ("interrupted", None, BASE + 2 * HOUR))            # due since an hour ago: now
        self.assertEqual(self.sql("SELECT status FROM autopilot_runs"), [("running",)])     # as the backup has it, until a run begins
        self.assertEqual(self.look(), [("voice.relearn", "done")])           # two hours after it began: tried again
        self.assertEqual(self.sql("SELECT status FROM autopilot_runs ORDER BY rowid"), [("interrupted",), ("done",)])

    def test_the_settings_stay_with_the_computer_a_backup_does_not_carry_them_and_a_restore_does_not_change_them(self):
        there = self.elsewhere()
        autopilot.save_settings(there, {"enabled": True, "level": "auto", "daily_model_calls": 99, "tasks": {"trends.refresh": True}})
        autopilot.save_other_setting(there, "watch_folder", "/Users/somebody/over-there")
        path = self.backup_of(there)
        with zipfile.ZipFile(str(path)) as archive:
            self.assertEqual(sorted(json.loads(archive.read("settings.json").decode("utf-8"))), ["trend_weight"])   # what a backup carries
            for name in archive.namelist():
                self.assertNotIn(b"over-there", archive.read(name), name)
        autopilot.save_settings(self.store, {"daily_model_calls": 3})
        autopilot.save_other_setting(self.store, "watch_folder", "/Users/somebody/here")
        self.backups.restore(self.staged(path)["restore_id"])
        self.assertEqual(autopilot.settings(self.store), {"enabled": False, "level": "suggest", "daily_model_calls": 3, "tasks": {}})
        self.assertEqual(self.store.load_config()["autopilot"]["watch_folder"], "/Users/somebody/here")
        self.assertEqual([script["title"] for script in self.store.list_scripts()], ["那边的一篇"])

    def test_no_task_starts_while_data_is_moved_in_from_an_earlier_folder_and_moving_is_refused_while_one_runs(self):
        earlier = self.tmp / "learn-my-voice-0.1.9"
        Store(earlier / "data").create_script(title="早期脚本")
        started, release = threading.Event(), self.hold()
        self.task("voice.relearn", run=lambda context: (started.set(), release.wait(10)) and {})
        self.task("trends.refresh")
        self.switch_on("voice.relearn")
        self.pilot.run_now("voice.relearn")
        self.assertTrue(started.wait(10))
        with self.assertRaises(updater.UpdateError) as refused:
            updater.migrate_from_previous_folder(self.store, earlier, backups=self.backups, jobs=self.jobs)
        self.assertEqual((refused.exception.code, refused.exception.status), ("migrate_busy", 409))
        self.assertRegex(refused.exception.message_zh, CHINESE)
        self.assertEqual(self.store.list_scripts(), [])
        release.set()
        self.assertTrue(self.pilot.wait(10))
        # The folder still counts as empty: one line in the log is not the creator's work. Now the other task is due.
        autopilot.save_settings(self.store, {"tasks": {"voice.relearn": False, "trends.refresh": True}})
        del self.ran[:]
        put_in_place = updater._apply_staged_to_empty_store

        def move(at_the_moment):
            def while_everybody_is_out(*args):
                at_the_moment()
                return put_in_place(*args)

            with mock.patch.object(updater, "_apply_staged_to_empty_store", while_everybody_is_out):
                updater.migrate_from_previous_folder(self.store, earlier, backups=self.backups, jobs=self.jobs)

        seen, written = self.look_while(move)
        self.assertEqual(seen, {"still_waiting": True, "ran": [], "jobs": 0})
        self.assertEqual([script["title"] for script in self.store.list_scripts()], ["早期脚本"])
        self.assertEqual(([line["task"] for line in written], self.ran), (["trends.refresh"], ["trends.refresh"]))


# ---------------------------------------------------------------------------------------------- 17. the version of the data
class DataVersionTest(RestoreCase):
    def test_the_tables_written_out_here_are_the_ones_data_version_3_stood_for(self):
        with mock.patch.object(store_module, "_SCHEMA", SCHEMA_AT_309D9F4):
            self.assertEqual(backup.layout_fingerprint(), LAYOUT_OF_DATA_VERSION_3)
        self.assertNotIn("autopilot_runs", SCHEMA_AT_309D9F4)
        self.assertEqual(backup.DATA_VERSION, 4)
        self.assertNotEqual(backup.layout_fingerprint(), LAYOUT_OF_DATA_VERSION_3)
        self.assertEqual(sorted(set(backup.reference_layout()["tables"]) - set(rows(self.version_3_folder().db_path))), ["autopilot_runs"])

    def version_3_folder(self):
        """A data folder as the app at data version 3 makes it, with something in it."""
        with mock.patch.object(store_module, "_SCHEMA", SCHEMA_AT_309D9F4):
            old = Store(self.tmp / ("version-3-%d" % self.count) / "data")
            self.count += 1
            old.create_script(title="版本三写的", lines=[{"spoken_script_zh": "今天去超市买菜啦 / 人特别多嘛"}], keywords=["秋冬护肤"])
            old.create_sample(title="素材", text="上周末去 Costco 补货嘛 / 结果某人又走不动哈", metrics={"views": 1200, "likes": 80})
            old.upsert_trends([{"keyword": "秋冬护肤", "heat": 125000}], source="manual")
            old.create_video_plan(title="下期计划", scheduled_for="2026-11-28")
            old.add_lesson(script_id="", ai="因此我强烈推荐", final="所以我真的很推荐嘛", note="口语一点")
        return old

    def test_a_backup_made_by_data_version_3_is_restored_by_this_version(self):
        old = self.version_3_folder()
        path = self.tmp / ("version-3.%s" % backup.EXTENSION)
        with mock.patch.object(store_module, "_SCHEMA", SCHEMA_AT_309D9F4), mock.patch.object(backup, "DATA_VERSION", 3):
            backup.create(old, path)                                         # written by the app's own code, as that version wrote it
        was = rows(old.db_path)
        with zipfile.ZipFile(str(path)) as archive:
            said = json.loads(archive.read(backup.MANIFEST).decode("utf-8"))
            (self.tmp / "inside.db").write_bytes(archive.read(backup.DATABASE))
        self.assertEqual(said["data_version"], 3)
        self.assertNotIn("autopilot_runs", rows(self.tmp / "inside.db"))
        self.store.create_script(title="这里原来的一篇")
        preview = self.staged(path)
        self.assertEqual((preview["file"]["data_version"], preview["file"]["older"], preview["file"]["holds"]["scripts"],
                          preview["file"]["holds"]["samples"], preview["now"]["scripts"]), (3, True, 1, 1, 1))
        self.assertTrue(self.backups.restore(preview["restore_id"])["restored"])
        now = rows(self.store.db_path)
        self.assertEqual(now.pop("autopilot_runs"), [])                      # the table this version adds is there, with nothing in it
        self.assertEqual(now, was)                                           # and every row of that backup is, as it was
        self.assertEqual([script["title"] for script in self.store.list_scripts()], ["版本三写的"])
        # Autopilot works on what was restored, and the folder can be backed up and restored again as version 4.
        self.task("trends.refresh")
        self.switch_on("trends.refresh")
        self.assertEqual(self.look(), [("trends.refresh", "done")])
        again = self.backup_of(self.store)
        with zipfile.ZipFile(str(again)) as archive:
            self.assertEqual(json.loads(archive.read(backup.MANIFEST).decode("utf-8"))["data_version"], 4)
        preview = self.staged(again)
        self.assertEqual((preview["file"]["data_version"], preview["file"]["older"]), (4, False))
        self.backups.restore(preview["restore_id"])
        self.assertEqual([(line["task"], line["status"]) for line in self.lines()], [("trends.refresh", "done")])

    def test_a_backup_of_this_version_is_refused_by_what_cannot_read_it_with_a_sentence(self):
        path = self.backup_of(self.store)
        with mock.patch.object(backup, "DATA_VERSION", 3):                   # an app that knows data version 3 and no later
            with self.assertRaises(backup.Refused) as refused:
                self.staged(path)
        self.assertEqual((refused.exception.code, refused.exception.status), ("backup_newer", 409))
        self.assertRegex(refused.exception.message_zh, CHINESE)

    def test_the_lines_of_the_log_do_not_count_as_the_creators_work(self):
        self.task("lessons.accept_recurring")
        self.switch_on("lessons.accept_recurring")
        self.assertEqual(self.look(), [("lessons.accept_recurring", "done")])
        self.assertEqual(len(self.sql("SELECT id FROM autopilot_runs")), 1)
        self.assertNotIn("autopilot_runs", updater._WORK_TABLES)
        self.assertTrue(updater.is_data_empty(self.store))                   # a folder autopilot ran in, with nothing of the creator's
        self.store.create_script(title="一篇")
        self.assertFalse(updater.is_data_empty(self.store))


if __name__ == "__main__":
    unittest.main()
