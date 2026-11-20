"""Tests for resilient manual copy-paste and .json file import across Step 3 (Voice DNA) and Step 4 (Scripts).

Covers:
- UTF-8 BOM and zero-width characters
- Markdown ```json fences surrounded by prose containing {braces}, and multiple fenced blocks
- // line comments, /* */ block comments, and trailing commas outside strings
- macOS TextEdit / Notes curly smart quotes (“key”: “value”) while preserving inner Chinese quotes
- Literal newlines and tabs inside copied string literals
- Strict refusal of NaN, Infinity, -Infinity, overflow floats, deep recursion, and O(n) linear speed
- Truncated JSON detection without mistakenly extracting inner nested property dicts
- Top-level array wrapping in ``parse_manual_reply`` while preserving ``parse_json_text("[1, 2, 3]")`` refusal
- Nested Voice DNA wrappers, omitted ``cadence_and_syntax`` / ``anti_voice_blacklist``, list-shaped blacklists
- Cross-step paste errors (``wrong_step_voice``, ``wrong_step_draft``) and HTTP 413 ``text_too_long``
- Real headless Google Chrome end-to-end flow: Step 3 .json upload & paste -> Accept -> Step 4 .json upload & paste
"""
from __future__ import annotations

import base64
import http.client
import json
import os
import re
import shutil
import socket
import struct
import subprocess
import tempfile
import threading
import time
import unittest
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, Optional

from studio import llm, pipeline, server
from studio import store as store_module
from studio.store import Store

CHROME_BIN = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
HER_ORGANIC_SAMPLE = "\n".join(
    [
        "今天去山姆补货嘛 / 结果排队排了半小时哈",
        "我老公说这个牛排真的超香啦 / 咱们下次还是早点出门吧",
    ]
    * 10
)


