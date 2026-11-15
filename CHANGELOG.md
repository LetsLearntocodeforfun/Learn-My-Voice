# Changelog

Each beta release is a tag on `main` and a pre-release on GitHub. Versions are `v0.<iteration>.0-beta`.
The README's Status section says what has and has not been verified; this file says what changed.

## v0.1.1-beta (15 November 2026)

Security and reliability fixes for v0.1.0-beta. Nothing else changes. **Anyone using v0.1.0-beta should update.**

**Fixed**

- **Requests from other websites are refused.** In v0.1.0-beta a web page open in the same browser could send
  writes to the studio while it was running: change the AI provider and key, replace the voice profile, add
  library items and scripts. It could not read anything back, and could not edit or delete existing items.
  The server now refuses any write that names another site as its origin, and accepts a request body only when
  its type is exactly `application/json`.
- **A wrong-shaped voice profile no longer breaks script checking.** A model reply with a field of the wrong
  kind was saved as the profile, and every check then failed with "internal error" until `data/voice_dna.json`
  was deleted by hand. The checks now skip what they cannot use. If this happened to you, updating is enough.
- **A voice profile that cannot be saved no longer replaces the one in use.** The previous profile used to be
  moved to the history folder before the new one was written, so a failed write left the studio on the example
  profile.
- **Two inputs that froze the studio.** A library item with very many opening brackets made the library slow to
  load and held every other request meanwhile (25 seconds for 250,000 of them, and four times longer each time
  the length doubled). A pasted reply that was mostly white space held the server in the same way.

**State of this release**

- 92 earlier tests and the new ones in `tests/test_security.py` pass. The cross-site fix was also checked in
  headless Chrome from a page on another origin.
- Still open in this version, to be fixed in v0.2.0-beta: a voice reply from the model takes effect as soon as
  it arrives, with no review step.
- Everything under "State of this release" for v0.1.0-beta still applies.

## v0.1.0-beta (15 November 2026)

**Superseded by v0.1.1-beta, which fixes security problems in this release. Please update.**

First beta. A local, bilingual (中文 / English) script studio for one Xiaohongshu creator, with nothing to
install except Python.

**In this release**

- Script editor: one card per spoken line, live character count and read time, autosave, teleprompter.
- Rule-based checks in Chinese and English: lines too long for one breath, phrases the creator never uses,
  influencer clichés, translated-sounding Chinese, risky advertising wording, length against the target duration.
- Voice learning from the creator's own scripts. Breath-length limits are measured in code, not taken from the model.
- Drafting anchored on the creator's own best-performing scripts.
- Topic ideas from past performance. Citations that do not match a real video title are removed.
- Trend keywords: paste keywords, rank them by heat, freshness and how the creator's own videos on the topic
  performed. Target keywords on a script, with a check of where each one ended up.
- The title options, cover text, caption and hashtags drafted with a script are shown and can be copied.
- Exports: 剪映 / CapCut kit, `.srt` with estimated timing, voiceover text, shot list, on-screen text list.
- Four ways to provide the AI: copy and paste with a chat subscription (no key), an MCP server, the Gemini or
  OpenAI API, or a local model through Ollama or LM Studio.

**State of this release**

- 92 automated tests pass. The interface was exercised in headless Chrome on macOS.
- AI steps were tested against stand-in model replies only. Output quality with real models has not been evaluated.
- Importing the exported files into 剪映 or CapCut has not been verified.
- The MCP server was tested at protocol level, not with ChatGPT, Codex or Claude Desktop.
- Developed on macOS with Python 3.9. Windows and Linux are untested.
- Prompts and English wording in this release assume one specific creator ("her", with an English-speaking husband).
