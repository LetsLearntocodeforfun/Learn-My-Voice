# Threat Model (STRIDE Analysis)

[Security & Privacy Notes](SECURITY_NOTES.md) · [v1.0 Roadmap & Sign-Off Gates](V1_ROADMAP.md) · [Quick Start Guide](QUICKSTART.md) | [快速上手指南](QUICKSTART.zh-CN.md) · [README](../README.md) | [中文说明](../README.zh-CN.md)

This document maps the trust boundaries, protected assets, STRIDE threat classes, implemented mitigations, and explicit residual risks of **Learn-My-Voice**. Every mitigation cited below points to code that runs today and is exercised by automated tests; residual risks that depend on external certificates, OS boundaries, or unverified third-party endpoints are stated plainly.

---

## 1. System Scope, Protected Assets & Trust Boundaries

### Protected Assets
1. **Creator Corpus & Unpublished Scripts (`data/studio.db`):** Draft and posted scripts, voiceover transcripts, Voice DNA (`voice_profiles`), confidential brand briefs (`sponsored_briefs`), audience metric snapshots, and planned topics.
2. **Cloud Provider Credentials (`studio/keystore.py`):** API keys for Google Gemini, OpenAI, and Anthropic Claude, sealed outside `data/config.json` (Win32 DPAPI on Windows; Encrypt-then-MAC `SHA-256` stream + `HMAC-SHA256` with a `0600` master key in the user's OS application-config directory on macOS/Linux).
3. **Creator Voice Integrity & Platform Standing:** Protection against silent overwriting of creator-written lines, hallucinated Voice DNA claims, corporate brief leakage, and Xiaohongshu (`蒲公英` / Advertising Law) high-risk phrasing (`studio/lexicon.json`, `studio/wordlist.py`, `studio/guardrails.py`).
4. **Host Filesystem & Local Execution Integrity:** Protection against arbitrary file reads/writes, zip-slip path traversal, malicious archive restore, command injection, and unpinned supply-chain artifacts.

### Trust Boundaries
| Boundary | Crossing Traffic | Primary Modules |
| :--- | :--- | :--- |
| **B1 — Local Browser / WebView ↔ Loopback HTTP Server** | `http://127.0.0.1:<port>` requests, static assets, `/api/*` JSON mutations | `studio/server.py`, `studio/outside.py`, `desktop/src-tauri/` |
| **B2 — External MCP Host ↔ Local MCP Server** | Line-delimited JSON-RPC 2.0 frames over `stdio` | `studio/mcp_server.py`, `run_mcp.py` |
| **B3 — Untrusted Files, Web Pages & Pasted Text ↔ Parsers** | `.docx`, `.xlsx`, `.csv`, `.srt`/`.vtt`, `.lmvbackup`, viewer comments, public `https://` trend pages, video/audio media | `studio/keyword_import.py`, `studio/rednote_analytics.py`, `studio/trend_sources.py`, `studio/transcribe.py`, `studio/backup.py` |
| **B4 — Untrusted Context & Model Replies ↔ LLM Prompts & Store** | Cloud/local model HTTP responses, copy-pasted JSON replies, untrusted excerpts inside prompts | `studio/llm.py`, `studio/pipeline.py`, `studio/evidence.py`, `studio/sponsored.py`, `prompts/*.md` |
| **B5 — GitHub Releases & CI Runners ↔ Installed App** | Update manifests, `.zip` packages, desktop installers, GitHub Actions workflows, PyPI Whisper wheels | `studio/updater.py`, `tools/build_release.py`, `tools/sbom.py`, `.github/workflows/*.yml` |

**Explicit Non-Goal (Same-User Local Malware):** Like any unprivileged user-space application, Learn-My-Voice does not defend against a compromised OS kernel or another process already executing under the same OS user account (`UID` / Windows user SID). Sealing API keys in `keystore.py` prevents plaintext exposure across backups, cloud-synced `data/` folders, and accidental file sharing, not memory or key-file inspection by same-UID malware.

---

## 2. STRIDE Analysis by Attack Surface

### Surface A: Loopback HTTP Server & Desktop WebView (`B1`)

| STRIDE | Threat Scenario | Implemented Mitigation | Verification |
| :--- | :--- | :--- | :--- |
| **Spoofing** | A malicious website visited in the user's browser sends cross-site requests (`fetch`, `<form>`, DNS rebinding) to `127.0.0.1:8765`. | `studio/server.py` binds exclusively to `127.0.0.1`, validates the `Host` header against exact loopback host/port pairs (blocking DNS rebinding), generates a per-launch 256-bit `data/server-token` (`0600`), sets a port-scoped `HttpOnly; SameSite=Strict` cookie, requires `X-Studio-Token` verified with `hmac.compare_digest` on all state-changing `/api/*` calls, and enforces `Sec-Fetch-Site` (`same-origin` or `none`) plus `Origin` checks. | `tests/test_hardening.py`, `tests/test_server.py` |
| **Tampering / Elevation** | Untrusted script title, comment, or model reply injects inline HTML/JS (`XSS`) into the Studio UI or Tauri WebView to call `/api/*` or `outside` endpoints. | Strict CSP (`default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; media-src 'self' blob:; connect-src 'self'`), zero inline event handlers or `<style>` blocks, mandatory `esc()` escaping across frontend renderers, top-level navigation blocking for non-app origins (`data:`, `blob:`, external URLs) in `desktop/src-tauri/`, and `outside.safe_name` sanitization + non-overwrite numbering (` (2)`) in `Downloads`. | `tests/test_hardening.py`, `tests/test_outside.py`, `tests/test_desktop.py` |
| **Information Disclosure** | Cloud API keys leak to the browser, terminal error logs, or HTTP redirect targets. | `GET /api/settings` returns only boolean `has_*_key` flags, never raw keys; `llm.py` rejects `3xx` redirects on cloud API calls so `Authorization` / `x-api-key` headers are never forwarded; upstream error strings scrub active tokens and `sk-` / `sk-ant-` / `AIza` patterns. | `tests/test_hardening.py`, `tests/test_providers.py` |
| **Denial of Service** | Oversized local request payloads or slow/stalled sockets wedge the local server. | `server.py` enforces per-route byte caps, header count/size bounds, socket read timeouts, and bounded JSON recursion/parse depth before routing. | `tests/test_hardening.py`, `tools/fuzz.py` |

---

### Surface B: MCP `stdio` Boundary (`B2`)

| STRIDE | Threat Scenario | Implemented Mitigation | Verification |
| :--- | :--- | :--- | :--- |
| **Elevation of Privilege** | A prompt-injected external MCP client attempts to enable Autopilot, overwrite creator-polished script beats, mutate Voice DNA without review, or read API keys. | `studio/mcp_server.py` exposes a fixed 21-tool surface with no settings, key, backup, update, or Autopilot-mutation tools (`get_autopilot_status` and `get_voice_calibration` are strictly read-only). `update_script_beats` refuses edits unless `base_updated_at` matches (optimistic concurrency) and protects human-edited lines against silent AI clobbering (`ai_lines` provenance). Voice DNA changes via MCP remain proposals until accepted in the UI. | `tests/test_integrations.py`, `tests/test_setup.py`, `tests/test_autopilot.py` |
| **Information Disclosure** | Viewer handles/PII from pasted RedNote comments leak to an external MCP client's cloud model. | Commenter display names and `@handles` are stripped at ingestion time before rows touch SQLite (`rednote_analytics.py`), and MCP `INSTRUCTIONS` explicitly document what summarized comment themes contain. | `tests/test_rednote_analytics.py`, `tests/test_integrations.py` |
| **Tampering / DoS** | Runaway MCP tool loops flood the video calendar or send multi-hundred-MB JSON-RPC frames over `stdio`. | `mcp_server.py` caps `stdio` lines at `12 MiB`, caps JSON-RPC batches at `64`, restricts `add_video_plan` strictly to `IDEA` status, and enforces `MCP_MAX_WAITING_PLAN_IDEAS = 50`. | `tests/test_integrations.py`, `tests/test_planner.py` |

---

### Surface C: Untrusted Web, Spreadsheet, Media & Comment Ingestion (`B3`)

| STRIDE | Threat Scenario | Implemented Mitigation | Verification |
| :--- | :--- | :--- | :--- |
| **Spoofing / SSRF** | Configured Autopilot trend URL redirects or DNS-rebinds to `127.0.0.1`, RFC1918 LAN hosts, or cloud metadata (`169.254.169.254`), or scrapes Xiaohongshu. | `studio/trend_sources.py` refuses `xiaohongshu.com`, `rednote.com`, and `xhslink.com` by name before DNS lookup, accepts only `https://` URLs (max 5), resolves DNS once, verifies every address satisfies `ip_address.is_global`, pins the verified IP at the TLS socket layer (defeating time-of-check/time-of-use DNS rebinding while preserving SNI + certificate hostname verification), and checks `robots.txt` for `Learn-My-Voice-Autopilot`. | `tests/test_trend_sources.py`, `tests/test_autopilot.py` |
| **Tampering / DoS** | Zip-bomb `.xlsx`/`.docx`, XML entity expansion (`XXE` / billion laughs), spreadsheet formulas (`=CMD(...)`), or malformed `.srt`/`.vtt` crash parsers or poison metrics. | `keyword_import.py`, `rednote_analytics.py`, and `.docx` readers inspect ZIP central-directory headers first, enforce compressed/uncompressed byte budgets and entry count caps, reject XML `<!DOCTYPE` / `<!ENTITY` declarations, ignore spreadsheet formula strings when reading numeric/text cells, and require a two-step preview-with-SHA256-fingerprint before Creator Center rows commit to `metric_snapshots`. | `tests/test_rednote_analytics.py`, `tests/test_trends.py`, `tools/fuzz.py` |
| **Elevation of Privilege** | Malicious media file exploits `afconvert` or `mlx-whisper` during local transcription on macOS. | `studio/transcribe.py` executes both audio extraction (`AUDIO_SEAL`) and `whisper_worker.py` (`WORKER_SEAL`) inside macOS `sandbox-exec` Seatbelt profiles that deny all network sockets, deny LaunchServices IPC, and deny `/Users` and `/Volumes` access outside the temporary WAV and `local_tools/whisper/`. Sandbox enforcement is self-tested before running. | `tests/test_transcribe.py` |

---

### Surface D: Prompt-Injection Isolation & Post-Generation Guardrails (`B4`)

| STRIDE | Threat Scenario | Implemented Mitigation | Verification |
| :--- | :--- | :--- | :--- |
| **Tampering (Indirect Prompt Injection)** | Hostile instructions embedded inside a public trend webpage, a viewer comment, or an agency's `.docx` brand brief hijack the LLM into injecting banned ad claims, altering Voice DNA, or ignoring creator rules. | 1. **Explicit Untrusted Fencing:** Public trend excerpts are stripped of `<script>`/`<style>`/HTML tags, truncated, and wrapped in `[UNTRUSTED_WEB_EXCERPT]` / `<untrusted_trend_page>` tags (`prompts/10_trend_page_reader.md`); audience comments are wrapped in `<untrusted_viewer_comments>` (`prompts/09_comment_insights.md`) with system instructions treating inner text strictly as inert data.<br>2. **Sponsored Brief Air-Gap (`studio/sponsored.py`):** Raw agency brief text is digested in Stage 3 with grounded quote verification and **never** enters Stage 4 script generation (`prompts/04`).<br>3. **Corpus Receipt Verification (`studio/evidence.py`):** Proposed Voice DNA habits and beat examples (`sample_beats`) are verified against verbatim confirmed non-sponsored creator text (`LIVE_CLIP` and unconfirmed transcripts excluded).<br>4. **Deterministic Post-LLM Gate (`studio/checks.py` → `wordlist.py` + `guardrails.py`):** Every generated or edited script is linted deterministically in Python—never delegated to an LLM—against `studio/lexicon.json` plus the creator's own forbidden phrases, compiling zero user-supplied strings as raw regexes. | `tests/test_wordlist.py`, `tests/test_sponsored.py`, `tests/test_evidence.py`, `tests/test_benchmark.py` |
| **Repudiation** | Background Autopilot modifies Voice DNA, lessons, or candidate topics without a clear audit trail or rollback path. | Autopilot defaults to `off`; `suggest` mode writes non-destructive chips only; `auto` mode enforces a daily model-call cap, logs every input/diff in `autopilot_runs`, and provides deterministic one-click **Undo**. | `tests/test_autopilot.py` |

---

### Surface E: `.lmvbackup` Restore, Auto-Updater & CI/Release Supply Chain (`B5`)

| STRIDE | Threat Scenario | Implemented Mitigation | Verification |
| :--- | :--- | :--- | :--- |
| **Tampering / Elevation (`.lmvbackup` Restore)** | A crafted `.lmvbackup` archive contains path traversal, a multi-GB decompression bomb, malicious SQLite triggers/views/SQL functions that execute on `SELECT`, or corrupt table schemas. | `studio/backup.py` inspects the ZIP End-of-Central-Directory first, enforces strict entry count and uncompressed byte caps, allows only the fixed member allowlist (`manifest.json`, `studio.db`, `config.json`), verifies SHA-256 digests in `manifest.json`, opens the candidate SQLite file with defensive PRAGMAs (`trusted_schema = OFF`, `cell_size_check = ON`), parses `sqlite_master` with a strict plain-DDL verifier that refuses `TRIGGER`, `VIEW`, `VIRTUAL TABLE`, `CHECK`, and expression indexes, verifies `layout_fingerprint()` against `DATA_VERSION`, copies rows column-by-column into a freshly initialized `Store` (never dropping the untrusted SQLite file directly onto `data/studio.db`), runs `backup._try_out` read checks across every table, creates a pre-restore safety backup with automatic rollback on failure, and bumps `X-Data-Epoch` so open tabs stop writing stale state. | `tests/test_backup.py` |
| **Tampering (Updater & Migration)** | Update download is truncated/corrupted in transit, `.zip` update contains symlinks or `../` entries, or `.zip` data migration clobbers an active workspace. | `studio/updater.py` streams downloads with a hard byte cap, requires a matching `.sha256` digest before saving, refuses symlinks, absolute/traversal paths, `data/`, and `local_tools/` entries inside update archives, and permits `POST /api/update/migrate` **only** into a fresh empty destination workspace after per-key `config.json` validation and full `backup._try_out` verification (`local_tools/whisper` is never copied). | `tests/test_updater.py` |
| **Tampering (CI & Release Supply Chain)** | Mutable GitHub Action tags, `${{ ... }}` shell injection in workflow `run:` steps, non-reproducible build artifacts, or unpinned Python dependencies introduce supply-chain drift. | 1. **Zero Runtime PyPI Dependencies:** `studio/*.py` and `tools/*.py` use Python 3.9+ stdlib exclusively, enforced via AST import scanning in `tools/sbom.py` (`tests/test_sbom.py`).<br>2. **Reproducible Builds & CycloneDX 1.5 SBOM:** `tools/build_release.py` reads committed git blobs at `HEAD` into `ZIP_STORED` archives with UTC commit timestamps and builds twice in CI (`cmp`); `tools/sbom.py` emits a deterministic CycloneDX 1.5 `sbom.cdx.json` (+ `.sha256`) covering `Cargo.lock` crates, 40-SHA pinned GitHub Actions, and SHA-256 digests of every prompt, lexicon, and schema file.<br>3. **Hardened Workflows (`WorkflowsTest`):** `.github/workflows/tests.yml`, `release-candidate.yml`, and `release.yml` default to `permissions: contents: read` (only `publish` has `contents: write`), pin every action to a 40-char commit SHA under `actions/*`, set `persist-credentials: false`, and forbid `${{` inside every `run:` block. | `tests/test_release.py`, `tests/test_sbom.py` |

---

## 3. Known Residual Risks & Honest Boundaries

1. **Unsigned Desktop Installers & Same-Origin Checksums:** Until Apple Developer ID + notarization and Windows Authenticode certificates are provisioned (tracked as Gate 1 in [V1_ROADMAP.md](V1_ROADMAP.md)), macOS Gatekeeper and Windows SmartScreen warn on first launch, and `.sha256` sidecars downloaded from the same GitHub Release verify transport integrity rather than cryptographic publisher signatures. Users can independently reproduce `learn-my-voice-<version>.zip` and `sbom.cdx.json` from git source and compare SHA-256 digests.
2. **Same-OS-User Local Access:** Processes running as the same OS user can read `data/studio.db`, `data/server-token`, and the user-scoped key file used by `keystore.py`. Full-disk encryption (FileVault / BitLocker / LUKS) and OS account separation remain the outer boundary.
3. **Probabilistic LLM Outputs Outside Lexicon Rules:** While `checks.lint_payload` deterministically catches banned/anti-voice phrases and `evidence.py` checks corpus receipts, subtle semantic inaccuracies or nuanced compliance edge cases in brand-new regulations still require human creator review before filming and posting.
