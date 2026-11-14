# RedNote Personal Voice-Cloning & Bilingual Script Studio — System Architecture

> **Status / 状态:** This is the original design document. Parts of it (link and video ingestion, transcription, sponsored briefs, the critic and the edit-diff learner, the vector store) are not built. See [README.md](./README.md) for what exists today. 这是最初的设计文档，其中一部分尚未实现；现状请看 [README.zh-CN.md](./README.zh-CN.md)。

## 1. Why Generic AI Fails for Chinese RedNote Creators ("AI味太重")

When standard LLM prompts generate Chinese RedNote (`小红书`) scripts, they fail across five predictable dimensions:
1. **Transcript Sanitization**: Normal speech-to-text strips modal particles (`啦、嘛、哈、喔、诶`), pauses, and spoken shortcuts, turning her real voice into flat written Chinese (`书面语`).
2. **Translationese (`翻译腔`)**: When an English-speaking partner drafts ideas or prompts in English, LLMs calque English sentence structures into unnatural Chinese (`"它完美地解决了我的痛点"` instead of `"一下就省事儿多了"`).
3. **Marketing-Account Slop (`营销号烂梗`)**: Without an explicit **Anti-Voice Blacklist** and real beat-by-beat exemplars, LLMs default to cringe influencer clichés (`姐妹们谁懂啊`, `绝绝子`, `按头安利`, `闭眼入`).
4. **Unnatural Code-Switching**: Cross-cultural creators have *very specific* rules for when an English word appears inside a Mandarin sentence vs. when it sounds pretentious or fake.
5. **Brand-PDF Leakage (`硬广感`)**: Feeding raw Chinese PR briefs (`品牌Brief`) into a prompt leaks stiff corporate claims (`专研修护屏障、赋能高智感`) straight into the script—and risks triggering RedNote `蒲公英/广告法` banned-word throttling (`限流`).

---

## 2. End-to-End Architecture

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                     PHASE 1: REDNOTE CORPUS INGESTION                        │
│                                                                              │
│  [RedNote Share Link / xhslink.com] ──► XHS-Downloader (Local API Mode)      │
│     (+ Drag & Drop .mp4 fallback)          ├── Watermark-free HD .mp4        │
│                                            └── Title, Caption, Tags, Stats   │
│                                                       │                      │
│          ┌────────────────────────────────────────────┴───────────┐          │
│          ▼                                                        ▼          │
│  [Local Mac Audio Pipeline]                         [Multimodal Visual Pass] │
│  • Optional vocal isolation (htdemucs if loud BGM)  • Gemini 2.5 Pro         │
│  • mlx-whisper (large-v3-turbo, Apple Silicon, $0)  • Burned-in 花字 OCR      │
│  • Verbatim Simplified ZH + EN code-switch prompt   • Live clip vs Voiceover │
│  • Word timestamps ─► Exact CPM & clause stats      • Beat boundary tagging  │
└──────────────────────────────────────────┬───────────────────────────────────┘
                                           ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│                 PHASE 2: VOICE MEMORY & EXEMPLAR KNOWLEDGE BASE              │
│                                                                              │
│  1. Organic-Only Voice DNA Synthesizer (Claude Opus/Sonnet, Prompt 02)       │
│     ──► voice_dna.json (Cadence, Particles, Husband Nicknames, Code-Switch   │
│         Grammar, Praise Calibration, Anti-Voice Blacklist)                   │
│  2. Beat-Segmented Vector + SQLite Store                                     │
│     ──► Every real segment indexed by beat_type (HOOK, VLOG_STORY,           │
│         COUPLE_BANTER, TRANSITION_BRIDGE, PRODUCT_EXPERIENCE, CLOSING_CTA)   │
│  3. Wife's Edit-Diff Preference Bank                                         │
│     ──► Learned (AI_Draft -> Her_Rewrite) pairs from approved scripts        │
└──────────────────────────────────────────┬───────────────────────────────────┘
                                           ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│              PHASE 3: BILINGUAL SCRIPT GENERATION & CRITIQUE                 │
