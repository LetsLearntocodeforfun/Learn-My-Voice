# Learn-My-Voice

**English** | [简体中文](README.zh-CN.md)

A local, bilingual (中文 / English) script studio for Xiaohongshu (RedNote) and other short-video creators.
It learns how one creator actually talks from her own past scripts, drafts new scripts in that voice,
checks them, and hands the result to 剪映 (Jianying) / CapCut for editing.

It runs on your own computer with nothing to install except Python. On macOS, Python comes with Apple's free Command Line Tools, and the launcher offers to install them if they are missing.

![Script editor with live checks](docs/images/script-zh.png)

*Screenshots use invented sample data.*

## Status

This is an early version. Read this before relying on it.

| Area | State |
| :--- | :--- |
| Script editor, checks, teleprompter, library, exports | Working. Covered by automated tests and exercised in a real browser (headless Chrome, macOS). |
| AI steps (draft, learn voice, topic ideas) | Working against stand-in model replies in tests. Output quality with real models has not been evaluated yet. |
| Trend keywords | Pasting, ranking and keyword checks work, are covered by automated tests and were exercised in headless Chrome. The ranking weights are judgement calls that have **not** been tuned against real results. Lookups by a web-searching AI have only been tested with stand-in replies. |
| 剪映 / CapCut | Exports standard files to import. Importing them has **not** been verified inside 剪映 or CapCut by the maintainers. It does not write 剪映 project drafts. |
| MCP server | Tested at protocol level (JSON-RPC over stdio). **Not** yet verified with ChatGPT, Codex or Claude Desktop. |
| Platforms | Developed on macOS with Python 3.9. Windows and Linux are untested. |

## Quick start

Requirements: Python 3.9 or newer. No packages to install.

```bash
git clone https://github.com/LetsLearntocodeforfun/Learn-My-Voice.git
cd Learn-My-Voice
python3 -m studio.server
```

The studio opens in your browser at `http://127.0.0.1:8765`.

On macOS you can instead double-click `启动工作台.command` or `Start Learn My Voice.command`.
If macOS refuses to open it the first time, right-click the file and choose Open.

The interface starts in Chinese. Use the **English** button at the bottom of the sidebar to switch.

## First 10 minutes

1. **设置 / Settings**: choose how AI is provided (see the next section). The default needs no key.
2. **素材库 / Library**: upload or paste scripts she wrote by hand (`.txt`, `.md`, `.docx`). Five or more is better. Add views, likes, collects and comments where you have them, and tick 商单 for sponsored ones.
3. **我的声音 / Her voice**: click *Learn her voice from the library*, then enter her real speaking rate.
4. **热点 / Trends** (optional): paste keywords you see trending on Xiaohongshu to have them ranked against her own results.
5. **选题建议 / Ideas**: ask for topic suggestions, pick one, and draft it.
6. **我的脚本 / Scripts**: edit the draft, read it with the teleprompter, and export the 剪映 kit.

Until step 3 is done the app uses a bundled *example* voice profile for a fictional creator, and says so on screen.

## Ways to provide the AI

| Option | What you need | Where her text goes |
| :--- | :--- | :--- |
| **ChatGPT subscription (copy & paste)**, the default | A ChatGPT account, or any chat AI. No API key. | The chat you paste into |
| **MCP** | An AI app that supports MCP (see below) | That app's provider |
| **Google Gemini** or **OpenAI** API | An API key | Google or OpenAI |
| **Local model** | Ollama or LM Studio running on the same computer | Nowhere; it stays local |

### Copy & paste with a subscription

A ChatGPT subscription does not include API access, so the studio cannot call it directly. In this mode,
each AI step shows a prepared prompt. Paste it into ChatGPT, then paste the reply back.
The prompt contains her voice profile and the relevant script text.

Looking up trend keywords always works this way, whichever AI option is selected, because it needs an AI that can
search the web at that moment. That prompt contains her video titles rather than script text.

![Copy and paste dialog](docs/images/chatgpt-bridge-zh.png)

### Local models

