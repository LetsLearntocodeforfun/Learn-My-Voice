# Learn-My-Voice — Deep Security Audit, Remediation & Second-Pass Verification Ledger

- **Repository:** `/Users/marccopeland/Video script creation` (`LetsLearntocodeforfun/Learn-My-Voice`)
- **Base Commit Audited:** `2e45ca6`
- **Scope:** Full-stack defensive security audit, functional showstopper remediation, RedNote (`小红书`) views/trending ranking fixes, algorithmic/SQLite resource-bound hardening, second-pass line-by-line audit, and automated + real headless Google Chrome E2E verification.
- **Purpose of This Document:** Complete, self-contained engineering ledger designed so an independent human or AI verification agent can audit every finding, inspect every changed file and symbol, and re-run deterministic verification commands to confirm completeness.

---

## 1. Executive Summary of Remediations

| ID | Severity | Area | Summary of Root Cause | Remediation Status |
| :--- | :--- | :--- | :--- | :--- |
| **BUG-01** | **P0 Showstopper** | Voice DNA Approval UI | `studio/web/setup.js` omitted `created_at` (required since backend optimistic proposal check) and `keep` (causing free-text summaries/playbooks to be discarded even if approved) on `POST /api/voice/approve`, returning HTTP `400 missing_created_at` on every click of **Accept this profile**. | **FIXED & Verified in Headless Chrome** |
| **SEC-01** | **P0 High** | Loopback HTTP API Auth & Browser Isolation | `127.0.0.1` server had zero authentication on `GET /api/*` (exposing full corpus, scripts, Voice DNA, file paths, and Python path to any local process or same-host DNS-rebinding/iframe flow) and lacked `Sec-Fetch-Site` checks. | **FIXED & Verified** |
| **SEC-02** | **P0 High** | Plaintext Cloud API Keys & Windows NTFS ACLs | Cloud API keys (`gemini`, `openai`) were written in plaintext to `data/config.json`; `os.chmod(..., 0o700)` on Windows is a no-op for multi-user NTFS DACLs; `desktop/src-tauri/src/main.rs` created `data/` and `desktop-running.json` with default umask. | **FIXED & Verified** |
| **SEC-03** | **P1 High** | Supply-Chain Integrity (`pip`, `npx`, Launchers) | `studio/transcribe.py` installed 37 PyPI wheels without `--require-hashes` (`SHA-256`) and inherited `SSL_CERT_FILE`/`REQUESTS_CA_BUNDLE`; `desktop/build.py` ran `npx @tauri-apps/cli` without `--ignore-scripts`; CLI launchers lacked `-B -E -s`; `run_mcp.py` left `""`/`.` on `sys.path`. | **FIXED & Verified** |
| **SEC-04** | **P1 Medium-High** | HTTP Redirect Credential/Prompt Leak & Model Injection | `studio/llm.py` followed `301`/`302`/`307`/`308` redirects on both local (`_DIRECT`) and cloud requests (risking `Authorization` / `x-goog-api-key` or private corpus leakage to redirect targets) and interpolated unvalidated `model` strings directly into Gemini URL paths. | **FIXED & Verified** |
| **SEC-05** | **P1 Medium-High** | macOS Seatbelt Sandbox Tightening (`WORKER_SEAL`) | `whisper_worker.py` Seatbelt profile only blocked a narrow list of named home subfolders (`Documents`, `Desktop`, etc.), leaving sibling folders under `/Users/<me>/` readable/writable and `com.apple.lsd.*` LaunchServices IPC open; model downloads also lacked HTTPS-only redirect enforcement and stream-size caps. | **FIXED & Verified (75/75 `test_transcribe` on Metal)** |
| **BUG-02** | **P1 Product Core** | RedNote `views` Ignored When Interactions Absent | `corpus.exemplars()`, `trends.score_trends()`, and `hooks.library()` / `hooks.best()` ranked and scored lift solely on `likes + collects + comments`, treating creators who imported/entered `views` without interaction breakdowns as having zero performance data (`0.5` flat neutral lift). | **FIXED & Verified** |
| **SEC-06** | **P2 Medium** | Tauri Desktop Shell Hardening | `tauri.conf.json` used `default-src 'self'` instead of `default-src 'none'`; `main.rs` permitted `data:` and `blob:` top-level navigations, did not sanitize suggested download filenames against reserved Windows names/BiDi chars, and fell back to overwriting `.999` in `free_name`. | **FIXED & Verified** |
| **SEC-07** | **P2 Medium** | MCP Server Stdin/JSON/Export Hardening | `studio/mcp_server.py` read unbounded lines via `for raw in stdin:` (OOM vector), accepted unbounded JSON-RPC batches, used `json.loads` instead of `llm.loads_strict` (allowing `NaN`/`Infinity` and `RecursionError`), and overwrote exports directly instead of using `outside.save()`. | **FIXED & Verified** |
| **SEC-08** | **P2 Medium** | Algorithmic & SQLite DoS Bounds | Unbounded script `title`/`notes`/`lines`/`post` payloads in `Store`; unbounded `trends` growth; `list_scripts()` parsing full `lines_json` on every row; `GET /api/state` loading all samples/trends just to count them; uncached `setup.status()` corpus scan; quadratic `SequenceMatcher.ratio()` in `learning.py`; `O(N^2)` `ranked_ids.index()` in `pipeline.py`; unbounded candidate loop in `suggest.py`. | **FIXED & Verified** |
| **SEC-09** | **P2 Low-Medium** | `normalize_local_base` & Error Secret Scrubbing | `llm.normalize_local_base()` accepted embedded `user:pass@`, `?query`, `#fragment`, and out-of-range ports; upstream HTTP error bodies echoing `sk-...` / `AIza...` or active header secrets were surfaced unsanitized. | **FIXED & Verified** |
| **BUG-03** | **P2 UX / Integrity** | Frontend-Backend Contract Gaps (5 Items) | 1) Voice DNA history/restore API unwired in UI; 2) `dna_unreadable` warning unwired in UI; 3) `base_updated_at` optimistic locking & `keepalive` unwired in `saveNow()`; 4) `rednote_post_package` (`post`) read-only in script editor; 5) `suggest.py` rejected Stage 05 `flagged_and_repaired_lines` critic replies and crashed if `hard_max` was absent. | **FIXED & Verified in Headless Chrome** |

