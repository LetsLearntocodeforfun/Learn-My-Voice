"""Tests for the teleprompter: the single file made for a phone, its route, and what the page script has to stay.

The pacing itself runs in a browser and is not exercised here: nothing in the standard library runs
JavaScript. These tests cover what Python builds (which breaths, at what rate and whose rate it is, the page
and how the script is written into it) and what can be read off the source of the page script.
"""
import http.client
import json
import re
import shutil
import tempfile
import threading
import unittest
import urllib.parse
from html.parser import HTMLParser
from pathlib import Path
from unittest import mock

from studio import creator, exports, server, textstats
from studio.store import Store

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "studio" / "web"
EXAMPLE = json.loads((ROOT / "schemas" / "voice_dna.example.json").read_text(encoding="utf-8"))
CREATOR = {"name": "小满", "pronoun": "she", "channel": "小满的日常", "niches": "护肤、日常 Vlog",
           "about": "在上海，拍通勤和周末。", "partner": None, "never": []}
RATE_300 = {"cadence_and_syntax": {"target_speaking_rate_cpm": 300}}
CLOSES_THE_SCRIPT = "</script><script>alert(1)</script>"
MARKUP = "<img src=x onerror=alert(1)> \u2028&amp;\u2029 <!-- 好啦"          # no slash in it, so it stays one breath
BAD_TITLE = "</title><script>alert(2)</script> https://evil.example/x.js"
# Never in the file, whatever the script says: anything that reads as an address, loads something, sends something or listens.
NOT_IN_THE_FILE = ("//", "http://", "https://", "url(", "@import", "fetch(", "XMLHttpRequest", "WebSocket", "EventSource",
                   "sendBeacon", "import(", "getUserMedia", "mediaDevices", "MediaRecorder", "AudioContext",
                   "SpeechRecognition")


def script_fixture():
    return {"title": "周日补货", "lines": [
        {"line_no": 1, "audio_mode": "VOICEOVER", "spoken_script_zh": "周日下午趁冰箱空了 / 拉着某人去趟超市嘛 [轻笑]"},
        {"line_no": 2, "audio_mode": "LIVE_CLIP", "spoken_script_zh": "Wait, four boxes?"},
        {"line_no": 3, "audio_mode": "voiceover", "spoken_script_zh": "［停顿］买了 Trader Joe's 的小笼包\n一共 4 盒 【原声】 / ～～"},
        {"line_no": 4, "audio_mode": "VOICEOVER", "spoken_script_zh": ""},
    ]}


BREATHS = [("周日下午趁冰箱空了", 9), ("拉着某人去趟超市嘛", 9), ("买了 Trader Joe's 的小笼包", 8), ("一共 4 盒", 4), ("～～", 1)]


def own_profile(rate, source):
    """The example profile as the creator's own, with a rate and where that rate came from."""
    dna = json.loads(json.dumps(EXAMPLE))
    dna["cadence_and_syntax"]["target_speaking_rate_cpm"] = rate
    dna["source_corpus_summary"] = {"speaking_rate_source": source, "speaking_rate_confirmed": source == "typed"}
    return dna


class Seen(HTMLParser):
    """What a browser finds in a page: every element with its attributes, the text of each script and style
    element, and the text outside them. Like a browser, it reads a script up to the first ``</script``."""

    def __init__(self, page):
        super().__init__(convert_charrefs=True)
        self.elements, self.scripts, self.styles, self.text = [], [], [], []
        self._open = None
        self.feed(page)
        self.close()

    def handle_starttag(self, tag, attrs):
        self.elements.append((tag, dict(attrs)))
        if tag in ("script", "style"):
            self._open = (tag, dict(attrs), [])

    def handle_endtag(self, tag):
        if self._open and tag == self._open[0]:
            _, attrs, parts = self._open
            (self.scripts if tag == "script" else self.styles).append((attrs, "".join(parts)))
            self._open = None

    def handle_data(self, data):
        (self._open[2] if self._open else self.text).append(data)

    def data(self):
        """The script written into the page for the prompter to read."""
        (attrs, text), = [found for found in self.scripts if found[0].get("type") == "application/json"]
        assert attrs == {"type": "application/json", "id": "tp-data"}, attrs
        return json.loads(text)

    def code(self):
        (_, text), = [found for found in self.scripts if found[0] == {}]
        return text


