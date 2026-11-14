# End-to-End Worked Blueprint Walkthrough: Sponsored Couple Vlog (`日常夫妻Vlog软植`)

This walkthrough demonstrates how the 5-stage prompt pipeline turns a stiff Chinese brand brief + a quick English idea from you into an authentic 60-second RedNote script in your wife's exact voice—and how it learns from her edits afterward.

---

## Step 1: Raw Inputs

### Husband's Quick Topic Note (Typed in English)
> *"We went to Trader Joe's on Sunday, I tried to buy 4 boxes of frozen soup dumplings, and it was super windy/freezing outside. Let's weave the sponsored barrier face cream into coming home from the cold."*

### Brand PR Brief Paste (`品牌原始Brief片段`)
> **产品**：XX神经酰胺修护面霜  
> **合作形式**：日常Vlog软植（60秒左右，前15秒自然生活切入，不硬广）  
> **必提核心卖点**：1. 专研三重神经酰胺科技，100%强韧修护受损屏障；2. 冰淇淋丝绒质地，24小时长效锁水保湿；3. 敏感肌换季退红消炎必备。  
> **画面要求**：展示面霜质地特写3秒，晚上护肤上脸画面。

---

## Step 2: Stage 3 Output — Brief De-Jargonizer & Compliance Audit

- **Compliance Risks Caught Automatically (`违禁词拦截`)**:
  1. `100%强韧修护` -> **Blocked** (`100%` violates China Ad Law `极限词`). Replaced with personal lived feel.
  2. `退红消炎` -> **Blocked** (`消炎` is a medical/pharmaceutical claim banned on skincare). Replaced with `安抚换季脸颊小情绪 / 泛红干痒压下去`.
- **Humanized Selling Points (`卖点说人话转化`)**:
  - *"专研三重神经酰胺修护屏障"* -> `里面有三重神经酰胺嘛，吹完冷风脸紧绷绷的时候涂它特别稳`
  - *"冰淇淋丝绒质地，24小时锁水"* -> `挖出来是那种软软糯糯像冰淇淋的质地，推开一点不糊脸，睡醒第二天洗脸还是润润的`

---

## Step 3: Stage 4 + 5A Output — Polished Bilingual Filming Script (~60s)

