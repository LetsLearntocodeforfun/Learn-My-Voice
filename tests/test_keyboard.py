"""Hardware keyboard & Global Command Palette tests (studio/web/palette.js, tests/browser.py).

Covers:
- CDP hardware key mapping & modifier bitmasks in ``tests/browser.py``
- ``palette.js`` I18N parity (Chinese script in ``zh``, gender-neutral tokens & zero CJK in ``en``) and CSP hygiene
- Real headless Chrome E2E keyboard-only workflows in BOTH ``zh`` + ``light`` AND ``en`` + ``dark``:
  1. Global Command Palette via ``Ctrl+K`` / ``Meta+K`` (ARIA combobox/listbox, type_text filtering across scripts,
     plans, samples, trends & all 9 pages, ``ArrowDown`` / ``ArrowUp`` ``aria-activedescendant``, ``Enter`` jump to
     ``#/script/<id>``, ``Escape`` close & focus restoration, cache invalidation on reopen, XSS escaping)
  2. Sidebar ``Tab`` & ``Enter`` keyboard navigation + ``#palette-btn`` (``搜索 / 跳转`` / ``Search / Jump`` + ``⌘K``)
  3. Script editor shortcuts (``Ctrl+Enter`` / ``Meta+Enter`` to insert & focus a new line below, ``Alt+ArrowUp`` /
     ``Alt+ArrowDown`` to jump between lines on the same field)
  4. Teleprompter hardware keys (``Space`` countdown/play/pause, ``ArrowDown`` / ``ArrowUp`` breath stepping,
     ``+`` / ``-`` speed adjustment, ``Escape`` exit & focus return)
  5. Planner keyboard view switching (``1`` / ``2`` / ``3`` when outside inputs + arrow keys on calendar day cells)
  6. Dangerous ``askConfirm`` keyboard safety (initial focus on Cancel ``#ask-no``, ``Tab`` trap, ``Escape`` cancel
     without deleting + focus return) and ``closeModal()`` focus restoration on ``#modal``
"""
from __future__ import annotations

import datetime
import http.client
import os
import re
import shutil
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from studio import planner, server
from studio.store import ROOT, Store
from tests import browser

WEB = ROOT / "studio" / "web"
CHINESE = re.compile(r"[\u4e00-\u9fff]")
GENDER_PRONOUNS = re.compile(r"\b(she|her|hers|herself|he|him|his|himself)\b", re.I)


def _extract_i18n(js_text: str) -> dict:
    out = {}
    for lang_code in ("zh", "en"):
        m = re.search(rf"Object\.assign\(I18N\.{lang_code},\s*\{{(.*?)\}}\);", js_text, re.S)
        if not m:
            raise AssertionError(f"Missing I18N.{lang_code} block")
        pairs = {}
        for km in re.finditer(r"\b([a-zA-Z0-9_]+)\s*:\s*'((?:\\'|[^'])*)'", m.group(1)):
            pairs[km.group(1)] = km.group(2)
        out[lang_code] = pairs
    return out


