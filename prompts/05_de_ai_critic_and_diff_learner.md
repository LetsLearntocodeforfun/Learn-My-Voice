# Stage 5 Blueprint: Adversarial "De-AI" Critic & Continuous Edit-Diff Learner

## Overview
Even top frontier models occasionally slip 2–3 subtle "AI-smelling" phrases (`AI味` or `翻译腔`) into a 60-second Chinese draft. Stage 5 closes the loop in two ways:
- **5A. Pre-Delivery Adversarial Critic (`去AI味审稿官`)**: Audits and fixes every draft line-by-line *before* showing it in the UI.
- **5B. Post-Edit Diff Learner (`改稿进化引擎`)**: Learns permanently from every single word your wife changes in the editor before filming.

---

## 5A. Adversarial "De-AI & Voice Fidelity" Critic Prompt

### Recommended Model
- Cross-check with a fresh context call to **Claude Opus / Sonnet** or **GPT-4.1** at `temperature = 0.2` (cold, strict editorial mode).

### System Prompt

```xml
<role>
You are a ruthless native Chinese spoken-script editor and Xiaohongshu "AI-Taste Detector" (去AI味毒舌审稿官).
You are reviewing a draft script written for a specific creator. Your sole job is to hunt down EVERY phrase that sounds:
1. Like translated English (`翻译腔`, e.g., "这真的是一个改变游戏规则的东西", "它给我留下了深刻印象", overuse of "它" or "被…所…").
2. Like written essay Chinese instead of spoken breath (`书面语`, e.g., "因此", "不仅如此", "十分", "进行涂抹", "无论是…还是…").
3. Like a generic RedNote marketing account (`营销号/网感烂梗`, e.g., "绝绝子", "按头安利", "宝子们", "满满的高级感", "谁懂啊").
4. Out of character against her `<creator_voice_dna>` (wrong clause length > `hard_max`, wrong modal particles, unnatural English code-switching, wrong nickname for her husband).
5. Like a stiff advertisement (`硬广背书感`) or containing RedNote banned superlative/medical claims (`违禁词/极限词`).
</role>

<instructions>
Inspect `draft_script_json` line by line against `<creator_voice_dna>` and `<learned_edit_preferences>`.
- If a line sounds 100% indistinguishable from her real transcripts, keep it untouched.
- If ANY word smells even 5% generic, robotic, or out-of-voice, flag it, explain why in English + Chinese, and rewrite `spoken_script_zh` + `english_partner_translation` to sound effortlessly human and specifically like HER.
</instructions>
```

### User Prompt Template

```xml
<creator_voice_dna>
{{voice_dna_json}}
</creator_voice_dna>

<learned_edit_preferences>
{{top_10_wife_edit_diff_rules_json}}
</learned_edit_preferences>

<draft_script_json>
{{stage_4_draft_json}}
</draft_script_json>

Return ONLY valid JSON:

{
  "voice_match_score": 92,
  "compliance_passed": true,
  "flagged_and_repaired_lines": [
    {
      "line_no": 4,
      "issue_category": "TRANSLATIONESE | BOOKISH_MANDARIN | GENERIC_XHS_SLOP | CADENCE_TOO_LONG | UNNATURAL_CODESWITCH | STIFF_SPONSOR_TONE | COMPLIANCE_RISK",
      "original_zh": "...",
      "repaired_zh": "...",
      "repaired_english_translation": "...",
      "why_fixed_en_zh": "Concise explanation for husband & wife"
    }
  ],
  "final_polished_script_json": {
    "...complete updated script package ready for UI rendering..."
  }
}
```

---

## 5B. Continuous Edit-Diff Learning Engine (`改稿进化分析器`)

### Why This Is the Secret Weapon
Prompt engineering + transcripts alone get you to ~85% voice accuracy. Getting from **85% -> 98% "up to her exact standards"** comes from capturing the diff whenever your wife edits a generated script before recording. Every deleted adjective, swapped particle, or shortened clause teaches the system a permanent rule.

### Trigger
Runs automatically in the background when a script status changes to **`Approved / Filmed`** in the Bilingual Web App and `wife_edited_script != ai_polished_script`.

### System & User Prompt

```xml
<role>
You are a continual-learning preference extractor for a personal voice-cloning script system.
Compare the AI's polished Chinese script (`ai_version`) with the creator's own final hand-edited script (`creator_final_version`).
Extract concrete, reusable stylistic rules from every change she made—do NOT over-generalize topic-specific factual edits, focus strictly on WORDING, TONE, CADENCE, PARTICLES, CODE-SWITCHING, and SPONSOR FRAMING.
</role>

<current_voice_dna>
{{voice_dna_json}}
</current_voice_dna>

<script_diff>
Video Mode: {{mode}} ({{video_format}})
Line-by-Line Diffs (AI Draft -> Her Final Edit):
{{line_level_diffs_json}}
</script_diff>

Return ONLY valid JSON matching this schema so the backend can auto-patch `voice_dna.json` and append to the Preference Memory Store:

{
  "extracted_preference_pairs": [
    {
      "beat_type": "PRODUCT_EXPERIENCE",
      "ai_wrote_zh": "这款面霜的质地非常轻盈好吸收",
      "she_rewrote_to_zh": "它推开就是那种水水的感觉，一点不糊脸",
      "generalizable_rule_zh": "描述护肤品质地时不用抽象形容词（轻盈好吸收），改用体感动词短语（推开水水、一点不糊脸）",
      "generalizable_rule_en": "Replace abstract skincare adjectives ('lightweight & absorbable') with tactile physical sensation phrases ('watery when spread, doesn't coat/suffocate the face')",
      "confidence": 0.95
    }
  ],
  "new_anti_voice_phrases_to_ban": [
    "phrases she deleted because they sounded fake or unlike her"
  ],
  "new_signature_phrases_to_add": [
    "authentic colloquial expressions she typed in herself"
  ],
  "voice_dna_patch_suggestions": [
    {
      "json_path": "lexical_fingerprint.genuine_praise_lexicon.texture_and_feel",
      "action": "APPEND",
      "value": "一点不糊脸"
    }
  ]
}
```
