"""Regression tests for at-rest key sealing, failure handling, and script/library ceilings.

Covers reviewer reproducers secrets-01 through secrets-06 (r01, r02, r05, r06, r07) plus test isolation.
"""
from __future__ import annotations

import http.client
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from studio import keystore, mcp_server, server
from studio.store import (
    MAX_LINE_CHARS,
    MAX_NOTES_CHARS,
    MAX_POST_CHARS,
    MAX_SAMPLE_CHARS,
    MAX_SCRIPT_LINES,
    MAX_SCRIPT_TITLE_CHARS,
    ROOT,
    Store,
    TextTooLong,
)


class IsolatedKeystoreCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="lmv-keys-test-"))
        self.addCleanup(shutil.rmtree, str(self.tmp), True)
        self.key_file = self.tmp / "os_cfg" / "keystore.key"
        self._env = mock.patch.dict(os.environ, {
            "STUDIO_KEYSTORE_FILE": str(self.key_file),
            "STUDIO_DATA_DIR": str(self.tmp / "data"),
        })
        self._env.start()
        self.addCleanup(self._env.stop)


class TestSuiteKeystoreIsolationTest(unittest.TestCase):
    def test_importing_tests_package_without_env_sets_temp_keystore_path(self) -> None:
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
        env.pop("STUDIO_KEYSTORE_FILE", None)
        code = (
            "import os, tests; from studio import keystore; "
            "p = str(keystore._key_path()); print(p); "
            "assert 'lmv-test-keystore-' in p, p"
        )
        done = subprocess.run(
            [sys.executable, "-B", "-c", code],
            cwd=str(ROOT), env=env, capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("lmv-test-keystore-", done.stdout)


class MasterKeySafetyAndFallbackTest(IsolatedKeystoreCase):
    """secrets-02 & secrets-03 (r01, r02): never overwrite damaged/unreadable key file; atomic creation; no ephemeral loss."""

    @unittest.skipIf(os.name == "nt", "POSIX file mode 000 test")
    def test_r01a_unreadable_key_file_is_never_overwritten_and_recovers(self) -> None:
        store = Store(self.tmp / "data")
        store.update_config(lambda c: (c["keys"].update(gemini="FAKE-gemini-0001"), c.update(provider="gemini", model="gemini-2.5-pro")))
        original_key = self.key_file.read_bytes()
        self.assertEqual(len(original_key), 32)

        os.chmod(str(self.key_file), 0o000)
        self.addCleanup(lambda: self.key_file.exists() and os.chmod(str(self.key_file), 0o600))

        # Reading must NOT replace keystore.key and must report gemini as unreadable.
        loaded = store.load_config()
        self.assertEqual(loaded["keys"], {})
        pub = store.public_settings()
        self.assertEqual(pub["keys_unreadable"], ["gemini"])
        self.assertFalse(pub["configured"])

        # Switching UI language/theme (keep_for_page) must NOT erase keys_sealed on disk.
        store.keep_for_page({"lang": "en"})
        disk = json.loads(store.config_path.read_text(encoding="utf-8"))
        self.assertIn("gemini", disk.get("keys_sealed", {}))

        # Repairing permissions restores the sealed key intact because keystore.key was never overwritten.
        os.chmod(str(self.key_file), 0o600)
        self.assertEqual(self.key_file.read_bytes(), original_key)
        self.assertEqual(store.load_config()["keys"], {"gemini": "FAKE-gemini-0001"})
        self.assertNotIn("keys_unreadable", store.public_settings())

    def test_r01c_truncated_key_file_is_never_overwritten_and_recovers(self) -> None:
        store = Store(self.tmp / "data")
        store.update_config(lambda c: c["keys"].update(gemini="FAKE-gemini-trunc"))
        original_key = self.key_file.read_bytes()
        self.key_file.write_bytes(original_key[:31])

        self.assertEqual(store.load_config()["keys"], {})
        self.assertEqual(self.key_file.read_bytes(), original_key[:31])
        store.keep_for_page({"theme": "dark"})
        self.assertIn("gemini", json.loads(store.config_path.read_text(encoding="utf-8")).get("keys_sealed", {}))

        self.key_file.write_bytes(original_key)
        self.assertEqual(store.load_config()["keys"], {"gemini": "FAKE-gemini-trunc"})

    @unittest.skipUnless(hasattr(os, "symlink"), "Symlinks unavailable")
    def test_r01d_symlink_key_file_is_refused_and_never_replaced(self) -> None:
        store = Store(self.tmp / "data")
        store.update_config(lambda c: c["keys"].update(gemini="FAKE-gemini-sym"))
        real_copy = self.tmp / "real_master.key"
        real_copy.write_bytes(self.key_file.read_bytes())
        self.key_file.unlink()
        os.symlink(str(real_copy), str(self.key_file))

        self.assertEqual(store.load_config()["keys"], {})
        self.assertTrue(self.key_file.is_symlink())
        with self.assertRaises(keystore.KeystoreUnavailable) as ctx:
            keystore.seal("another-secret")
        self.assertEqual(ctx.exception.reason, "symlink")
        self.assertTrue(self.key_file.is_symlink())

    def test_r01b_e_unwritable_keystore_falls_back_to_owner_only_plaintext_across_restart(self) -> None:
        # Place a directory where keystore.key should be so sealing raises KeystoreUnavailable.
        self.key_file.mkdir(parents=True)
        store = Store(self.tmp / "data")
        store.update_config(lambda c: (c["keys"].update(openai="FAKE-openai-fallback"), c.update(provider="openai", model="gpt-4o")))

        disk = json.loads(store.config_path.read_text(encoding="utf-8"))
        self.assertEqual(disk["keys"], {"openai": "FAKE-openai-fallback"})
        if os.name == "posix":
            self.assertEqual(stat.S_IMODE(store.config_path.stat().st_mode), 0o600)
        self.assertFalse(hasattr(keystore, "_EPHEMERAL_KEYS"))

        # Fresh Store simulating process restart still sees the saved key and surfaces keystore_unavailable_path.
        restarted = Store(self.tmp / "data")
        self.assertEqual(restarted.load_config()["keys"], {"openai": "FAKE-openai-fallback"})
        pub = restarted.public_settings()
        self.assertTrue(pub["configured"])
        self.assertEqual(pub.get("keystore_unavailable_path"), str(self.key_file))

    def test_r02_two_processes_creating_first_key_concurrently_never_lose_sealed_keys(self) -> None:
        child_code = (
            "import os, sys, time\n"
            "from studio.store import Store\n"
            "while time.time() < float(sys.argv[2]): pass\n"
            "store = Store(sys.argv[1])\n"
            "store.keep_for_page({'lang': 'en'})\n"
        )
        for trial_idx in range(10):
            trial_dir = self.tmp / ("race_%d" % trial_idx)
            key_path = trial_dir / "shared" / "keystore.key"
            folders = [trial_dir / "zip_data", trial_dir / "desktop_data"]
            for idx, folder in enumerate(folders):
                folder.mkdir(parents=True, mode=0o700)
                (folder / "config.json").write_text(
                    json.dumps({"provider": "gemini", "model": "gemini-2.5-pro", "keys": {"gemini": "FAKE-race-%d" % idx}}),
                    encoding="utf-8",
                )
            env = dict(os.environ, STUDIO_KEYSTORE_FILE=str(key_path), PYTHONDONTWRITEBYTECODE="1")
            go_at = time.time() + 0.15
            procs = [
                subprocess.Popen([sys.executable, "-B", "-c", child_code, str(f), repr(go_at)], cwd=str(ROOT), env=env)
                for f in folders
            ]
            for p in procs:
                self.assertEqual(p.wait(timeout=30), 0)

            with mock.patch.dict(os.environ, {"STUDIO_KEYSTORE_FILE": str(key_path)}):
                for idx, folder in enumerate(folders):
                    self.assertEqual(Store(folder).load_config()["keys"], {"gemini": "FAKE-race-%d" % idx}, f"trial {trial_idx}")
                    self.assertNotIn("FAKE-race-", (folder / "config.json").read_text(encoding="utf-8"))

    def test_o_creat_o_excl_fallback_when_hard_link_is_unsupported(self) -> None:
        with mock.patch.object(keystore.os, "link", side_effect=OSError("hard links unsupported")):
            token = keystore.seal("FAKE-excl-secret")
        self.assertEqual(keystore.unseal(token), "FAKE-excl-secret")
        self.assertEqual(len(self.key_file.read_bytes()), 32)


class MovedFolderAndPlaintextPrecedenceTest(IsolatedKeystoreCase):
    """secrets-01 & secrets-05 (r05, r06): preserve unopened keys_sealed, auto-migrate on open, plaintext wins over older sealed."""

    def test_r05_legacy_plaintext_seals_on_store_open_and_newer_plaintext_wins_over_older_sealed(self) -> None:
        data_dir = self.tmp / "data"
        data_dir.mkdir(parents=True, mode=0o700)
        (data_dir / "config.json").write_text(
            json.dumps({"provider": "gemini", "model": "gemini-2.5-pro", "keys": {"gemini": "FAKE-old-plain"}}),
            encoding="utf-8",
        )
        # Simply opening Store seals the legacy plaintext key without waiting for a setting change.
        store = Store(data_dir)
        raw_after_open = (data_dir / "config.json").read_text(encoding="utf-8")
        self.assertNotIn("FAKE-old-plain", raw_after_open)
        self.assertEqual(store.load_config()["keys"], {"gemini": "FAKE-old-plain"})

        # Simulate user running an older build that wrote a new key into plaintext `keys` alongside `keys_sealed`.
        disk = json.loads(raw_after_open)
        disk["keys"] = {"gemini": "FAKE-rotated-in-older-build"}
        (data_dir / "config.json").write_text(json.dumps(disk), encoding="utf-8")

        store2 = Store(data_dir)
        self.assertEqual(store2.load_config()["keys"]["gemini"], "FAKE-rotated-in-older-build")
        self.assertNotIn("FAKE-rotated-in-older-build", (data_dir / "config.json").read_text(encoding="utf-8"))

    def test_r06_moving_folder_to_another_computer_preserves_keys_sealed_and_reports_unreadable(self) -> None:
        comp1_key = self.tmp / "comp1" / "keystore.key"
        comp2_key = self.tmp / "comp2" / "keystore.key"
        data_dir = self.tmp / "synced_data"

        with mock.patch.dict(os.environ, {"STUDIO_KEYSTORE_FILE": str(comp1_key)}):
            s1 = Store(data_dir)
            s1.update_config(lambda c: (
                c["keys"].update(gemini="FAKE-comp1-gemini", openai="FAKE-comp1-openai"),
                c.update(provider="gemini", model="gemini-2.5-pro"),
            ))

        # Open on computer 2 (comp2_key does not exist yet).
        with mock.patch.dict(os.environ, {"STUDIO_KEYSTORE_FILE": str(comp2_key)}):
            s2 = Store(data_dir)
            pub = s2.public_settings()
            self.assertEqual(pub["keys"], {"gemini": False, "openai": False, "anthropic": False, "local": False})
            self.assertEqual(pub["keys_unreadable"], ["gemini", "openai"])
            self.assertFalse(pub["configured"])
            # Reading must NOT create comp2_key on disk.
            self.assertFalse(comp2_key.exists())

            # Page keeps language/theme on computer 2: keys_sealed must remain intact and comp2_key still absent.
            s2.keep_for_page({"theme": "light", "lang": "en"})
            self.assertFalse(comp2_key.exists())
            disk = json.loads((data_dir / "config.json").read_text(encoding="utf-8"))
            self.assertEqual(sorted(disk["keys_sealed"].keys()), ["gemini", "openai"])

        # Back on computer 1: both keys still unseal cleanly!
        with mock.patch.dict(os.environ, {"STUDIO_KEYSTORE_FILE": str(comp1_key)}):
            s1_again = Store(data_dir)
            self.assertEqual(
                s1_again.load_config()["keys"],
                {"gemini": "FAKE-comp1-gemini", "openai": "FAKE-comp1-openai"},
            )


class CeilingsAndGrandfatheringTest(IsolatedKeystoreCase):
    """secrets-04 & secrets-06 (r07): field-specific TextTooLong messages and grandfathering of existing long work."""

    def test_field_specific_bilingual_messages(self) -> None:
        store = Store(self.tmp / "data")

        with self.assertRaises(TextTooLong) as sample_exc:
            store.create_sample(title="ok", text="字" * (MAX_SAMPLE_CHARS + 1))
        self.assertIn("too long for the library", sample_exc.exception.message)
        self.assertIn("素材库放不下", sample_exc.exception.message_zh)

        with self.assertRaises(TextTooLong) as lines_exc:
            store.create_script(lines=[{"spoken_script_zh": "好"}] * (MAX_SCRIPT_LINES + 5))
        self.assertEqual(lines_exc.exception.field, "script_lines")
        self.assertIn("505 lines", lines_exc.exception.message)
        self.assertNotIn("library", lines_exc.exception.message)
        self.assertIn("505 行", lines_exc.exception.message_zh)

        with self.assertRaises(TextTooLong) as line_exc:
            store.create_script(lines=[{"spoken_script_zh": "一"}, {"spoken_script_zh": "二" * (MAX_LINE_CHARS + 2)}])
        self.assertEqual((line_exc.exception.field, line_exc.exception.line_no), ("line", 2))
        self.assertIn("Line 2", line_exc.exception.message)
        self.assertIn("第 2 行", line_exc.exception.message_zh)

        with self.assertRaises(TextTooLong) as title_exc:
            store.create_script(title="题" * (MAX_SCRIPT_TITLE_CHARS + 1))
        self.assertIn("title", title_exc.exception.message.lower())
        self.assertIn("标题", title_exc.exception.message_zh)

        with self.assertRaises(TextTooLong) as notes_exc:
            store.create_script(notes="记" * (MAX_NOTES_CHARS + 1))
        self.assertIn("notes", notes_exc.exception.message.lower())
        self.assertIn("备注", notes_exc.exception.message_zh)

        # Post copy counts visible characters: exactly MAX_POST_CHARS characters in caption_body_zh succeeds!
        ok_script = store.create_script(post={"caption_body_zh": "文" * MAX_POST_CHARS})
        self.assertEqual(len(ok_script["post"]["caption_body_zh"]), MAX_POST_CHARS)
        with self.assertRaises(TextTooLong) as post_exc:
            store.create_script(post={"caption_body_zh": "文" * (MAX_POST_CHARS + 1)})
        self.assertIn("30,001", post_exc.exception.message)
        self.assertIn("发布文案", post_exc.exception.message_zh)

        # video_format and status over 64 chars are refused with a clear sentence rather than silently truncated.
        with self.assertRaises(TextTooLong) as fmt_exc:
            store.update_script(ok_script["id"], {"video_format": "F" * 100})
        self.assertEqual(fmt_exc.exception.field, "video_format")
        with self.assertRaises(TextTooLong) as st_exc:
            store.update_script(ok_script["id"], {"status": "S" * 100})
        self.assertEqual(st_exc.exception.field, "status")

    def test_r07_existing_over_ceiling_scripts_and_samples_still_save_when_not_growing(self) -> None:
        store = Store(self.tmp / "data")
        now = time.time()
        long_lines = [{"line_no": i + 1, "audio_mode": "VOICEOVER", "spoken_script_zh": ("长" * 8000 if i == 1 else "短句")}
                      for i in range(800)]
        long_post = {"caption_body_zh": "文" * 40000}
        with store._db() as db:
            db.execute(
                "INSERT INTO scripts (id, title, video_format, status, target_sec, notes, lines_json, post_json, created_at, updated_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?)",
                ("a1b2c3d4e5f6", "题" * 700, "F" * 100, "S" * 100, 60, "记" * 80000,
                 json.dumps(long_lines, ensure_ascii=False), json.dumps(long_post, ensure_ascii=False), now, now),
            )
            db.execute(
                "INSERT INTO samples (id, title, text, created_at, updated_at) VALUES (?,?,?,?,?)",
                ("f6e5d4c3b2a1", "题" * 700, "今天去超市买菜嘛", now, now),
            )

        httpd = server.make_server(0, store)
        port = httpd.server_address[1]
        t = threading.Thread(target=httpd.serve_forever, daemon=True)
        t.start()
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)

        def call(method: str, path: str, body: object = None):
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
            try:
                raw = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
                conn.request(method, path, body=raw, headers={"Host": "127.0.0.1:%d" % port, "Content-Type": "application/json"})
                res = conn.getresponse()
                return res.status, json.loads(res.read().decode("utf-8"))
            finally:
                conn.close()

        status, s = call("GET", "/api/scripts/a1b2c3d4e5f6")
        self.assertEqual(status, 200)
        edited_lines = list(s["lines"])
        edited_lines[0] = dict(edited_lines[0], spoken_script_zh="短句修改呀")
        save_payload = {
            "title": s["title"],
            "target_sec": s["target_sec"],
            "lines": edited_lines,
            "keywords": s["keywords"],
            "post": s["post"],
            "base_updated_at": s["updated_at"],
        }
        status, saved = call("PUT", "/api/scripts/a1b2c3d4e5f6", save_payload)
        self.assertEqual(status, 200, saved)
        self.assertEqual(saved["lines"][0]["spoken_script_zh"], "短句修改呀")

        # Growing beyond the grandfathered 800-line ceiling is still refused with 413.
        status, refused = call("PUT", "/api/scripts/a1b2c3d4e5f6", dict(save_payload, base_updated_at=saved["updated_at"],
                                                                        lines=edited_lines + [{"spoken_script_zh": "多出一行"}]))
        self.assertEqual((status, refused.get("error")), (413, "text_too_long"))
        self.assertIn("801 lines", refused["message"])

        # Marking the over-ceiling 700-char-title script as posted also succeeds (r07 line 73).
        status, posted_res = call("POST", "/api/scripts/a1b2c3d4e5f6/posted", {"views": 100, "base_updated_at": saved["updated_at"]})
        self.assertEqual(status, 201, posted_res)

        # Updating metrics + unchanged 700-char title on existing sample succeeds.
        status, updated_sample = call("PUT", "/api/samples/f6e5d4c3b2a1", {"title": "题" * 700, "views": 1234})
        self.assertEqual(status, 200, updated_sample)
        self.assertEqual(updated_sample["views"], 1234)

        # MCP save_script with 501 lines also surfaces the line-count message.
        mcp_out = mcp_server.call_tool(store, "save_script", {
            "script": {"script_lines": [{"spoken_script_zh": "句"} for _ in range(501)]},
            "notes": "测试",
        })
        self.assertTrue(mcp_out["isError"])
        self.assertIn("501 lines", mcp_out["content"][0]["text"])


if __name__ == "__main__":
    unittest.main()
