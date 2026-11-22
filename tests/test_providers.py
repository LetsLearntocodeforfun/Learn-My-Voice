"""Tests for AI provider clients, Anthropic Messages hardening, settings routes, and per-stage temperatures.

Every call uses a stand-in ``transport`` or a loopback HTTP stub on ``127.0.0.1`` under ``loopback_only()``:
no real API key is used and no network connection leaves this computer.
"""
from __future__ import annotations

import http.client
import http.server
import json
import os
import shutil
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

import re

from studio import llm, pipeline, rednote_analytics, server, suggest, video_ingest
from studio.jobs import JobError
from studio.store import CLOUD_PROVIDERS, EXAMPLE_DNA_PATH, PROVIDERS, ROOT, Store
from tests import browser
from tests.test_hardening import loopback_only


def _start_stub(handler_fn):
    seen = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def _handle(self):
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b""
            seen.append({
                "method": self.command,
                "path": self.path,
                "headers": {k.lower(): v for k, v in self.headers.items()},
                "body": raw.decode("utf-8", "replace"),
            })
            handler_fn(self)

        do_GET = do_POST = _handle

        def log_message(self, *args):
            pass

    srv = server.StudioServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, "http://127.0.0.1:%d" % srv.server_address[1], seen


class AnthropicClientHardeningTest(unittest.TestCase):
    """L1: Anthropic docstring honesty, model validation, stop_reason handling, max_tokens fallback, redirects, secrets."""

    def test_docstring_states_anthropic_was_written_from_memory_and_never_run_live(self) -> None:
        doc = llm.__doc__ or ""
        self.assertIn("anthropic", doc)
        self.assertIn("21 November 2026", doc)
        self.assertIn("NEVER run against the live", doc)
        self.assertNotIn("docs.anthropic.com", doc)

    def test_anthropic_model_validation_enforces_cloud_rules_with_bilingual_errors(self) -> None:
        for valid in ("claude-sonnet-4-5", "claude-3-5-haiku-20241022", "claude-sonnet-4-5@20250929"):
            self.assertEqual(llm.validate_model_name(valid, "anthropic"), valid)
        for bad in ("", "claude sonnet", "claude?x=1", "claude#frag", r"claude\x", "../claude", "a\nb", "x" * 201):
            with self.assertRaises(llm.LLMError) as ctx:
                llm.validate_model_name(bad, "anthropic")
            self.assertEqual(ctx.exception.code, "bad_model", bad)
            self.assertTrue(ctx.exception.message_zh, bad)

    def test_list_models_sends_headers_filters_invalid_entries_and_rejects_bad_shape(self) -> None:
        captured = {}

        def fake_transport(method, url, headers, body):
            captured.update(method=method, url=url, headers=headers, body=body)
            return {
                "data": [
                    {"id": "claude-sonnet-4-5"},
                    {"id": "claude-3-5-haiku-20241022"},
                    {"id": "claude-sonnet-4-5"},
                    {"id": "bad model with spaces"},
                    {"id": ""},
                    "not-a-dict",
                ]
            }

        models = llm.list_models("anthropic", "sk-ant-fake-key-0001", transport=fake_transport)
        self.assertEqual(models, ["claude-3-5-haiku-20241022", "claude-sonnet-4-5"])
        self.assertEqual(captured["method"], "GET")
        self.assertEqual(captured["url"], llm.ANTHROPIC_BASE + "/models?limit=100")
        self.assertEqual(captured["headers"], {"x-api-key": "sk-ant-fake-key-0001", "anthropic-version": llm.ANTHROPIC_VERSION})
        self.assertIsNone(captured["body"])

        for bad_shape in ("oops", {"data": None}, {"data": "not-a-list"}):
            with self.assertRaises(llm.LLMError) as ctx:
                llm.list_models("anthropic", "sk-ant-fake-key-0001", transport=lambda *a, s=bad_shape: s)
            self.assertEqual(ctx.exception.code, "bad_response")
            self.assertTrue(ctx.exception.message_zh)

    def test_stop_reason_truncation_and_refusal_raise_clear_bilingual_llm_error(self) -> None:
        # Truncated mid-JSON due to max_tokens: caught via stop_reason instead of cryptic JSON parse failure.
        def truncated_transport(method, url, headers, body):
            return {
                "stop_reason": "max_tokens",
                "content": [{"type": "text", "text": '{"script_lines": [{"spoken_script_zh": "半句'}],
            }

        with self.assertRaises(llm.LLMError) as trunc:
            llm.complete_json("anthropic", "sk-ant-fake-0001", "claude-sonnet-4-5", "sys", "usr", truncated_transport)
        self.assertEqual(trunc.exception.code, "cut_off")           # one code for one thing, whichever service cut the answer
        self.assertIn("max_tokens", str(trunc.exception))
        self.assertIn("max_tokens", trunc.exception.message_zh)

        # Explicit stop_reason == "refusal"
        def refusal_transport(method, url, headers, body):
            return {"stop_reason": "refusal", "content": []}

        with self.assertRaises(llm.LLMError) as ref:
            llm.complete_json("anthropic", "sk-ant-fake-0001", "claude-sonnet-4-5", "sys", "usr", refusal_transport)
        self.assertEqual(ref.exception.code, "refused")
        self.assertIn("refus", str(ref.exception).lower())
        self.assertIn("拒绝", ref.exception.message_zh)

        # Content block of type "refusal" or empty text content
        for reply, code in (
            ({"stop_reason": "end_turn", "content": [{"type": "refusal", "refusal": "Cannot comply"}]}, "refused"),
            ({"stop_reason": "end_turn", "content": []}, "bad_response"),
            ({"stop_reason": "end_turn", "content": [{"type": "thinking", "thinking": "..."}]}, "bad_response"),
        ):
            with self.assertRaises(llm.LLMError) as empty_err:
                llm.complete_json("anthropic", "sk-ant-fake-0001", "claude-sonnet-4-5", "sys", "usr", lambda *a, r=reply: r)
            self.assertEqual(empty_err.exception.code, code)
            self.assertTrue(empty_err.exception.message_zh)

    def test_max_tokens_400_retries_once_at_4096_and_never_loops(self) -> None:
        seen_max_tokens = []

        def fallback_ok_transport(method, url, headers, body):
            seen_max_tokens.append(body["max_tokens"])
            if body["max_tokens"] > 4096:
                raise llm.LLMError(
                    "HTTP 400: max_tokens: 8192 > 4096, which is the maximum allowed number of output tokens for claude-3-haiku",
                    "http",
                )
            return {"stop_reason": "end_turn", "content": [{"type": "text", "text": '{"ok": 1}'}]}

        out = llm.complete_json("anthropic", "sk-ant-fake-0001", "claude-3-haiku-20240307", "sys", "usr", fallback_ok_transport)
        self.assertEqual(out, {"ok": 1})
        self.assertEqual(seen_max_tokens, [8192, 4096])

        # If 4096 also fails with 400 max_tokens, it stops after the single retry and raises.
        attempts = []

        def always_400_transport(method, url, headers, body):
            attempts.append(body["max_tokens"])
            raise llm.LLMError("HTTP 400: max_tokens is still invalid", "http")

        with self.assertRaises(llm.LLMError) as ctx:
            llm.complete_json("anthropic", "sk-ant-fake-0001", "claude-3-haiku-20240307", "sys", "usr", always_400_transport)
        self.assertEqual(ctx.exception.code, "http")
        self.assertEqual(attempts, [8192, 4096])

        # Non-max_tokens 400 errors do NOT retry at all.
        non_token_attempts = []

        def bad_req_transport(method, url, headers, body):
            non_token_attempts.append(body["max_tokens"])
            raise llm.LLMError("HTTP 400: messages: roles must alternate", "http")

        with self.assertRaises(llm.LLMError):
            llm.complete_json("anthropic", "sk-ant-fake-0001", "claude-sonnet-4-5", "sys", "usr", bad_req_transport)
        self.assertEqual(non_token_attempts, [8192])

        # Named integer cap (< requested and >= 1024) on HTTP 400 or 422 is parsed instead of blindly using 4096,
        # while falling back to 4096 when no such integer is named in the message.
        parsed_caps = []

        def cap_6000_on_422(method, url, headers, body):
            parsed_caps.append(body["max_tokens"])
            if body["max_tokens"] > 6000:
                raise llm.LLMError("HTTP 422: max_tokens (8192) exceeds model limit of 6000 (min 1024) for claude-custom-2025", "http")
            return {"stop_reason": "end_turn", "content": [{"type": "text", "text": '{"cap": 6000}'}]}

        self.assertEqual(
            llm.complete_json("anthropic", "sk-ant-fake-0001", "claude-custom-2025", "sys", "usr", cap_6000_on_422),
            {"cap": 6000},
        )
        self.assertEqual(parsed_caps, [8192, 6000])

        fallback_caps = []

        def unnamed_cap_on_422(method, url, headers, body):
            fallback_caps.append(body["max_tokens"])
            if body["max_tokens"] > 4096:
                raise llm.LLMError("HTTP 422: max_tokens is too high for this model", "http")
            return {"stop_reason": "end_turn", "content": [{"type": "text", "text": '{"cap": 4096}'}]}

        self.assertEqual(
            llm.complete_json("anthropic", "sk-ant-fake-0001", "claude-3-haiku", "sys", "usr", unnamed_cap_on_422),
            {"cap": 4096},
        )
        self.assertEqual(fallback_caps, [8192, 4096])

    def test_wire_http_400_scrubs_sk_ant_keys_and_refuses_redirects(self) -> None:
        secret = "sk-ant-api03-FakeSecretKey987654321"

        def error_echo_handler(h):
            err_body = json.dumps({
                "type": "error",
                "error": {
                    "type": "authentication_error",
                    "message": "invalid x-api-key %s provided" % secret,
                },
            }).encode("utf-8")
            h.send_response(401)
            h.send_header("Content-Type", "application/json")
            h.send_header("Content-Length", str(len(err_body)))
            h.end_headers()
            h.wfile.write(err_body)

        err_srv, err_url, _ = _start_stub(error_echo_handler)
        self.addCleanup(err_srv.server_close)
        self.addCleanup(err_srv.shutdown)

        canary_srv, canary_url, canary_seen = _start_stub(
            lambda h: (h.send_response(200), h.end_headers(), h.wfile.write(b"{}"))
        )
        self.addCleanup(canary_srv.server_close)
        self.addCleanup(canary_srv.shutdown)

        with loopback_only(), mock.patch.object(llm, "ANTHROPIC_BASE", err_url + "/v1"):
            with self.assertRaises(llm.LLMError) as auth_err:
                llm.complete_json("anthropic", secret, "claude-sonnet-4-5", "sys", "usr")
            self.assertEqual(auth_err.exception.code, "http")
            self.assertNotIn(secret, str(auth_err.exception))
            self.assertIn("[REDACTED]", str(auth_err.exception))

        # Pattern-only scrubbing when headers are absent
        self.assertEqual(llm._redact_secrets("bad key sk-ant-short1 in log"), "bad key [REDACTED] in log")

        # Redirects on Anthropic endpoints are refused on every 3xx status and never leak the key
        for code in (301, 302, 303, 307, 308):
            r_srv, r_url, _ = _start_stub(
                lambda h, c=code: (
                    h.send_response(c),
                    h.send_header("Location", "%s/steal?k=%s" % (canary_url, secret)),
                    h.end_headers(),
                )
            )
            self.addCleanup(r_srv.server_close)
            self.addCleanup(r_srv.shutdown)
            with loopback_only(), mock.patch.object(llm, "ANTHROPIC_BASE", r_url + "/v1"):
                for call_fn in (
                    lambda: llm.list_models("anthropic", secret),
                    lambda: llm.complete_json("anthropic", secret, "claude-sonnet-4-5", "sys", "usr"),
                ):
                    with self.assertRaises(llm.LLMError) as redir_err:
                        call_fn()
                    self.assertEqual(redir_err.exception.code, "http")
                    self.assertNotIn(secret, str(redir_err.exception))
                    self.assertTrue(redir_err.exception.message_zh)
        self.assertEqual(canary_seen, [])

    def test_anthropic_web_search_stays_false_and_discover_skips_web_lookup_cleanly(self) -> None:
        self.assertFalse(llm.can_search("anthropic"))
        with self.assertRaises(llm.LLMError) as ctx:
            llm.search_json("anthropic", "sk-ant-fake-0001", "claude-sonnet-4-5", "prompt", transport=lambda *a: {})
        self.assertEqual(ctx.exception.code, "cannot_browse")
        self.assertTrue(ctx.exception.message_zh)