Start Ollama or LM Studio, download a model, then choose **本地模型 / Local model** in Settings.
The studio lists whatever models are installed. Only addresses on the same computer are accepted.
Check your runtime's context-window setting: the prompts are long (often over 10,000 characters) and a small default window will cut them off.

### MCP server

`run_mcp.py` is a Model Context Protocol server over stdio. With it, an AI app does the writing while this
tool supplies her voice, her past scripts, the checks and the storage. Scripts saved through MCP appear in the studio.

Tools: `get_script_brief`, `save_script`, `update_script`, `check_script`, `list_scripts`, `get_script`,
`get_voice_profile`, `list_library`, `get_voice_learning_brief`, `save_voice_profile`,
`get_topic_ideas_brief`, `save_topic_ideas`, `export_jianying_kit`, `list_trends`, `add_trend_keywords`.

The setups below follow each vendor's documentation as of November 2026. None has been verified by the maintainers yet.
Use an absolute path; a path without spaces avoids quoting problems.

**Codex (signed in with a ChatGPT plan)**: add to `~/.codex/config.toml`:

```toml
[mcp_servers.learn_my_voice]
command = "python3"
args = ["/absolute/path/to/Learn-My-Voice/run_mcp.py"]
```

The Settings page shows this snippet with the real paths filled in.

**ChatGPT on the web**: ChatGPT can add a custom MCP server, but it must be reachable over HTTP or through
OpenAI's Secure MCP Tunnel; it cannot start a local program by itself. The tunnel client can launch this server:

```bash
tunnel-client init --sample sample_mcp_stdio_local --profile learn-my-voice \
  --tunnel-id <your tunnel id> \
  --mcp-command "python3 /absolute/path/to/Learn-My-Voice/run_mcp.py"
tunnel-client run --profile learn-my-voice
```

