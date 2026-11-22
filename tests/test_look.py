"""The look of the app, and what the core keeps for the page from one run to the next.

What these tests show:

- the page may ask the core to keep a few named things (the language, light or dark, the accent colour, how the
  teleprompter was set), and nothing else and no other values;
- what is kept is still there for a new run on the same data folder, beside the settings and without touching them;
- the app's page arrives with it written onto its ``<html>`` element, so the language and the look are right
  before any script has run, also at an address the page was never opened at (inside the desktop app the port
  differs at every start);
- the request that keeps something refuses what it should, and another site cannot send it;
- the stylesheet names every colour it uses for both appearances, and the page offers exactly the accents and
  appearances the stylesheet and the core know;
- every text colour has a contrast of at least 4.5 to 1 against each surface it is used on, worked out from
  the sheet's values for both appearances and every accent.

Not shown here: how any of it looks. That was looked at in pictures of every page (see the commit message).
"""
import html
import http.client
import json
import re
import shutil
import tempfile
import threading
import unittest
from pathlib import Path

from studio import server, store
from studio.store import Store

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "studio" / "web"


def opening_of(page):
    """The ``<html ...>`` element a served page begins with, as its attributes: ``{name: value}``."""
    text = page.decode("utf-8") if isinstance(page, bytes) else page
    found = re.search(r"<html\b([^>]*)>", text)
    return {name: html.unescape(value) for name, value in re.findall(r'([a-z-]+)="([^"]*)"', found.group(1))}


class KeptValueTest(unittest.TestCase):
    def test_each_word_the_page_may_keep_is_kept_as_it_is(self):
        for name, words in store.PAGE_WORDS.items():
            for word in words:
                self.assertEqual(store.kept_value(name, word), word, name)
        self.assertEqual(sorted(store.PAGE_WORDS), ["accent", "lang", "rf-open", "theme"])
        self.assertEqual(store.PAGE_WORDS["lang"], ("zh", "en"))
        self.assertEqual(store.PAGE_WORDS["theme"], ("auto", "light", "dark"))

    def test_any_other_word_is_refused(self):
        for name, value in [("lang", "fr"), ("lang", "EN"), ("lang", ""), ("lang", "zh "), ("theme", "black"), ("theme", "Dark"),
                            ("accent", "red"), ("accent", "#0071e3"), ("accent", "blue;"), ("theme", "auto\n"),
                            ("theme", '"><script>'), ("accent", "blue\" onload=\"x")]:
            self.assertIsNone(store.kept_value(name, value), (name, value))

    def test_a_number_is_a_whole_one_of_at_most_four_digits_and_is_kept_in_its_plain_form(self):
        for name in store.PAGE_NUMBERS:
            for given, wanted in [("0", "0"), ("7", "7"), ("12", "12"), ("9999", "9999"), ("-3", "-3"), ("007", "7"), ("-0", "0")]:
                self.assertEqual(store.kept_value(name, given), wanted, (name, given))
            for given in ("", " ", "1.5", "1e3", "12345", "-12345", "+1", " 1", "1 ", "1\n", "１２", "٣", "0x10", "one", "NaN"):
                self.assertIsNone(store.kept_value(name, given), (name, given))
        self.assertEqual(store.PAGE_NUMBERS, ("tp-pace", "tp-size", "tp-mirror"))

    def test_only_text_under_a_known_name_is_kept(self):
        for name, value in [("lang", 1), ("lang", None), ("lang", ["en"]), ("lang", {"a": 1}), ("theme", True), ("tp-pace", 12),
                            ("tp-pace", 1.0), (None, "en"), (1, "en"), (("lang",), "en"), ("language", "en"), ("keys", "x"),
                            ("provider", "manual"), ("", "en"), ("Lang", "en"), ("tp-other", "1"), ("__proto__", "x")]:
            self.assertIsNone(store.kept_value(name, value), (name, value))


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, str(self.tmp), True)
        self.store = Store(self.tmp / "data")

    def test_nothing_is_kept_at_first(self):
        self.assertEqual(self.store.load_page_keeps(), {})

    def test_what_is_kept_is_there_for_the_next_run_and_adds_up(self):
        self.assertEqual(self.store.keep_for_page({"lang": "en"}), {"lang": "en"})
        self.assertEqual(self.store.keep_for_page({"theme": "dark", "tp-pace": "12"}), {"lang": "en", "theme": "dark", "tp-pace": "12"})
        self.assertEqual(self.store.keep_for_page({"lang": "zh"}), {"lang": "zh", "theme": "dark", "tp-pace": "12"})
        again = Store(self.tmp / "data")                       # the app is closed and opened again
        self.assertEqual(again.load_page_keeps(), {"lang": "zh", "theme": "dark", "tp-pace": "12"})

    def test_the_settings_and_what_is_kept_do_not_touch_each_other(self):
        self.store.update_config(lambda config: (config.update(provider="openai", model="model-x"), config["keys"].update(openai="sk-secret")))
        self.store.keep_for_page({"accent": "purple"})
        config = self.store.load_config()
        self.assertEqual((config["provider"], config["model"], config["keys"]), ("openai", "model-x", {"openai": "sk-secret"}))
        self.store.update_config(lambda config: config.update(model="model-y"))          # the settings are saved again later
        self.assertEqual(self.store.load_page_keeps(), {"accent": "purple"})
        self.assertNotIn("page", self.store.public_settings())
        self.assertNotIn("sk-secret", json.dumps(self.store.load_page_keeps()))

    def test_what_the_file_holds_that_the_page_may_not_keep_is_left_out(self):
        for held in ("en", ["en"], None, 3, {"lang": "fr", "theme": 1, "other": "x", "accent": "green", "tp-size": "5", "tp-pace": "1.5"}):
            self.store.update_config(lambda config: config.update(page=held))
            wanted = {"accent": "green", "tp-size": "5"} if isinstance(held, dict) else {}
            self.assertEqual(self.store.load_page_keeps(), wanted, held)
        # Keeping something new does not fail on such a file, and what was good in it stays.
        self.store.update_config(lambda config: config.update(page="en"))
        self.assertEqual(self.store.keep_for_page({"lang": "en"}), {"lang": "en"})