---

## 2. File-by-File Inventory of All Changes

### 2.1 Created Files
1. **`studio/keystore.py`** (New stdlib-only module — `SEC-02`)
   - **Windows (`os.name == "nt"`):** Protects API keys via Win32 `CryptProtectData` / `CryptUnprotectData` (`crypt32.dll`) with `CRYPTPROTECT_UI_FORBIDDEN = 0x1` and domain entropy `b"learn-my-voice:config:v1"`, freeing buffers with `kernel32.LocalFree`. Also implements `restrict_windows_acl(folder)` using `whoami /user /fo csv /nh` (validated against `^S-1-\d+(?:-\d+)+$`) and locale-independent well-known SIDs (`*S-1-5-18` SYSTEM, `*S-1-5-32-544` Administrators, `*<user-sid>`) via `icacls ... /inheritance:r /grant:r`.
   - **macOS / Linux:** Protects API keys via Encrypt-then-MAC (`SHA-256` counter stream + `HMAC-SHA256` tag verified with `hmac.compare_digest` over domain-separated keys) using a 32-byte `0600` master key stored outside `data/` in the user's OS application-config directory (`~/Library/Application Support/io.github.letslearntocodeforfun.learn-my-voice/keystore.key` on macOS, `$XDG_CONFIG_HOME/learn-my-voice/keystore.key` on Linux, overrideable via `STUDIO_KEYSTORE_FILE` for isolated tests). Avoids extra files inside `data/` and avoids modal macOS Keychain GUI dialogs on unsigned/headless runs.