class _CdpClient:
    """Minimal stdlib WebSocket client for Chrome DevTools Protocol."""

    def __init__(self, ws_url: str) -> None:
        parsed = urllib.parse.urlparse(ws_url)
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or 80
        path = parsed.path or "/"
        self.sock = socket.create_connection((host, port), timeout=15)
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        handshake = (
            "GET %s HTTP/1.1\r\n"
            "Host: %s:%d\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            "Sec-WebSocket-Key: %s\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n" % (path, host, port, key)
        )
        self.sock.sendall(handshake.encode("ascii"))
        header_buf = b""
        while b"\r\n\r\n" not in header_buf:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise RuntimeError("CDP handshake closed early")
            header_buf += chunk
        self._leftover = header_buf.split(b"\r\n\r\n", 1)[1]
        self._next_id = 0

    def _recv_exact(self, n: int) -> bytes:
        out = bytearray()
        if self._leftover:
            take = self._leftover[:n]
            out.extend(take)
            self._leftover = self._leftover[len(take):]
        while len(out) < n:
            chunk = self.sock.recv(n - len(out))
            if not chunk:
                raise RuntimeError("CDP socket closed")
            out.extend(chunk)
        return bytes(out)

    def _send_frame(self, payload: bytes, opcode: int = 0x1) -> None:
        mask = os.urandom(4)
        header = bytearray([0x80 | opcode])
        n = len(payload)
        if n < 126:
            header.append(0x80 | n)
        elif n < 65536:
            header.append(0x80 | 126)
            header.extend(struct.pack("!H", n))
        else:
            header.append(0x80 | 127)
            header.extend(struct.pack("!Q", n))
        header.extend(mask)
        masked = bytes(b ^ mask[i & 3] for i, b in enumerate(payload))
        self.sock.sendall(bytes(header) + masked)

    def _recv_frame(self) -> bytes:
        while True:
            b0, b1 = self._recv_exact(2)
            opcode = b0 & 0x0F
            masked = bool(b1 & 0x80)
            length = b1 & 0x7F
            if length == 126:
                length = struct.unpack("!H", self._recv_exact(2))[0]
            elif length == 127:
                length = struct.unpack("!Q", self._recv_exact(8))[0]
            mask = self._recv_exact(4) if masked else b""
            data = self._recv_exact(length)
            if masked:
                data = bytes(b ^ mask[i & 3] for i, b in enumerate(data))
            if opcode == 0x9:
                self._send_frame(data, opcode=0xA)
                continue
            if opcode == 0x8:
                raise RuntimeError("CDP closed")
            return data

    def call(self, method: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        self._next_id += 1
        call_id = self._next_id
        msg = json.dumps({"id": call_id, "method": method, "params": params or {}}).encode("utf-8")
        self._send_frame(msg)
        while True:
            frame = json.loads(self._recv_frame().decode("utf-8"))
            if frame.get("id") == call_id:
                if "error" in frame:
                    raise RuntimeError("CDP error on %s: %s" % (method, frame["error"]))
                return frame.get("result", {})

    def eval_js(self, expression: str) -> Any:
        res = self.call("Runtime.evaluate", {"expression": expression, "awaitPromise": True, "returnByValue": True})
        if "exceptionDetails" in res:
            raise AssertionError("JS exception: %s" % json.dumps(res["exceptionDetails"], ensure_ascii=False))
        return res.get("result", {}).get("value")

    def wait_for(self, js_predicate: str, timeout: float = 10.0) -> Any:
        deadline = time.time() + timeout
        last = None
        while time.time() < deadline:
            last = self.eval_js(js_predicate)
            if last:
                return last
            time.sleep(0.08)
        raise AssertionError("Timed out waiting for %s (last=%r)" % (js_predicate, last))

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass


class ManualJsonParserTest(unittest.TestCase):
    def test_utf8_bom_and_zero_width_spaces_are_stripped(self) -> None:
        raw = '\ufeff\u200b{\u200c"persona_summary": {"one_line_essence_zh": "真实松弛"}\u200d}\ufeff\u2060'
        self.assertEqual(llm.parse_json_text(raw), {"persona_summary": {"one_line_essence_zh": "真实松弛"}})

    def test_prose_with_braces_before_and_after_fenced_block(self) -> None:
        reply = (
            'Here is your {Voice DNA} profile (note: {"draft": false} is omitted):\n'
            "```json\n"
            '{"persona_summary": {"one_line_essence_zh": "周末采购松弛感"}, "list": [1, 2]}\n'
            "```\n"
            "Let me know if you want {any adjustments} or {more examples}!"
        )
        self.assertEqual(
            llm.parse_json_text(reply),
            {"persona_summary": {"one_line_essence_zh": "周末采购松弛感"}, "list": [1, 2]},
        )

    def test_multiple_fenced_blocks_picks_valid_json_block(self) -> None:
        reply = (
            "Summary:\n"
            "```text\n"
            "Not JSON {just notes}\n"
            "```\n"
            "Downloadable JSON:\n"
            "```json\n"
            '{"script_lines": [{"spoken_script_zh": "今天去山姆补货嘛"}]}\n'
            "```\n"
        )
        self.assertEqual(
            llm.parse_json_text(reply),
            {"script_lines": [{"spoken_script_zh": "今天去山姆补货嘛"}]},
        )

    def test_comments_and_trailing_commas_outside_strings(self) -> None:
        reply = """{
            // ChatGPT added a helpful comment here
            "source_url": "https://www.xiaohongshu.com/explore/123 // keep inside string",
            "note": "/* keep block marker inside string */",
            /* Multi-line comment
               explaining the array */
            "items": [
                "第一条",
                "第二条", // trailing comma with comment
            ],
        }"""
        self.assertEqual(
            llm.parse_json_text(reply),
            {
                "source_url": "https://www.xiaohongshu.com/explore/123 // keep inside string",
                "note": "/* keep block marker inside string */",
                "items": ["第一条", "第二条"],
            },
        )

    def test_macos_textedit_smart_quotes_and_nested_chinese_quotes(self) -> None:
        # macOS TextEdit converts structural quotes " to “ and ” while Chinese text also has inner “...”
        smart = (
            "{\n"
            "  \u201cpersona_summary\u201d: {\n"
            "    \u201cone_line_essence_zh\u201d: \u201c我老公说\u201c这也太好吃了吧\u201d，结果真香\u201d,\n"
            '    "mixed_quote_key": "结尾被输入法改成了弯引号\u201d,\n'
            "  },\n"
            "}"
        )
        self.assertEqual(
            llm.parse_json_text(smart),
            {
                "persona_summary": {
                    "one_line_essence_zh": "我老公说“这也太好吃了吧”，结果真香",
                    "mixed_quote_key": "结尾被输入法改成了弯引号",
                }
            },
        )
        # Valid ASCII JSON containing curly quotes followed by ASCII comma must remain untouched
        ascii_with_inner_curly = '{"line": "she said \u201chi\u201d, then smiled",}'
        self.assertEqual(llm.parse_json_text(ascii_with_inner_curly), {"line": "she said “hi”, then smiled"})

    def test_literal_newlines_and_tabs_inside_strings(self) -> None:
        raw = '{"spoken_script_zh": "第一口气\n\t第二口气"}'
        self.assertEqual(llm.parse_json_text(raw), {"spoken_script_zh": "第一口气\n\t第二口气"})

    def test_combined_chatgpt_and_macos_blemishes(self) -> None:
        messy = (
            "\ufeff好的！这是为你生成的 {Voice DNA} 档案：\n"
            "```json\n"
            "{\n"
            "  // 一句话人设\n"
            "  \u201cpersona_summary\u201d: {\n"
            "    \u201cone_line_essence_zh\u201d: \u201c松弛自然的日常分享\n不端着\u201d,\n"
            "  }, /* trailing comma below */\n"
            "}\n"
            "```\n"
            "希望这份 {JSON} 对你有帮助！"
        )
        self.assertEqual(
            llm.parse_json_text(messy),
            {"persona_summary": {"one_line_essence_zh": "松弛自然的日常分享\n不端着"}},
        )

    def test_non_finite_numbers_and_deep_nesting_still_refused(self) -> None:
        for bad in ("NaN", "Infinity", "-Infinity", "1e999", "-1e999"):
            for wrapper in ('{"a": %s}' % bad, "```json\n{\"a\": %s,}\n```" % bad, "Prose {x}: {\"a\": %s}" % bad):
                with self.assertRaises(llm.LLMError) as ctx:
                    llm.parse_json_text(wrapper)
                self.assertEqual(ctx.exception.code, "bad_json")
        deep = '{"a":' * 1500 + "1" + "}" * 1500
        with self.assertRaises(llm.LLMError) as ctx2:
            llm.parse_json_text(deep)
        self.assertEqual(ctx2.exception.code, "bad_json")

    def test_truncated_json_is_not_mistaken_for_inner_subobject(self) -> None:
        cut_off = '{\n  "persona_summary": {"one_line_essence_zh": "松弛"},\n  "lexical_fingerprint": ['
        with self.assertRaises(llm.LLMError) as ctx:
            llm.parse_json_text(cut_off)
        self.assertEqual(ctx.exception.code, "bad_json")
        self.assertIn("完整", ctx.exception.message_zh)

    def test_top_level_arrays_in_parse_manual_reply_vs_parse_json_text(self) -> None:
        with self.assertRaises(llm.LLMError) as ctx:
            llm.parse_json_text("[1, 2, 3]")
        self.assertEqual(ctx.exception.code, "bad_json")

        draft_arr = '```json\n[{"line_no": 1, "spoken_script_zh": "今天去山姆补货嘛"},]\n```'
        self.assertEqual(
            llm.parse_manual_reply(draft_arr, "draft"),
            {"script_lines": [{"line_no": 1, "spoken_script_zh": "今天去山姆补货嘛"}]},
        )
        self.assertEqual(
            llm.parse_manual_reply('[{"title_zh": "山姆必买"}]', "ideas"),
            {"ideas": [{"title_zh": "山姆必买"}]},
        )
        self.assertEqual(
            llm.parse_manual_reply('[{"keyword": "山姆采购", "source_url": "https://example.com/1"}]', "trends"),
            {"keywords": [{"keyword": "山姆采购", "source_url": "https://example.com/1"}]},
        )
        self.assertEqual(
            llm.parse_manual_reply('[{"spoken_script_zh": "换个说法"}]', "suggest"),
            {"suggestions": [{"spoken_script_zh": "换个说法"}]},
        )
        with self.assertRaises(llm.LLMError):
            llm.parse_manual_reply("[1, 2, 3]", "draft")
        with self.assertRaises(llm.LLMError):
            llm.parse_manual_reply('[{"persona_summary": {}}]', "voice")

    def test_linear_time_on_large_hostile_inputs(self) -> None:
        big_comments = "// comment\n" * 10000 + '{"ok": 1,}'
        big_smart = "{\u201ca\u201d: \u201c" + ("好" * 80000) + "\u201d,}"
        big_prose = ("{not json} " * 10000) + '{"ok": true}'
        for payload in (big_comments, big_smart, big_prose):
            started = time.time()
            out = llm.parse_json_text(payload)
            self.assertIsInstance(out, dict)
            self.assertLess(time.time() - started, 1.5)


class ManualPipelineAndServerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, str(self.tmp), True)
        self.store = Store(self.tmp / "data")
        self.store.create_sample(title="周日山姆采购", text=HER_ORGANIC_SAMPLE)
        self.httpd = server.make_server(0, self.store)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
        self.thread.start()
        self.addCleanup(self._stop)

    def _stop(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)

    def post(self, path: str, payload: Dict[str, Any]):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=20)
        try:
            conn.request("POST", path, body=body, headers={"Content-Type": "application/json"})
            res = conn.getresponse()
            raw = res.read()
        finally:
            conn.close()
        return res.status, json.loads(raw.decode("utf-8"))

    def test_voice_wrappers_omitted_sections_and_list_blacklist_are_accepted(self) -> None:
        wrapped = {
            "result": {
                "voice_dna": {
                    "persona_summary": {
                        "one_line_essence_zh": "松弛自然的山姆采购日常",
                        "audience_address_terms": {"uses": ["咱们"], "never_uses": ["家人们"]},
                    },
                    "lexical_fingerprint": {
                        "signature_openers": [{"pattern_zh": "今天去…", "verbatim_sample": "今天去山姆补货嘛"}],
                    },
                    # cadence_and_syntax intentionally omitted!
                    # anti_voice_blacklist given as a flat list (common ChatGPT mistake)!
                    "anti_voice_blacklist": ["宝子们", "绝绝子"],
                }
            }
        }
        status, out = self.post("/api/manual/reply", {"task": "voice", "reply": json.dumps(wrapped, ensure_ascii=False)})
        self.assertEqual(status, 200, out)
        dna = out["pending"]["dna"]
        self.assertEqual(dna["persona_summary"]["one_line_essence_zh"], "松弛自然的山姆采购日常")
        self.assertEqual(dna["anti_voice_blacklist"]["banned_phrases_zh"], ["宝子们", "绝绝子"])
        self.assertEqual(dna["anti_voice_blacklist"]["learned_from_her_edits"], [])
        self.assertIn("target_speaking_rate_cpm", dna["cadence_and_syntax"])
        self.assertIn("ideal_clause_length_chars", dna["cadence_and_syntax"])

    def test_empty_or_unrelated_objects_still_raise_empty_result(self) -> None:
        for bad in ({}, {"foo": "bar"}, {"persona_summary": {}}, {"cadence_and_syntax": {}}):
            status, out = self.post("/api/manual/reply", {"task": "voice", "reply": json.dumps(bad)})
            self.assertEqual((status, out.get("error"), out.get("reason")), (502, "ai_failed", "empty_result"), bad)

    def test_cross_step_paste_gives_clear_bilingual_errors(self) -> None:
        # Pasting a Stage 4 script into Step 3 (voice)
        script_json = json.dumps({"script_lines": [{"spoken_script_zh": "今天去山姆补货嘛"}]})
        status, out = self.post("/api/manual/reply", {"task": "voice", "reply": script_json})
        self.assertEqual((status, out.get("error"), out.get("reason")), (502, "ai_failed", "wrong_step_voice"))
        self.assertIn("脚本", out.get("message_zh", ""))
        self.assertIn("script", out.get("message", "").lower())

        # Pasting a Step 3 Voice DNA profile into Step 4 (draft)
        voice_json = json.dumps({"persona_summary": {"one_line_essence_zh": "松弛自然"}})
        status, out = self.post("/api/manual/reply", {"task": "draft", "notes": "山姆采购", "reply": voice_json})
        self.assertEqual((status, out.get("error"), out.get("reason")), (502, "ai_failed", "wrong_step_draft"))
        self.assertIn("声音档案", out.get("message_zh", ""))
        self.assertIn("Voice DNA", out.get("message", ""))

    def test_draft_wrappers_line_aliases_top_level_array_and_text_too_long(self) -> None:
        # Wrapped under "draft" with "lines" and "spoken_zh" / "text" aliases, and empty notes (replyOnly import)
        reply_obj = {
            "draft": {
                "lines": [
                    {"spoken_zh": "今天去山姆补货嘛 / 结果排队排了半小时哈"},
                    {"text": "这个牛排真的超香啦"},
                ],
                "post_package": {
                    "title_options": [{"title_zh": "山姆周末采购清单", "title_en": "Sam's Club haul"}],
                },
            }
        }
        status, script = self.post("/api/manual/reply", {"task": "draft", "notes": "", "reply": json.dumps(reply_obj, ensure_ascii=False)})
        self.assertEqual(status, 201, script)
        self.assertEqual(script["title"], "山姆周末采购清单")
        self.assertEqual([l["spoken_script_zh"] for l in script["lines"]], [
            "今天去山姆补货嘛 / 结果排队排了半小时哈",
            "这个牛排真的超香啦",
        ])

        # Top-level array of line dicts
        arr_reply = '[{"voiceover_zh": "第一句口播"}, {"script_zh": "第二句口播"}]'
        status2, script2 = self.post("/api/manual/reply", {"task": "draft", "notes": "测试数组", "reply": arr_reply})
        self.assertEqual(status2, 201, script2)
        self.assertEqual(len(script2["lines"]), 2)

        # Oversized line returns 413 text_too_long, never 500
        huge_line = json.dumps({"script_lines": [{"spoken_script_zh": "好" * (store_module.MAX_LINE_CHARS + 10)}]})
        status3, err3 = self.post("/api/manual/reply", {"task": "draft", "notes": "超长", "reply": huge_line})
        self.assertEqual((status3, err3.get("error"), err3.get("limit")), (413, "text_too_long", store_module.MAX_LINE_CHARS))
        self.assertTrue(err3.get("message_zh"))


