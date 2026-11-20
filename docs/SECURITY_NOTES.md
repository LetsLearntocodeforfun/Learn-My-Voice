# Security & Privacy Architecture Notes

[Quick Start Guide](QUICKSTART.md) | [快速上手指南](QUICKSTART.zh-CN.md) · [README](../README.md) | [中文说明](../README.zh-CN.md)

Learn-My-Voice runs locally on your own computer. This note summarizes the defensive boundaries built into `v0.2.0-beta` so creators and contributors can see how local scripts, credentials, and external inputs are protected.

## 1. Loopback Isolation & Per-Launch Capability Token

- **Loopback-only binding:** `studio/server.py` binds exclusively to `127.0.0.1` and verifies the `Host` header on every request.
- **Per-launch capability token:** When started from the portable `.zip` launcher or command line (as well as inside the desktop app), the server generates a fresh 256-bit capability token (`data/server-token`, mode `0600`) and a port-scoped `HttpOnly; SameSite=Strict` session cookie.
- **Cross-site protection:** Non-`GET` `/api/*` requests require a constant-time (`hmac.compare_digest`) `X-Studio-Token` header and enforce `Sec-Fetch-Site` (`same-origin` or `none`), preventing cross-site pages or unauthenticated local tabs from reading or modifying your scripts, library, or settings.

## 2. Encrypted-at-Rest API Keys & Local Data Permissions

- **Sealed API keys (`studio/keystore.py`):** Cloud API keys (`Gemini`, `OpenAI`) are never stored in plaintext inside `data/config.json` and are never returned to the browser.
  - **Windows:** Keys are protected with Win32 DPAPI (`CryptProtectData` / `CryptUnprotectData` with domain entropy).
  - **macOS & Linux:** Keys are sealed with Encrypt-then-MAC (`SHA-256` counter stream + `HMAC-SHA256`) using a `0600` master key stored outside `data/` in your user application-config folder. *(Threat model: sealing keeps plaintext keys out of `data/` copies, sync folders, and backups; any program running under your same OS user account shares your OS permissions.)*
  - **Safe cross-machine handling:** If a `data/` folder is copied to another computer or user account where the sealed key cannot be opened, the sealed blob is preserved untouched (never silently wiped) and Settings asks you to enter the key on that machine.
- **Owner-only permissions:** On macOS and Linux, `data/` and its files are set to `0700` / `0600`. On Windows, `icacls` restricts `data/` to the current user SID, `SYSTEM`, and `Administrators`.
- **Single-file backups exclude keys:** Backups created from **Settings → Backup and restore** never include API keys, so backup archives can be stored on cloud drives without exposing credentials.

## 3. Outbound Network & Redirect Hardening

- **No redirect credential forwarding:** Cloud API requests reject `3xx` HTTP redirects before `Authorization` / API key headers or prompts can be forwarded elsewhere. Local-model (`Ollama` / `LM Studio`) requests bypass system proxies, reject embedded URL credentials, queries, or fragments, and follow at most 3 redirects only when the redirect target also resolves strictly to loopback (`127.0.0.1` / `::1`).
- **Secret scrubbing:** Upstream HTTP/network error messages scrub active header tokens and `sk-...` / `AIza...` key patterns before surfacing diagnostics.
- **No platform scraping:** The studio never logs into or scrapes Xiaohongshu (`xhslink.com` / `xiaohongshu.com`). All material enters from files you choose or text you paste.

## 4. On-Device Transcription Supply Chain & macOS Sandbox Seal

- **SHA-256 wheel & model pinning:** Setting up local Apple Silicon transcription (`studio/transcribe.py`) enforces `--require-hashes` across all pinned PyPI wheel SHA-256 digests and verifies exact byte sizes and SHA-256 checksums on speech model weights over HTTPS-only connections.
- **macOS Seatbelt seal (`AUDIO_SEAL` / `WORKER_SEAL`):** Both audio extraction (`afconvert`) and `whisper_worker.py` execute under macOS `sandbox-exec` rules that block all network access, block LaunchServices IPC, and default-deny reads and writes across `/Users` and `/Volumes` outside the single temporary audio file and Whisper runtime folder. Before transcribing, the app tests that macOS is actively enforcing the seal.

## 5. Desktop App Shell & Hostile-Input Bounds

- **Tauri 2 shell (`desktop/src-tauri/`):** Enforces a strict Content Security Policy (`default-src 'none'`), blocks `data:` and `blob:` top-level navigations, sanitizes suggested download filenames against path separators, BiDi control characters, and Windows reserved device names, and never overwrites existing files in `Downloads`.
- **Bounded parsers & fuzzing (`tools/fuzz.py`):** `.docx` archives, `.xlsx`/`.csv` keyword imports, `.srt`/`.vtt` subtitles, manual JSON replies/files, MCP stdio frames, and HTTP payloads enforce strict byte, decompression, row/column, and JSON recursion limits.