2. **`tests/test_audit_remediation.py`** (New 23-test regression & E2E browser suite)
   - Exercises every `SEC-*` and `BUG-*` remediation end-to-end, including a real headless Google Chrome (`--headless=new`) DevTools WebSocket test (`RealChromeEndToEndUiTest`).
3. **`AUDIT_AND_REMEDIATION_LOG.md`** (This verification ledger; added to `LEFT_OUT` in `tools/build_release.py` so end-user downloadable `.zip` packages stay minimal).

### 2.2 Modified Backend Modules (`studio/`)
1. **`studio/store.py`** (`SEC-02`, `SEC-08`, Second-Audit Hardening)
   - `load_config()` / `save_config()`: Seals `config["keys"]` into `keys_sealed` and writes `"keys": {}` on disk; transparently unseals on load and migrates legacy plaintext keys on next save.
   - `_owner_only()`: Calls `keystore.restrict_windows_acl(root)` on Windows directories and handles single-file paths cleanly without `os.walk` overhead when `root` is a file.
   - `_db()`: Enables `PRAGMA busy_timeout = 10000` (keeping rollback journal mode so `data/` never accumulates `-wal`/`-shm` sidecars).
   - `loads_stored()`: Catches `RecursionError` and raises `ValueError("JSON nested too deeply")`.
   - Added constants `MAX_TRENDS = 2000`, `MAX_SCRIPT_TITLE_CHARS = 500`, `MAX_NOTES_CHARS = 50000`, `MAX_SCRIPT_LINES = 500`, `MAX_LINE_CHARS = 5000`, `MAX_POST_CHARS = 30000`, enforced in `normalize_lines()`, `create_script()`, `update_script()`, `create_sample()`, `update_sample()`, and `replace_sample_from_video()`, plus 64-char bounds on `video_format` and `status`.
   - Added `line_count` and `preview` columns to `scripts` (migrated via `_ADDED_COLUMNS` with legacy fallback in `list_scripts()`), eliminating full `lines_json` parsing on script-list loads.
   - Added `samples_count()`, `trends_count()`, expanded `samples_version()` (`MAX(reviewed_at)`, `SUM(LENGTH(text))`, `SUM(LENGTH(timing_json))`, `SUM(LENGTH(ai_json))`), and added `MAX_TRENDS` pruning in `upsert_trends()` (evicting oldest non-manual trends before manual ones).
2. **`studio/server.py`** (`SEC-01`, `SEC-04`, `SEC-08`, Second-Audit Hardening)
   - `App`: Generates 256-bit `token` and `session_cookie` (`secrets.token_urlsafe(32)`). When `require_token=True` (enabled by default in `server.main()`, disabled only with `--no-auth-token`; `make_server` defaults `require_token=False` so existing unit tests require no boilerplate), atomically writes `data/server-token` with `0600` permissions and deletes it in `StudioServer.server_close()`.
   - `Handler._check_auth()` & `_dispatch()`:
     - Enforces `Sec-Fetch-Site` (`same-origin` or `none` when present) on all `/api/*` and non-`GET` routes (`403 bad_origin`).
     - Bootstraps `GET /?token=<token>` via `302` redirect to normalized `"/" + path.lstrip("/")` (blocking `///evil.example` protocol-relative open redirects) with `Set-Cookie: studio_session=...; Path=/; HttpOnly; SameSite=Strict`.
     - Injects `data-token="<token>"` into `<html>` via `page_with_keeps()` when `require_token=True`.
     - Requires constant-time (`hmac.compare_digest`) `X-Studio-Token` header on all non-`GET` `/api/*` requests (`session_cookie` alone is insufficient for writes, preventing cross-subdomain/cookie CSRF), and requires either `X-Studio-Token` or `studio_session` on `GET` routes.
     - Maps `TextTooLong` centrally to HTTP `413`.
   - `GET /api/state`: Uses `store.samples_count()` and `store.trends_count()` instead of materializing full tables.
   - `POST /api/settings`: Validates `model` with `llm.validate_model_name()` (`400 bad_model`).
   - `main()`: Prints both the loopback base address (`http://127.0.0.1:<port>/`, preserving compatibility with the Tauri shell parser) and the authenticated launch URL, opening `launch_url` in the user's browser.