def page_of(script, dna=None, lang="zh", **more):
    name, body, content_type = exports.export_file(script, "prompter", dna, lang, **more)
    return name, body.decode("utf-8"), content_type


class BreathsTest(unittest.TestCase):
    def test_live_clips_are_skipped_cues_removed_and_lines_split_into_breaths(self):
        self.assertEqual(exports.prompter_breaths(script_fixture()), BREATHS)

    def test_the_breaths_are_the_lines_of_the_voiceover_text(self):
        script = script_fixture()
        self.assertEqual([text for text, _ in exports.prompter_breaths(script)], exports.voiceover_text(script).splitlines())

    def test_units_are_counted_as_everywhere_else_and_never_zero(self):
        for text, units in exports.prompter_breaths(script_fixture()):
            self.assertEqual(units, max(1, textstats.spoken_units(text)), text)
        lines = [{"spoken_script_zh": "！！！ / 😂 / ありがとう / 好"}]
        self.assertEqual(exports.prompter_breaths({"lines": lines}), [("！！！", 1), ("😂", 1), ("ありがとう", 1), ("好", 1)])

    def test_a_line_of_the_wrong_shape_is_passed_over(self):
        lines = [None, "text", {"spoken_script_zh": None}, {"spoken_script_zh": 5}, {}, {"audio_mode": None, "spoken_script_zh": "好啦"}]
        self.assertEqual(exports.prompter_breaths({"lines": lines}), [("好啦", 2)])
        self.assertEqual(exports.prompter_breaths({"lines": []}), [])


class PaceTest(unittest.TestCase):
    def test_without_a_usable_rate_the_default_is_used_and_called_that(self):
        for rate in (None, 0, -5, True, "fast", float("nan"), float("inf")):
            dna = own_profile(rate, "measured")
            self.assertEqual(exports.prompter_pace(dna), (exports.DEFAULT_RATE, exports.SOURCE_DEFAULT), rate)
        for dna in (None, {}, [], "x", {"cadence_and_syntax": []}, {"cadence_and_syntax": {}}):
            self.assertEqual(exports.prompter_pace(dna), (240.0, "default"), dna)

    def test_the_example_profiles_rate_is_never_called_hers(self):
        self.assertEqual(exports.prompter_pace(EXAMPLE, True), (238, exports.SOURCE_EXAMPLE))
        self.assertEqual(exports.prompter_pace(own_profile(300, "measured"), True), (300, "example"))

    def test_a_rate_is_hers_once_it_was_measured_or_typed_in(self):
        self.assertEqual(exports.prompter_pace(own_profile(300, "measured")), (300, exports.SOURCE_MINE))
        self.assertEqual(exports.prompter_pace(own_profile(271.5, "typed")), (271.5, "mine"))
        self.assertEqual(exports.prompter_pace(own_profile(240, "default")), (240, "default"))
        self.assertEqual(exports.prompter_pace(RATE_300), (300, "default"))                       # nothing says where it is from
        for summary in ([], "measured", {"speaking_rate_confirmed": "yes"}, {"speaking_rate_source": "guessed"}):
            self.assertEqual(exports.prompter_pace(dict(RATE_300, source_corpus_summary=summary)), (300, "default"), summary)

    def test_the_page_script_has_a_sentence_for_every_answer_in_both_languages(self):
        text = (WEB / "prompter.js").read_text(encoding="utf-8")
        for source in (exports.SOURCE_MINE, exports.SOURCE_DEFAULT, exports.SOURCE_EXAMPLE):
            said = re.findall(r"\btp_src_%s: '((?:[^'\\]|\\.)*)'" % source, text)
            self.assertEqual(len(said), 2, source)
            for sentence in said:
                self.assertIn("{n}", sentence)
                self.assertIn("{x}", sentence)
        self.assertIn("const TP_DEFAULT_CPM = %d;" % exports.DEFAULT_RATE, text)


