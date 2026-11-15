"""Tests for the 0.1.1 fixes.

- Writes sent by another website are refused.
- A voice profile of the wrong shape does not break the checks, and one that cannot be written does not
  replace the profile in use.
- Two inputs that used to hold the server for a long time are handled in linear time.

The timing tests use a generous limit. The fixed code needs a few milliseconds; the code before the fix needed
several seconds for the same input, so a slow machine cannot make them fail by accident.
"""
import copy
import http.client
import json
import shutil
import tempfile
import threading
import time
import unittest
from pathlib import Path

from studio import guardrails, llm, server, textstats
from studio.store import ROOT, Store

EXAMPLE = json.loads((ROOT / "schemas" / "voice_dna.example.json").read_text(encoding="utf-8"))
HER_SCRIPT = "\n".join(["今天去超市买菜 / 结果人特别多嘛 / 排队排了半小时啦"] * 12)
LINES = [
    {"line_no": 1, "spoken_script_zh": "宝子们今天去超市买菜 / 结果人特别多嘛"},
    {"line_no": 2, "spoken_script_zh": "这个 brunch 真的绝绝子哒 [轻笑]"},
    {"line_no": 3, "spoken_script_zh": "因此我们决定下次再来", "audio_mode": "LIVE_CLIP", "time_range": "00:10-00:15"},
]
# What a model can put where something else belongs.
WRONG = ["text", 7, None, True, [None, 3, {"unexpected": 1}, "text", ["nested"]], {"unexpected": "text"}]
SLOW = 3.0  # seconds


def positions(node, prefix=()):
    """Every position in a profile: each key of each object, and the first entry of each list."""
    if isinstance(node, dict):
        for key, value in node.items():
            yield prefix + (key,)
            for deeper in positions(value, prefix + (key,)):
                yield deeper
    elif isinstance(node, list) and node:
        yield prefix + (0,)
        for deeper in positions(node[0], prefix + (0,)):
            yield deeper


def with_value(profile, path, value):
    """A copy of ``profile`` with ``value`` at ``path``."""
    out = copy.deepcopy(profile)
    node = out
    for step in path[:-1]:
        node = node[step]
    node[path[-1]] = value
    return out


