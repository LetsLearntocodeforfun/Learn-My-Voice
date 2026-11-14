# Stage 1 Blueprint: Verbatim Audio & Visual Ingestion (RedNote -> Structured Corpus)

## Purpose
Standard speech-to-text pipelines fail for Chinese creators because they silently normalize colloquial speech into textbook written Chinese (`书面语`), drop sentence-final modal particles (`啦、嘛、哈、喔、诶`), hallucinate during vlog background music (BGM), or mangle Mandarin–English code-switching.

This stage ingests a RedNote share link (`xhslink.com` or `xiaohongshu.com/explore/...?xsec_token=...`), extracts watermark-free video + post metadata via `XHS-Downloader`, transcribes verbatim speech locally on Apple Silicon via `mlx-whisper`, and runs a multimodal pass via **Gemini 2.5 Pro** to capture on-screen text (`花字`), live couple dialogue vs. voiceover (`旁白`), and visual shot pacing.

---

## 1A. Local Apple Silicon `mlx-whisper` Configuration

Use `mlx-community/whisper-large-v3-turbo` on Mac unified memory.

> **Critical BGM Anti-Hallucination Rule:** RedNote vlogs almost always mix background music under voiceover. Always set `condition_on_previous_text=False` and `hallucination_silence_threshold=1.0` (or pre-isolate vocals via `audio-separator` with `htdemucs` when BGM is loud) so Whisper never loops phrases during musical interludes.

```python
import mlx_whisper

VERBATIM_MANDARIN_CODESWITCH_PROMPT = (
    "以下是小红书日常Vlog旁白与好物分享原声逐字稿，使用简体中文，包含自然的中英文混说（code-switching）。"
    "请严格逐字记录口语原话，保留所有句尾语气词（如：啦、嘛、哈、喔、呐、诶、呗、呀）和口头连接词"
    "（如：其实、就是说、跟你们讲、说实话），不要把口语改成书面语。"
    "Example: 今天weekend跟我老公去trader joe's买点东西，其实这个cream我用了一周啦，真的蛮舒服的哈。"
)

result = mlx_whisper.transcribe(
    audio_file_path,
    path_or_hf_repo="mlx-community/whisper-large-v3-turbo",
    initial_prompt=VERBATIM_MANDARIN_CODESWITCH_PROMPT,
    word_timestamps=True,
    condition_on_previous_text=False,
    hallucination_silence_threshold=1.0,
    temperature=0.0,
)
```

### Derived Acoustic Metrics (Computed Deterministically in Python)
From `word_timestamps`, compute and store per video before calling any LLM:
- `speaking_rate_cpm`: Spoken Chinese characters + English words per spoken minute (excluding silences > 1.2s).
- `avg_breath_clause_chars`: Mean character count between pauses >= 300ms.
- `pause_per_100_chars`: Frequency of micro-pauses.

---

## 1B. Multimodal Video & Beat Extraction Prompt (Gemini 2.5 Pro)

Feed the downloaded RedNote `.mp4` (downsampled to 480p / 1 fps for low token cost) + the raw `mlx-whisper` timestamped segments + the `XHS-Downloader` caption metadata into **Gemini 2.5 Pro**.

### System Prompt

```xml
<role>
You are an expert Mandarin–English bilingual linguistic annotator and Xiaohongshu (小红书 / RedNote) video editor analyst.
Your job is to align a creator's raw speech transcript with the actual RedNote video so we capture 100% of her real speaking style, on-screen text habits, and narrative beat structure.
</role>

<critical_rules>
1. DO NOT "clean up", formalize, or rewrite her spoken words. Preserve every modal particle (啦, 嘛, 哈, 喔, 诶, 呗), colloquial contraction, self-correction, and English code-switched word.
2. Fix ONLY obvious phonetic speech-to-text homophone errors (同音错别字) or misspelled brand/English names by checking the video's burned-in subtitles and product packaging on screen.
3. Distinguish between:
   - `VOICEOVER` (旁白: recorded over B-roll)
   - `TALKING_HEAD` (口播: speaking directly to camera)
   - `LIVE_CLIP` (现场原声: candid moment, e.g., speaking English/Mandarin with her husband in the scene)
4. Extract burned-in "Flower Text" / comedic aside callouts (`花字 / 屏幕小字吐槽`) separately from the spoken subtitle track.
5. Classify whether this video appears to be `ORGANIC` (pure normal content) or `SPONSORED` (报备笔记 / 软广 / 恰饭合作), and if sponsored, mark the exact timestamp where the sponsor pivot (`软植转折`) happens.
</critical_rules>
```

### User Prompt Template

```xml
<xhs_post_metadata>
Note ID: {{note_id}}
Post Title: {{post_title}}
Post Caption & Hashtags: {{post_caption}}
Likes / Collects / Comments: {{likes}} / {{collects}} / {{comments}}
Video Duration (sec): {{duration_sec}}
</xhs_post_metadata>

<raw_whisper_segments>
{{whisper_timestamped_json}}
</raw_whisper_segments>

Analyze the attached video alongside the raw Whisper segments and return ONLY valid JSON matching the schema below:

{
  "content_classification": {
    "is_sponsored": false,
    "video_primary_format": "VLOG_VOICEOVER | TALKING_HEAD | FAVORITES_ROUNDUP | SINGLE_REVIEW | COUPLE_STORY",
    "sponsor_brand_if_any": null,
    "sponsor_pivot_timestamp_sec": null
  },
  "delivery_observations": {
    "vocal_energy": "string describing warmth, pitch variation, dry humor vs bubbly energy",
    "laugh_or_sigh_moments": ["timestamp + brief note"],
    "code_switching_instances": [
      {
        "timestamp": "00:14",
        "full_clause": "verbatim clause containing English + Mandarin",
        "english_tokens": ["tokens"],
        "trigger_reason": "HUSBAND_DIALOGUE | DAILY_NOUN | BRAND_NAME | VIBE_ADJECTIVE | WORK_TERM"
      }
    ]
  },
  "onscreen_flower_texts": [
    {
      "timestamp": "00:08",
      "text": "exact on-screen aside / 花字",
      "purpose": "SELF_DEPRECATION | HUSBAND_ROAST | EMPHASIS | PRODUCT_KEY_POINT"
    }
  ],
  "beats": [
    {
      "beat_index": 1,
      "beat_type": "HOOK | VLOG_STORY | COUPLE_BANTER | TRANSITION_BRIDGE | PRODUCT_EXPERIENCE | REALISTIC_CAVEAT | CLOSING_CTA",
      "start_sec": 0.0,
      "end_sec": 4.2,
      "audio_mode": "VOICEOVER | TALKING_HEAD | LIVE_CLIP",
      "verbatim_transcript_zh_en": "Exact spoken lines separated by '/' at natural breath pauses",
      "english_meaning": "Natural English translation for bilingual partner reference",
      "visual_broll_summary": "What is shown on screen during this beat"
    }
  ]
}
```