@unittest.skipUnless(CHROME_BIN.exists(), "Google Chrome is not installed on this machine")
class RealChromeManualJsonFlowTest(unittest.TestCase):
    """End-to-end headless Chrome verification of .json upload + paste in Step 3 -> Accept -> Step 4 .json upload + paste."""

    def test_step3_and_step4_json_file_upload_and_paste_in_chrome(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, str(tmp), True)
        store = Store(tmp / "data")
        store.save_creator({"name": "小满", "pronoun": "she", "niches": ["日常 Vlog"], "never": ["绝绝子"]})
        store.create_sample(title="周日山姆采购", text=HER_ORGANIC_SAMPLE, metrics={"views": 20000, "likes": 600})
        # Deliberately start WITHOUT AI configured yet, proving vo-import-json works immediately!

        voice_file = tmp / "voice_dna_chatgpt.json"
        voice_file.write_text(
            "\ufeff{\n"
            "  // Exported from ChatGPT Canvas and opened in macOS TextEdit\n"
            "  \u201cvoice_dna\u201d: {\n"
            "    \u201cpersona_summary\u201d: {\n"
            "      \u201cone_line_essence_zh\u201d: \u201c真实松弛的周末采购口吻\u201d,\n"
            "      \u201caudience_address_terms\u201d: {\u201cuses\u201d: [\u201c咱们\u201d], \u201cnever_uses\u201d: [\u201c家人们\u201d]},\n"
            "    },\n"
            "    \u201clexical_fingerprint\u201d: {\n"
            "      \u201csignature_openers\u201d: [{\u201cpattern_zh\u201d: \u201c今天去…\u201d, \u201cverbatim_sample\u201d: \u201c今天去山姆补货嘛\u201d}],\n"
            "    },\n"
            "    \u201canti_voice_blacklist\u201d: [\u201c宝子们\u201d],\n"
            "  },\n"
            "}\n",
            encoding="utf-8",
        )

        script_file = tmp / "first_script_chatgpt.json"
        script_file.write_text(
            "\ufeff[\n"
            "  // Top-level array of script lines with spoken_zh alias and trailing comma\n"
            "  {\u201cline_no\u201d: 1, \u201cspoken_zh\u201d: \u201c今天去山姆补货嘛 / 结果人真的超多哈\u201d,},\n"
            "  {\u201cline_no\u201d: 2, \u201cspoken_zh\u201d: \u201c我老公说\u201c这个牛排太香了\u201d，果断拿了两盒啦\u201d,},\n"
            "]\n",
            encoding="utf-8",
        )

        httpd = server.make_server(0, store, require_token=True)
        port = httpd.server_address[1]
        token = httpd.app.token
        thread = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
        thread.start()
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)

        udd = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, str(udd), True)
        proc = subprocess.Popen(
            [
                str(CHROME_BIN),
                "--headless=new",
                "--disable-gpu",
                "--no-first-run",
                "--no-default-browser-check",
                "--user-data-dir=" + str(udd),
                "--remote-debugging-port=0",
                "about:blank",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        def _stop_chrome() -> None:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=5)
            for pipe in (proc.stdout, proc.stderr):
                if pipe:
                    pipe.close()

        self.addCleanup(_stop_chrome)

        cdp_port = None
        for _ in range(80):
            line = proc.stderr.readline()
            m = re.search(r"ws://127\.0\.0\.1:(\d+)/devtools/browser/\S+", line)
            if m:
                cdp_port = int(m.group(1))
                break
        self.assertIsNotNone(cdp_port, "Chrome DevTools port did not start")

        with urllib.request.urlopen("http://127.0.0.1:%d/json" % cdp_port, timeout=10) as resp:
            targets = json.loads(resp.read().decode("utf-8"))
        page_ws = next(t["webSocketDebuggerUrl"] for t in targets if t.get("type") == "page")

        cdp = _CdpClient(page_ws)
        self.addCleanup(cdp.close)
        cdp.call("Page.enable")
        cdp.call("DOM.enable")
        cdp.call("Runtime.enable")

        cdp.call("Page.navigate", {"url": "http://127.0.0.1:%d/?token=%s" % (port, token)})
        cdp.wait_for(
            "typeof appState !== 'undefined' && Boolean(appState && appState.settings)"
            " && Boolean(document.getElementById('view')) && document.getElementById('view').innerHTML.length > 20"
        )

        # 1. Open Step 3 (Your Voice): verify #vo-import-json is available even before AI settings are configured
        cdp.eval_js(
            "(async () => { setupUi.open = 'voice';"
            " if (location.hash !== '#/start') { location.hash = '#/start'; } else { await renderSetup('voice'); }"
            " return true; })()"
        )
        cdp.wait_for("location.hash === '#/start' && Boolean(document.getElementById('vo-import-json'))")
        cdp.eval_js("document.getElementById('vo-import-json').click()")
        cdp.wait_for("Boolean(document.getElementById('mb-reply')) && !document.getElementById('modal').hidden")
        # replyOnly mode has no Step 1 prompt box
        self.assertIsNone(cdp.eval_js("document.getElementById('mb-prompt')"))

        # 2. Paste a wrong-step script JSON first -> verify persistent inline #mb-err banner stays visible!
        cdp.eval_js("""(() => {
            const area = document.getElementById('mb-reply');
            area.value = '{"script_lines": [{"spoken_script_zh": "今天去山姆补货嘛"}]}';
            document.getElementById('mb-done').click();
            return true;
        })()""")
        cdp.wait_for("!document.getElementById('mb-err').hidden && document.getElementById('mb-err').textContent.length > 5")
        err_banner = cdp.eval_js("document.getElementById('mb-err').textContent")
        self.assertIn("脚本", err_banner)

        # 3. Now select the messy voice_dna_chatgpt.json file via the real <input type="file" id="mb-file">!
        doc_root = cdp.call("DOM.getDocument")["root"]["nodeId"]
        file_node = cdp.call("DOM.querySelector", {"nodeId": doc_root, "selector": "#mb-file"})["nodeId"]
        cdp.call("DOM.setFileInputFiles", {"nodeId": file_node, "files": [str(voice_file)]})
        cdp.wait_for("document.getElementById('mb-file-name').textContent === 'voice_dna_chatgpt.json' && document.getElementById('mb-err').hidden")
        cdp.eval_js("document.getElementById('mb-done').click()")

        # Modal closes and Step 3 immediately renders the evidence-checked review (#rv-approve)!
        cdp.wait_for("document.getElementById('modal').hidden && Boolean(document.getElementById('rv-approve'))")
        cdp.eval_js("document.getElementById('rv-approve').click()")
        cdp.wait_for("!document.getElementById('rv-approve') && Boolean(document.getElementById('sc-import-json'))")

        active_dna, is_ex = store.load_dna()
        self.assertFalse(is_ex)
        self.assertEqual(active_dna["persona_summary"]["one_line_essence_zh"], "真实松弛的周末采购口吻")

        # 4. In Step 4 (First Script), click #sc-import-json and upload first_script_chatgpt.json!
        cdp.eval_js("document.getElementById('sc-import-json').click()")
        cdp.wait_for("Boolean(document.getElementById('mb-file')) && !document.getElementById('modal').hidden")
        doc_root2 = cdp.call("DOM.getDocument")["root"]["nodeId"]
        file_node2 = cdp.call("DOM.querySelector", {"nodeId": doc_root2, "selector": "#mb-file"})["nodeId"]
        cdp.call("DOM.setFileInputFiles", {"nodeId": file_node2, "files": [str(script_file)]})
        cdp.wait_for("document.getElementById('mb-file-name').textContent === 'first_script_chatgpt.json'")
        cdp.eval_js("document.getElementById('mb-done').click()")

        # Navigates straight into the script workspace (#/s/<id>) with the 2 spoken lines loaded!
        cdp.wait_for("location.hash.startsWith('#/s/') && document.querySelectorAll('.line .spoken').length === 2")
        spoken_lines = cdp.eval_js("Array.from(document.querySelectorAll('.line .spoken')).map(x => x.value)")
        self.assertEqual(
            spoken_lines,
            [
                "今天去山姆补货嘛 / 结果人真的超多哈",
                "我老公说“这个牛排太香了”，果断拿了两盒啦",
            ],
        )

        # 5. Also verify pasting fenced JSON with {prose} on #/new via #import-script-json!
        cdp.eval_js("location.hash = '#/new'")
        cdp.wait_for("Boolean(document.getElementById('import-script-json'))")
        cdp.eval_js("document.getElementById('import-script-json').click()")
        cdp.wait_for("Boolean(document.getElementById('mb-reply')) && !document.getElementById('modal').hidden")
        fenced_paste = (
            "Here is your {Stage 4} script:\n```json\n"
            '{"script_lines": [{"spoken_script_zh": "周末带大家逛Trader Joes啦"}], '
            '"rednote_post_package": {"title_options": [{"title_zh": "TJ周末采购"}]}}\n'
            "```\nEnjoy {filming}!"
        )
        cdp.eval_js(
            "(() => { const a = document.getElementById('mb-reply'); a.value = %s; a.dispatchEvent(new Event('input'));"
            " document.getElementById('mb-done').click(); return true; })()" % json.dumps(fenced_paste)
        )
        cdp.wait_for("location.hash.startsWith('#/s/') && document.getElementById('title') && document.getElementById('title').value === 'TJ周末采购'")
        self.assertEqual(len(store.list_scripts()), 2)

        # 6. Verify #/voice (#learn-import-json) AND drag-and-drop onto .sheet!
        cdp.eval_js("location.hash = '#/voice'")
        cdp.wait_for("Boolean(document.getElementById('learn-import-json'))")
        cdp.eval_js("document.getElementById('learn-import-json').click()")
        cdp.wait_for("Boolean(document.getElementById('mb-reply')) && !document.getElementById('modal').hidden")
        dropped_ok = cdp.eval_js("""(() => {
            const sheet = document.querySelector('#modal .sheet');
            const dt = new DataTransfer();
            const f = new File(['{"persona_summary": {"one_line_essence_zh": "拖拽导入的第二版声音"},}'], 'dragged_voice.json', { type: 'application/json' });
            dt.items.add(f);
            sheet.dispatchEvent(new DragEvent('dragenter', { bubbles: true, cancelable: true, dataTransfer: dt }));
            const hadActive = sheet.classList.contains('drop-active');
            sheet.dispatchEvent(new DragEvent('drop', { bubbles: true, cancelable: true, dataTransfer: dt }));
            return hadActive && !sheet.classList.contains('drop-active');
        })()""")
        self.assertTrue(dropped_ok)
        cdp.wait_for("document.getElementById('mb-file-name').textContent === 'dragged_voice.json'")
        cdp.eval_js("document.getElementById('mb-done').click()")
        cdp.wait_for("location.hash === '#/start' && Boolean(document.getElementById('rv-approve'))")
        self.assertEqual(store.load_pending()["dna"]["persona_summary"]["one_line_essence_zh"], "拖拽导入的第二版声音")


if __name__ == "__main__":
    unittest.main()