3. **`studio/llm.py`** (`SEC-04`, `SEC-07`, `SEC-09`, Second-Audit Hardening)
   - Added `_NoRedirect(urllib.request.HTTPRedirectHandler)` to both `_DIRECT` (loopback) and cloud openers in `_open()`, refusing all `3xx` redirects before credentials or prompts can be forwarded.
   - Added `validate_model_name()`, `_gemini_model_id()` (for Gemini `interactions` JSON body), and `_gemini_model_segment()` (URL-encoded path segment for `models/<id>:generateContent`), enforced across `search_json()` and `complete_json()`.
   - Hardened `normalize_local_base()` to reject `username`, `password`, `query`, `fragment`, non-loopback hosts, non-`http(s)` schemes, and invalid ports.
   - Caught `RecursionError` in `loads_strict()`.
   - Added `_redact_secrets()` scrubbing active request header secrets, `sk-...`, and `AIza...` tokens from HTTP/network error messages.
4. **`studio/transcribe.py` & `studio/whisper_worker.py`** (`SEC-03`, `SEC-05`, Second-Audit Hardening)
   - Added `PACKAGE_HASHES` containing all 45 official PyPI `SHA-256` wheel digests (`macosx_11_0_arm64` / `macosx_12_0_arm64` / `py3-none-any`) for `PIP_PIN` (`pip==26.0.1`) and all 36 `PACKAGES`, formatted via `_requirements_text()` and enforced with `--require-hashes` in `_pip_install()`.
   - Stripped `SSL_CERT_FILE` and `REQUESTS_CA_BUNDLE` in `_install_env()`.
   - Replaced `WORKER_SEAL` subfolder denylist with a default-deny read/write profile over `/Users`, `/Volumes`, `/private/tmp`, `/private/var/folders`, and `/private/var/tmp`, allowing reads only for `WAV`, `WORKER`, literal `ROOT_DIR`, `STUDIO_DIR`, `WHISPER_HOME`, and `SCRATCH`, allowing writes only to `WHISPER_HOME` and `SCRATCH`, and denying `com.apple.lsd.*` Mach lookups. `whisper_worker.py` `chdir`s into `os.path.dirname(wav_path)` before importing `mlx_whisper`.
   - Added `_HttpsRedirectHandler` in `_open_url()` (refusing non-`https://` downloads or redirects) and bounded `_fetch_file()` stream reads so oversized responses abort immediately at `size + 1` bytes and delete `.part`.
5. **`studio/mcp_server.py`** (`SEC-07`)
   - Replaced unbounded `for raw in stdin:` with bounded `stdin.readline(MAX_MCP_LINE_BYTES + 1)` (`12 MB`), draining oversized lines in 64 KB chunks and returning JSON-RPC `-32700`.
   - Capped JSON-RPC batch arrays at `MAX_MCP_BATCH = 64` (and rejected empty `[]` with `-32600`), switched parsing to `llm.loads_strict()`, and set `allow_nan=False` on `json.dumps`.
   - Updated `t_export_jianying_kit()` to lock `exports/` (`0700`), delegate file creation to `outside.save()` (preventing overwrites and reserved-device-name hazards), and lock the resulting `.zip` (`0600`).
