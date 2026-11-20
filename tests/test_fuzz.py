"""Deterministic fast tests for tools/fuzz.py and parser regressions it uncovered."""
from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from studio import llm
from tools import fuzz


class FuzzHarnessTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="lmv-test-fuzz-"))

    def tearDown(self) -> None:
        shutil.rmtree(str(self.tmp), ignore_errors=True)

    def test_deterministic_seed_ten_cases_across_all_targets(self) -> None:
        targets = [name for name, t in fuzz.TARGETS.items() if not t.planted]
        self.assertGreaterEqual(len(targets), 14)
        out_dir = self.tmp / "run"
        summary = fuzz.run_suite(targets, cases=10, seed_val="1", limit=15.0, out_dir=out_dir, quiet=True)
        self.assertEqual(summary["findings"], [])
        for name in targets:
            self.assertEqual(summary["targets"][name]["inputs"], 10)
            self.assertEqual(summary["targets"][name]["faults"], 0)
        self.assertTrue((out_dir / "summary.json").is_file())

    def test_same_seed_produces_identical_inputs(self) -> None:
        for name in ("docx", "json_text", "model_replies", "request_bodies"):
            first = fuzz.pack(fuzz.TARGETS[name].make(fuzz.case("42", name, 3)))
            second = fuzz.pack(fuzz.TARGETS[name].make(fuzz.case("42", name, 3)))
            self.assertEqual(first, second)

    def test_oracles_catch_planted_raise_nan_bloat_and_replay(self) -> None:
        env = fuzz.Env()
        try:
            # 1. Unexpected exception raised.
            t_raise = fuzz.Target("plant_raise", "planted raise", lambda rng: {}, lambda e, p: 1 / 0, planted=True)
            res_raise = fuzz.examine(t_raise, env, {})
            self.assertEqual(res_raise["found"]["kind"], "raised")

            # 2. Non-JSON (NaN) output.
            t_nan = fuzz.Target("plant_nan", "planted nan", lambda rng: {}, lambda e, p: {"x": float("nan")}, planted=True)
            res_nan = fuzz.examine(t_nan, env, {})
            self.assertEqual(res_nan["found"]["kind"], "answer is not JSON")

            # 3. Answer far larger than input.
            t_big = fuzz.Target("plant_big", "planted bloat", lambda rng: "x", lambda e, p: "y" * 20000,
                                floor=1000, ratio=10, planted=True)
            res_big = fuzz.examine(t_big, env, "x")
            self.assertEqual(res_big["found"]["kind"], "answer far larger than the input")
        finally:
            env.close()

        # 4. Replay saved finding roundtrip in worker subprocess.
        finding_file = self.tmp / "finding.json"
        finding_file.write_text(json.dumps({
            "target": "json_text",
            "seed": "1",
            "index": 0,
            "found": None,
            "payload": {"text": '{"ok": 1}'},
        }), encoding="utf-8")
        replayed = fuzz.replay_finding(finding_file, limit=15.0)
        self.assertIsNone(replayed["found"])

    def test_parser_regressions_found_by_fuzzing(self) -> None:
        # 1. Unpaired UTF-16 surrogates in JSON are refused at the door, while valid paired emoji surrogates work.
        self.assertEqual(llm.loads_strict('"\\ud83d\\ude00"'), "\U0001f600")
        for bad in ('"\\ud800"', '"\\udc00"', '{"a": ["\\ud83d"]}', '{"\\ud800": 1}'):
            with self.assertRaises(ValueError, msg=bad):
                llm.loads_strict(bad)

        # 2. Malformed provider model lists raise LLMError("bad_response") instead of AttributeError / TypeError.
        for provider, bad_list in (("gemini", {"models": "oops"}), ("openai", {"data": 123}), ("local", {"data": "oops"})):
            for bad_payload in ("oops", [1, 2], bad_list):
                with self.assertRaises(llm.LLMError) as ctx:
                    llm.list_models(provider, "not-a-key", transport=lambda *a: bad_payload,
                                    base_url="http://127.0.0.1:11434/v1" if provider == "local" else None)
                self.assertEqual(ctx.exception.code, "bad_response")
        # Gemini model entry with float supportedGenerationMethods skips cleanly without TypeError.
        models = llm.list_models("gemini", "k", transport=lambda *a: {
            "models": [
                {"name": "models/bad", "supportedGenerationMethods": 1.5},
                {"name": "models/gemini-2.5-flash", "supportedGenerationMethods": ["generateContent"]},
            ]
        })
        self.assertEqual(models, ["gemini-2.5-flash"])

        # 3. Malformed provider completion shapes raise LLMError("bad_response") instead of AttributeError / TypeError.
        for bad_reply in ("oops", {"choices": [{"message": {"content": ["not-a-string"]}}]},
                          {"candidates": [{"content": {"parts": "not-a-list"}}]}):
            for provider in ("openai", "gemini"):
                with self.assertRaises(llm.LLMError) as ctx:
                    llm.complete_json(provider, "k", "gpt-4o", "sys", "usr", transport=lambda *a: bad_reply)
                self.assertEqual(ctx.exception.code, "bad_response")


if __name__ == "__main__":
    unittest.main()
