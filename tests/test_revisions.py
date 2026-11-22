"""Tests for Workstream V2:
1. Plain SQLite ``script_revisions`` table, deduplication, 50-revision pruning, breath-by-breath diffs,
   safe ``before_restore`` restore, A/B variant duplication, and delete cleanup (``studio/revisions.py``).
2. Automatic checkpoints on AI draft creation, before AI critic/polish updates, and before ``posted.mark``.
3. HTTP revision & duplicate routes + read-only ``list_script_revisions`` MCP tool.
4. Headless Chrome UI verification for ``#/`` script search & filter pills, variant duplication, line
   Split / Merge / Move Up, Bilingual Partner Mode persistence, and Revision checkpoint + diff + restore.
"""
from __future__ import annotations

import contextlib
import http.client
import json
import os
import shutil
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from studio import backup, mcp_server, pipeline, posted, revisions, server, suggest
from studio.store import MODE_ORGANIC, MODE_SPONSORED, Store
from tests import browser


class _Harness(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="lmv-rev-"))
        self.addCleanup(shutil.rmtree, str(self.tmp), True)
        env = mock.patch.dict(os.environ, {
            "STUDIO_KEYSTORE_FILE": str(self.tmp / "os-keystore.key"),
            "LEARN_MY_VOICE_NO_SIBLING_SCAN": "1",
        })
        env.start()
        self.addCleanup(env.stop)
        self.store = Store(self.tmp / "data")


