# Changelog

Each beta release is a tag on `main` and a pre-release on GitHub. Versions are `v0.<iteration>.0-beta`.
The README's Status section says what has and has not been verified; this file says what changed.

## v0.1.0-beta (15 November 2026)

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