class PageOpeningTest(unittest.TestCase):
    PAGE = (WEB / "index.html").read_bytes()

    def test_the_page_begins_as_the_core_expects(self):
        self.assertEqual(self.PAGE.count(server.PAGE_OPENING), 1)

    def test_with_nothing_kept_the_page_is_sent_as_it_is(self):
        self.assertEqual(server.page_with_keeps(self.PAGE, {}), self.PAGE)

    def test_what_is_kept_is_written_onto_the_page(self):
        kept = {"lang": "en", "theme": "dark", "accent": "purple", "tp-pace": "12"}
        page = server.page_with_keeps(self.PAGE, kept)
        opening = opening_of(page)
        self.assertEqual(json.loads(opening["data-kept"]), kept)
        self.assertEqual((opening["lang"], opening["data-theme"], opening["data-accent"]), ("en", "dark", "purple"))
        self.assertEqual(page.count(b"<html"), 1)
        # The rest of the page is as it was.
        self.assertEqual(page.split(b">", 2)[2], self.PAGE.split(b">", 2)[2])

    def test_the_usual_look_is_not_written_out(self):
        """ "auto" is the system's appearance, which only the page can know; blue is what the stylesheet has without being told."""
        opening = opening_of(server.page_with_keeps(self.PAGE, {"theme": "auto", "accent": "blue", "lang": "zh"}))
        self.assertEqual(sorted(opening), ["data-kept", "lang"])
        self.assertEqual(opening["lang"], "zh-CN")
        self.assertEqual(opening_of(server.page_with_keeps(self.PAGE, {"theme": "light"}))["data-theme"], "light")

    def test_a_page_that_begins_otherwise_is_sent_as_it_is(self):
        for page in (b"<!doctype html><html><body></body></html>", b"", self.PAGE + self.PAGE):
            self.assertEqual(server.page_with_keeps(page, {"lang": "en"}), page)

    def test_nothing_kept_can_end_the_attribute_it_is_written_into(self):
        """The core only keeps words from its lists; this is the second line of defence, should a list ever hold more."""
        page = server.page_with_keeps(self.PAGE, {"lang": 'en"><script>alert(1)</script>', "accent": 'x" onload="y'})
        text = page.decode("utf-8")
        self.assertNotIn("<script>alert", text)
        self.assertNotIn('onload="y', text)
        self.assertEqual(json.loads(opening_of(page)["data-kept"])["lang"], 'en"><script>alert(1)</script>')


class RequestTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, str(self.tmp), True)
        self.store = Store(self.tmp / "data")
        self.httpd = server.make_server(0, self.store, lambda *args: {})
        self.port = self.httpd.server_address[1]
        thread = threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
        thread.start()
        self.addCleanup(lambda: (self.httpd.shutdown(), self.httpd.server_close(), thread.join(5)))

    def send(self, method, path, payload=None, headers=None, body=None):
        head = dict(headers or {})
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            head.setdefault("Content-Type", "application/json")
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        try:
            conn.request(method, path, body=body, headers=head)
            res = conn.getresponse()
            raw = res.read()
        finally:
            conn.close()
        return res.status, (json.loads(raw.decode("utf-8")) if "json" in (res.getheader("Content-Type") or "") else raw)

    def test_the_page_is_the_file_until_something_is_kept(self):
        status, page = self.send("GET", "/")
        self.assertEqual((status, page), (200, (WEB / "index.html").read_bytes()))

    def test_what_the_page_asks_to_be_kept_comes_back_with_the_page(self):
        status, answer = self.send("POST", "/api/kept", {"lang": "en", "theme": "dark"})
        self.assertEqual((status, answer), (200, {"kept": {"lang": "en", "theme": "dark"}}))
        status, answer = self.send("POST", "/api/kept", {"accent": "pink", "tp-pace": "14", "tp-mirror": "1"})
        wanted = {"lang": "en", "theme": "dark", "accent": "pink", "tp-pace": "14", "tp-mirror": "1"}
        self.assertEqual((status, answer), (200, {"kept": wanted}))
        for address in ("/", "/index.html"):
            status, page = self.send("GET", address)
            opening = opening_of(page)
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(opening["data-kept"]), wanted)
            self.assertEqual((opening["lang"], opening["data-theme"], opening["data-accent"]), ("en", "dark", "pink"))
        self.assertEqual(Store(self.tmp / "data").load_page_keeps(), wanted)          # and it is on the disk, for the next run

    def test_a_second_run_on_another_port_gets_the_same_page(self):
        """What this is for: inside the desktop app the port, and so the page's address, differs at every start."""
        self.send("POST", "/api/kept", {"lang": "en", "theme": "light", "accent": "graphite"})
        second = server.make_server(0, Store(self.tmp / "data"), lambda *args: {})
        thread = threading.Thread(target=second.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
        thread.start()
        self.addCleanup(lambda: (second.shutdown(), second.server_close(), thread.join(5)))
        self.assertNotEqual(second.server_address[1], self.port)
        conn = http.client.HTTPConnection("127.0.0.1", second.server_address[1], timeout=30)
        try:
            conn.request("GET", "/")
            opening = opening_of(conn.getresponse().read())
        finally:
            conn.close()
        self.assertEqual(json.loads(opening["data-kept"]), {"lang": "en", "theme": "light", "accent": "graphite"})
        self.assertEqual((opening["lang"], opening["data-theme"], opening["data-accent"]), ("en", "light", "graphite"))

    def test_one_thing_that_may_not_be_kept_refuses_the_whole_request(self):
        self.send("POST", "/api/kept", {"lang": "en"})
        for body in ({"lang": "fr"}, {"theme": "dark", "accent": "red"}, {"theme": "dark", "keys": "x"}, {"tp-pace": 12},
                     {"tp-pace": "12345"}, {"lang": None}, {"lang": ["zh"]}, {"provider": "manual"}, {"page": {"lang": "zh"}}):
            status, answer = self.send("POST", "/api/kept", body)
            self.assertEqual((status, answer.get("error")), (400, "bad_value"), body)
        self.assertEqual(self.store.load_page_keeps(), {"lang": "en"})
        self.assertEqual(self.store.load_config()["provider"], "")

    def test_an_empty_request_keeps_nothing_and_says_what_is_kept(self):
        self.send("POST", "/api/kept", {"theme": "dark"})
        self.assertEqual(self.send("POST", "/api/kept", {}), (200, {"kept": {"theme": "dark"}}))

    def test_it_is_a_request_like_the_others_and_another_site_cannot_send_it(self):
        status, _ = self.send("POST", "/api/kept", {"lang": "en"}, headers={"Origin": "https://evil.example"})
        self.assertEqual(status, 403)
        status, _ = self.send("POST", "/api/kept", body=b"lang=en", headers={"Content-Type": "application/x-www-form-urlencoded"})
        self.assertEqual(status, 415)
        status, _ = self.send("GET", "/api/kept")
        self.assertEqual(status, 404)
        self.assertEqual(self.store.load_page_keeps(), {})

    def test_no_key_reaches_the_page(self):
        self.store.update_config(lambda config: config["keys"].update(openai="sk-very-secret"))
        self.send("POST", "/api/kept", {"lang": "en"})
        _, page = self.send("GET", "/")
        self.assertNotIn(b"sk-very-secret", page)
        self.assertNotIn(b"keys", page.split(b">", 2)[1])


class StylesheetTest(unittest.TestCase):
    CSS = (WEB / "app.css").read_text(encoding="utf-8")

    def blocks(self, selector):
        """The declarations of every rule whose selector is exactly ``selector``, as one ``{name: value}``."""
        found = {}
        for body in re.findall(r"(?:^|\})\s*%s\s*\{([^{}]*)\}" % re.escape(selector), self.CSS, re.M):
            for name, value in re.findall(r"(--[a-z0-9-]+)\s*:\s*([^;]+);", body):
                found[name] = value.strip()
        return found

    def test_every_name_the_sheet_uses_is_given_a_value(self):
        light = self.blocks(":root")
        used = set(re.findall(r"var\((--[a-z0-9-]+)", self.CSS))
        self.assertGreater(len(used), 20)
        # Two names are not for the whole page; each is set by the rule of the one part that uses it.
        own = {"--swatch": '.swatch[data-pick="accent:blue"] { --swatch:',       # each swatch in Settings, its colour
               "--rf-w": ".wrap.rf-on { --rf-w:",                                # the references beside the editor: their width,
               "--rf-out": " --rf-out: calc(var(--view-side) - 16px);"}          # and how far they stand out to the side
        self.assertEqual(sorted(used - set(light) - set(own)), [])
        for name, where in own.items():
            self.assertIn(name, used)
            self.assertIn(where, self.CSS, name)
        self.assertEqual(sorted(set(light) - used), [])          # and none is named for nothing

    def test_the_dark_appearance_gives_every_colour_its_own_value(self):
        light, dark = self.blocks(":root"), self.blocks(':root[data-theme="dark"]')
        not_colours = {"--radius", "--view-pad", "--view-side", "--accent-soft", "--accent-ring"}      # sizes; and two made from --accent-rgb
        self.assertEqual(sorted(set(light) - set(dark) - not_colours), [])
        self.assertEqual(sorted(set(dark) - set(light)), [])
        for name in ("--bg", "--card", "--ink", "--muted", "--side", "--field"):
            self.assertNotEqual(light[name], dark[name], name)

    def test_each_accent_the_core_keeps_has_its_colours_in_both_appearances(self):
        for accent in store.PAGE_WORDS["accent"]:
            if accent == "blue":                        # what the sheet has when no accent is named
                self.assertNotIn(':root[data-accent="blue"]', self.CSS)
                continue
            for selector in (':root[data-accent="%s"]' % accent, ':root[data-theme="dark"][data-accent="%s"]' % accent):
                self.assertEqual(sorted(self.blocks(selector)), ["--accent", "--accent-ink", "--accent-rgb"], selector)
            self.assertIn('.swatch[data-pick="accent:%s"]' % accent, self.CSS)
        self.assertIn('.swatch[data-pick="accent:blue"]', self.CSS)
        named = set(re.findall(r':root(?:\[data-theme="dark"\])?\[data-accent="([a-z]+)"\]', self.CSS))
        self.assertEqual(named, set(store.PAGE_WORDS["accent"]) - {"blue"})

    def test_a_dark_system_gets_a_dark_window_before_any_script_has_run(self):
        self.assertRegex(self.CSS, r"@media \(prefers-color-scheme: dark\) \{ :root:not\(\[data-theme\]\) \{[^}]*--bg:")

    def test_the_teleprompter_keeps_its_own_colours(self):
        """Its block is copied into the file made for a phone, where the names from the top of the sheet do not exist."""
        block = self.CSS[self.CSS.index("tp-look:start"):self.CSS.index("tp-look:end")]
        self.assertNotIn("var(", block)
        self.assertIn("#000", block)


# Every pairing of a text colour and a surface that the stylesheet uses: (the text, the surfaces it stands on
# from the bottom up, where in the app that is). A surface above the first is see-through and is laid over
# what is under it. A new pairing in the sheet belongs here.
PAIRINGS = [
    ("--ink", ["--bg"], "text on the window"),
    ("--ink", ["--card"], "text on a card"),
    ("--ink", ["--pop"], "an item of a menu"),
    ("--ink", ["--pop", "--fill"], "a category badge in the command palette"),
    ("--ink", ["--pop", "--fill-2"], "a command palette row under the pointer"),
    ("--ink", ["--side"], "the sidebar"),
    ("--ink", ["--side", "--fill"], "the chosen item in the sidebar"),
    ("--ink", ["--bg", "--fill"], "a quiet button on the window"),
    ("--ink", ["--card", "--fill"], "a quiet button on a card"),
    ("--ink", ["--bg", "--fill-hover"], "a quiet button under the pointer, on the window"),
    ("--ink", ["--card", "--fill-hover"], "a quiet button under the pointer, on a card"),
    ("--ink", ["--card", "--fill-2"], "a soft box in a card"),
    ("--ink", ["--bg", "--warn-soft"], "a notice on the window"),
    ("--ink", ["--card", "--warn-soft"], "a notice in a card"),
    ("--ink", ["--field"], "what is typed into a field"),
    ("--muted", ["--bg"], "a hint on the window"),
    ("--muted", ["--card"], "a hint on a card"),
    ("--muted", ["--pop"], "the small print of a menu item"),
    ("--muted", ["--pop", "--fill-2"], "the footer and hovered subtitle in the command palette"),
    ("--muted", ["--side"], "the language button in the sidebar"),
    ("--muted", ["--bg", "--fill"], "the number in a keyword chip on the window"),
    ("--muted", ["--card", "--fill"], "the number of a line, of a step"),
    ("--muted", ["--card", "--fill-2"], "a hint in a soft box"),
    ("--muted", ["--bg", "--fill-2"], "a hint that a line sounds written, on the window"),
    ("--accent-ink", ["--bg"], "a link on the window"),
    ("--accent-ink", ["--card"], "a link on a card"),
    ("--accent-ink", ["--card", "--accent-soft"], "the score of a keyword"),
    ("--accent-ink", ["--card", "--fill-2"], "a link in a soft box"),
    ("--accent-ink", ["--bg", "--warn-soft"], "a link in a notice on the window"),
    ("--accent-ink", ["--card", "--warn-soft"], "a link in a notice in a card"),
    ("--ok", ["--bg"], "fine, on the window"),
    ("--ok", ["--card"], "fine, on a card"),
    ("--ok", ["--bg", "--ok-soft"], "a green label on the window"),
    ("--ok", ["--card", "--ok-soft"], "a green label on a card"),
    ("--warn", ["--bg"], "wants a look, on the window"),
    ("--warn", ["--card"], "wants a look, on a card"),
    ("--warn", ["--bg", "--warn-soft"], "an orange label on the window"),
    ("--warn", ["--card", "--warn-soft"], "an orange label on a card"),
    ("--bad", ["--bg"], "a problem, on the window"),
    ("--bad", ["--card"], "a problem, on a card"),
    ("--bad", ["--bg", "--bad-soft"], "a finding on the window"),
    ("--bad", ["--card", "--bad-soft"], "a finding in a card"),
    ("--on-accent", ["--accent"], "the label of the main button, the chosen chip, the menu item under the pointer"),
    ("--on-accent", ["--danger"], "the label of a button that deletes"),
]


class ContrastTest(unittest.TestCase):
    """Text can be read on what it stands on: at least 4.5 to 1, the usual measure for text of ordinary size.

    Worked out from the values in the sheet, for both appearances and all six accents. It says nothing about a
    pairing that is not on the list above, about the hint inside an empty field (which is lighter on purpose),
    or about the dark message at the bottom of the window, whose see-through ground depends on what is under it.
    """
    CSS = (WEB / "app.css").read_text(encoding="utf-8")

    def names(self, theme, accent):
        look = StylesheetTest()
        named = dict(look.blocks(":root"))
        if theme == "dark":
            named.update(look.blocks(':root[data-theme="dark"]'))
        if accent != "blue":
            named.update(look.blocks(':root[data-accent="%s"]' % accent))
            if theme == "dark":
                named.update(look.blocks(':root[data-theme="dark"][data-accent="%s"]' % accent))
        return named

    @staticmethod
    def colour(value, named):
        """``(red, green, blue, how solid)`` of a value of the sheet: #rgb, #rrggbb, rgba(r, g, b, a), rgba(var(--x-rgb), a)."""
        value = value.strip()
        if value.startswith("#"):
            digits = value[1:] if len(value) == 7 else "".join(digit * 2 for digit in value[1:])
            return tuple(float(int(digits[at:at + 2], 16)) for at in (0, 2, 4)) + (1.0,)
        inner = re.fullmatch(r"rgba?\((.*)\)", value).group(1)
        inner = re.sub(r"var\((--[a-z0-9-]+)\)", lambda found: named[found.group(1)], inner)
        parts = [float(part) for part in inner.split(",")]
        return (parts[0], parts[1], parts[2], parts[3] if len(parts) > 3 else 1.0)

    @staticmethod
    def contrast(one, other):
        def luminance(rgb):
            channels = [v / 255.0 for v in rgb]
            linear = [v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4 for v in channels]
            return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]
        lighter, darker = sorted((luminance(one), luminance(other)), reverse=True)
        return (lighter + 0.05) / (darker + 0.05)

    def test_the_measure_itself(self):
        self.assertAlmostEqual(self.contrast((0, 0, 0), (255, 255, 255)), 21.0, places=2)
        self.assertAlmostEqual(self.contrast((255, 255, 255), (255, 255, 255)), 1.0, places=6)
        self.assertAlmostEqual(self.contrast((118, 118, 118), (255, 255, 255)), 4.54, places=2)     # the well-known grey that just passes
        self.assertEqual(self.colour("#fff", {}), (255.0, 255.0, 255.0, 1.0))
        self.assertEqual(self.colour("#0071e3", {}), (0.0, 113.0, 227.0, 1.0))
        self.assertEqual(self.colour("rgba(var(--accent-rgb), .10)", {"--accent-rgb": "0, 113, 227"}), (0.0, 113.0, 227.0, 0.1))

    def test_every_text_colour_reads_on_every_surface_it_is_used_on(self):
        worked_out, too_faint = 0, []
        for theme in ("light", "dark"):
            for accent in store.PAGE_WORDS["accent"]:
                named = self.names(theme, accent)
                for text, surfaces, where in PAIRINGS:
                    ground = None
                    for surface in surfaces:
                        red, green, blue, solid = self.colour(named[surface], named)
                        if ground is None:
                            self.assertEqual(solid, 1.0, "%s lies at the bottom and has to be solid" % surface)
                            ground = (red, green, blue)
                        else:
                            ground = tuple(ground[i] + solid * ((red, green, blue)[i] - ground[i]) for i in range(3))
                    ink = self.colour(named[text], named)
                    self.assertEqual(ink[3], 1.0, "%s is a text colour and has to be solid" % text)
                    ratio = self.contrast(ink[:3], ground)
                    worked_out += 1
                    if ratio < 4.5:
                        too_faint.append("%.2f %s, %s: %s (%s)" % (ratio, theme, accent, where, text))
        self.assertEqual(too_faint, [])
        self.assertEqual(worked_out, 2 * 6 * len(PAIRINGS))

    def test_the_list_names_only_colours_the_sheet_has_and_uses(self):
        named = self.names("light", "blue")
        for text, surfaces, where in PAIRINGS:
            for name in [text] + surfaces:
                self.assertIn(name, named, where)
                self.assertIn("var(%s)" % name, self.CSS, where)
        # The colours that carry text are all on the list.
        self.assertEqual({text for text, _, _ in PAIRINGS}, {"--ink", "--muted", "--accent-ink", "--ok", "--warn", "--bad", "--on-accent"})