class KeyMappingAndPaletteStaticTest(unittest.TestCase):
    def test_cdp_key_mapping_and_modifier_bitmask(self) -> None:
        self.assertEqual(
            (browser.MOD_ALT, browser.MOD_CTRL, browser.MOD_META, browser.MOD_SHIFT),
            (1, 2, 4, 8),
        )
        self.assertEqual(browser._resolve_key_event("Enter", 0), ("Enter", "Enter", 13, "\r", 0))
        self.assertEqual(browser._resolve_key_event("Escape", 0), ("Escape", "Escape", 27, "", 0))
        self.assertEqual(browser._resolve_key_event("Tab", 0), ("Tab", "Tab", 9, "", 0))
        self.assertEqual(browser._resolve_key_event("Space", 0), (" ", "Space", 32, " ", 0))
        self.assertEqual(browser._resolve_key_event(" ", 0), (" ", "Space", 32, " ", 0))
        self.assertEqual(browser._resolve_key_event("Backspace", 0), ("Backspace", "Backspace", 8, "", 0))
        self.assertEqual(browser._resolve_key_event("ArrowUp", 0), ("ArrowUp", "ArrowUp", 38, "", 0))
        self.assertEqual(browser._resolve_key_event("ArrowDown", 0), ("ArrowDown", "ArrowDown", 40, "", 0))
        self.assertEqual(browser._resolve_key_event("ArrowLeft", 0), ("ArrowLeft", "ArrowLeft", 37, "", 0))
        self.assertEqual(browser._resolve_key_event("ArrowRight", 0), ("ArrowRight", "ArrowRight", 39, "", 0))
        self.assertEqual(browser._resolve_key_event("k", browser.MOD_META), ("k", "KeyK", 75, "k", browser.MOD_META))
        self.assertEqual(browser._resolve_key_event("K", browser.MOD_CTRL), ("K", "KeyK", 75, "K", browser.MOD_CTRL))
        self.assertEqual(browser._resolve_key_event("2", 0), ("2", "Digit2", 50, "2", 0))
        self.assertEqual(browser._resolve_key_event("+", 0), ("+", "Equal", 187, "+", browser.MOD_SHIFT))
        self.assertEqual(browser._resolve_key_event("-", 0), ("-", "Minus", 189, "-", 0))
        self.assertEqual(browser._resolve_key_event("?", 0), ("?", "Slash", 191, "?", browser.MOD_SHIFT))
        with self.assertRaises(ValueError):
            browser._resolve_key_event("NoSuchKey", 0)

    def test_palette_i18n_parity_gender_neutral_english_and_static_hygiene(self) -> None:
        source = (WEB / "palette.js").read_text(encoding="utf-8")
        blocks = _extract_i18n(source)
        self.assertEqual(set(blocks["zh"]), set(blocks["en"]))
        self.assertGreaterEqual(len(blocks["zh"]), 25)
        for key, zh_val in blocks["zh"].items():
            self.assertRegex(zh_val, CHINESE, f"zh.{key} must contain Chinese characters")
            self.assertNotIn("{@", zh_val, f"zh.{key} must not use English pronoun tokens")
        for key, en_val in blocks["en"].items():
            self.assertIsNone(CHINESE.search(en_val), f"en.{key} must not contain CJK: {en_val}")
            self.assertIsNone(GENDER_PRONOUNS.search(en_val), f"en.{key} must be gender-neutral: {en_val}")
        self.assertTrue(any("{@names}" in v or "{@their}" in v for v in blocks["en"].values()))

        index_html = (WEB / "index.html").read_text(encoding="utf-8")
        self.assertLess(index_html.index('src="/palette.js"'), index_html.index('src="/pages.js"'))
        for forbidden in ("fetch(", "XMLHttpRequest", "location.href", "confirm(", "alert(", "prompt(", "<form", "style="):
            self.assertNotIn(forbidden, source, forbidden)
        api_calls = sorted(set(re.findall(r"api\('([A-Z]+)',\s*'([^']+)'\)", source)))
        self.assertEqual(
            api_calls,
            [("GET", "/api/plans"), ("GET", "/api/samples"), ("GET", "/api/scripts"), ("GET", "/api/trends")],
        )

    def test_palette_js_is_served_by_studio_server(self) -> None:
        tmp = Path(tempfile.mkdtemp(prefix="lmv-pal-srv-"))
        self.addCleanup(shutil.rmtree, str(tmp), True)
        with mock.patch.dict(os.environ, {"STUDIO_KEYSTORE_FILE": str(tmp / "k.key"), "LEARN_MY_VOICE_NO_SIBLING_SCAN": "1"}):
            store = Store(tmp / "data")
            httpd = server.make_server(0, store)
            threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True).start()
            self.addCleanup(lambda: (httpd.shutdown(), httpd.server_close()))
            conn = http.client.HTTPConnection("127.0.0.1", httpd.server_address[1], timeout=10)
            try:
                conn.request("GET", "/palette.js")
                res = conn.getresponse()
                body = res.read()
            finally:
                conn.close()
            self.assertEqual((res.status, res.getheader("Content-Type")), (200, "text/javascript; charset=utf-8"))
            self.assertEqual(body, (WEB / "palette.js").read_bytes())