│                                                                              │
│  [Topic Notes (EN/ZH)] + [Optional Brand Brief PDF/Text]                     │
│          │                                                                   │
│          ├── (If Sponsored) Stage 3: Brief De-Jargonizer & RedNote Ad-Law    │
│          │   Compliance Audit ─► 3 Natural Vlog Soft-Pivot Angles            │
│          ▼                                                                   │
│  Stage 4: Beat Exemplar Retrieval + Claude Script Writer                     │
│          │   (Breath-by-breath ZH + Partner EN + B-Roll + 花字 + Titles)      │
│          ▼                                                                   │
│  Stage 5A: Adversarial "De-AI" Critic (GPT-4.1 / Claude Fresh Pass, T=0.2)   │
│          │   (Flags translationese, bookish ZH, clichés, >max clause length) │
│          ▼                                                                   │
│  [Bilingual Side-by-Side Web Studio (EN + ZH)]                               │
│          │   Husband & Wife review ─► Wife makes final micro-tweaks          │
│          ▼                                                                   │
│  Stage 5B: Continuous Edit-Diff Learner ─► Auto-upgrades voice_dna.json      │
└──────────────────────────────────────────────────────────────────────────────┘
```

---

## 3. Recommended Tech Stack (100% Local-First on macOS)

| Layer | Choice | Why This Fits Your Setup |
| :--- | :--- | :--- |
| **RedNote Link Extractor** | `XHS-Downloader` (Server/API mode) + `.mp4` dropzone fallback | Natively resolves `xhslink.com` mobile share snippets & `xsec_token` URLs; supports optional browser cookie sync for 1080p watermark-free downloads + full caption/title metadata. |
| **Audio Pre-processing** | `ffmpeg` + optional `audio-separator` (`htdemucs`) | Strips heavy vlog BGM when needed so speech transcription never hallucinates during music breaks. |
| **Local Speech-to-Text** | `mlx-whisper` (`whisper-large-v3-turbo`) | Free, private, blazing-fast on Apple Silicon unified memory; top-tier accuracy on rapid Mandarin–English code-switching. |
| **Multimodal Video & OCR** | **Gemini 2.5 Pro** | Watches downsampled video frames + audio to read CapCut/剪映 burned-in `花字` callouts, detect candid couple clips vs. voiceover, and segment beats. |
| **Primary Script Writer** | **Claude Sonnet / Opus** | Best-in-class frontier model at warm, non-robotic colloquial Chinese (`去AI味`) when anchored with verbatim beat exemplars. |
| **Adversarial Critic & Compliance** | **GPT-4.1 / o3** or **Claude (separate cold session)** | Using a separate strict editorial pass (`temperature = 0.2`) catches subtle translationese and RedNote banned words (`违禁词`) that single-pass drafting misses. |
| **Storage & Retrieval** | **SQLite** + **ChromaDB** (or `sqlite-vec`) + `text-embedding-3-large` | Zero external cloud DB setup; stores videos, beats, `voice_dna.json` versions, sponsored briefs, and edit-diff preference pairs locally inside `./data/`. |
| **Local Bilingual Web App** | **FastAPI (Python)** + **Next.js / Tailwind** | Clean side-by-side English + Chinese collaborative UI running locally at `http://localhost:3000`. |

---

## 4. Core Data Schema (SQLite + JSON)

### `videos` (Ingested RedNote Corpus)
- `id` (TEXT PK, RedNote `note_id` or local UUID)
- `source_url` (TEXT, original `xhslink.com` or explore link)
- `title_zh` (TEXT), `caption_zh` (TEXT), `hashtags_json` (TEXT)
- `likes` (INT), `collects` (INT), `comments` (INT) — *used to weight high-resonance videos*
- `is_sponsored` (BOOLEAN) — *critical: keeps old brand copy out of core organic Voice DNA*
- `primary_format` (`VLOG_VOICEOVER` | `COUPLE_STORY` | `FAVORITES_ROUNDUP` | `SINGLE_REVIEW`)
- `speaking_rate_cpm` (FLOAT), `avg_clause_chars` (FLOAT)
- `local_video_path` (TEXT), `ingested_at` (TIMESTAMP)

### `video_beats` (Retrievable Verbatim Exemplar Bank)
- `id` (UUID PK), `video_id` (FK)
- `beat_type` (`HOOK` | `VLOG_STORY` | `COUPLE_BANTER` | `TRANSITION_BRIDGE` | `PRODUCT_EXPERIENCE` | `REALISTIC_CAVEAT` | `CLOSING_CTA`)
- `audio_mode` (`VOICEOVER` | `TALKING_HEAD` | `LIVE_CLIP`)
- `verbatim_text_zh_en` (TEXT)
- `english_gloss` (TEXT)
- `onscreen_flower_text` (TEXT)
- `visual_broll_summary` (TEXT)
- `embedding` (VECTOR — embedded on `beat_type + english_gloss + verbatim_text_zh_en` so English topic queries from husband retrieve matching Chinese beats!)