class PageTest(unittest.TestCase):
    def test_it_is_one_html_file_with_everything_inside(self):
        name, page, content_type = page_of(script_fixture(), RATE_300)
        self.assertEqual((name, content_type), ("周日补货-提词器.html", "text/html; charset=utf-8"))
        self.assertTrue(page.startswith("<!doctype html>\n"))
        seen = Seen(page)
        tags = [tag for tag, _ in seen.elements]
        self.assertEqual(sorted(set(tags)), ["body", "div", "head", "html", "meta", "noscript", "p", "script", "style", "title"])
        self.assertEqual((tags.count("script"), tags.count("style")), (2, 1))
        # No attribute that could name an address, and no handler written into the markup.
        self.assertEqual(sorted({key for _, attrs in seen.elements for key in attrs}), ["charset", "class", "content", "id", "lang", "name", "type"])
        self.assertEqual(seen.code(), "\n'use strict';\n" + exports._shared("prompter.js", "tp-core") + exports._PHONE_START)
        self.assertEqual(seen.styles, [({}, "\n" + exports._shared("app.css", "tp-look") + exports._PHONE_CSS)])

    def test_it_names_no_address_and_nothing_that_listens(self):
        typed_an_address = {"title": BAD_TITLE, "lines": [{"spoken_script_zh": "看 https://example.com/a//b 这里"}]}
        for script in (script_fixture(), typed_an_address):
            for lang in ("zh", "en"):
                _, page, _ = page_of(script, RATE_300, lang, who=CREATOR)
                for word in NOT_IN_THE_FILE:
                    self.assertNotIn(word, page, word)
        # The file's own code and styles do not so much as mention one. (Above, "https:" is there as words she typed.)
        _, page, _ = page_of(script_fixture(), RATE_300, "en", who=CREATOR)
        self.assertNotIn("http", page.lower())
        self.assertIn("看 https:", Seen(page_of(typed_an_address, RATE_300)[1]).text)             # her words are shown as typed

    def test_it_holds_the_breaths_and_the_rate(self):
        _, page, _ = page_of(script_fixture(), own_profile(300, "measured"))
        self.assertEqual(Seen(page).data(), {"lang": "zh", "cpm": 300, "source": "mine", "names": "the creator's",
                                              "breaths": [[text, units] for text, units in BREATHS]})
        _, page, _ = page_of(script_fixture(), None)
        data = Seen(page).data()
        self.assertEqual((data["cpm"], data["source"]), (240.0, "default"))
        _, page, _ = page_of(script_fixture(), EXAMPLE, dna_is_example=True)
        data = Seen(page).data()
        self.assertEqual((data["cpm"], data["source"]), (238, "example"))

    def test_its_text_follows_the_language(self):
        name, page, _ = page_of(script_fixture(), RATE_300, "zh", who=CREATOR)
        seen = Seen(page)
        self.assertEqual((name, seen.elements[0], seen.data()["lang"]), ("周日补货-提词器.html", ("html", {"lang": "zh-CN"}), "zh"))
        self.assertIn("周日补货 · 提词器", seen.text)
        self.assertTrue(any("不能运行提词器" in text for text in seen.text))
        name, page, _ = page_of(script_fixture(), RATE_300, "en", who=CREATOR)
        seen = Seen(page)
        self.assertEqual((name, seen.elements[0], seen.data()["lang"]), ("周日补货-teleprompter.html", ("html", {"lang": "en"}), "en"))
        self.assertIn("周日补货 · Teleprompter", seen.text)
        self.assertTrue(any("The prompter cannot run in this view" in text for text in seen.text))
        self.assertEqual(seen.data()["names"], "小满's")                                          # English text names her
        for anything_else in ("fr", "", None, "EN"):
            self.assertEqual(Seen(page_of(script_fixture(), RATE_300, anything_else)[1]).data()["lang"], "zh")

    def test_text_that_looks_like_markup_stays_text(self):
        script = {"title": BAD_TITLE, "lines": [{"spoken_script_zh": CLOSES_THE_SCRIPT}, {"spoken_script_zh": MARKUP}]}
        breaths = exports.prompter_breaths(script)
        self.assertIn((MARKUP, 9), breaths)
        _, page, _ = page_of(script, RATE_300, "en")
        seen = Seen(page)
        # The same elements as for a harmless script: nothing she typed became an element or ended one early.
        harmless = Seen(page_of({"title": "t", "lines": [{"spoken_script_zh": "a"}] * len(breaths)}, RATE_300, "en")[1])
        self.assertEqual(seen.elements, harmless.elements)
        self.assertEqual((seen.code(), seen.styles), (harmless.code(), harmless.styles))        # and none of it is in the code
        self.assertEqual(seen.data()["breaths"], [[text, units] for text, units in breaths])    # a JSON reader gets it back whole
        (_, written), = [found for found in seen.scripts if found[0].get("type") == "application/json"]
        for raw in ("<", ">", "&", "\u2028", "\u2029", "//"):
            self.assertNotIn(raw, written, repr(raw))
        self.assertEqual(page.count("<script"), 2)
        self.assertEqual(page.count("</script"), 2)
        self.assertIn(BAD_TITLE + " · Teleprompter", seen.text)                                  # shown as typed
        for text, _ in breaths:
            self.assertIn(text, seen.text)                                                       # also where code is not run

    def test_json_written_into_the_page_reads_back_the_same(self):
        for value in ("</script>", "<!-- <script>", "a\\/b", "\\u003c", "\u2028\u2029", "&amp;", "http://x//y", {"k/": ["<", 1.5, None]}):
            written = exports._page_json(value)
            self.assertEqual(json.loads(written), value)
            self.assertIsNone(re.search("[<>&\u2028\u2029]|//", written), written)
        with self.assertRaises(ValueError):
            exports._page_json(float("nan"))

    def test_a_script_with_nothing_to_read(self):
        script = {"title": "空", "lines": [{"audio_mode": "LIVE_CLIP", "spoken_script_zh": "Wait"}, {"spoken_script_zh": "[轻笑]"}]}
        for lang, words in (("zh", "脚本还是空的。"), ("en", "The script is empty.")):
            seen = Seen(page_of(script, RATE_300, lang)[1])
            self.assertEqual(seen.data()["breaths"], [])
            self.assertIn(words, seen.text)

    def test_the_other_exports_are_as_before(self):
        self.assertEqual(exports.KINDS, ("jianying", "srt", "flower_srt", "voiceover", "shots", "flower", "fcpxml", "prompter"))
        script = script_fixture()
        for kind in exports.KINDS[:-1]:
            self.assertEqual(exports.export_file(script, kind, RATE_300, "en", dna_is_example=True, who=CREATOR),
                             exports.export_file(script, kind, RATE_300, "en"))
        with self.assertRaises(KeyError):
            exports.export_file(script, "prompter.html", RATE_300)