class RevisionEngineUnitTest(_Harness):
    def test_snapshot_deduplication_and_named_checkpoint(self) -> None:
        script = self.store.create_script(
            title="周末超市采购",
            lines=[
                {"spoken_script_zh": "周末去超市买菜嘛 / 结果买了两盒草莓哈"},
                {"spoken_script_zh": "回家洗干净马上吃了一半啦"},
            ],
            post={"caption_body_zh": "周末采购记录"},
            keywords=["超市分享"],
        )
        sid = script["id"]

        r1 = revisions.snapshot_script(self.store, sid, kind="before_critic")
        r2 = revisions.snapshot_script(self.store, sid, kind="before_critic")
        r3 = revisions.snapshot_script(self.store, sid, kind="before_posted")
        self.assertEqual(r1["id"], r2["id"])
        self.assertEqual(r1["id"], r3["id"])
        self.assertEqual(len(revisions.list_revisions(self.store, sid)), 1)

        # User-named checkpoint on top of an automatic snapshot IS kept.
        named = revisions.snapshot_script(self.store, sid, kind="checkpoint", label="初版留档")
        self.assertNotEqual(named["id"], r1["id"])
        self.assertEqual(named["kind"], "checkpoint")
        self.assertEqual(named["label"], "初版留档")
        self.assertEqual(len(revisions.list_revisions(self.store, sid)), 2)

        # Editing lines or post allows a new automatic snapshot.
        self.store.update_script(sid, {
            "lines": [
                {"spoken_script_zh": "周末去超市买菜嘛 / 结果买了三盒蓝莓哈"},
                {"spoken_script_zh": "回家洗干净马上吃了一半啦"},
            ]
        })
        r4 = revisions.snapshot_script(self.store, sid, kind="before_critic")
        self.assertNotEqual(r4["id"], named["id"])
        self.assertEqual(len(revisions.list_revisions(self.store, sid)), 3)

    def test_cap_50_prunes_oldest_automatic_revisions_keeping_named_checkpoints(self) -> None:
        script = self.store.create_script(
            title="容量上限测试",
            lines=[{"spoken_script_zh": "初始第一句嘛"}],
        )
        sid = script["id"]

        # Create 3 early user-named checkpoints that should survive pruning.
        kept_labels = ["关键节点A", "关键节点B", "关键节点C"]
        for idx, lbl in enumerate(kept_labels):
            self.store.update_script(sid, {"lines": [{"spoken_script_zh": f"节点版本 {idx} 嘛"}]})
            revisions.snapshot_script(self.store, sid, kind="checkpoint", label=lbl)

        # Push 55 automatic snapshots beyond the 50-revision cap.
        for i in range(55):
            self.store.update_script(sid, {"lines": [{"spoken_script_zh": f"自动留档第 {i} 版哈"}]})
            revisions.snapshot_script(self.store, sid, kind="before_critic")

        items = revisions.list_revisions(self.store, sid)
        self.assertEqual(len(items), revisions.MAX_REVISIONS_PER_SCRIPT)
        present_labels = {r["label"] for r in items if r["kind"] == "checkpoint"}
        self.assertEqual(present_labels, set(kept_labels))
        # Newest automatic snapshot is kept; earliest automatic snapshots (0..7) were pruned.
        self.assertEqual(revisions.get_revision(self.store, sid, items[0]["id"])["lines"][0]["spoken_script_zh"], "自动留档第 54 版哈")

    def test_diff_revision_line_and_breath_accuracy(self) -> None:
        script = self.store.create_script(
            title="原版标题",
            target_sec=60,
            lines=[
                {"spoken_script_zh": "一入冬脸就特别干嘛 / 所以我换了面霜"},
                {"spoken_script_zh": "这句保持一模一样哈"},
                {"spoken_script_zh": "这句后来会被删掉啦"},
            ],
            keywords=["干皮面霜"],
        )
        sid = script["id"]
        snap = revisions.snapshot_script(self.store, sid, kind="checkpoint", label="V1")

        #Identical right after snapshot
        same_diff = revisions.diff_revision(self.store, sid, snap["id"])
        self.assertTrue(same_diff["identical"])
        self.assertEqual(same_diff["counts"], {"added": 0, "removed": 0, "changed": 0, "unchanged": 3})

        # Modify title, target_sec, clause 2 of line 1, delete line 3, and add a new closing line
        self.store.update_script(sid, {
            "title": "新版标题",
            "target_sec": 90,
            "keywords": ["干皮面霜", "秋冬护肤"],
            "lines": [
                {"spoken_script_zh": "一入冬脸就特别干嘛 / 结果换了精粹水稳多了哈"},
                {"spoken_script_zh": "这句保持一模一样哈"},
                {"spoken_script_zh": "新增的结尾互动句嘛"},
            ],
        })

        diff = revisions.diff_revision(self.store, sid, snap["id"])
        self.assertFalse(diff["identical"])
        self.assertEqual(diff["title"], {"before": "原版标题", "after": "新版标题", "changed": True})
        self.assertEqual(diff["target_sec"], {"before": 60, "after": 90, "delta": 30, "changed": True})
        self.assertTrue(diff["keywords_changed"])
        self.assertEqual(len(diff["unchanged"]), 1)
        self.assertEqual(diff["unchanged"][0]["after"], "这句保持一模一样哈")
        self.assertEqual(len(diff["changed"]), 2)

        first_changed = diff["changed"][0]
        ops = [(b["op"], b["text"]) for b in first_changed["breaths"]]
        self.assertIn(("unchanged", "一入冬脸就特别干嘛"), ops)
        self.assertIn(("removed", "所以我换了面霜"), ops)
        self.assertIn(("added", "结果换了精粹水稳多了哈"), ops)

    def test_restore_revision_saves_before_restore_and_duplicate_clones_cleanly(self) -> None:
        script = self.store.create_script(
            title="护手霜实测",
            notes="护手霜对比",
            target_sec=30,
            video_format="SINGLE_PRODUCT_SHARE",
            lines=[{"spoken_script_zh": "冬天洗手后手背超干嘛 / 这支涂完不黏手机哈"}],
            post={"caption_body_zh": "初版正文"},
            keywords=["护手霜"],
            mode=MODE_SPONSORED,
            from_ai=True,
            basis={"sponsor": {"requirements": 2, "way": "own"}},
        )
        sid = script["id"]
        initial_revs = revisions.list_revisions(self.store, sid)
        self.assertEqual(len(initial_revs), 1)
        self.assertEqual(initial_revs[0]["kind"], "ai_draft")
        v1_id = initial_revs[0]["id"]

        # Edit live script to V2 without manual checkpoint
        self.store.update_script(sid, {
            "title": "护手霜实测改版",
            "target_sec": 60,
            "lines": [{"spoken_script_zh": "完全重写的一句台词啦"}],
            "post": {"caption_body_zh": "改版正文"},
        })

        # Restore V1 -> saves V2 as before_restore first!
        out = revisions.restore_revision(self.store, sid, v1_id)
        restored = out["script"]
        self.assertEqual(restored["title"], "护手霜实测")
        self.assertEqual(restored["target_sec"], 30)
        self.assertEqual(restored["lines"][0]["spoken_script_zh"], "冬天洗手后手背超干嘛 / 这支涂完不黏手机哈")
        self.assertEqual(restored["post"], {"caption_body_zh": "初版正文"})

        kinds = [r["kind"] for r in out["revisions"]]
        self.assertIn("before_restore", kinds)
        before_restore_rev = next(r for r in out["revisions"] if r["kind"] == "before_restore")
        self.assertEqual(
            revisions.get_revision(self.store, sid, before_restore_rev["id"])["lines"][0]["spoken_script_zh"],
            "完全重写的一句台词啦",
        )

        # Duplicate script for A/B variant testing
        variant = revisions.duplicate_script(self.store, sid, lang="zh")
        self.assertNotEqual(variant["id"], sid)
        self.assertEqual(variant["title"], "护手霜实测 (版本 B)")
        self.assertEqual(variant["mode"], MODE_SPONSORED)
        self.assertEqual(variant["video_format"], "SINGLE_PRODUCT_SHARE")
        self.assertEqual(variant["target_sec"], 30)
        self.assertEqual(variant["keywords"], ["护手霜"])
        self.assertEqual(variant["basis"], {"sponsor": {"requirements": 2, "way": "own"}})
        var_revs = revisions.list_revisions(self.store, variant["id"])
        self.assertEqual(len(var_revs), 1)
        self.assertEqual(var_revs[0]["kind"], "checkpoint")

        # Deleting original script removes its revisions while leaving variant's intact
        self.store.delete_script(sid)
        self.assertEqual(revisions.list_revisions(self.store, sid), [])
        self.assertEqual(len(revisions.list_revisions(self.store, variant["id"])), 1)

        # Backup & restore carries script_revisions cleanly
        bk_path = self.tmp / ("check.%s" % backup.EXTENSION)
        backup.create(self.store, bk_path)
        fresh = Store(self.tmp / "restored-data")
        backups = backup.Backups(fresh)
        staged = backups.stage(bk_path)
        backups.restore(staged["restore_id"])
        self.assertEqual(len(revisions.list_revisions(fresh, variant["id"])), 1)