6. **`studio/corpus.py`, `studio/trends.py`, `studio/hooks.py`** (`BUG-02`, `SEC-08`)
   - `corpus.exemplars()`: Added `s.get("views") or 0` to organic sample sort key so high-view videos rank first when interaction breakdowns are absent.
   - `trends.score_trends()`: Added `views_baseline` median fallback when `baseline` interactions are absent so keywords matching high-view videos earn real performance lift (`0.0–1.0`), and hoisted `guardrails.compile_rules(dna, lexicon)` outside the per-trend loop.
   - `hooks.library()` & `hooks.best()`: Added `usual_views` fallback when `usual_interactions` is `None`, computing `times_usual`, `videos_with_numbers`, pattern `times_usual`, and `hooks.best()` from `views`.
7. **`studio/guardrails.py`, `studio/setup.py`, `studio/pipeline.py`, `studio/learning.py`, `studio/suggest.py`** (`SEC-08`, `BUG-03.5`)
   - `guardrails.py`: Added `CompiledRules` + `compile_rules()` and optional `compiled` parameter on `lint_script()`.
   - `setup.py`: Cached `_material_summary()` on `Store` keyed by `store.samples_version()`.
   - `pipeline.py`: Replaced `O(N^2)` `ranked_ids.index()` in `ideas_prompt()` with `O(1)` `rank_by_id` dictionary lookup.
   - `learning.py`: Added `matcher.real_quick_ratio()` and `matcher.quick_ratio()` upper-bound guards before `matcher.ratio()` in `line_changes()`.
   - `suggest.py`: Capped raw model candidate list at `max_raw = 64`, hoisted `line_keys` precomputation outside the candidate loop, added fallback for `stats["hard_max"] is None` (`16`), and mapped Stage 05 `flagged_and_repaired_lines` (`repaired_zh` / `why_fixed_en_zh`) when `kind == CRITIC`.

### 2.3 Modified Frontend Modules (`studio/web/`)
1. **`studio/web/app.js`** (`SEC-01`, `BUG-03.2`, `BUG-03.3`)
   - Reads `<html data-token="...">` (`STUDIO_TOKEN`) and attaches `X-Studio-Token` via `studioHeaders()` on all `api()` calls.
   - Sends `base_updated_at` and supports `{ keepalive: true }` on `saveNow()` / `beforeunload`, surfacing bilingual `err_stale_script` conflict notices.
   - Displays `dna_unreadable_warn` banner on `#/voice` when `GET /api/dna` reports `unreadable: true`.
2. **`studio/web/setup.js`** (`BUG-01`, `SEC-01`)
   - Uses `studioHeaders()` on raw `PUT /api/uploads`.
   - Renders default-checked checkboxes (`data-free-path`) for `p.report.unverified` free-text items in `reviewHtml(p)`, marks `not_found` habits and `not_checkable` items with `data-needs-keep="1"`, and posts `{ created_at: p.created_at, keep, rejected }` to `POST /api/voice/approve`, refreshing gracefully on `stale_proposal` / `no_pending`.
3. **`studio/web/pages.js`** (`BUG-03.1`, `BUG-03.4`)
   - Fetches `GET /api/voice/history` on `#/voice`, renders archived Voice DNA snapshots with confirmation (`askConfirm(..., { danger: true })`), and restores via `POST /api/voice/history/<id>/restore`.
   - Upgrades `paintPost()` in the script editor so `title_options`, `cover_thumbnail_text_options_zh`, `caption_body_zh`, and `hashtags_zh` are editable in-place (`data-post-kind`) and trigger `scheduleSave()`.
4. **`studio/web/discover.js` & `studio/web/posted.js`** (`SEC-01`)
   - Attached `studioHeaders()` on raw `PUT /api/uploads` video upload requests.