class PageTest(unittest.TestCase):
    APP = (WEB / "app.js").read_text(encoding="utf-8")
    PAGES = (WEB / "pages.js").read_text(encoding="utf-8")

    def listed(self, name):
        return re.findall(r"'([^']+)'", re.search(r"const %s = \[([^\]]*)\];" % name, self.APP).group(1))

    def test_the_page_and_the_core_agree_on_what_may_be_kept(self):
        self.assertEqual(tuple(self.listed("THEMES")), store.PAGE_WORDS["theme"])
        self.assertEqual(tuple(self.listed("ACCENTS")), store.PAGE_WORDS["accent"])
        kept = re.search(r"const KEPT = \{([^}]*)\};", self.APP).group(1)
        self.assertIn("lang: ['zh', 'en']", kept)
        self.assertIn("theme: THEMES", kept)
        self.assertIn("accent: ACCENTS", kept)
        self.assertEqual(tuple(re.findall(r"'(tp-[a-z]+)': null", kept)), store.PAGE_NUMBERS)
        self.assertEqual(len(re.findall(r"[a-z'-]+: ", kept)), len(store.PAGE_WORDS) + len(store.PAGE_NUMBERS))

    def test_the_teleprompter_keeps_its_settings_under_the_names_the_core_knows(self):
        prompter = (WEB / "prompter.js").read_text(encoding="utf-8")
        self.assertEqual(set(re.findall(r"tp(?:Recall|Keep)\('([^']+)'", prompter)), set(store.PAGE_NUMBERS))
        self.assertIn("keepWithCore();", prompter)

    def test_every_choice_has_its_words_in_both_languages(self):
        for lang in ("zh", "en"):
            block = self.APP[self.APP.index("  %s: {" % lang):]
            block = block[:block.index("\n  },")]
            for key in ["look_title", "look_theme", "look_accent"] + ["look_" + theme for theme in store.PAGE_WORDS["theme"]] + \
                       ["accent_" + accent for accent in store.PAGE_WORDS["accent"]]:
                self.assertRegex(block, r"\b%s: '[^']+'" % key, (lang, key))

    def test_the_language_is_read_from_what_is_kept_and_a_change_is_sent_to_the_core(self):
        self.assertIn("let lang = recall('lang') === 'en' ? 'en' : 'zh';", self.APP)
        button = self.APP[self.APP.index("document.getElementById('lang-btn').onclick"):]
        button = button[:button.index("};")]
        self.assertIn("remember('lang', lang);", button)
        self.assertIn("keepWithCore();", button)
        # Nothing else in the pages keeps the language or the look for itself only.
        for name in ("app.js", "pages.js", "setup.js", "discover.js", "craft.js", "kwimport.js", "posted.js", "wordlist.js", "refs.js"):
            text = (WEB / name).read_text(encoding="utf-8")
            self.assertEqual(re.findall(r"localStorage\.setItem\(\s*'(?:lang|theme|accent)'", text), [], name)

    def test_settings_offers_every_appearance_and_every_accent(self):
        self.assertIn("THEMES.map(", self.PAGES)
        self.assertIn("ACCENTS.map(", self.PAGES)
        self.assertIn("setLook(name, ", self.PAGES)


if __name__ == "__main__":
    unittest.main()