### `voice_profiles`
- `version` (INT PK), `voice_dna_json` (JSONB), `is_active` (BOOLEAN), `created_at` (TIMESTAMP)

### `scripts` & `edit_preference_pairs`
- `scripts`: Stores `request_payload`, `humanized_brief_json`, `ai_polished_json`, `wife_final_json`, `status` (`DRAFT` | `APPROVED`), `voice_match_score`.
- `edit_preference_pairs`: Stores `beat_type`, `ai_wrote_zh`, `she_rewrote_to_zh`, `rule_en`, `rule_zh`, `active` (BOOLEAN).

---

## 5. Bilingual Husband + Wife Collaborative Workflow (UI Design)

### Tab 1: Voice Library & DNA Inspector (`她的声音基因库`)
- **Paste RedNote Share Links**: Paste 1 or 20 `xhslink.com` share texts directly from the RedNote mobile app (plus drag-and-drop `.mp4` fallback + 1-click browser cookie paste for 1080p access).
- **Interactive Voice DNA Card (EN + ZH)**: Both of you can see (and toggle/edit!) what the system learned:
  - Speaking speed (`235 chars/min`), clause length (`8–14 chars`), modal particle chart (`啦 38% | 嘛 24% | 哈 18%`).
  - How she calls you (`我老公 / 某人`), her English code-switching triggers, and her **Never-Say Blacklist (`禁用词`)**.

### Tab 2: Sponsored Brief Translator (`商单Brief翻译与软植策划`)
- Paste a Chinese PR brief (or drop screenshots/PDF text) -> Instant **English Partner Digest** for you + **RedNote Banned-Word Warning (`违禁词预警`)** + **3 Soft-Integration Story Hooks** that you and your wife can pick from in 30 seconds before generating the full script.

### Tab 3: Side-by-Side Script Studio (`双语逐句脚本工作台`)
- **Left Panel (Bilingual Controls)**: Mode (`Normal` vs `Sponsored`), Format (`Couple Vlog`, `Daily Voiceover`, `Favorites Share`), Duration slider (`30s / 60s / 90s` -> auto-calculates exact Chinese character budget!), and rough bullet points in **English or Chinese**.
- **Center Panel (Synchronized Breath-by-Breath Script Table)**:
  - Each row = **1 spoken breath line** (`口播/旁白气口`) with live read-time calculator, editable Chinese text, parallel natural English meaning for you, `[B-Roll Shot Cue]`, and optional `[花字 On-Screen Joke]`.
  - Teleprompter / Voiceover Recording Export (`提词器模式`) formatted cleanly for her phone screen while recording in CapCut/剪映.
- **Right Panel (Voice Authenticity & Continual Learning)**:
  - Shows the exact **real lines from her past RedNote videos** used as rhythm anchors for this script.
  - Shows what the **De-AI Critic** automatically caught and repaired.
  - **"Approve & Learn From Her Edits" (`定稿并学习改稿习惯`)** button: Computes the diff between AI draft and her edits, displaying the newly learned rules in English + Chinese.

---

## 6. Prompt, Schema & Reference Blueprint Index

### Production Prompts ([`./prompts/`](./prompts))
1. [`01_verbatim_transcription_and_visual_ingest.md`](./prompts/01_verbatim_transcription_and_visual_ingest.md) — `mlx-whisper` verbatim code-switch setup + Gemini 2.5 Pro multimodal video/beat/`花字` extractor.
2. [`02_voice_dna_synthesizer.md`](./prompts/02_voice_dna_synthesizer.md) — Corpus-to-`voice_dna.json` forensic style extractor (with Organic vs. Sponsored separation).
3. [`03_sponsored_brief_translator.md`](./prompts/03_sponsored_brief_translator.md) — Corporate PR brief humanizer + bilingual partner summary + RedNote `违禁词` compliance filter.
4. [`04_script_generator_normal_and_sponsored.md`](./prompts/04_script_generator_normal_and_sponsored.md) — Dynamic exemplar-anchored bilingual script, B-roll, `花字`, title & caption writer.
5. [`05_de_ai_critic_and_diff_learner.md`](./prompts/05_de_ai_critic_and_diff_learner.md) — Pre-delivery Adversarial "De-AI" Critic + Post-edit Diff-to-DNA continual learning engine.