### 2.4 Modified Desktop Shell, Launchers, Release Tools & Existing Tests
1. **`desktop/src-tauri/src/main.rs` & `desktop/src-tauri/tauri.conf.json`** (`SEC-01`, `SEC-02`, `SEC-06`)
   - Set `csp` in `tauri.conf.json` to `"default-src 'none'; script-src 'self'; style-src 'self'; base-uri 'none'; form-action 'none'"`.
   - Enforced `0700` on `data_folder()` and `0600` in `write_private()` (`desktop-running.json`, `desktop-problem.txt`).
   - Appended `?token=<token>` read from `data/server-token` onto `launch_url` while keeping `shown` clean in `desktop-running.json`.
   - Removed `"data"` and `"blob"` from `.on_navigation`.
   - Added `sanitize_download_name()` (stripping control/BiDi chars, path separators, trailing dots, reserved Windows names, and capping UTF-8 bytes) and updated `free_name() -> Option<PathBuf>` to cancel download (`return false`) if all 999 slots are occupied.
2. **`desktop/build.py`** (`SEC-03`): Added `--ignore-scripts` to `npx` invocation in `tauri_command()`.
3. **`Start Learn My Voice.command`, `启动工作台.command`, `start-learn-my-voice.sh`, `Start Learn My Voice.bat`, `run_mcp.py`** (`SEC-03`)
   - Added `-X utf8 -B -E -s` flags to Python invocations (preserving CRLF line endings on `Start Learn My Voice.bat`), and stripped `""`/`.` from `sys.path` in `run_mcp.py`.
4. **`tools/try_release.py`, `desktop/try_installer.py`, `tools/build_release.py`**
   - Updated `try_release.py` and `desktop/try_installer.py` to read `data/server-token`, verify unauthenticated `GET /api/state` is rejected with `401`, and include `X-Studio-Token` on authenticated trial requests. Added `AUDIT_AND_REMEDIATION_LOG.md` to `LEFT_OUT` in `tools/build_release.py`.
5. **`tests/test_desktop.py`, `tests/test_outside.py`, `tests/test_transcribe.py`, `tests/test_wordlist.py`**
   - Updated expectations for the hardened Tauri CSP / `--ignore-scripts` (`test_desktop.py`), CLI server `server-token` header and 7th `askConfirm` call (`test_outside.py`), `--require-hashes` pip mock (`test_transcribe.py`), and encrypted `keys_sealed` on disk (`test_wordlist.py`).

---

## 3. Second Deep Post-Remediation Audit Findings & Fixes

After completing the initial implementation across all 12 items, a second line-by-line audit was conducted across all `git diff` patches and untouched `studio/*.py` modules. Six subtle edge cases were discovered and immediately fixed prior to final sign-off:

1. **Protocol-Relative Open Redirect on `GET /?token=` (`studio/server.py`):**
   - *Discovery:* `GET ///evil.example/?token=<valid_token>` yielded `split.path == "//evil.example/"`, which would emit `Location: //evil.example/`.
   - *Fix:* Normalized redirect target to `dest = "/" + (path or "").lstrip("/")`, guaranteeing a single leading slash. Verified in `TokenAuthAndFetchMetadataTest`.
2. **Gemini Model Identifier Encoding in JSON Body vs URL Path (`studio/llm.py`):**
   - *Discovery:* `search_json()` sent `_gemini_model_segment(model)` (URL-percent-encoded) inside a JSON body (`{"model": ...}`), whereas URL percent-encoding belongs only in `complete_json()`'s URL path.
   - *Fix:* Split `_gemini_model_id()` (validated unquoted ID for JSON bodies) from `_gemini_model_segment()` (URL-quoted segment for URL paths).
3. **Model Download HTTPS Downgrade & Infinite Stream Disk Exhaustion (`studio/transcribe.py`):**
   - *Discovery:* `_open_url()` used default `urlopen` (which follows `https://` -> `http://` redirects), and `_fetch_file()` looped `response.read(1024 * 1024)` without stopping if a rogue server streamed gigabytes past the pinned `size`.
   - *Fix:* Enforced `https` scheme + `_HttpsRedirectHandler` in `_open_url()` and capped `_fetch_file()` reads at `size + 1` bytes, unlinking `.part` and raising `download_damaged` immediately on overflow. Verified in `SupplyChainAndSandboxHardeningTest`.