class SharedPartsTest(unittest.TestCase):
    """The phone file runs code and styles copied out of studio/web/. They have to be able to stand alone."""

    def test_the_code_uses_nothing_from_the_rest_of_the_app(self):
        core = exports._shared("prompter.js", "tp-core")
        self.assertTrue(core.startswith("/* tp-core:start"))
        self.assertTrue(core.endswith("/* tp-core:end */\n"))
        self.assertIn("function tpMount(root, opts) {", core)
        self.assertIn("const TP_TEXT = {", core)
        for name in ("I18N", "appState", "$prompter", "$view", "$toast", "spokenUnits", "creatorWords", "showError", "openPrompter"):
            self.assertNotIn(name, core, name)
        for call in (r"(?<![\w.])t\(", r"\besc\(", r"\bapi\(", r"\btf\(", r"\bcur\b", r"\blang\b", r"\binnerHTML\b"):
            self.assertIsNone(re.search(call, core), call)
        for word in NOT_IN_THE_FILE + ("</script", "<!--"):
            self.assertNotIn(word, core, word)

    def test_the_styles_use_nothing_from_the_rest_of_the_sheet(self):
        look = exports._shared("app.css", "tp-look")
        self.assertTrue(look.startswith("/* tp-look:start"))
        self.assertIn(".tp-mirror .tp-flip { transform: scaleX(-1); }", look)
        for word in NOT_IN_THE_FILE + ("var(--", "</style", "#prompter"):
            self.assertNotIn(word, look, word)
        for extra in (exports._PHONE_CSS, exports._PHONE_START):
            for word in NOT_IN_THE_FILE + ("var(--", "</style", "</script", "<!--"):
                self.assertNotIn(word, extra, word)

    def test_a_missing_mark_is_an_error_and_not_an_empty_page(self):
        folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, str(folder), True)
        (folder / "prompter.js").write_text("function tpMount() {}\n/* tp-core:end */\n", encoding="utf-8")
        (folder / "app.css").write_text("/* tp-look:start */ .tp {}\n", encoding="utf-8")
        with mock.patch.object(exports, "WEB_DIR", folder):
            for name, mark in (("prompter.js", "tp-core"), ("app.css", "tp-look")):
                with self.assertRaises(ValueError):
                    exports._shared(name, mark)

    def test_both_languages_have_the_same_words(self):
        core = exports._shared("prompter.js", "tp-core")
        table = core[core.index("const TP_TEXT = {"):core.index("\n};\n")]
        chinese, english = table.split("\n  en: {")
        keys = [re.findall(r"\b(tp_\w+): '", part) for part in (chinese, english)]
        self.assertEqual(keys[0], keys[1])
        self.assertGreater(len(keys[0]), 20)
        self.assertEqual(len(set(keys[0])), len(keys[0]))
        self.assertIsNotNone(re.search(r"[\u4e00-\u9fff]", chinese))
        self.assertIsNone(re.search(r"[\u4e00-\u9fff]", english))