class ProviderIntegrationRoutesAndUiTest(unittest.TestCase):
    """Audit of CLOUD_PROVIDERS, public_settings, server settings routes, _require_ai, video_ingest, and Settings UI."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="lmv-providers-"))
        self.addCleanup(shutil.rmtree, str(self.tmp), True)
        self.env = mock.patch.dict(os.environ, {"STUDIO_KEYSTORE_FILE": str(self.tmp / "keystore.key")})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.store = Store(self.tmp / "data")
        self.calls = []

        def stand_in(method, url, headers, body):
            self.calls.append((method, url, headers, body))
            if method == "GET" and url.endswith("/models?limit=100"):
                return {"data": [{"id": "claude-sonnet-4-5"}, {"id": "claude-haiku-4-5"}]}
            if method == "POST" and url.endswith("/messages"):
                return {
                    "stop_reason": "end_turn",
                    "content": [{"type": "text", "text": json.dumps({
                        "ideas": [{"title_zh": "周末采购实话", "evidence_titles": ["样本一"]}],
                        "script_lines": [{"spoken_script_zh": "今天去超市买菜啦 / 顺便带了两盒草莓嘛"}],
                    }, ensure_ascii=False)}],
                }
            raise AssertionError("Unexpected call: %s %s" % (method, url))

        self.httpd = server.make_server(0, self.store, transport=stand_in)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True).start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)

    def api(self, method: str, path: str, body=None, raw_body: bytes = None, content_type: str = "application/json"):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            headers = {"Host": "127.0.0.1:%d" % self.port}
            payload = raw_body
            if body is not None:
                payload = json.dumps(body).encode("utf-8")
                headers["Content-Type"] = "application/json"
            elif raw_body is not None:
                headers["Content-Type"] = content_type
            elif method == "POST":
                payload = b"{}"
                headers["Content-Type"] = "application/json"
            conn.request(method, path, body=payload, headers=headers)
            res = conn.getresponse()
            data = json.loads(res.read().decode("utf-8"))
            return res.status, data
        finally:
            conn.close()

    def test_store_settings_routes_require_ai_discover_and_video_ingest_messaging(self) -> None:
        self.assertEqual(CLOUD_PROVIDERS, ("gemini", "openai", "anthropic"))
        self.assertEqual(PROVIDERS, ("gemini", "openai", "anthropic", "local"))

        # Before connecting, _require_ai refuses AI generation
        st0, err0 = self.api("POST", "/api/generate", {"notes": "买草莓"})
        self.assertEqual((st0, err0["error"]), (400, "ai_not_configured"))

        # Connect Anthropic models and verify public_settings never exposes raw key
        st_m, mod_out = self.api("POST", "/api/settings/models", {"provider": "anthropic", "key": "sk-ant-fake-key-7777"})
        self.assertEqual(st_m, 200, mod_out)
        self.assertEqual(mod_out["models"], ["claude-haiku-4-5", "claude-sonnet-4-5"])
        self.assertTrue(mod_out["settings"]["keys"]["anthropic"])
        self.assertFalse(mod_out["settings"]["configured"])  # model not saved yet
        self.assertNotIn("sk-ant-fake-key-7777", json.dumps(mod_out))

        # Invalid Anthropic model name in POST /api/settings returns bilingual 400 bad_model
        st_bad, bad_out = self.api("POST", "/api/settings", {"provider": "anthropic", "model": "bad model name"})
        self.assertEqual((st_bad, bad_out["error"]), (400, "bad_model"))
        self.assertTrue(bad_out.get("message_zh"))

        # Valid save configures Anthropic without needing to resend key
        st_ok, ok_out = self.api("POST", "/api/settings", {"provider": "anthropic", "model": "claude-sonnet-4-5"})
        self.assertEqual(st_ok, 200, ok_out)
        self.assertTrue(ok_out["settings"]["configured"])
        self.assertEqual(ok_out["settings"]["provider"], "anthropic")

        # GET /api/state reports lookup_available == False for Anthropic
        _, state = self.api("GET", "/api/state")
        self.assertFalse(state["lookup_available"])

        # _require_ai passes and POST /api/generate drafts via Anthropic
        self.store.create_sample(title="样本一", text="今天去超市买菜啦 / 买了两盒草莓嘛")
        st_gen, script = self.api("POST", "/api/generate", {"notes": "买草莓"})
        self.assertEqual(st_gen, 201, script)
        self.assertEqual(script["lines"][0]["spoken_script_zh"], "今天去超市买菜啦 / 顺便带了两盒草莓嘛")

        # pipeline.discover skips web lookup with no_web_search and still completes ideas via Anthropic
        disc = pipeline.discover(self.store, focus="超市日常", transport=self.httpd.app.transport)
        self.assertEqual(disc["lookup"]["status"], pipeline.LOOKUP_SKIPPED)
        self.assertEqual(disc["lookup"]["reason"], "no_web_search")
        self.assertEqual(disc["ideas"]["status"], "done")

        # video_ingest explains plainly why Anthropic (and local) do not take direct video/audio uploads
        info = video_ingest.ai_video_info(self.store)
        self.assertEqual(info, {"ai_video_capable": False, "ai_video_provider": ""})
        st_up, up = self.api("PUT", "/api/uploads?name=clip.mp4", raw_body=b"fakevideo", content_type="application/octet-stream")
        self.assertEqual(st_up, 201)
        st_vi, vi_err = self.api("POST", "/api/videos/ai", {"upload_id": up["upload_id"]})
        self.assertEqual((st_vi, vi_err["error"]), (400, "ai_video_not_configured"))
        self.assertIn("Anthropic Claude", vi_err["message"])
        self.assertIn("Anthropic Claude", vi_err["message_zh"])

        # Direct call to transcribe_via_ai also cleans up temp file and reports Anthropic-specific message
        tmp_clip = self.tmp / "temp_clip.mp4"
        tmp_clip.write_bytes(b"fakevideo")
        with self.assertRaises(JobError) as j_err:
            video_ingest.transcribe_via_ai(self.store, {"path": str(tmp_clip), "name": "clip.mp4"})
        self.assertIn("Anthropic Claude", str(j_err.exception))
        self.assertIn("Anthropic Claude", j_err.exception.message_zh)
        self.assertFalse(tmp_clip.exists())

    @unittest.skipUnless(browser.AVAILABLE, browser.WHY_NOT)
    def test_settings_ui_renders_anthropic_honesty_hint_in_zh_en_and_light_dark(self) -> None:
        self.store.update_config(lambda c: (
            c.update(provider="anthropic", model="claude-sonnet-4-5"),
            c["keys"].update(anthropic="sk-ant-fake-key-7777"),
        ))
        for lang_code, theme, expected_phrase in (
            ("zh", "light", "尚未连接真实 Anthropic 服务实测"),
            ("en", "dark", "not yet verified against the live Anthropic service"),
        ):
            with browser.open_app(self.store, lang=lang_code, theme=theme) as page:
                page.go("#/settings", "Boolean(document.getElementById('anthropic-hint'))")
                hint = page.eval_js("document.getElementById('anthropic-hint').textContent")
                self.assertIn(expected_phrase, hint)
                # Switching to OpenAI and back to Anthropic keeps the saved Anthropic model selected
                page.eval_js("document.querySelector('[data-provider=\"openai\"]').click()")
                page.eval_js("document.querySelector('[data-provider=\"anthropic\"]').click()")
                selected = page.eval_js("document.getElementById('model').value")
                self.assertEqual(selected, "claude-sonnet-4-5")
                self.assertEqual(page.problems, [])


class StageTemperatureAndFallbackTest(unittest.TestCase):
    """Task 2: per-stage temperature table aligned with prompts/, passed on all providers, with 400 retry-once."""

    def test_stage_temperature_table_matches_prompt_blueprints(self) -> None:
        prompts_dir = ROOT / "prompts"
        expected_in_files = (
            ("02_voice_dna_synthesizer.md", "voice", 0.2),
            ("03_sponsored_brief_translator.md", "sponsored_brief", 0.3),
            ("04_script_generator_normal_and_sponsored.md", "draft", 0.55),
            ("05_de_ai_critic_and_diff_learner.md", "critic", 0.2),
        )
        for filename, stage_key, expected in expected_in_files:
            text = (prompts_dir / filename).read_text(encoding="utf-8")
            match = re.search(r"temperature\s*=\s*([0-9.]+)", text)
            self.assertIsNotNone(match, filename)
            self.assertEqual(float(match.group(1)), expected, filename)
            self.assertEqual(llm.STAGE_TEMPERATURES[stage_key], expected)
            self.assertEqual(pipeline.STAGE_TEMPERATURES[stage_key], expected)

        self.assertEqual(llm.STAGE_TEMPERATURES["ideas"], 0.3)
        self.assertEqual(llm.STAGE_TEMPERATURES["shorten"], 0.3)
        self.assertEqual(llm.STAGE_TEMPERATURES["hooks"], 0.55)
        self.assertEqual(llm.STAGE_TEMPERATURES["rewrite"], 0.55)
        self.assertEqual(llm.STAGE_TEMPERATURES["sentiment"], 0.2)
        self.assertEqual(llm.STAGE_TEMPERATURES["comment_ocr"], 0.1)

    def test_pipeline_and_suggest_pass_stage_temperatures_including_local_server(self) -> None:
        tmp = Path(tempfile.mkdtemp(prefix="lmv-temps-"))
        self.addCleanup(shutil.rmtree, str(tmp), True)
        store = Store(tmp / "data")
        store.update_config(lambda c: c.update(
            provider="local", model="qwen2.5:14b", local_base_url="http://127.0.0.1:11434/v1"
        ))
        sample = store.create_sample(title="样本A", text="今天去超市买菜啦 / 买了两盒草莓嘛")
        store.add_sample_comments(sample["id"], [{"text": "求链接！干皮能用吗？", "likes": 5, "is_question": True}])
        example_dna = json.loads(EXAMPLE_DNA_PATH.read_text(encoding="utf-8"))

        recorded_temps = []

        def local_stand_in(method, url, headers, body):
            self.assertEqual(url, "http://127.0.0.1:11434/v1/chat/completions")
            recorded_temps.append(body.get("temperature"))
            return {
                "choices": [{
                    "message": {
                        "content": json.dumps({
                            "persona_summary": example_dna["persona_summary"],
                            "cadence_and_syntax": example_dna["cadence_and_syntax"],
                            "anti_voice_blacklist": example_dna["anti_voice_blacklist"],
                            "script_lines": [{"spoken_script_zh": "今天去超市买菜啦 / 顺手拿了两盒草莓嘛"}],
                            "ideas": [{"title_zh": "周末超市第二期", "evidence_titles": ["样本A"]}],
                            "suggestions": [{"line_no": 1, "spoken_script_zh": "今天顺路去超市买菜啦", "why_zh": "更顺口", "why_en": "Smoother"}],
                            "overall_sentiment": "positive",
                            "summary_zh": "好评",
                            "summary_en": "Positive",
                        }, ensure_ascii=False)
                    }
                }]
            }

        pipeline.learn_voice(store, transport=local_stand_in)
        script = pipeline.generate_script(store, notes="买草莓", target_sec=30, video_format="VLOG_VOICEOVER", transport=local_stand_in)
        pipeline.recommend_topics(store, transport=local_stand_in)
        pipeline.discover(store, focus="买菜", transport=local_stand_in)
        suggest.suggest(store, script["id"], suggest.HOOKS, 0, transport=local_stand_in)
        suggest.suggest(store, script["id"], suggest.REWRITE, 0, transport=local_stand_in)
        suggest.suggest(store, script["id"], suggest.SHORTEN, 0, transport=local_stand_in)
        suggest.suggest(store, script["id"], suggest.CRITIC, transport=local_stand_in)
        rednote_analytics.analyze_sample_sentiment_ai(store, sample["id"], transport=local_stand_in)

        self.assertEqual(recorded_temps, [0.2, 0.55, 0.3, 0.3, 0.55, 0.55, 0.3, 0.2, 0.2])

    def test_http_400_mentioning_temperature_retries_once_without_temperature_across_all_providers(self) -> None:
        # 1. OpenAI reasoning model rejecting temperature
        oai_bodies = []

        def oai_transport(method, url, headers, body):
            oai_bodies.append(dict(body))
            if "temperature" in body:
                raise llm.LLMError(
                    "HTTP 400: Unsupported parameter: 'temperature' is not supported with this model.",
                    "http",
                )
            return {"choices": [{"message": {"content": '{"provider": "openai"}'}}]}

        self.assertEqual(
            llm.complete_json("openai", "sk-fake-00000000", "o3-mini", "sys", "usr", oai_transport, temperature=0.55),
            {"provider": "openai"},
        )
        self.assertEqual([b.get("temperature") for b in oai_bodies], [0.55, None])
        self.assertNotIn("temperature", oai_bodies[1])

        # 2. Local server rejecting temperature
        local_bodies = []

        def local_transport(method, url, headers, body):
            local_bodies.append(dict(body))
            if "temperature" in body:
                raise llm.LLMError("HTTP 400: temperature is not supported for reasoning mode", "http")
            return {"choices": [{"message": {"content": '{"provider": "local"}'}}]}

        self.assertEqual(
            llm.complete_json("local", "", "deepseek-r1", "sys", "usr", local_transport,
                              base_url="http://127.0.0.1:11434/v1", temperature=0.3),
            {"provider": "local"},
        )
        self.assertEqual([b.get("temperature") for b in local_bodies], [0.3, None])

        # 3. Gemini rejecting temperature in generationConfig
        gem_cfgs = []

        def gem_transport(method, url, headers, body):
            gem_cfgs.append(dict(body["generationConfig"]))
            if "temperature" in body["generationConfig"]:
                raise llm.LLMError("HTTP 400: Invalid value at 'generation_config.temperature'", "http")
            return {"candidates": [{"content": {"parts": [{"text": '{"provider": "gemini"}'}]}}]}

        self.assertEqual(
            llm.complete_json("gemini", "AIzaSyFake0000000000", "gemini-2.5-pro", "sys", "usr", gem_transport, temperature=0.2),
            {"provider": "gemini"},
        )
        self.assertEqual(gem_cfgs, [{"responseMimeType": "application/json", "temperature": 0.2},
                                     {"responseMimeType": "application/json"}])

        # 4. Anthropic rejecting temperature (and sequential max_tokens + temperature)
        ant_bodies = []

        def ant_transport(method, url, headers, body):
            ant_bodies.append(dict(body))
            if "temperature" in body:
                raise llm.LLMError("HTTP 400: `temperature` may only be set to 1 when thinking is enabled", "http")
            return {"stop_reason": "end_turn", "content": [{"type": "text", "text": '{"provider": "anthropic"}'}]}

        self.assertEqual(
            llm.complete_json("anthropic", "sk-ant-fake-0001", "claude-sonnet-4-5", "sys", "usr", ant_transport, temperature=0.55),
            {"provider": "anthropic"},
        )
        self.assertEqual([b.get("temperature") for b in ant_bodies], [0.55, None])

        both_bodies = []

        def ant_both_transport(method, url, headers, body):
            both_bodies.append((body.get("max_tokens"), body.get("temperature")))
            if body.get("max_tokens") > 4096:
                raise llm.LLMError("HTTP 400: max_tokens: 8192 > 4096", "http")
            if "temperature" in body:
                raise llm.LLMError("HTTP 400: temperature is not supported", "http")
            return {"stop_reason": "end_turn", "content": [{"type": "text", "text": '{"both": true}'}]}

        self.assertEqual(
            llm.complete_json("anthropic", "sk-ant-fake-0001", "claude-3-haiku", "sys", "usr", ant_both_transport, temperature=0.2),
            {"both": True},
        )
        self.assertEqual(both_bodies, [(8192, 0.2), (4096, 0.2), (4096, None)])

        # 5. Never loops when the second attempt also fails with HTTP 400 mentioning temperature
        for provider, key, model, base_url in (
            ("openai", "sk-fake-00000000", "o3", None),
            ("gemini", "AIzaSyFake0000000000", "gemini-2.5-pro", None),
            ("anthropic", "sk-ant-fake-0001", "claude-sonnet-4-5", None),
            ("local", "", "local-model", "http://127.0.0.1:11434/v1"),
        ):
            tries = []

            def always_reject_temp(method, url, headers, body, tries=tries):
                tries.append(1)
                raise llm.LLMError("HTTP 400: temperature is rejected even when omitted", "http")

            with self.assertRaises(llm.LLMError) as ctx:
                llm.complete_json(provider, key, model, "sys", "usr", always_reject_temp, base_url=base_url, temperature=0.3)
            self.assertEqual(ctx.exception.code, "http", provider)
            self.assertEqual(len(tries), 2, provider)

        # 6. End-to-end wire HTTP 400 JSON fallback over loopback local server
        def wire_temp_reject(h):
            body = json.loads(h.rfile_last if hasattr(h, "rfile_last") else "{}")  # unused; inspect seen[-1]
            req = json.loads(seen[-1]["body"])
            if "temperature" in req:
                err = b'{"error":{"message":"Unsupported parameter: temperature"}}'
                h.send_response(400)
                h.send_header("Content-Type", "application/json")
                h.send_header("Content-Length", str(len(err)))
                h.end_headers()
                h.wfile.write(err)
            else:
                ok = b'{"choices":[{"message":{"content":"{\\"wire\\":true}"}}]}'
                h.send_response(200)
                h.send_header("Content-Type", "application/json")
                h.send_header("Content-Length", str(len(ok)))
                h.end_headers()
                h.wfile.write(ok)

        srv, base, seen = _start_stub(wire_temp_reject)
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        with loopback_only():
            res = llm.complete_json("local", "", "qwen-reasoning", "sys", "usr", base_url=base + "/v1", temperature=0.55)
        self.assertEqual(res, {"wire": True})
        self.assertEqual(len(seen), 2)
        self.assertEqual(json.loads(seen[0]["body"]).get("temperature"), 0.55)
        self.assertNotIn("temperature", json.loads(seen[1]["body"]))

        # 7. HTTP 422 temperature retry-once works on complete_json and rednote_analytics._vision_json
        picture = [("aGVsbG8=", "image/png")]
        for provider in ("gemini", "openai", "anthropic", "local"):
            vis_calls = []

            def vis_422(method, url, headers, body, prov=provider, vis_calls=vis_calls):
                vis_calls.append(dict(body))
                has_temp = ("temperature" in body["generationConfig"]) if prov == "gemini" else ("temperature" in body)
                if prov == "anthropic" and body.get("max_tokens", 0) > 3072:
                    raise llm.LLMError("HTTP 422: max_tokens must be <= 3072", "http")
                if has_temp:
                    raise llm.LLMError("HTTP 422: temperature is not supported by this model", "http")
                return _answer(prov, '{"comments": [{"text": "好用！", "likes": 3}]}')

            vis_out = rednote_analytics._vision_json(
                provider, "sk-fake-00000000", "model-v", picture, transport=vis_422,
                base_url="http://127.0.0.1:11434/v1" if provider == "local" else None,
            )
            self.assertEqual(vis_out["comments"][0]["text"], "好用！")
            if provider == "gemini":
                self.assertEqual(vis_calls[0]["generationConfig"].get("temperature"), 0.1)
                self.assertNotIn("temperature", vis_calls[-1]["generationConfig"])
            elif provider == "anthropic":
                self.assertEqual([(c.get("max_tokens"), c.get("temperature")) for c in vis_calls],
                                 [(8192, 0.1), (3072, 0.1), (3072, None)])
            else:
                self.assertEqual(vis_calls[0].get("temperature"), 0.1)
                self.assertNotIn("temperature", vis_calls[-1])


# A draft reply that stops inside its second line. Its first line stands whole inside it.
CUT_DRAFT = ('{"title": "周末", "script_lines": [{"line_no": 1, "spoken_script_zh": "第一句"}, '
             '{"line_no": 2, "spoken_script_zh": "第二')
FIRST_LINE = {"line_no": 1, "spoken_script_zh": "第一句"}
WHOLE = {"title": "周末", "script_lines": [FIRST_LINE]}
PASTE_ADVICE = ("ChatGPT", "Copy the full reply", "pasted", "复制")


def _answer(provider, text, why=None):
    """A made-up answer of one service holding ``text``, ended for the reason ``why`` (that service's own word for it)."""
    if provider == "gemini":
        return {"candidates": [dict({"content": {"parts": [{"text": text}]}}, **({"finishReason": why} if why else {}))]}
    if provider == "anthropic":
        return dict({"content": [{"type": "text", "text": text}]}, **({"stop_reason": why} if why else {}))
    return {"choices": [dict({"message": {"content": text}}, **({"finish_reason": why} if why else {}))]}


def _ask(provider, answer):
    return llm.complete_json(provider, "sk-NOTAREALKEY000000", "model-x", "s", "u", transport=lambda *a: answer,
                             base_url="http://127.0.0.1:11434/v1" if provider == "local" else None)


class StoppedAndCutOffAnswersTest(unittest.TestCase):
    """An answer that was cut off or declined is never read as JSON, and never comes back as a part of itself.

    The fields that say why an answer stopped are from memory (``llm.GEMINI_UNCHECKED``, ``llm.OPENAI_UNCHECKED``):
    these tests show what the code does with such an answer, not that a service sends one.
    """

    CUT = {"gemini": "MAX_TOKENS", "openai": "length", "local": "length", "anthropic": "max_tokens"}

    def assert_said(self, error, code):
        self.assertEqual(error.code, code, str(error))
        self.assertTrue(re.search(r"[\u4e00-\u9fff]", error.message_zh), "no Chinese message")
        for advice in PASTE_ADVICE:                     # nothing was pasted: no advice to copy it again
            self.assertNotIn(advice, str(error) + error.message_zh)

    def test_an_answer_the_service_says_it_cut_off_is_refused_in_two_languages_whatever_arrived(self) -> None:
        for provider, why in self.CUT.items():
            for text in (CUT_DRAFT, json.dumps(WHOLE, ensure_ascii=False), "{"):
                with self.subTest(provider=provider, text=text[:20]), self.assertRaises(llm.LLMError) as caught:
                    _ask(provider, _answer(provider, text, why))
                self.assert_said(caught.exception, "cut_off")

    def test_a_cut_off_answer_whose_service_did_not_say_so_is_still_never_taken_for_its_first_line(self) -> None:
        for provider in ("gemini", "openai", "local", "anthropic"):
            for why in (None, {"gemini": "STOP", "anthropic": "end_turn"}.get(provider, "stop")):
                with self.subTest(provider=provider, why=why), self.assertRaises(llm.LLMError) as caught:
                    _ask(provider, _answer(provider, CUT_DRAFT, why))
                self.assert_said(caught.exception, "cut_off")
        with self.assertRaises(llm.LLMError) as caught:      # the web lookup reads its answer the same way
            llm.search_json("openai", "sk-NOTAREALKEY000000", "model-x", "p", transport=lambda *a: {
                "output": [{"type": "message", "content": [{"type": "output_text", "text": CUT_DRAFT}]}]})
        self.assert_said(caught.exception, "cut_off")

    def test_an_answer_the_service_declined_is_refused_in_two_languages(self) -> None:
        declined = [("gemini", {"candidates": [{"finishReason": reason}]}) for reason in ("SAFETY", "RECITATION", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII")]
        declined += [("gemini", {"promptFeedback": {"blockReason": "SAFETY"}}), ("gemini", {"candidates": [], "promptFeedback": {"blockReason": "OTHER"}}),
                     ("openai", _answer("openai", "", "content_filter")), ("local", _answer("local", "", "content_filter")),
                     ("openai", {"choices": [{"message": {"content": None, "refusal": "I can't."}, "finish_reason": "stop"}]}),
                     ("anthropic", _answer("anthropic", "I can't help with that.", "refusal"))]
        for provider, answer in declined:
            with self.subTest(provider=provider, answer=answer), self.assertRaises(llm.LLMError) as caught:
                _ask(provider, answer)
            self.assert_said(caught.exception, "refused")

    def test_a_whole_answer_is_read_as_before_also_with_words_or_a_fence_around_it(self) -> None:
        whole = json.dumps(WHOLE, ensure_ascii=False)
        for provider, why in (("gemini", "STOP"), ("gemini", None), ("openai", "stop"), ("openai", None), ("local", None), ("anthropic", "end_turn")):
            for text in (whole, "Here is the JSON:\n" + whole + "\nHope this helps!", "```json\n" + whole + "\n```"):
                with self.subTest(provider=provider, text=text[:12]):
                    self.assertEqual(_ask(provider, _answer(provider, text, why)), WHOLE)
        for shape in ({}, [], None, {"candidates": "x"}, {"choices": [{}]}, {"choices": [{"message": {"content": 5}}]}, {"finishReason": {"a": 1}}):
            for provider in ("gemini", "openai", "local"):
                with self.subTest(provider=provider, shape=shape), self.assertRaises(llm.LLMError) as caught:
                    _ask(provider, shape)
                self.assertEqual(caught.exception.code, "bad_response")

    def test_a_pasted_reply_that_was_copied_incompletely_is_refused_with_the_advice_to_copy_it_again(self) -> None:
        fenced = "```json\n" + CUT_DRAFT
        ideas = '{"ideas": [{"title_zh": "第一个"}, {"title_zh": "第二'
        for read in (lambda: llm.parse_manual_reply(CUT_DRAFT, "draft"), lambda: llm.parse_json_text(CUT_DRAFT),
                     lambda: llm.parse_manual_reply(fenced, "draft"), lambda: llm.parse_manual_reply("好的，脚本如下：\n" + CUT_DRAFT, "draft"),
                     lambda: llm.parse_manual_reply(ideas, "ideas"), lambda: llm.parse_json_any(ideas, allow_array=True)):
            with self.assertRaises(llm.LLMError) as caught:
                read()
            self.assertEqual(caught.exception.code, "bad_json")
            self.assertIn("Copy the full reply again", str(caught.exception))
            self.assertIn("重新完整复制", caught.exception.message_zh)

    def test_only_what_stands_inside_something_that_never_closes_is_left_and_a_stray_bracket_in_the_words_around_changes_nothing(self) -> None:
        whole = json.dumps(WHOLE, ensure_ascii=False)
        for text in ("See [draft {v2) and :-{ below.\n" + whole,              # brackets in prose that never close
                     "note [1: " + whole, whole + '\n{"more": [{"a": 1}, {"b',  # a whole reply, then something cut off
                     'He said "use { here" then:\n' + whole, whole + " }]"):
            with self.subTest(text=text[:24]):
                self.assertEqual(llm.parse_manual_reply(text, "draft"), WHOLE)
        self.assertEqual(llm._unclosed_from(CUT_DRAFT), 0)
        self.assertEqual(llm._unclosed_from('words {"a": [1, 2'), 6)
        for closed_or_prose in (whole, "[see note", ":-{", '"{" in quotes', "", '{"a": 1} [x'):
            self.assertIsNone(llm._unclosed_from(closed_or_prose), closed_or_prose)

    def test_a_video_reply_and_a_screenshot_reply_are_read_the_same_way(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, str(tmp), True)
        store = Store(tmp / "data")
        store.update_config(lambda c: (c.update(provider="gemini", model="gemini-x"), c["keys"].update(gemini="AIzaNOTAREALKEY000000")))
        cut_video = '{"beats": [{"beat_index": 1, "beat_type": "HOOK", "audio_mode": "VOICEOVER", "verbatim_transcript_zh_en": "开头"}, {"beat_index": 2, "verb'
        for answer, code in ((_answer("gemini", cut_video, "MAX_TOKENS"), "cut_off"), (_answer("gemini", cut_video, "STOP"), "cut_off"),
                             ({"promptFeedback": {"blockReason": "SAFETY"}}, "refused")):
            clip = tmp / "clip.mp4"
            clip.write_bytes(b"\x00\x00\x00\x18ftypmp42")
            with self.subTest(code=code), self.assertRaises(JobError) as caught:
                video_ingest.transcribe_via_ai(store, {"path": str(clip), "name": "clip.mp4"}, transport=lambda *a: answer)
            self.assertEqual(caught.exception.code, code)
            self.assertTrue(caught.exception.message_zh)
        self.assertEqual(store.list_samples(), [])              # the first beat alone was not stored as the video
        picture = [("aGVsbG8=", "image/png")]
        for provider, why in self.CUT.items():
            with self.subTest(provider=provider), self.assertRaises(llm.LLMError) as caught:
                rednote_analytics._vision_json(provider, "k", "model-x", picture, transport=lambda *a: _answer(provider, CUT_DRAFT, why),
                                               base_url="http://127.0.0.1:11434/v1" if provider == "local" else None)
            self.assert_said(caught.exception, "cut_off")

    def test_the_fields_these_rest_on_are_listed_as_never_checked(self) -> None:
        for listed, names in (
            (llm.GEMINI_UNCHECKED, ("finishReason", "MAX_TOKENS", "promptFeedback.blockReason")),
            (llm.OPENAI_UNCHECKED, ("finish_reason", "'length'", "content_filter", "message.refusal")),
            (llm.ANTHROPIC_UNCHECKED, ("stop_reason", "max_tokens", "refusal")),
        ):
            for name in names:
                self.assertIn(name, " ".join(listed))
        self.assertEqual(
            llm.PROVIDER_STOP_REASON_UNCHECKED,
            llm.GEMINI_UNCHECKED + llm.OPENAI_UNCHECKED + llm.ANTHROPIC_UNCHECKED,
        )


if __name__ == "__main__":
    unittest.main()
