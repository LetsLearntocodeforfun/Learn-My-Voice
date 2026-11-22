# Learn-My-Voice `v1.0` Architecture Summary & Human Sign-Off Roadmap

[Threat Model (STRIDE)](THREAT_MODEL.md) · [Security & Privacy Notes](SECURITY_NOTES.md) · [Quick Start Guide](QUICKSTART.md) | [快速上手指南](QUICKSTART.zh-CN.md) · [README](../README.md) | [中文说明](../README.zh-CN.md)

This document summarizes the six engineering pillars assembled for the `v1.0` release line and defines the **mandatory human sign-off gates** that must be completed by a maintainer with real credentials, real creator exports, and physical hardware before pushing the `v1.0.0` git tag to trigger `.github/workflows/release.yml`.

> **Honesty Rule:** Automated tests in this repository prove deterministic local logic, schema/backup safety, UI rendering in headless Chrome, protocol framing, scale latency, and build reproducibility against stand-in transports. They do **not** substitute for live cloud API runs, paid OS code-signing certificates, real third-party NLE imports, or human listening evaluation.

---

## 1. The Six `v1.0` Engineering Pillars

| Pillar | Scope & Key Modules | Automated Verification |
| :--- | :--- | :--- |
| **0. Core Local-First Studio, Safety & Autopilot Foundation** | Pure-stdlib loopback studio (`studio/server.py`, `studio/store.py`), evidence-backed Voice DNA & beat exemplars (`studio/evidence.py`, `studio/video_ingest.py`), air-gapped sponsored brief digestion (`studio/sponsored.py`), Creator Center time-series analytics & comment insights (`studio/rednote_analytics.py`), video calendar (`studio/planner.py`), bounded Autopilot with Undo (`studio/autopilot.py`), 25-tool MCP server (`studio/mcp_server.py`), plain-DDL verified `.lmvbackup` (`studio/backup.py`, `DATA_VERSION = 5`), and Tauri 2 desktop shell (`desktop/`). | Cross-platform unit, fuzz, backup, hardening, contrast (`test_look.py`), MCP, and headless Chrome E2E suites. |
| **1. Voiceover Recording Booth, Audio-Aligned SRT & FCPXML 1.9 (`V1`)** | Browser 16 kHz mono WAV voiceover recorder & audio/subtitle upload (`studio/recording.py`, `studio/web/recording.js`, `script_takes`), `SequenceMatcher` forced transcript/subtitle alignment (`whisper_aligned` / `subtitle_aligned`) + spoken ad-lib diff detection, pure-stdlib PCM WAV speech-pause island alignment (`pause_aligned`), audio-timed `字幕.srt` & `花字.srt` + **FCPXML 1.9** timeline export (`studio/exports.py`), and read-only MCP `get_script_timing_and_takes`. | `tests/test_recording.py` (unit, FCPXML XML validation, HTTP, MCP, backup round-trip, and headless Chrome E2E). |
| **2. Script Revisions, Diffs, Variants & Bilingual Partner Studio (`V2`)** | Automatic + named checkpoints up to 50 per script (`studio/revisions.py`, `studio/web/revisions.js`, `script_revisions`), breath-by-breath `+` / `−` diff & non-destructive restore (`before_restore`), 1-click A/B variant cloning, line Split at `/` & Merge with Next, `#/` instant search & filter bar, read-only MCP `list_script_revisions`, and 2-column Chinese + English Partner Editor (`bilingual` preference). | `tests/test_revisions.py` (unit, HTTP, MCP, and headless Chrome UI in `zh`/`en`). |
| **3. RedNote Cover, Title & First-3s Packaging Lab + Readiness Gate (`V3`)** | Stage 11 Packaging & Hook Lab (`prompts/11_packaging_hook_lab.md`, `studio/packaging.py`, `studio/web/packaging.js`), live 3:4 mobile feed card simulator, 20/28-unit truncation & compliance/never-phrase checks (`checks.lint_payload`), Pre-Filming Readiness strip, `发布文案与封面.txt` / `post_and_cover.txt` in 剪映/CapCut kits, and MCP `get_packaging_brief` + `save_script_packaging`. | `tests/test_packaging.py` + `tests/test_wordlist.py` + `tests/test_look.py` + headless Chrome E2E. |
| **4. Global Command Palette (`⌘K`) & Hardware Keyboard E2E (`V4`)** | Global `⌘K` / `Ctrl+K` Command Palette (`studio/web/palette.js`), editor `Ctrl+Enter` / `⌘Enter` & `Alt+ArrowUp`/`Down` line shortcuts, `#/plan` `1`/`2`/`3` view switching, modal focus restoration, and real CDP `Input.dispatchKeyEvent` (`Page.press` / `Page.type_text` in `tests/browser.py`). | `tests/test_keyboard.py` (real hardware key E2E in `zh`/light & `en`/dark) + `tests/test_look.py`. |
| **5. Voice Calibration, Scale Suite, SBOM & Production Release (`V5`)** | Leave-one-out holdout voice calibration benchmark (`studio/benchmark.py`, `GET /api/voice/calibration`, MCP `get_voice_calibration`, `#/voice` self-check card), 600-sample / 5,000-comment scale & SQLite query-count suite (`tests/test_performance.py`), deterministic CycloneDX 1.5 SBOM (`tools/sbom.py`), STRIDE threat model (`docs/THREAT_MODEL.md`), and semantic-tag production workflow (`.github/workflows/release.yml`). | `test_benchmark.py`, `test_performance.py` (`< 1.0s` per hot path), `test_sbom.py`, and `test_release.py` (`WorkflowsTest`). |