class RevisionApiAndMcpTest(_Harness):
    def setUp(self) -> None:
        super().setUp()
        self.store.update_config(lambda c: (
            c.update(provider="openai", model="gpt-4o-mini"),
            c["keys"].update(openai="sk-test-dummy"),
        ))
        self.canned_reply: dict = {}

        def fake_transport(url: str, headers: dict, payload: dict, timeout: int = 90) -> dict:
            return {"choices": [{"message": {"content": json.dumps(self.canned_reply, ensure_ascii=False)}}]}

        self.httpd = server.make_server(0, self.store, transport=fake_transport)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True).start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)

    def api(self, method: str, path: str, body: object = None) -> tuple[int, dict]:
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            headers = {"Host": "127.0.0.1:%d" % self.port}
            raw = None
            if body is not None:
                raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
                headers["Content-Type"] = "application/json"
            elif method == "POST":
                raw = b"{}"
                headers["Content-Type"] = "application/json"
            conn.request(method, path, body=raw, headers=headers)
            resp = conn.getresponse()
            return resp.status, json.loads(resp.read().decode("utf-8"))
        finally:
            conn.close()

    def test_auto_checkpoints_http_routes_and_mcp_tool(self) -> None:
        # 1. AI draft creation snapshots kind="ai_draft"
        self.canned_reply = {
            "title": "秋冬护肤真话",
            "script_lines": [
                {"beat_type": "HOOK", "audio_mode": "VOICEOVER", "spoken_script_zh": "一换季脸就开始起皮嘛 / 我最近换了面霜"},
                {"beat_type": "CLOSING_CTA", "audio_mode": "VOICEOVER", "spoken_script_zh": "你们冬天用哪瓶可以在评论区聊聊哈"},
            ],
            "rednote_post_package": {"caption_body_zh": "换季护肤记录"},
        }
        st, drafted = self.api("POST", "/api/generate", {"notes": "换季面霜分享", "target_sec": 60})
        self.assertEqual(st, 201)
        sid = drafted["id"]

        st, rev_list = self.api("GET", f"/api/scripts/{sid}/revisions")
        self.assertEqual(st, 200)
        self.assertEqual([r["kind"] for r in rev_list["revisions"]], ["ai_draft"])
        ai_rev_id = rev_list["revisions"][0]["id"]

        # 2. User edits draft, then runs critic -> automatic before_critic snapshot!
        st, edited = self.api("PUT", f"/api/scripts/{sid}", {
            "lines": [
                {"spoken_script_zh": "一换季脸就开始起皮嘛 / 我最近换了厚面霜"},
                {"spoken_script_zh": "你们冬天用哪瓶可以在评论区聊聊哈"},
            ]
        })
        self.assertEqual(st, 200)

        self.canned_reply = {
            "suggestions": [
                {
                    "line_no": 1,
                    "spoken_script_zh": "每次一降温我这张干皮就起皮嘛 / 最近换了这罐面霜稳多了哈",
                    "why_zh": "更贴近日常口吻",
                }
            ]
        }
        st, sug = self.api("POST", f"/api/scripts/{sid}/suggest", {"kind": "critic"})
        self.assertEqual(st, 200)
        self.assertEqual(len(sug["suggestions"]), 1)

        # Apply critic line via PUT with before_critic=True (deduplicated against suggest's before_critic!)
        st, after_critic = self.api("PUT", f"/api/scripts/{sid}", {
            "before_critic": True,
            "lines": [
                {"spoken_script_zh": sug["suggestions"][0]["text"]},
                {"spoken_script_zh": "你们冬天用哪瓶可以在评论区聊聊哈"},
            ],
        })
        self.assertEqual(st, 200)
        st, rev_list = self.api("GET", f"/api/scripts/{sid}/revisions")
        self.assertEqual([r["kind"] for r in rev_list["revisions"]], ["before_critic", "ai_draft"])

        # 3. Invalid posted.mark does NOT add before_posted; valid posted.mark DOES add before_posted!
        st_bad, _ = self.api("POST", f"/api/scripts/{sid}/posted", {"posted_on": "2099-01-01"})
        self.assertEqual(st_bad, 400)
        self.assertEqual(len(revisions.list_revisions(self.store, sid)), 2)

        st_post, _ = self.api("POST", f"/api/scripts/{sid}/posted", {"likes": 42})
        self.assertEqual(st_post, 201)
        st, rev_list = self.api("GET", f"/api/scripts/{sid}/revisions")
        self.assertEqual([r["kind"] for r in rev_list["revisions"]], ["before_posted", "before_critic", "ai_draft"])

        # 4. Save named checkpoint, inspect diff via GET /revisions/<rev_id>, and restore safely
        st_cp, cp_res = self.api("POST", f"/api/scripts/{sid}/revisions", {"label": "发布版定稿"})
        self.assertEqual(st_cp, 201)
        self.assertEqual(cp_res["revision"]["label"], "发布版定稿")

        st_diff, diff_res = self.api("GET", f"/api/scripts/{sid}/revisions/{ai_rev_id}")
        self.assertEqual(st_diff, 200)
        self.assertFalse(diff_res["diff"]["identical"])
        self.assertEqual(len(diff_res["diff"]["changed"]), 1)

        st_rest, rest_res = self.api("POST", f"/api/scripts/{sid}/revisions/{ai_rev_id}/restore", {})
        self.assertEqual(st_rest, 200)
        self.assertIn("check", rest_res)
        self.assertEqual(rest_res["script"]["lines"][0]["spoken_script_zh"], "一换季脸就开始起皮嘛 / 我最近换了面霜")

        # 5. Duplicate endpoint
        st_dup, dup_res = self.api("POST", f"/api/scripts/{sid}/duplicate", {"lang": "en"})
        self.assertEqual(st_dup, 201)
        self.assertTrue(dup_res["script"]["title"].endswith(" (Variant B)"))

        # 6. MCP list_script_revisions tool (summary + detail with diff)
        mcp_list = mcp_server.call_tool(self.store, "list_script_revisions", {"script_id": sid})
        self.assertFalse(mcp_list["isError"])
        parsed_list = json.loads(mcp_list["content"][0]["text"])
        self.assertEqual(parsed_list["script_id"], sid)
        self.assertGreaterEqual(len(parsed_list["revisions"]), 4)

        mcp_detail = mcp_server.call_tool(self.store, "list_script_revisions", {"script_id": sid, "revision_id": ai_rev_id})
        self.assertFalse(mcp_detail["isError"])
        parsed_detail = json.loads(mcp_detail["content"][0]["text"])
        self.assertEqual(parsed_detail["revision"]["id"], ai_rev_id)
        self.assertTrue(parsed_detail["diff"]["identical"])

        mcp_missing = mcp_server.call_tool(self.store, "list_script_revisions", {"script_id": "000000000000"})
        self.assertTrue(mcp_missing["isError"])