class PageScriptTest(unittest.TestCase):
    def setUp(self):
        self.text = (WEB / "prompter.js").read_text(encoding="utf-8")

    def test_it_is_loaded_after_discover_and_before_the_script_that_starts_the_app(self):
        html = (WEB / "index.html").read_text(encoding="utf-8")
        order = [html.index('src="/%s.js"' % name) for name in ("app", "setup", "discover", "prompter", "pages")]
        self.assertEqual(order, sorted(order))

    def test_the_prompter_lives_in_its_own_file_only(self):
        app = (WEB / "app.js").read_text(encoding="utf-8")
        self.assertNotIn("function openPrompter", app)
        self.assertNotIn("const prompter", app)
        self.assertIn("document.getElementById('prompter-btn').onclick = openPrompter;", app)   # resolved when the editor is drawn
        self.assertIn('data-export="prompter"', app)
        self.assertEqual(self.text.count("\nfunction openPrompter() {"), 1)
        for key in ("tp_exp", "tp_exp_d"):                                   # the export menu's words, in both languages
            self.assertEqual(len(re.findall(r"\b%s: '" % key, self.text)), 2, key)

    def test_it_never_listens_and_asks_the_server_for_one_thing_only(self):
        for word in ("getUserMedia", "mediaDevices", "MediaRecorder", "AudioContext", "SpeechRecognition", "fetch(", "XMLHttpRequest"):
            self.assertNotIn(word, self.text, word)
        self.assertEqual(re.findall(r"\bapi\(([^)]*)\)", self.text), ["'GET', '/api/dna'"])   # whose rate it is; sends nothing

    def test_it_counts_and_splits_as_python_does(self):
        cue = re.sub(r"\\u([0-9a-f]{4})", lambda found: chr(int(found.group(1), 16)), textstats._BRACKET_CUE.pattern)
        self.assertIn("const TP_CUE = /%s/g;" % cue, self.text)
        self.assertIn(".replace(TP_CUE, '').replace(/`/g, '').split(/[\\/\\n]/)", self.text)
        self.assertIn("units: Math.max(1, spokenUnits(text))", self.text)
        self.assertIn("const TP_PACE = { least: 6, most: 16, usual: 10 };", self.text)          # 0.6 to 1.6 in tenths
        self.assertIn("b.units * 60 / opts.cpm", self.text)                                      # seconds at pace 1.0


class RouteTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, str(self.tmp), True)
        self.store = Store(self.tmp)
        self.httpd = server.make_server(0, self.store)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True).start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)
        self.script = self.store.create_script(title="周日补货", lines=script_fixture()["lines"])

    def get(self, path, method="GET", body=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=20)
        conn.request(method, path, body=body, headers={"Content-Type": "application/json"} if body else {})
        res = conn.getresponse()
        raw = res.read()
        conn.close()
        return res.status, raw, res

    def test_the_route_gives_the_file_as_a_download(self):
        status, raw, res = self.get("/api/scripts/%s/export/prompter?lang=en" % self.script["id"])
        self.assertEqual((status, res.getheader("Content-Type")), (200, "text/html; charset=utf-8"))
        self.assertEqual(res.getheader("Content-Disposition"),
                         "attachment; filename*=UTF-8''" + urllib.parse.quote("周日补货-teleprompter.html"))
        # Served from the app's own address it is a download, and the policy would not let its code run there.
        self.assertEqual(res.getheader("Content-Security-Policy"), server.CONTENT_SECURITY_POLICY)
        self.assertEqual(res.getheader("X-Content-Type-Options"), "nosniff")
        stored = self.store.get_script(self.script["id"])
        self.assertEqual(raw, exports.export_file(stored, "prompter", EXAMPLE, "en", dna_is_example=True)[1])
        data = Seen(raw.decode("utf-8")).data()
        self.assertEqual((data["lang"], data["cpm"], data["source"], data["names"]), ("en", 238, "example", "the creator's"))
        self.assertEqual(data["breaths"], [[text, units] for text, units in BREATHS])

    def test_the_route_knows_whose_rate_it_is(self):
        self.store.save_creator(creator.normalize(CREATOR))
        self.store.save_dna(own_profile(300, "measured"))
        status, raw, res = self.get("/api/scripts/%s/export/prompter" % self.script["id"])
        self.assertEqual(status, 200)
        self.assertIn(urllib.parse.quote("周日补货-提词器.html"), res.getheader("Content-Disposition"))
        data = Seen(raw.decode("utf-8")).data()
        self.assertEqual((data["lang"], data["cpm"], data["source"], data["names"]), ("zh", 300, "mine", "小满's"))
        self.store.save_dna(own_profile(240, "default"))
        _, raw, _ = self.get("/api/scripts/%s/export/prompter" % self.script["id"])
        self.assertEqual(Seen(raw.decode("utf-8")).data()["source"], "default")

    def test_an_unknown_script_an_unknown_kind_and_another_method(self):
        for path, error in (("/api/scripts/0123456789ab/export/prompter", "not_found"),
                            ("/api/scripts/not-an-id/export/prompter", "not_found"),
                            ("/api/scripts/%s/export/prompter.html" % self.script["id"], "not_found"),
                            ("/api/scripts/%s/export/teleprompter" % self.script["id"], "unknown_export")):
            status, raw, _ = self.get(path)
            self.assertEqual((status, json.loads(raw.decode("utf-8"))["error"]), (404, error), path)
        status, raw, _ = self.get("/api/scripts/%s/export/prompter" % self.script["id"], "POST", b"{}")
        self.assertEqual((status, json.loads(raw.decode("utf-8"))["error"]), (404, "not_found"))
        self.assertEqual(self.store.get_script(self.script["id"])["lines"], self.script["lines"])     # asking changes nothing

    def test_the_script_is_served_as_a_script(self):
        status, raw, res = self.get("/prompter.js")
        self.assertEqual((status, res.getheader("Content-Type")), (200, "text/javascript; charset=utf-8"))
        self.assertIn(b"function tpMount(root, opts) {", raw)


if __name__ == "__main__":
    unittest.main()