4. **Deeply Nested Stored JSON `RecursionError` (`studio/store.py`):**
   - *Discovery:* `llm.loads_strict()` caught `RecursionError`, while `store.loads_stored()` only caught `RecursionError` in `_as_object()` and not in `_json_object()` / other callers.
   - *Fix:* Caught `RecursionError` inside `loads_stored()` and converted to `ValueError`.
5. **Strict SID Validation & Cross-Platform `creationflags` (`studio/keystore.py`):**
   - *Discovery:* Validated `whoami /user` SID strictly against `^S-1-\d+(?:-\d+)+$` and guarded `creationflags=_CREATE_NO_WINDOW` with `sys.platform == "win32"`.
6. **Frontend `paintPost` Auto-Save & Missing `hard_max` Fallback (`studio/web/pages.js`, `studio/suggest.py`):**
   - *Discovery:* `paintPost.oninput` called `markDirty()` instead of `scheduleSave()`, and `suggest.suggest_prompt()` called `int(stats["hard_max"])` without guarding `None` when a hand-edited Voice DNA omitted `hard_max`.
   - *Fix:* Changed to `scheduleSave()` (verified in real headless Chrome) and defaulted `hard_max` to `16` when `None`.

---

## 4. Architectural Scope Notes (Transparent Product Capabilities)

The following product-architecture boundaries are intentional offline/privacy design choices rather than code bugs, and are documented here for full transparency:
1. **RedNote (`小红书`) Platform Ingestion Model:** The app does not scrape Xiaohongshu (`xhslink.com` / `xiaohongshu.com`) directly with headless browsers or cookies (which violates platform ToS and triggers anti-bot bans). Instead, it ingests local video/audio files (transcribed offline via Apple Silicon MLX Whisper), `.srt`/`.vtt` subtitles, `.txt`/`.md`/`.docx`/`.csv` keyword exports, pasted scripts, manual view/interaction counts, and optional Gemini/OpenAI web-search grounding.
2. **Text/Timing Cadence vs Acoustic Pitch:** "Voice DNA" models lexical phrasing, modal particles, openers, code-switching, anti-slop bans, clause breath lengths, and measured speaking rate (CPM from timestamped subtitles/Whisper segments); it does not extract acoustic pitch (`F0`) or burn-in video OCR (`花字`) from raw video pixels.
3. **Code Signing Certificates:** `desktop/src-tauri/tauri.conf.json` uses ad-hoc signing (`"-"`) on macOS and unsigned NSIS on Windows until Apple Developer ID / Windows Authenticode certificates are provisioned in CI secrets.

---

## 5. Independent Verification Checklist (Commands for Another Agent)

Any verification agent can run the following commands from `/Users/marccopeland/Video script creation` to independently verify every fix:

```bash
# 1. Inspect full git status and diffs across all modified files
git status -s
git diff

# 2. Run the dedicated 23-test security, views-lift & real headless Chrome E2E suite (~6s)
python3 -m unittest -v tests.test_audit_remediation

# 3. Run the real Apple Silicon MLX Whisper + macOS Seatbelt sandbox suite (75 tests, ~29s)
python3 -m unittest tests.test_transcribe

# 4. Run the complete 886-test repository suite (~160s)
python3 tools/run_tests.py

# 5. Syntax-check all 11 frontend & desktop JavaScript files via macOS JavaScriptCore
python3 -c '
import glob, subprocess
jsc = "/System/Library/Frameworks/JavaScriptCore.framework/Versions/A/Helpers/jsc"
files = sorted(glob.glob("studio/web/*.js") + glob.glob("desktop/ui/*.js"))
for p in files:
    r = subprocess.run([jsc, "-e", f"checkSyntax({p!r})"], capture_output=True, text=True)
    assert r.returncode == 0, f"{p}: {r.stdout} {r.stderr}"
print("All", len(files), "JS files syntax-clean")
'
```