@unittest.skipUnless(browser.AVAILABLE, browser.WHY_NOT)
class RevisionAndBilingualUiTest(_Harness):
    def test_home_search_filter_duplicate_line_ops_bilingual_and_revisions_ui(self) -> None:
        # Seed 3 scripts: 1 organic draft, 1 posted organic script, 1 sponsored draft
        draft_s = self.store.create_script(
            title="周末逛农夫市集",
            lines=[
                {
                    "spoken_script_zh": "周六早上去农夫市集逛了一圈嘛 / 买了一束向日葵哈",
                    "english_partner_translation": "Went to the farmers market on Saturday morning / picked up sunflowers",
                    "visual_broll_en_zh": "Walking through stalls",
                    "onscreen_flower_text_zh": "周末市集",
                },
                {
                    "spoken_script_zh": "顺便买了一袋现烤酸种面包啦",
                    "english_partner_translation": "",
                    "visual_broll_en_zh": "Holding warm sourdough loaf",
                    "onscreen_flower_text_zh": "",
                },
            ],
            keywords=["周末日常", "面包控"],
            mode=MODE_ORGANIC,
        )
        posted_s = self.store.create_script(
            title="秋冬保湿面霜空瓶",
            lines=[{"spoken_script_zh": "这罐面霜我已经空瓶两罐了嘛"}],
            keywords=["空瓶记"],
            mode=MODE_ORGANIC,
        )
        posted.mark(self.store, posted_s["id"], {"likes": 88})

        sponsored_s = self.store.create_script(
            title="吹风机合作初稿",
            lines=[{"spoken_script_zh": "洗完头五分钟吹干真的省事哈"}],
            keywords=["护发日常"],
            mode=MODE_SPONSORED,
        )

        with browser.open_app(self.store) as page:
            # 1. Home (#/) search & filter pills
            page.go("#/", "document.querySelectorAll('[data-script-card]').length === 3")
            self.assertEqual(page.problems, [])

            # Search by keyword ("面包控") -> matches only draft_s
            page.eval_js("""(() => {
              const el = document.querySelector('[data-script-search]');
              el.value = '面包控';
              el.dispatchEvent(new Event('input', { bubbles: true }));
            })()""")
            page.wait_for("document.querySelectorAll('[data-script-card]').length === 1")
            self.assertEqual(page.eval_js("document.querySelector('[data-script-card]').dataset.scriptCard"), draft_s["id"])

            # Search by spoken line ("空瓶两罐") -> matches posted_s
            page.eval_js("""(() => {
              const el = document.querySelector('[data-script-search]');
              el.value = '空瓶两罐';
              el.dispatchEvent(new Event('input', { bubbles: true }));
            })()""")
            page.wait_for("document.querySelectorAll('[data-script-card]').length === 1")
            self.assertEqual(page.eval_js("document.querySelector('[data-script-card]').dataset.scriptCard"), posted_s["id"])

            # Clear search and test filter pills: posted, sponsored, draft, all
            page.eval_js("""(() => {
              const el = document.querySelector('[data-script-search]');
              el.value = '';
              el.dispatchEvent(new Event('input', { bubbles: true }));
            })()""")
            page.wait_for("document.querySelectorAll('[data-script-card]').length === 3")

            page.eval_js("document.querySelector('[data-script-filter=\"posted\"]').click()")
            page.wait_for("document.querySelectorAll('[data-script-card]').length === 1")
            self.assertEqual(page.eval_js("document.querySelector('[data-script-card]').dataset.scriptCard"), posted_s["id"])

            page.eval_js("document.querySelector('[data-script-filter=\"sponsored\"]').click()")
            page.wait_for("document.querySelectorAll('[data-script-card]').length === 1")
            self.assertEqual(page.eval_js("document.querySelector('[data-script-card]').dataset.scriptCard"), sponsored_s["id"])

            page.eval_js("document.querySelector('[data-script-filter=\"draft\"]').click()")
            page.wait_for("document.querySelectorAll('[data-script-card]').length === 2")

            page.eval_js("document.querySelector('[data-script-filter=\"all\"]').click()")
            page.wait_for("document.querySelectorAll('[data-script-card]').length === 3")

            # 2. Duplicate variant from home card -> navigates to #/s/<new_id>
            page.eval_js(f"document.querySelector('[data-dup-script=\"{sponsored_s['id']}\"]').click()")
            page.wait_for("location.hash.startsWith('#/s/') && Boolean(document.getElementById('title'))")
            dup_title = page.eval_js("document.getElementById('title').value")
            self.assertIn("版本 B", dup_title)
            self.assertEqual(page.problems, [])

            # 3. Navigate to draft_s editor and test Split, Move Up, and Merge
            page.go("#/s/" + draft_s["id"], "document.querySelectorAll('.line').length === 2")
            # Line 1 has 2 '/' clauses -> Split creates 3 lines!
            page.eval_js("document.querySelector('.line[data-i=\"0\"] [data-act=\"split\"]').click()")
            page.wait_for("document.querySelectorAll('.line').length === 3")
            self.assertEqual(page.eval_js("document.querySelectorAll('.line .spoken')[0].value"), "周六早上去农夫市集逛了一圈嘛")
            self.assertEqual(page.eval_js("document.querySelectorAll('.line .spoken')[1].value"), "买了一束向日葵哈")

            # Move Line 2 up to position 1
            page.eval_js("document.querySelector('.line[data-i=\"1\"] [data-act=\"up\"]').click()")
            page.wait_for("document.querySelectorAll('.line .spoken')[0].value === '买了一束向日葵哈'")

            # Merge Line 1 with Line 2 -> back to 2 lines joined by ' / '
            page.eval_js("document.querySelector('.line[data-i=\"0\"] [data-act=\"merge\"]').click()")
            page.wait_for("document.querySelectorAll('.line').length === 2")
            self.assertEqual(
                page.eval_js("document.querySelectorAll('.line .spoken')[0].value"),
                "买了一束向日葵哈 / 周六早上去农夫市集逛了一圈嘛",
            )

            # 4. Bilingual Partner Mode toggle, missing-gloss badge & persistence
            page.eval_js("document.getElementById('bilingual-btn').click()")
            page.wait_for("document.querySelectorAll('.line.bilingual').length === 2")
            self.assertEqual(page.eval_js("document.querySelector('[data-bi-missing]').dataset.biMissing"), "1")

            # Fill in the missing English gloss on line 2 -> badge updates live to 0!
            page.eval_js("""(() => {
              const ta = document.querySelectorAll('[data-english-gloss]')[1];
              ta.value = 'Also grabbed a fresh sourdough loaf';
              ta.dispatchEvent(new Event('input', { bubbles: true }));
            })()""")
            page.wait_for("document.querySelector('[data-bi-missing]').dataset.biMissing === '0'")
            page.wait_for("!dirty && !saveInFlight")
            page.wait_for("keptByCore.bilingual === '1'")
            self.assertEqual(self.store.load_page_keeps().get("bilingual"), "1")

            # 5. Revisions panel: save named checkpoint, edit script, inspect diff, restore non-destructively
            page.eval_js("document.getElementById('rev-open-btn').click()")
            page.wait_for("Boolean(document.getElementById('rv-panel')) && !rvState.loading && Boolean(document.getElementById('rv-label-input'))")
            page.eval_js("""(() => {
              const inp = document.getElementById('rv-label-input');
              inp.value = '市集定稿 V1';
              inp.dispatchEvent(new Event('input', { bubbles: true }));
              document.getElementById('rv-checkpoint-btn').click();
            })()""")
            page.wait_for("!rvState.saving && !rvState.diffLoading && document.querySelectorAll('[data-rv-select]').length >= 1")

            # Edit line 1 spoken text and wait for autosave + diff refresh
            page.eval_js("""(() => {
              const sp = document.querySelectorAll('.line .spoken')[0];
              sp.value = '买了一束向日葵哈 / 结果路过面包摊又走不动道啦';
              sp.dispatchEvent(new Event('input', { bubbles: true }));
            })()""")
            page.wait_for("!dirty && !saveInFlight && !rvState.diffLoading && document.querySelectorAll('.rv-breath.add').length >= 1 && Boolean(document.getElementById('rv-restore-btn'))")
            self.assertGreaterEqual(page.eval_js("document.querySelectorAll('.rv-breath.del').length"), 1)

            # Click Restore -> no modal confirm (#ask remains hidden), restores V1 and records before_restore!
            page.eval_js("document.getElementById('rv-restore-btn').click()")
            page.wait_for(
                "document.querySelectorAll('.line .spoken')[0].value === '买了一束向日葵哈 / 周六早上去农夫市集逛了一圈嘛'"
            )
            self.assertTrue(page.eval_js("document.getElementById('ask') ? document.getElementById('ask').hidden : true"))
            self.assertGreaterEqual(page.eval_js("document.querySelectorAll('[data-rv-select]').length"), 2)
            self.assertEqual(page.problems, [])

            # Also check English UI + full reload persistence of Bilingual Partner Mode with zero problems
            page.eval_js("document.getElementById('lang-btn').click()")
            page.wait_for("document.documentElement.lang === 'en' && document.querySelectorAll('.line.bilingual').length === 2")
            page.call("Page.reload")
            page.wait_for("document.documentElement.lang === 'en' && document.querySelectorAll('.line.bilingual').length === 2")
            self.assertEqual(page.problems, [])


if __name__ == "__main__":
    unittest.main()