class StudioCase(unittest.TestCase):
    """A running studio on a temporary data folder. The model transport is a stand-in that records calls."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.store = Store(self.tmp)
        self.provider_calls = []
        self.httpd = server.make_server(0, self.store, self._transport)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)
        shutil.rmtree(str(self.tmp), ignore_errors=True)

    def _transport(self, method, url, headers, body):
        self.provider_calls.append((method, url))
        return {}

    def send(self, method, path, payload=None, content_type="application/json", origin=None, host=None):
        """Send one request; returns ``(status, parsed body, response headers)``."""
        headers = {"Host": host or "127.0.0.1:%d" % self.port}
        data = None
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            if content_type is not None:
                headers["Content-Type"] = content_type
        if origin is not None:
            headers["Origin"] = origin
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=60)
        try:
            conn.request(method, path, body=data, headers=headers)
            res = conn.getresponse()
            raw = res.read()
        finally:
            conn.close()
        try:
            body = json.loads(raw.decode("utf-8"))
        except ValueError:
            body = raw.decode("utf-8", "replace")
        return res.status, body, res.headers

    def titles(self):
        _, body, _ = self.send("GET", "/api/samples")
        return sorted(item["title"] for item in body["samples"])


class CrossSiteWriteTest(StudioCase):
    ITEM = {"title": "planted", "text": "this was not added by the creator"}

    def foreign_origins(self):
        return ["https://attacker.example", "http://attacker.example", "null", "http://127.0.0.1:1",
                "https://127.0.0.1:%d" % self.port, "http://127.0.0.1:%d.attacker.example" % self.port,
                "http://localhost.attacker.example:%d" % self.port]

    def test_a_write_that_names_another_site_is_refused(self):
        for origin in self.foreign_origins():
            status, body, _ = self.send("POST", "/api/samples", self.ITEM, origin=origin)
            self.assertEqual((status, body.get("error")), (403, "bad_origin"), origin)
        self.assertEqual(self.titles(), [])

    def test_changing_and_deleting_from_another_site_are_refused(self):
        status, made, _ = self.send("POST", "/api/samples", {"title": "mine", "text": "今天去超市买菜"})
        self.assertEqual(status, 201)
        for method, payload in (("PUT", {"title": "changed"}), ("DELETE", None)):
            status, body, _ = self.send(method, "/api/samples/" + made["id"], payload, origin="https://attacker.example")
            self.assertEqual((status, body.get("error")), (403, "bad_origin"), method)
        self.assertEqual(self.titles(), ["mine"])

    def test_the_studios_own_pages_and_local_programs_can_still_write(self):
        own = "http://127.0.0.1:%d" % self.port
        self.assertEqual(self.send("POST", "/api/samples", {"title": "a", "text": "甲"}, origin=own)[0], 201)
        local = "localhost:%d" % self.port
        self.assertEqual(self.send("POST", "/api/samples", {"title": "b", "text": "乙"}, origin="http://" + local, host=local)[0], 201)
        self.assertEqual(self.send("POST", "/api/samples", {"title": "c", "text": "丙"})[0], 201)   # no Origin: not a web page
        self.assertEqual(self.titles(), ["a", "b", "c"])

    def test_a_request_body_must_be_exactly_json(self):
        # The first is what a page on another site can send without the browser asking this server first.
        refused = ["text/plain;application/json", "text/plain; charset=application/json", "application/x-www-form-urlencoded",
                   "multipart/form-data; boundary=application/json", "application/jsonx", "text/application/json", None]
        for content_type in refused:
            status, body, _ = self.send("POST", "/api/samples", self.ITEM, content_type=content_type)
            self.assertEqual((status, body.get("error")), (415, "json_required"), content_type)
        self.assertEqual(self.titles(), [])
        for content_type in ("application/json", "application/json; charset=utf-8", "Application/JSON", "application/json ; charset=utf-8"):
            self.assertEqual(self.send("POST", "/api/samples", self.ITEM, content_type=content_type)[0], 201, content_type)

    def test_settings_cannot_be_changed_from_another_site(self):
        self.assertEqual(self.send("POST", "/api/settings", {"provider": "manual"})[0], 200)
        before = self.store.public_settings()
        status, body, _ = self.send("POST", "/api/settings", {"provider": "openai", "model": "gpt-4o", "key": "sk-not-a-real-key"},
                                    content_type="text/plain;application/json", origin="https://attacker.example")
        self.assertEqual((status, body.get("error")), (403, "bad_origin"))
        self.assertEqual(self.store.public_settings(), before)
        self.assertEqual(self.provider_calls, [])

    def test_the_voice_profile_cannot_be_replaced_from_another_site(self):
        self.send("POST", "/api/samples", {"title": "超市那天", "text": HER_SCRIPT})
        self.send("POST", "/api/settings", {"provider": "manual"})
        status, body, _ = self.send("POST", "/api/manual/reply", {"task": "voice", "reply": json.dumps(EXAMPLE, ensure_ascii=False)},
                                    content_type="text/plain;application/json", origin="https://attacker.example")
        self.assertEqual((status, body.get("error")), (403, "bad_origin"))
        self.assertTrue(self.store.load_dna()[1])        # still the bundled example: nothing was saved

    def test_reads_still_work_and_grant_nothing_to_other_sites(self):
        status, _, headers = self.send("GET", "/api/samples", origin="https://attacker.example")
        self.assertEqual(status, 200)
        self.assertIsNone(headers.get("Access-Control-Allow-Origin"))


class WrongShapedProfileTest(StudioCase):
    def test_the_checks_run_on_any_shape_of_profile(self):
        script = {"script_lines": LINES}
        tried = 0
        for path in positions(EXAMPLE):
            for value in WRONG:
                dna = with_value(EXAMPLE, path, value)
                try:
                    guardrails.lint_script(script, dna, target_duration_sec=60)
                    guardrails.script_stats(script, dna, 60)
                except Exception as exc:        # the point of the test: name the shape that broke it
                    self.fail("%s = %r broke the checks: %s: %s" % (".".join(map(str, path)), value, type(exc).__name__, exc))
                tried += 1
        self.assertGreater(tried, 200)
        guardrails.lint_script(script, {}, target_duration_sec=60)

    def test_the_studio_keeps_working_with_a_wrong_shaped_profile_on_disk(self):
        """What 0.1.0 could leave behind: a profile saved from a wrong-shaped reply."""
        self.send("POST", "/api/samples", {"title": "超市那天", "text": HER_SCRIPT})
        self.send("POST", "/api/settings", {"provider": "manual"})
        broken = [
            with_value(EXAMPLE, ("lexical_fingerprint", "signature_openers"), ["just a string"]),
            with_value(EXAMPLE, ("anti_voice_blacklist", "banned_phrases_zh"), "绝绝子"),
            with_value(EXAMPLE, ("lexical_fingerprint", "modal_particles", "rare_or_zero"), [None, {"particle": "哒"}]),
            with_value(EXAMPLE, ("persona_summary",), "松弛"),
            with_value(EXAMPLE, ("lexical_fingerprint",), ["text"]),
            with_value(EXAMPLE, ("cadence_and_syntax",), None),
        ]
        requests = [("POST", "/api/lint", {"lines": LINES, "target_sec": 60}), ("GET", "/api/dna", None),
                    ("GET", "/api/samples", None), ("GET", "/api/scripts", None), ("GET", "/api/state", None),
                    ("POST", "/api/manual/prompt", {"task": "draft", "notes": "去超市", "target_sec": 60}),
                    ("POST", "/api/manual/prompt", {"task": "ideas"}),
                    ("POST", "/api/manual/prompt", {"task": "trends", "focus": "护肤"})]
        for number, dna in enumerate(broken):
            self.store.save_dna(dna)
            for method, path, payload in requests:
                status, body, _ = self.send(method, path, payload)
                self.assertEqual(status, 200, "profile %d, %s %s %r: %r" % (number, method, path, payload, body))

    def test_a_wrong_shaped_voice_reply_does_not_break_the_checks(self):
        self.send("POST", "/api/samples", {"title": "超市那天", "text": HER_SCRIPT})
        self.send("POST", "/api/settings", {"provider": "manual"})
        reply = with_value(EXAMPLE, ("lexical_fingerprint", "signature_openers"), ["just a string"])
        self.send("POST", "/api/manual/prompt", {"task": "voice"})
        status, _, _ = self.send("POST", "/api/manual/reply", {"task": "voice", "reply": json.dumps(reply, ensure_ascii=False)})
        self.assertEqual(status, 200)
        status, body, _ = self.send("POST", "/api/lint", {"lines": LINES, "target_sec": 60})
        self.assertEqual(status, 200)
        self.assertTrue(body["findings"])

    def test_a_profile_that_cannot_be_written_leaves_the_one_in_use(self):
        self.store.save_dna(dict(EXAMPLE, profile_version="mine-1"))
        with self.assertRaises(UnicodeEncodeError):
            self.store.save_dna({"profile_version": "cannot be written", "note": "\ud800"})   # a lone surrogate cannot be encoded
        dna, is_example = self.store.load_dna()
        self.assertEqual((dna["profile_version"], is_example), ("mine-1", False))
        self.store.save_dna(dict(EXAMPLE, profile_version="mine-2"))                          # and saving still works afterwards
        self.assertEqual(self.store.load_dna()[0]["profile_version"], "mine-2")
        kept = sorted(json.loads(p.read_text(encoding="utf-8"))["profile_version"] for p in self.store.dna_history_dir.iterdir())
        self.assertIn("mine-1", kept)


class NoFreezeTest(StudioCase):
    def test_cue_brackets_are_removed(self):
        """Same results as 0.1.0 except the last case: with brackets inside brackets, the innermost pair is the cue."""
        cases = {
            "今天去超市 [轻笑] 买菜": "今天去超市  买菜",
            "【停顿0.5s】好了": "好了",
            "\uff3b原声：好\uff3d走吧": "走吧",
            "没有提示": "没有提示",
            "a [b] c [d] e": "a  c  e",
            "只有左括号 [ 没有右括号": "只有左括号 [ 没有右括号",
            "[a [b] c] d": "[a  c] d",       # a cue is the innermost pair
        }
        for text, expected in cases.items():
            self.assertEqual(textstats.strip_cues(text), expected, text)

    def test_many_opening_brackets_do_not_hold_the_server(self):
        for bracket in ("[", "\uff3b", "\u3010"):
            started = time.time()
            self.assertEqual(len(textstats.strip_cues(bracket * 150000)), 150000)
            self.assertLess(time.time() - started, SLOW, repr(bracket))
        status, _, _ = self.send("POST", "/api/samples", {"title": "brackets", "text": "[" * 150000})
        self.assertEqual(status, 201)
        started = time.time()
        self.assertEqual(self.send("GET", "/api/samples")[0], 200)
        self.assertLess(time.time() - started, SLOW)

    def test_a_reply_that_is_mostly_white_space_is_refused_quickly(self):
        for text in (" " * 100000 + "x", "x" + " " * 100000, "```" + " " * 100000 + "x", "x" + " " * 100000 + "```", "\n" * 100000):
            started = time.time()
            with self.assertRaises(llm.LLMError):
                llm.parse_json_text(text)
            self.assertLess(time.time() - started, SLOW, repr(text[:4]))

    def test_fenced_and_plain_replies_still_parse(self):
        value = {"a": 1, "text": "```not a fence```", "list": ["甲", "乙"]}
        raw = json.dumps(value, ensure_ascii=False)
        replies = [raw, "```json\n%s\n```" % raw, "```\n%s\n```" % raw, "  \n```json %s ```  \n" % raw, "```JSON\n%s```" % raw,
                   "Here you go:\n```json\n%s\n```\nAnything else?" % raw, "\n\n%s\n\n" % raw]
        for reply in replies:
            self.assertEqual(llm.parse_json_text(reply), value, reply[:24])
        for reply in ("", "   ", "```json\n```", "not json at all", "[1, 2, 3]"):
            with self.assertRaises(llm.LLMError):
                llm.parse_json_text(reply)


if __name__ == "__main__":
    unittest.main()