OpenAI's guide says the tunnel needs a tunnel id and an API key from the OpenAI Platform for the tunnel itself.
See [Add custom MCP server](https://developers.openai.com/api/docs/guides/custom-mcp-server) and
[Secure MCP Tunnel](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels).

**Claude Desktop and other stdio clients**:

```json
{ "mcpServers": { "learn-my-voice": { "command": "python3", "args": ["/absolute/path/to/Learn-My-Voice/run_mcp.py"] } } }
```

Then ask in chat, for example: *"Use Learn My Voice to write a 60-second script about our weekend Costco run."*

## What it does

- **Script editor**: one card per spoken line, with shot and on-screen text (花字) fields, live character count and read time, autosave.
- **Live checks**, in Chinese and English: lines too long for one breath, phrases she never uses, influencer clichés, translated-sounding Chinese, risky advertising wording, and total length against the target duration. These are rules, not AI, so they are instant and repeatable.
- **Voice learning**: builds a voice profile from her own scripts. Breath-length limits are measured in code rather than taken from the model. Sponsored scripts are kept out of the core profile. Earlier profiles are kept as backups.
- **Drafting**: every draft is anchored on her own best-performing scripts as examples. A script can have up to 8 target keywords; her past scripts on those topics are then used as the examples first, and the editor shows whether each keyword ended up in the spoken lines or in the drafted title options, caption or hashtags. The title options, cover text, caption and hashtags that the AI drafts with a script are shown under it, to read and copy.
- **Topic ideas**: ranks her past videos by interactions and suggests what to film next. Each idea must cite her own videos; citations that do not match a real title are removed. Tracked trend keywords are passed in as a second signal, and an idea can only name keywords that are actually tracked.
- **Trend keywords** (热点): paste keywords you see trending, with an optional heat number, and the studio ranks them by how hot and recent they are and by how her own videos on that topic performed. You choose how much each side counts. The ranking is arithmetic, not AI. Keywords that clash with her voice profile are marked and left out of suggestions. A web-searching chat AI can also look keywords up through the copy & paste dialog or MCP; those need a source link and are labelled as unverified.
- **Teleprompter**: full screen, adjustable speed and size.
- **Exports**: 剪映 / CapCut kit, subtitle `.srt`, voiceover text, shot list, on-screen text list.

![Topic ideas](docs/images/ideas-zh.png)

## 剪映 (Jianying) / CapCut

From a script, **导出 → 剪映素材包** downloads a zip:

| File | Use |
| :--- | :--- |
| `口播文稿.txt` | One breath per line. Paste into 剪映's 文稿匹配 (script matching) to get subtitles aligned to the real recording. |
| `字幕.srt` | Subtitle file to import. **Timing is estimated** from her speaking rate, not taken from a recording, so it needs adjusting. |
| `花字.srt` | On-screen text placed near the line it belongs to. |
| `拍摄清单.txt` | What to film for each line. |
| `使用说明.txt` | Import steps. |

Switch the app to English before exporting to get English file names and instructions.

![English interface with the export menu](docs/images/script-en.png)

What it does not do yet: write a 剪映 project draft with clips and text already on the timeline, or time subtitles
from her actual audio. The draft format is undocumented and unreadable in recent versions, and the main open-source
generator reports limits for drafts produced on macOS, so this needs testing against a real installation first.

## Data and privacy

- Everything is stored in `data/` inside the project folder: scripts, library, voice profile, settings. The folder is git-ignored.
- API keys are stored in `data/config.json` with owner-only permissions and are never sent back to the browser.
- The web server listens on `127.0.0.1` only and rejects requests with an unexpected Host header.
- AI steps send her voice profile, her notes and parts of her past scripts to whichever AI you chose. With a local model nothing leaves the computer.
- Views, likes, collects and comments are typed in by hand. Nothing is fetched from any platform.
- Trend keywords are pasted in by hand, or returned by a web-searching AI that you ask yourself. The studio makes no web requests for them. A trend lookup prompt contains her video titles, a one-line description of her persona if her voice has been learned, the area you typed and the keywords already tracked. It does not contain script text.

## Project layout

| Path | Contents |
| :--- | :--- |
| `studio/server.py`, `studio/web/` | Local web server and the interface |
| `studio/guardrails.py`, `studio/lexicon.json`, `studio/checks.py` | Rule-based checks and their bilingual messages |
| `studio/pipeline.py`, `studio/llm.py` | Prompt building, reply validation, model providers |
| `studio/corpus.py` | Reading her scripts, measured statistics, performance ranking |
| `studio/trends.py` | Reading pasted trend keywords, scoring them against her own results, keyword coverage |
| `studio/exports.py` | Text exports, `.srt`, 剪映 kit |
| `studio/mcp_server.py`, `run_mcp.py` | MCP server |
| `prompts/` | The prompt text. The app reads prompts 02, 04, 06 and 07 directly from these files, so editing them changes behaviour. |
| `SYSTEM_DESIGN.md` | The original, larger design. Parts of it are not built. |

The prompts are written for one scenario: a Chinese creator who makes lifestyle and couple vlogs with an
English-speaking husband. Edit `prompts/` to fit someone else.

## Development

```bash
python3 -m unittest discover -t . -s tests        # all tests, no dependencies
python3 -m studio.guardrails tests/fixtures/worked_example_script.json --dna schemas/voice_dna.example.json
```

## Known limits

- The advertising-rule word lists are a starting point collected for this project. They have not been checked against current platform or legal rules and are not legal advice.
- Speaking rate cannot be learned from text. It is a default until she enters her own.
- With only a few videos that have numbers, topic suggestions are weak. The app says so.
- Trend scores are a guide, not a measurement. The weights and the 14-day half-life are judgement calls that have not been tuned against real outcomes, and heat numbers are whatever was typed in. Matching a keyword to her past videos is by shared characters and words, so it can miss a video that covers the topic in other words.
- Keywords found by an AI are only as good as the page it cites. The studio requires a web link and drops entries whose stated source date is more than 30 days old, but it never opens the page, so it cannot confirm the page says what the AI claims. They stay marked as unverified unless you paste the same keyword in yourself.
- Sponsored-brief handling, a second-pass critic, learning from her edits to drafts, and video transcription are designed in `prompts/` and `SYSTEM_DESIGN.md` but not built.

## License

MIT. See [LICENSE](LICENSE).