@unittest.skipUnless(browser.AVAILABLE, browser.WHY_NOT)
class HardwareKeyboardBrowserE2ETest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="lmv-kb-chrome-"))
        self.addCleanup(shutil.rmtree, str(self.tmp), True)
        env = mock.patch.dict(os.environ, {
            "STUDIO_KEYSTORE_FILE": str(self.tmp / "keystore.key"),
            "LEARN_MY_VOICE_NO_SIBLING_SCAN": "1",
        })
        env.start()
        self.addCleanup(env.stop)
        self.store = Store(self.tmp / "data")
        self.today_iso = datetime.date.today().isoformat()

        self.script_a = self.store.create_script(
            title="周日山姆补货日常",
            keywords=["山姆采购", "周末Vlog"],
            lines=[
                {"line_no": 1, "audio_mode": "VOICEOVER", "spoken_script_zh": "周日下午趁冰箱空了嘛 / 拉着某人去趟超市哈", "visual_broll_en_zh": "推购物车进门"},
                {"line_no": 2, "audio_mode": "VOICEOVER", "spoken_script_zh": "结果牛排真的超香啦 / 下期见咯", "visual_broll_en_zh": "煎牛排特写"},
            ],
        )
        self.script_b = self.store.create_script(
            title="秋冬晚间护肤实测",
            keywords=["干皮面霜", "nightcare"],
            lines=[
                {"line_no": 1, "audio_mode": "VOICEOVER", "spoken_script_zh": "一入冬吹暖气脸就紧绷嘛 / 换了这罐稳多了哈", "visual_broll_en_zh": "睡前护肤台"},
            ],
        )
        self.plan_item = planner.create_plan_item(self.store, {
            "title": "暖气房晨间保湿计划",
            "scheduled_for": self.today_iso,
            "notes": "重点测上妆服帖度",
        })
        self.sample = self.store.create_sample(
            title="旧版超市开箱素材",
            text="以前去超市拍的一段口播嘛 / 留着做参考哈",
            metrics={"views": 12000, "likes": 450},
        )
        self.store.upsert_trends([{"keyword": "冬日热红酒", "heat": 160000}], source="manual")

    def test_full_keyboard_workflows_in_zh_light_and_en_dark(self) -> None:
        # ==================== PASS 1: zh + light ====================
        with browser.open_app(self.store, lang="zh", theme="light") as page:
            page.go("#/", "Boolean(document.getElementById('palette-btn'))")
            self.assertEqual(page.eval_js("document.documentElement.dataset.theme"), "light")
            self.assertEqual(page.eval_js("document.documentElement.lang"), "zh-CN")
            self.assertIn("搜索 / 跳转", page.eval_js("document.getElementById('palette-btn').textContent"))
            self.assertIn("⌘K", page.eval_js("document.getElementById('palette-btn').textContent"))

            # 1. Sidebar Tab & Enter navigation + sidebar ⌘K button keyboard activation
            page.eval_js("document.getElementById('palette-btn').focus()")
            self.assertEqual(page.active_selector_info()["id"], "palette-btn")
            page.press("Enter")
            page.wait_for("!document.getElementById('palette').hidden && document.getElementById('palette').dataset.palLoaded === '1'")
            info = page.active_selector_info()
            self.assertEqual((info["id"], info["role"]), ("palette-input", "combobox"))
            self.assertEqual(page.eval_js("document.getElementById('palette-dialog').getAttribute('role')"), "dialog")
            self.assertEqual(page.eval_js("document.getElementById('palette-dialog').getAttribute('aria-modal')"), "true")
            self.assertEqual(page.eval_js("document.getElementById('palette-list').getAttribute('role')"), "listbox")

            # Escape closes palette and restores focus to #palette-btn
            page.press("Escape")
            page.wait_for("document.getElementById('palette').hidden === true")
            self.assertEqual(page.active_selector_info()["id"], "palette-btn")

            # Tab through sidebar links (#/start -> #/ -> #/plan) and press Enter to open #/plan!
            page.press("Tab")
            self.assertEqual(page.active_selector_info()["href"], "#/start")
            page.press("Tab")
            self.assertEqual(page.active_selector_info()["href"], "#/")
            page.press("Tab")
            self.assertEqual(page.active_selector_info()["href"], "#/plan")
            page.press("Enter")
            page.wait_for("location.hash === '#/plan' && Boolean(document.getElementById('pl-list'))")

            # 2. Planner keyboard view switching (1 / 2 / 3) + calendar day cell arrow keys
            page.press("2")
            page.wait_for("Boolean(document.querySelector('#pl-cal.week'))")
            self.assertEqual(page.eval_js("""document.querySelector('[data-pl-view="week"]').getAttribute('aria-pressed')"""), "true")

            page.press("3")
            page.wait_for("Boolean(document.querySelector('#pl-cal.month'))")
            self.assertEqual(page.eval_js("""document.querySelector('[data-pl-view="month"]').getAttribute('aria-pressed')"""), "true")
            # ArrowRight / ArrowDown on calendar day buttons moves focus cleanly across days
            page.eval_js("document.querySelector('#pl-cal [data-pl-daybtn]').focus()")
            day0 = page.active_selector_info()["dataset"].get("plDaybtn")
            page.press("ArrowRight")
            day1 = page.active_selector_info()["dataset"].get("plDaybtn")
            self.assertNotEqual(day0, day1)
            page.press("ArrowDown")
            day8 = page.active_selector_info()["dataset"].get("plDaybtn")
            self.assertNotEqual(day1, day8)

            page.press("1")
            page.wait_for("Boolean(document.getElementById('pl-list'))")
            self.assertEqual(page.eval_js("""document.querySelector('[data-pl-view="list"]').getAttribute('aria-pressed')"""), "true")

            # Typing digits inside #pl-new-title must type into input and NOT switch planner views
            page.eval_js("document.getElementById('pl-new-title').focus()")
            page.type_text("测试23")
            self.assertEqual(page.eval_js("document.getElementById('pl-new-title').value"), "测试23")
            self.assertTrue(page.eval_js("Boolean(document.getElementById('pl-list'))"))

            # 3. Command Palette via Ctrl+K FROM INSIDE an <input>, CJK type_text filtering, ArrowDown & Enter jump to #/script/<id>
            page.press("k", ctrl=True)
            page.wait_for("!document.getElementById('palette').hidden && document.getElementById('palette').dataset.palLoaded === '1'")
            self.assertEqual(page.active_selector_info()["id"], "palette-input")
            # Verify all 9 pages + scripts + plans + samples + trends are indexed
            self.assertGreaterEqual(page.eval_js("""document.querySelectorAll('#palette-list [role="option"]').length"""), 15)

            # Filter by broad '脚本' so ArrowDown steps to pal-opt-1 and ArrowUp back to pal-opt-0
            page.type_text("脚本")
            count_opts = page.eval_js("""document.querySelectorAll('#palette-list [role="option"]').length""")
            self.assertGreaterEqual(count_opts, 2)
            self.assertEqual(page.active_selector_info()["ariaActiveDescendant"], "pal-opt-0")
            page.press("ArrowDown")
            self.assertEqual(page.active_selector_info()["ariaActiveDescendant"], "pal-opt-1")
            self.assertEqual(page.eval_js("document.getElementById('pal-opt-1').getAttribute('aria-selected')"), "true")
            page.press("ArrowUp")
            self.assertEqual(page.active_selector_info()["ariaActiveDescendant"], "pal-opt-0")

            # Filter specifically to script_a by its keyword "山姆采购" and press Enter -> navigates to #/script/<id>
            page.eval_js("document.getElementById('palette-input').select()")
            page.press("Backspace")
            page.type_text("山姆采购")
            page.wait_for("""document.querySelectorAll('#palette-list [role="option"]').length === 1""")
            page.press("Enter")
            page.wait_for(f"location.hash === '#/script/{self.script_a['id']}' && Boolean(document.getElementById('lines'))")
            self.assertTrue(page.eval_js("document.getElementById('palette').hidden"))

            # 4. Script Editor keyboard shortcuts: type_text, Ctrl+Enter new line, Alt+ArrowUp / Alt+ArrowDown
            page.eval_js("""document.querySelector('#lines .line[data-i="0"] [data-f="spoken_script_zh"]').focus()""")
            self.assertEqual(page.eval_js("document.querySelectorAll('#lines .line').length"), 2)
            page.press("Enter", ctrl=True)
            page.wait_for("document.querySelectorAll('#lines .line').length === 3")
            self.assertEqual(
                page.eval_js("document.activeElement.closest('.line').dataset.i + ':' + document.activeElement.dataset.f"),
                "1:spoken_script_zh",
            )
            page.type_text("新插入的中间一句哈")
            self.assertEqual(
                page.eval_js("""document.querySelector('#lines .line[data-i="1"] [data-f="spoken_script_zh"]').value"""),
                "新插入的中间一句哈",
            )
            page.press("ArrowUp", alt=True)
            self.assertEqual(
                page.eval_js("document.activeElement.closest('.line').dataset.i + ':' + document.activeElement.dataset.f"),
                "0:spoken_script_zh",
            )
            page.press("ArrowDown", alt=True)
            self.assertEqual(
                page.eval_js("document.activeElement.closest('.line').dataset.i + ':' + document.activeElement.dataset.f"),
                "1:spoken_script_zh",
            )

            # 5. Teleprompter hardware keys: open via Enter on #prompter-btn, Space, ArrowDown/ArrowUp, +/-, Escape
            page.eval_js("document.getElementById('prompter-btn').focus()")
            page.press("Enter")
            page.wait_for("""!document.getElementById('prompter').hidden && Boolean(document.querySelector('[data-tp="toggle"]'))""")
            self.assertEqual(page.eval_js("document.getElementById('prompter').getAttribute('data-state')"), "idle")
            page.press("Space")
            self.assertEqual(page.eval_js("document.getElementById('prompter').getAttribute('data-state')"), "counting")
            page.press("Space")
            self.assertEqual(page.eval_js("document.getElementById('prompter').getAttribute('data-state')"), "running")
            page.press("Space")
            self.assertEqual(page.eval_js("document.getElementById('prompter').getAttribute('data-state')"), "paused")

            self.assertEqual(page.eval_js("Array.from(document.querySelectorAll('.tp-text p')).findIndex(p => p.classList.contains('tp-now'))"), 0)
            page.press("ArrowDown")
            self.assertEqual(page.eval_js("Array.from(document.querySelectorAll('.tp-text p')).findIndex(p => p.classList.contains('tp-now'))"), 1)
            page.press("ArrowUp")
            self.assertEqual(page.eval_js("Array.from(document.querySelectorAll('.tp-text p')).findIndex(p => p.classList.contains('tp-now'))"), 0)

            pace_before = page.eval_js("document.querySelector('.tp-pace').textContent")
            page.press("+")
            pace_faster = page.eval_js("document.querySelector('.tp-pace').textContent")
            self.assertNotEqual(pace_before, pace_faster)
            page.press("-")
            self.assertEqual(page.eval_js("document.querySelector('.tp-pace').textContent"), pace_before)

            page.press("Escape")
            page.wait_for("document.getElementById('prompter').hidden === true")
            self.assertEqual(page.active_selector_info()["id"], "prompter-btn")

            # 6. Dangerous askConfirm keyboard safety: Enter on #delete-btn -> focus lands on Cancel (#ask-no), Escape cancels & restores focus
            page.eval_js("document.getElementById('delete-btn').focus()")
            page.press("Enter")
            page.wait_for("Boolean(document.getElementById('ask')) && !document.getElementById('ask').hidden")
            self.assertEqual(page.active_selector_info()["id"], "ask-no")
            page.press("Tab")
            self.assertEqual(page.active_selector_info()["id"], "ask-yes")
            page.press("Tab")
            self.assertEqual(page.active_selector_info()["id"], "ask-no")
            page.press("Escape")
            page.wait_for("document.getElementById('ask').hidden === true")
            self.assertEqual(page.active_selector_info()["id"], "delete-btn")
            self.assertIsNotNone(self.store.get_script(self.script_a["id"]))

            page.eval_js("flushSave()")
            self.assertEqual(page.problems, [])

        # ==================== PASS 2: en + dark ====================
        with browser.open_app(self.store, lang="en", theme="dark") as page:
            page.go("#/", "Boolean(document.getElementById('palette-btn'))")
            self.assertEqual(page.eval_js("document.documentElement.dataset.theme"), "dark")
            self.assertEqual(page.eval_js("document.documentElement.lang"), "en")
            self.assertIn("Search / Jump", page.eval_js("document.getElementById('palette-btn').textContent"))

            # 1. Meta+K (⌘K) opens palette; focus restoration works from #lang-btn; English search & jump to #/script/<id>
            page.eval_js("document.getElementById('lang-btn').focus()")
            page.press("k", meta=True)
            page.wait_for("!document.getElementById('palette').hidden && document.getElementById('palette').dataset.palLoaded === '1'")
            self.assertEqual(page.active_selector_info()["id"], "palette-input")
            page.press("Escape")
            page.wait_for("document.getElementById('palette').hidden === true")
            self.assertEqual(page.active_selector_info()["id"], "lang-btn")

            page.press("k", meta=True)
            page.wait_for("!document.getElementById('palette').hidden && document.getElementById('palette').dataset.palLoaded === '1'")
            page.type_text("nightcare")
            page.wait_for("""document.querySelectorAll('#palette-list [role="option"]').length === 1""")
            self.assertEqual(page.active_selector_info()["ariaActiveDescendant"], "pal-opt-0")
            page.press("Enter")
            page.wait_for(f"location.hash === '#/script/{self.script_b['id']}' && Boolean(document.getElementById('lines'))")

            # 2. On #/script/<id>, Meta+Enter inserts a new line below & Alt+ArrowUp jumps back; Open Teleprompter appears in palette!
            page.eval_js("""document.querySelector('#lines .line[data-i="0"] [data-f="spoken_script_zh"]').focus()""")
            page.press("Enter", meta=True)
            page.wait_for("document.querySelectorAll('#lines .line').length === 2")
            self.assertEqual(
                page.eval_js("document.activeElement.closest('.line').dataset.i + ':' + document.activeElement.dataset.f"),
                "1:spoken_script_zh",
            )
            page.press("ArrowUp", alt=True)
            self.assertEqual(
                page.eval_js("document.activeElement.closest('.line').dataset.i + ':' + document.activeElement.dataset.f"),
                "0:spoken_script_zh",
            )

            # Open Teleprompter directly from Command Palette while on #/script/<id>
            page.press("k", meta=True)
            page.wait_for("!document.getElementById('palette').hidden")
            page.type_text("Teleprompter")
            page.wait_for("""document.querySelectorAll('#palette-list [role="option"]').length >= 1""")
            page.press("Enter")
            page.wait_for("!document.getElementById('prompter').hidden")
            page.press("Escape")
            page.wait_for("document.getElementById('prompter').hidden === true")

            # 3. Built-in New script command via Command Palette + closeModal() focus restoration on #modal
            page.press("k", meta=True)
            page.wait_for("!document.getElementById('palette').hidden")
            page.type_text("New script")
            page.wait_for("""document.querySelectorAll('#palette-list [role="option"]').length >= 1""")
            page.press("Enter")
            page.wait_for("location.hash === '#/new' && Boolean(document.getElementById('import-script-json'))")

            page.eval_js("document.getElementById('import-script-json').focus()")
            page.press("Enter")
            page.wait_for("!document.getElementById('modal').hidden && Boolean(document.getElementById('mb-reply'))")
            page.press("Escape")
            page.wait_for("document.getElementById('modal').hidden === true")
            self.assertEqual(page.active_selector_info()["id"], "import-script-json")

            # 4. Planner 1/2/3 view switching + dangerous askConfirm on plan deletion in English + dark
            page.press("k", meta=True)
            page.wait_for("!document.getElementById('palette').hidden && document.getElementById('palette').dataset.palLoaded === '1'")
            page.type_text("Plan")
            page.press("Enter")
            page.wait_for("location.hash === '#/plan' && Boolean(document.getElementById('pl-list'))")
            page.press("2")
            page.wait_for("Boolean(document.querySelector('#pl-cal.week'))")
            page.press("1")
            page.wait_for("Boolean(document.getElementById('pl-list'))")

            del_sel = f'[data-pl-del="{self.plan_item["id"]}"]'
            page.eval_js(f"document.querySelector('{del_sel}').focus()")
            page.press("Enter")
            page.wait_for("Boolean(document.getElementById('ask')) && !document.getElementById('ask').hidden")
            self.assertEqual(page.active_selector_info()["id"], "ask-no")
            page.press("Escape")
            page.wait_for("document.getElementById('ask').hidden === true")
            self.assertEqual(page.active_selector_info()["dataset"].get("plDel"), self.plan_item["id"])
            self.assertIsNotNone(self.store.get_video_plan(self.plan_item["id"]))

            # 5. Built-in theme toggle & language switch via Command Palette + jump to Library & Trends
            page.press("k", meta=True)
            page.wait_for("!document.getElementById('palette').hidden && document.getElementById('palette').dataset.palLoaded === '1'")
            page.type_text("旧版超市")
            page.wait_for("""document.querySelectorAll('#palette-list [role="option"]').length === 1""")
            page.press("Enter")
            page.wait_for("location.hash === '#/library'")

            page.press("k", meta=True)
            page.wait_for("!document.getElementById('palette').hidden && document.getElementById('palette').dataset.palLoaded === '1'")
            page.type_text("冬日热红酒")
            page.wait_for("""document.querySelectorAll('#palette-list [role="option"]').length === 1""")
            page.press("Enter")
            page.wait_for("location.hash === '#/trends'")

            page.press("k", meta=True)
            page.wait_for("!document.getElementById('palette').hidden")
            page.type_text("Light mode")
            page.wait_for("""document.querySelectorAll('#palette-list [role="option"]').length >= 1""")
            page.press("Enter")
            self.assertEqual(page.eval_js("document.documentElement.dataset.theme"), "light")

            page.press("k", meta=True)
            page.wait_for("!document.getElementById('palette').hidden")
            page.type_text("Switch language")
            page.wait_for("""document.querySelectorAll('#palette-list [role="option"]').length >= 1""")
            page.press("Enter")
            page.wait_for("document.documentElement.lang === 'zh-CN'")

            self.assertEqual(page.problems, [])

    def test_hostile_markup_in_palette_items_stays_escaped(self) -> None:
        xss = "<img src=x onerror=alert(1)>"
        self.store.create_script(title=f"危险脚本{xss}", keywords=[f"热词{xss}"])
        planner.create_plan_item(self.store, {"title": f"危险计划{xss}"})
        self.store.create_sample(title=f"危险素材{xss}", text="安全正文")
        self.store.upsert_trends([{"keyword": f"危险趋势{xss}", "heat": 1000}], source="manual")

        with browser.open_app(self.store, lang="zh", theme="light") as page:
            page.go("#/", "Boolean(document.getElementById('palette-btn'))")
            page.press("k", ctrl=True)
            page.wait_for("!document.getElementById('palette').hidden && document.getElementById('palette').dataset.palLoaded === '1'")
            page.type_text("危险")
            page.wait_for("""document.querySelectorAll('#palette-list [role="option"]').length === 4""")
            self.assertEqual(page.eval_js("document.querySelectorAll('#palette img').length"), 0)
            self.assertEqual(page.problems, [])


if __name__ == "__main__":
    unittest.main()
