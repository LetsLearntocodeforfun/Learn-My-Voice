"""Tests for autopilot's core (studio/autopilot.py): what the app does by itself, within what the creator chose.

Each class covers one thing a creator, or a module that registers a task, can rely on:

- nothing runs unless this start of the app may run tasks AND the creator switched autopilot on AND switched
  that task on; with any of them off, a run asked for by hand is refused in English and in Chinese;
- registering a task or an undo handler only puts it on a list, and a name is checked when it is registered;
- a task is handed the store, the clock, a copy of the settings it cannot change, the level, the model
  transport, its run's id, and ways to reserve model calls, to say how a change is reversed and how far it is;
- tasks that are due run in order, the one that has waited longest first, one at a time, and what is overdue
  when the app is opened is caught up on;
- a task that raises is logged with what it said, cut short; the next one still runs; it is tried again after
  an hour, then two, then four, and never later than its cadence;
- a task over the day's limit of automatic model calls is skipped, logged as skipped, and not tried again that
  day; inside a run a call over the limit is refused;
- an undo reverses a run's changes last first, once, and refuses a second time;
- a run the app was closed in the middle of is marked as interrupted at the next start;
- the log is bounded, and a log from elsewhere with anything in it can still be shown;
- damaged settings switch nothing on, and what other modules keep beside these settings survives a save;
- a run begins inside the gate, waits there while data is being replaced, and looks again afterwards.

No model and no network are involved, and no test waits for time to pass: the clock is handed in.
"""
import contextlib
import io
import json
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from studio import autopilot
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
        self.assertEqual(self.pilot.status()["tasks"][0]["next_run_at"], 0.0)
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

    def test_a_skipped_task_also_waits_for_its_cadence(self):
        self.calling("ideas.refresh", every=3 * DAY)
        self.calling("trends.refresh", every=600)
        self.switch_on("ideas.refresh", "trends.refresh", daily_model_calls=1)
        self.clock.now = BASE + 14 * HOUR                                    # eleven at night
        self.assertEqual(self.look(), [("ideas.refresh", "done"), ("trends.refresh", "skipped")])
        self.later(HOUR)                                                     # midnight
        self.assertEqual(self.look(), [("trends.refresh", "done")])          # its ten minutes passed long ago
        self.later(3 * DAY - 2 * HOUR)
        self.assertEqual([name for name, _ended in self.look()], ["trends.refresh"])    # ideas.refresh: an hour to go

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
        self.assertEqual(self.ran, ["plans.suggest"])

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


if __name__ == "__main__":
    unittest.main()