### Populated Schemas, Compliance Lexicon & End-to-End Example
- [`voice_dna.example.json`](./schemas/voice_dna.example.json) — Complete populated Voice DNA example for a cross-cultural couple vlog + beauty/lifestyle creator.
- [`rednote_anti_ai_and_compliance_lexicon.md`](./reference/rednote_anti_ai_and_compliance_lexicon.md) — Translationese -> spoken Mandarin dictionary + RedNote `蒲公英/广告法` banned-word safe replacements.
- [`worked_sponsored_vlog_example.md`](./examples/worked_sponsored_vlog_example.md) — Full walkthrough from raw Chinese PR brief + English husband note -> bilingual script -> De-AI Critic repairs -> Wife's Edit-Diff learning patch.

### Deterministic Guardrails (Python stdlib only, no installs)
- [`studio/guardrails.py`](./studio/guardrails.py) — Rule-based linter for Stage 4 / 5A script JSON: clause length, Voice DNA blacklists, lexicon clichés/translationese/compliance terms, unattested address terms and English, duration budget. Run: `python3 -m studio.guardrails <script.json> --dna <voice_dna.json>`.
- [`studio/lexicon.json`](./studio/lexicon.json) — Machine-readable mirror of the compliance lexicon; keep in sync with the markdown reference.
- [`studio/textstats.py`](./studio/textstats.py) — Spoken-unit counting, breath-clause splitting, duration budget.
- Tests: `python3 -m unittest discover -t . -s tests`

### Local Workstation App (Chinese-first, Python stdlib only)
- Start: double-click `启动工作台.command`, or run `python3 -m studio.server` (opens `http://127.0.0.1:8765`).
- [`studio/server.py`](./studio/server.py) — Loopback-only web server and JSON API. [`studio/web/`](./studio/web) — UI (中文 default, English toggle).
- [`studio/pipeline.py`](./studio/pipeline.py) — Stage 4 generation; reads prompt text directly from `prompts/04_…md`. [`studio/llm.py`](./studio/llm.py) — Gemini / OpenAI clients.
- [`studio/store.py`](./studio/store.py) — Scripts, settings and API keys under `./data/` (git-ignored). A real profile at `data/voice_dna.json` replaces the bundled example.
- [`studio/corpus.py`](./studio/corpus.py) — Her imported scripts: `.docx` reading, measured clause lengths and particles, performance ranking, exemplar selection.
- Voice learning runs `prompts/02_…md` over the library; topic ideas run [`prompts/06_content_recommender.md`](./prompts/06_content_recommender.md).
- Providers: Gemini, OpenAI, or a local OpenAI-compatible server on this machine (Ollama, LM Studio). Local addresses are restricted to loopback.
- Copy-and-paste mode lets a chat subscription with no API key do the AI steps; [`studio/mcp_server.py`](./studio/mcp_server.py) + [`run_mcp.py`](./run_mcp.py) expose the same steps to MCP clients over stdio.
- [`studio/exports.py`](./studio/exports.py) — Text exports, `.srt` with estimated timing, and the 剪映 / CapCut kit. [`studio/checks.py`](./studio/checks.py) — Bilingual check messages.


---

## 7. Phased Build Roadmap (When You Are Ready to Build)

- **Milestone 1 — Ingestion & Voice DNA CLI/API (1–2 days)**: Wire `XHS-Downloader` + `ffmpeg` + `mlx-whisper` + Gemini 2.5 Pro; ingest 15–20 of her RedNote links and generate `voice_dna.json` v1.
- **Milestone 2 — Script Pipeline + De-AI Critic (1–2 days)**: Implement Chroma/SQLite beat-level retrieval + Stages 3, 4, and 5A across Claude & OpenAI/Gemini; validate on 2 normal + 2 sponsored test topics with your wife.
- **Milestone 3 — Local Bilingual Web Studio & Diff Learner (2–3 days)**: Build the Next.js + FastAPI side-by-side English/Chinese workspace, mobile voiceover teleprompter view, and 1-click Edit-Diff continual learning loop.