---

## 2. Mandatory Human Sign-Off Gates Before Tagging `v1.0.0`

Pushing a tag matching `v[0-9]+.[0-9]+.[0-9]+` (for example `v1.0.0`) triggers `.github/workflows/release.yml` and publishes a non-prerelease GitHub Release with `learn-my-voice-<version>.zip`, `sbom.cdx.json`, SHA-256 checksums, and passing macOS/Windows installers. **Do not push `v1.0.0` until every gate below is checked off and `README.md` / `README.zh-CN.md` / `CHANGELOG.md` are updated with what was actually observed.**

### Gate 1 — OS Code Signing, Notarization & SmartScreen Verification
- [ ] **macOS (`macos-arm64.dmg` & `macos-x64.dmg`):** Sign the app bundle and DMG with an **Apple Developer ID Application** certificate, submit to Apple `notarytool`, staple the notarization ticket (`xcrun stapler staple`), and verify clean first launch on a clean macOS 14/15 user account without Gatekeeper's "unidentified developer" block.
- [ ] **Windows (`windows-x64-setup.exe`):** Sign the NSIS installer and inner `.exe` binaries with a **Windows Authenticode** certificate (`signtool.exe` with SHA-256 timestamping) and verify clean installation and launch on Windows 11 without SmartScreen tampering errors.
- [ ] **Detached Release Signature (Recommended):** Add cryptographic signing (such as `cosign` / Sigstore or Minisign / GPG) alongside `.sha256` checksums so `studio/updater.py` and manual downloaders can verify publisher authenticity in addition to file integrity.

### Gate 2 — Live Cloud Provider & Grounded Search Smoke Matrix
All AI clients (`studio/llm.py`) were developed against vendor API documentation and deterministic stand-in test transports (`llm.ANTHROPIC_UNCHECKED`, `llm.PROVIDER_STOP_REASON_UNCHECKED`). Run a real end-to-end session against live endpoints with real API keys:
- [ ] **Anthropic Claude (`claude-sonnet-4-*` / `claude-opus-4-*`):** Verify Stage 2 Voice DNA synthesis, Stage 3 sponsored brief digest, Stage 4 script generation, Stage 5A editorial polish, Stage 11 packaging, `max_tokens` handling, temperature fallback on reasoning/strict models, and truncation/refusal stop-reason reporting.
- [ ] **Google Gemini (`gemini-2.5-pro` / `gemini-2.5-flash`):** Verify multimodal video frame + burned-in `花字` OCR ingestion, screenshot comment OCR preview, grounded Google Search trend lookup, and normal script drafting.
- [ ] **OpenAI (`gpt-4.1` / `o3` / Responses web search):** Verify grounded web trend lookup, JSON schema/reply parsing, and stop-reason handling when token limits are hit.
- [ ] **External MCP Clients:** Connect `run_mcp.py` to live **Claude Desktop**, **ChatGPT Desktop**, or **Codex CLI**, and verify all 25 tools (`get_script_brief`, `get_packaging_brief`, `save_script_packaging`, `get_script_timing_and_takes`, `list_script_revisions`, `get_voice_calibration`, optimistic-concurrency updates, and comment-summary boundaries).