| # | Beat & Time | Mode | Spoken Script (Her Exact Voice) | Partner English & Tone Note | Visual B-Roll + `花字` |
| :- | :--- | :--- | :--- | :--- | :--- |
| 1 | **HOOK** `00:00–00:04` | `VOICEOVER` | 周日下午趁冰箱空了 / 拉着某人去趟 Trader Joe's 补货嘛 | Sunday TJ's restock run *(opens mid-action, uses "某人" + premise particle 嘛)* | Walking into TJ's in winter coats · 花字: `(周末采购小分队)` |
| 2 | **COUPLE** `00:04–00:11` | `LIVE_CLIP` + `VOICEOVER` | `[原声: "Wait can we get four boxes of the soup dumplings?"]` / 你们看他每次路过冰柜都走不动道哈 / 最后被我摁住只拿了两盒 `[轻笑]` | Candid 2.5s English clip of you + her dry amused commentary | You holding frozen boxes looking hopeful; her hand putting 2 back · 花字: `(眼神逐渐渴望)` |
| 3 | **VLOG** `00:11–00:16` | `VOICEOVER` | 买完出来外头那个妖风真的巨冷 / 吹得我俩脸都有点干干紧绷绷的 | Weather detail sets up natural skin trigger without sounding like an ad | Carrying paper bags across windy parking lot, scarf pulled up |
| 4 | **BRIDGE** `00:16–00:22` | `VOICEOVER` | 所以最近回家洗完澡第一件事 / 就是赶紧把我这罐面霜挖出来厚涂一层 | Seamless habit bridge at 16s mark (right inside RedNote's 15–20s sweet spot) | Cozy warm bathroom lighting; opening jar on counter |
| 5 | **PRODUCT** `00:22–00:35` | `VOICEOVER` | 我这种干敏皮一到换季就容易闹脾气嘛 / 它是那种三重神经酰胺的配方 / 质地软软糯糯像化开的冰淇淋一样 / 推开直接就吃进去了，一点不糊脸 | Covers mandatory `三重神经酰胺` + texture shot requirement using her tactile phrases | **3s macro texture shot** (brief requirement checked) + applying to cheeks |
| 6 | **COUPLE PROOF** `00:35–00:45` | `VOICEOVER` | 最搞笑的是某人前两天鼻翼起皮 / 顺手偷挖了我一点去涂 / 第二天早上还跑来问我那个 jar 叫什么哈 | Cross-cultural couple detail proves efficacy naturally (`natural English noun: jar`) | Bathroom mirror two-shot or husband pointing at jar · 花字: `(护肤品共享警告)` |
| 7 | **CAVEAT** `00:45–00:53` | `VOICEOVER` | 我现在睡前涂完，第二天上妆基本不怎么卡粉啦 / 不过它是偏滋润挂的 / 大油皮姐妹薄涂一层就够了 | Honest skin-type boundary builds viewer trust | Morning natural window light; patting cushion compact smoothly |
| 8 | **CLOSING** `00:53–00:59` | `VOICEOVER` | 好啦，去煮刚才买的小笼包当晚饭啦 / 你们入冬还有什么救命面霜也可以种草给我哈，拜拜～ | Loops back to the TJ's soup dumplings from Beat 2! Cozy low-pressure sign-off | Steaming soup dumplings in kitchen |

### What Stage 5A (Cold De-AI Critic) Repaired Before Showing You
- **Line 5 Draft**: *"它搭载了三重神经酰胺，能够深层修护肌肤"* -> **Repaired to**: *"它是那种三重神经酰胺的配方"* *(removed brochure verb `搭载` and `能够深层`)*.
- **Line 7 Draft**: *"第二天底妆100%服帖"* -> **Repaired to**: *"第二天上妆基本不怎么卡粉啦"* *(removed banned `100%` + matched her understated cadence)*.

### Generated RedNote Titles & Caption
- **Title Option 1**: `和外国老公的周日碎片🛒 吹完冷风回家的小确幸🍂` *(Weekend fragments with foreign husband | Cozy ritual after cold wind)*
- **Title Option 2**: `干敏皮入冬的安全感🥣 连某人都偷挖去涂的面霜哈` *(Winter security for dry sensitive skin | Even my husband steals scoops)*
- **Caption (`正文文案`)**:
  > 周日去 Trader Joe's 差点又被某人搬空冷冻柜哈哈  
  > 最近降温刮大风，干皮出门真的太难熬啦  
  >   
  > 晚上洗完热水澡最舒服的时刻就是厚涂一层修护面霜  
  > 软软糯糯的质地，一点不黏枕头，睡醒脸还是软软稳稳的✨  
  >   
  > `#夫妻日常` `#跨国情侣` `#秋冬护肤` `#干敏皮面霜` `#周末vlog`

---

## Step 4: Stage 5B Output — Learning From Her Final Edits

Suppose before recording, your wife tweaks **Line 3** in the editor:
- **AI Polished**: `买完出来外头那个妖风真的巨冷`
- **Wife's Final Edit**: `买完出来外头那个风真的吹得脑壳疼`

When she clicks **Approve & Learn From Her Edits**, Stage 5B records:
```json
{
  "beat_type": "VLOG_STORY",
  "ai_wrote_zh": "外头那个妖风真的巨冷",
  "she_rewrote_to_zh": "外头那个风真的吹得脑壳疼",
  "generalizable_rule_en": "Uses playful colloquial Southwest/casual idiom '脑壳疼' for cold wind/headache moments rather than generic internet slang '妖风'.",
  "voice_dna_patch": {
    "path": "persona_summary.regional_and_phonetic_nuances.preserve_colloquialisms",
    "append": "脑壳疼"
  }
}
```
From the very next script onward, the system knows that subtle personal expression automatically.