### Gate 3 — Real Creator Center Spreadsheets & Real Video Editor Imports
- [ ] **Xiaohongshu Creator Center (`创作服务平台`) Exports:** Export real note-analytics `.xlsx` and `.csv` tables from an active Xiaohongshu creator account (plus optional third-party sheets such as 新红 / 千瓜 / 灰豚 if supported), run the two-step preview-and-import on `#/library`, and confirm column mapping, date parsing (`views/day`), and character encodings on files saved by Microsoft Excel, WPS Office, and Apple Numbers.
- [ ] **剪映专业版 (Jianying Pro) & CapCut Desktop:** Import an exported `-剪映素材包.zip` (`.srt` voiceover subtitles, on-screen `花字` `.srt`, shot list, and audio/timeline assets) into real 剪映 and CapCut on macOS and Windows; verify timecode alignment, UTF-8 BOM handling, and CJK line wrapping.
- [ ] **Final Cut Pro (`FCPXML`):** Import exported timeline XML into Final Cut Pro and verify compound clip / title lane compatibility without schema warnings.

### Gate 4 — Multi-Day Unattended Autopilot Soak Test
- [ ] **72-Hour Local Soak (`suggest` and `auto` modes):** Enable `#/autopilot` on a creator workstation across sleep/wake cycles with a watched folder containing real `.mp4`/`.mov` clips (including `商单/` subfolder items) and metric spreadsheets, plus 2–3 real public `https://` trend URLs.
- [ ] **Safety & Undo Audit:** Confirm daily model-call caps hold across midnight rollovers, `robots.txt` caching behaves politely, zero requests ever target `xiaohongshu.com` / `rednote.com` / `xhslink.com`, and clicking **Undo** cleanly restores prior Voice DNA / plan / idea state after real runs.

### Gate 5 — Hardware Audio, Installed Desktop Transcription & Human Blind Voice Evaluation
- [ ] **Packaged Desktop App Transcription & Microphone:** Inside the installed `.dmg` app on an Apple Silicon Mac (and `.exe` on Windows for browser audio features), verify real microphone input on `#/ideas` / Recording Booth and full `mlx-whisper` setup + `sandbox-exec` video transcription.
- [ ] **Creator Blind Listening & Calibration Validation:** Have at least one real bilingual Mandarin–English creator onboard >= 10 confirmed organic scripts/videos, confirm `#/voice` **Voice Calibration Check** reaches the `calibrated` tier (`margin >= 15`, `pairwise_separation_rate >= 85%`, `own_guardrail_clean_rate >= 85%`), and conduct a blind read-aloud check confirming AI drafts match natural spoken cadence without `翻译腔` (translationese) or `营销号` clichés.

---

## 3. Final Tagging Checklist (`.github/workflows/release.yml`)

Once Gates 1–5 are signed off:
1. Update the top heading of `CHANGELOG.md` to `## v1.0.0` with honest release notes, and set `__version__ = "1.0.0"` in `studio/__init__.py` (plus `desktop/src-tauri/tauri.conf.json` / `Cargo.toml` if bumping desktop metadata under maintainer review).
2. Run the full local verification suite and deterministic SBOM build:
   ```bash
   python3 tools/run_tests.py --label "local pre-tag" --out /tmp/pretag-tests.json
   python3 tools/build_release.py build --release --expect-tag v1.0.0 --out /tmp/pretag-dist
   python3 tools/sbom.py --version 1.0.0 --out /tmp/pretag-dist/sbom.cdx.json
   ```
3. Commit to `main`, create an annotated tag `git tag -a v1.0.0 -m "Learn-My-Voice v1.0.0"`, and push the tag to run `.github/workflows/release.yml`.
