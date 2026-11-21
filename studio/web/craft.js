'use strict';
/* Writing tools in the script editor. Loaded after discover.js and before pages.js.

   Voice match: how close the script's countable speech habits are to the creator's own material. The numbers
   come from /api/lint (studio/voicematch.py); nothing here is an AI's opinion, and the box says so. */

Object.assign(I18N.zh, {
  vm_title: '像你的程度', vm_band: '你自己的脚本：{low}–{high}（{n} 篇）', vm_band_none: '你自己的脚本还不够多，暂时没有对照范围',
  vm_inside: '在你自己的范围里', vm_below: '比你自己的脚本低', vm_how: '怎么算的',
  vm_need_material: '素材还不够，算不出「像不像你」：需要 {need} 口气你自己写的或确认过的非商单内容，现在有 {have}。',
  vm_need_script: '脚本至少要有 {need} 口气才能比。',
  vm_breath: '一口气的长短', vm_endings: '句尾语气词', vm_starts: '开头的连接词', vm_english: '夹英文',
  vm_breath_d: '你的一口气大多是 {p5}–{p95} 个字（中间值 {m}）。这篇的中间值是 {sm}，{pct}% 的气口在你的范围里。',
  vm_endings_d: '你有 {own}% 的气口以语气词结尾{top}。这篇是 {script}%。',
  vm_starts_d: '你有 {own}% 的气口用连接词开头{top}。这篇是 {script}%。',
  vm_english_d: '你有 {own}% 的气口夹英文。这篇是 {script}%。',
  vm_top: '，最常用：{list}', vm_unfamiliar: '你的素材里没出现过：{list}',
  vm_chance: '这篇只有 {n} 口气，差这么一点属于正常波动，不扣分。',
  vm_note: '这个分数是程序数出来的，不是 AI 的判断：只数了上面四种说话习惯，和你自己写的或确认过的 {n} 篇非商单素材（{b} 口气）比。它不说明脚本好不好，也不说明会不会火。',
});
Object.assign(I18N.en, {
  vm_title: 'Voice match', vm_band: '{@Names} own scripts: {low}–{high} ({n} scripts)', vm_band_none: 'not enough of {@names} own scripts yet for a range to compare with',
  vm_inside: 'inside {@names} own range', vm_below: 'below {@names} own scripts', vm_how: 'How this is worked out',
  vm_need_material: 'Not enough material for a voice match yet: it needs {need} breaths of non-sponsored text that {@name} wrote or confirmed, and there are {have}.',
  vm_need_script: 'A script needs at least {need} breaths to be compared.',
  vm_breath: 'Length of a breath', vm_endings: 'Sentence-final particles', vm_starts: 'Connectors at the start', vm_english: 'English mixed in',
  vm_breath_d: '{@Names} breaths are mostly {p5}–{p95} units long (middle value {m}). This script\'s middle value is {sm}, and {pct}% of its breaths are inside that span.',
  vm_endings_d: '{own}% of {@names} breaths end with a particle{top}. In this script it is {script}%.',
  vm_starts_d: '{own}% of {@names} breaths begin with a connector{top}. In this script it is {script}%.',
  vm_english_d: '{own}% of {@names} breaths have English in them. In this script it is {script}%.',
  vm_top: ', most often {list}', vm_unfamiliar: 'Never in {@names} material: {list}',
  vm_chance: 'With {n} breaths in this script, a difference this small is ordinary chance and is not counted.',
  vm_note: 'This number is counted by the program, not judged by an AI: only the four habits above, compared with {n} non-sponsored items ({b} breaths) that {@name} wrote or confirmed. It does not say whether the script is good or how it will do.',
});

// Whether "how this is worked out" is open. Kept here and not read off the page: adding, deleting or moving a
// line draws the whole editor again, and the section would close under her hands.
let vmOpen = false;

/* The voice-match box under the editor's summary. `match` is `voice_match` from /api/lint. */
function paintVoiceMatch(el, match) {
  if (!el) return;
  if (!match) { el.innerHTML = ''; return; }
  const toneSlot = cur && cur.script && cur.script.id ? '<div id="tone-cmp-box"></div>' : '';
  if (!match.available) {
    const need = match.needed || {};
    const text = match.reason === 'too_little_material'
      ? tf('vm_need_material', { need: need.reference_breaths, have: match.reference ? match.reference.breaths : 0 })
      : tf('vm_need_script', { need: need.script_breaths });
    el.innerHTML = `<p class="hint vm-none"><b>${esc(t('vm_title'))}</b> · ${esc(text)}</p>${toneSlot}`;
    syncScriptTone();
    return;
  }
  const band = match.band;
  const below = !!(band && match.score < band.low);
  const sep = lang === 'zh' ? '、' : ', ';
  const list = (items) => (items || []).join(sep);
  const top = (items) => ((items || []).length ? tf('vm_top', { list: list(items) }) : '');
  const p = match.parts;
  const row = (key, part, text) => `<div class="vm-row">
      <div class="vm-name">${esc(t(key))} <span class="meta" data-part="${key}">${Math.round(part.score * 100)}</span></div>
      <div class="meter" aria-hidden="true"><i class="${part.score < 0.6 ? 'under' : ''}" style="width:${Math.round(part.score * 100)}%"></i></div>
      <div class="vm-text">${esc(text)}${part.script.within_chance ? ' ' + esc(tf('vm_chance', { n: match.script.breaths })) : ''}${(part.script.unfamiliar || []).length
        ? ` <span class="tag warn">${esc(tf('vm_unfamiliar', { list: list(part.script.unfamiliar) }))}</span>` : ''}</div>
    </div>`;
  el.innerHTML = `<div class="vm">
    <div class="row">
      <span class="vm-score ${below ? 'low' : ''}" id="vm-score">${esc(match.score)}</span>
      <span><b>${esc(t('vm_title'))}</b>
        <span class="meta"> · ${esc(band ? tf('vm_band', { low: band.low, high: band.high, n: band.scripts }) : t('vm_band_none'))}</span>
        ${band ? `<span class="tag ${below ? 'warn' : 'ok'}">${esc(t(below ? 'vm_below' : 'vm_inside'))}</span>` : ''}</span>
    </div>
    <details ${vmOpen ? 'open' : ''}><summary>${esc(t('vm_how'))}</summary>
      ${row('vm_breath', p.breath, tf('vm_breath_d', { p5: p.breath.own.p5, p95: p.breath.own.p95, m: p.breath.own.median, sm: p.breath.script.median, pct: p.breath.script.inside_pct }))}
      ${row('vm_endings', p.endings, tf('vm_endings_d', { own: p.endings.own.rate_pct, script: p.endings.script.rate_pct, top: top(p.endings.own.top) }))}
      ${row('vm_starts', p.starts, tf('vm_starts_d', { own: p.starts.own.rate_pct, script: p.starts.script.rate_pct, top: top(p.starts.own.top) }))}
      ${row('vm_english', p.english, tf('vm_english_d', { own: p.english.own.rate_pct, script: p.english.script.rate_pct, top: '' }))}
      <p class="hint">${esc(tf('vm_note', { n: match.reference.scripts, b: match.reference.breaths }))}</p>
    </details></div>${toneSlot}`;
  const details = el.querySelector('.vm > details');
  if (details) details.ontoggle = () => { vmOpen = details.open; };
  syncScriptTone();
}

/* ---------- line tools: other openings, other wordings, one-breath cuts, a critic's read ----------

   An AI writes the proposals; the server checks each one with the same rules as any line and with the counted
   speech habits before it comes back (studio/suggest.py). Nothing here changes the script until the creator
   presses "use this", and what was there can be put back. The server keeps a note of every proposed text, so a
   line taken over from a proposal is never shown as the creator's own edit. */
Object.assign(I18N.zh, {
  cr_hooks: '换开场', cr_rewrite: '换个说法', cr_shorten: '缩短', cr_critic: 'AI 审一遍',
  cr_bar_hint: 'AI 只提建议：每条建议先过一遍规则检查，用不用由你定。',
  cr_hooks_title: '换个开场', cr_rewrite_title: '换个说法', cr_shorten_title: '缩到每口气都念得完', cr_critic_title: 'AI 审稿的建议',
  cr_close: '收起', cr_own_openings: '你自己的开场',
  cr_own_none: '素材库里还没有你写的或确认过的非商单内容，所以还看不到你自己的开场。',
  cr_own_hint: '按那条视频的互动数排。一条视频的数据不只取决于开头：这里看的是你平常怎么开场，不是公式。标签是按字面规则贴的。',
  cr_times: '互动是你平常的 {x} 倍', cr_no_numbers: '没填数据',
  cr_pattern_row: '{name} {n} 条', cr_pattern_mid: '（{m} 条有数据，中间值是你平常的 {x} 倍）',
  cr_ask_hooks: '让 AI 照你的路子写几个开场', cr_ask: '让 AI 写', cr_ask_again: '再要一批',
  cr_instruction_ph: '想怎么改？可以不填。比如：更口语一点', cr_working: 'AI 正在写…',
  cr_use: '用这句', cr_used: '已换成 AI 的建议。', cr_undo: '撤销',
  cr_checks_clean: '规则检查：没有发现问题', cr_checks_n: '规则检查：{n} 处要看', cr_was: '（现在这句 {n} 处）', cr_was_clean: '（现在这句也没有）',
  cr_fit: '{inside}/{n} 口气在你平常的长度里', cr_fit_new: '你的素材里没出现过：{list}',
  cr_match: '像你的程度 {from} → {to}', cr_one_breath: '每口气都念得完', cr_not_one_breath: '还有一口气太长', cr_units: '{n} 字',
  cr_note: '这些句子是 AI 写的。程序只查了规则和说话习惯，好不好由你判断。你采用的句子不会被算成你自己的改法。',
  cr_left_out: '另有 {n} 条没法用或超出条数上限（空的、和原句一样、重复或过多），没有列出。',
  cr_findings_more: '另有 {n} 处规则提示未展开。',
  cr_ai_reading: 'AI 的看法：{why}',
  cr_issue_TRANSLATIONESE: 'AI 判断：翻译腔', cr_issue_BOOKISH_MANDARIN: 'AI 判断：书面语',
  cr_issue_GENERIC_XHS_SLOP: 'AI 判断：营销号套话', cr_issue_CADENCE_TOO_LONG: 'AI 判断：一口气太长',
  cr_issue_UNNATURAL_CODESWITCH: 'AI 判断：夹英文不自然', cr_issue_STIFF_AD_TONE: 'AI 判断：像硬广',
  cr_issue_NOT_THEIR_VOICE: 'AI 判断：不像你平时说话',
  cr_rules_agree_yes: '（规则检查也标了这类问题）', cr_rules_agree_no: '（规则检查没标这类词）',
  cr_flagged: 'AI 指出的词：「{p}」', cr_flagged_in_own: '（你的非商单素材里出现 {c} 次 / {s} 篇）',
  cr_flagged_not_in_own: '（你的 {of} 篇非商单素材里没出现过）',
  cr_flagged_missing: 'AI 指出的词不在原句里，已略去。',
  cr_claim_says_ok: 'AI 说你会说「{p}」· 素材核对：{of} 篇里出现 {c} 次（{s} 篇）',
  cr_claim_says_miss: 'AI 说你会说「{p}」· {of} 篇素材里未找到（仅是 AI 的看法）',
  cr_claim_says_none: 'AI 说你会说「{p}」· 还没有素材可核对（仅是 AI 的看法）',
  cr_claim_never_ok: 'AI 说你从不说「{p}」· 素材核对：{of} 篇里出现 0 次',
  cr_claim_never_bad: 'AI 说你从不说「{p}」· 与素材不符：{of} 篇里其实出现了 {c} 次（{s} 篇）',
  cr_claim_never_none: 'AI 说你从不说「{p}」· 还没有素材可核对（仅是 AI 的看法）',
  cr_claim_opens_ok: 'AI 说你常以「{p}」开场 · 素材核对：{of} 个开场里出现 {s} 次',
  cr_claim_opens_miss: 'AI 说你常以「{p}」开场 · {of} 个开场里未找到（仅是 AI 的看法）',
  cr_claim_opens_none: 'AI 说你常以「{p}」开场 · 还没有开场可核对（仅是 AI 的看法）',
  cr_in_dna_uses: '（也在你的声音档案里）', cr_in_dna_banned: '（也在你的禁用词里）',
  cr_worse_head: '按计数这句改法比原句退步：{list}。',
  cr_worse_rules: '规则问题 {from} → {to} 处', cr_worse_hints: '书面语提示 {from} → {to} 处',
  cr_worse_voice_match: '像你的程度 {from} → {to}',
  cr_worse_length: '在平常长度里的气口 {fi}/{fb} → {ti}/{tb}',
  cr_worse_unfamiliar: '未见过的词 {from} → {to} 个',
  cr_critic_none: 'AI 没有挑出要改的句子。这不等于脚本没有问题，只是它没发现。',
  cr_line_no: '第 {n} 句', cr_now: '现在',
  cr_stale: '这句后来又改过了，这批建议已经对不上。', cr_save_first: '还没保存成功，请稍后再试。',
  cr_mb_title: '请 AI 提修改建议', cr_mb_intro: '把下面这段话复制到你常用的 AI 对话里，再把它的回复整段粘贴回来。建议会先过规则检查再给你看，脚本不会被自动改动。',
  pat_question: '提问', pat_number: '有数字', pat_time: '从时间说起', pat_turn: '有转折', pat_viewer: '对观众说话', pat_plain: '平铺直叙',
});
Object.assign(I18N.en, {
  cr_hooks: 'Openings', cr_rewrite: 'Reword', cr_shorten: 'Shorten', cr_critic: 'AI read-through',
  cr_bar_hint: 'The AI only proposes: every proposal is run through the rule checks first, and {@name} decides.',
  cr_hooks_title: 'Other openings', cr_rewrite_title: 'Other wordings', cr_shorten_title: 'Cut so every breath can be said in one go', cr_critic_title: 'Proposals from the AI read-through',
  cr_close: 'Close', cr_own_openings: '{@Names} own openings',
  cr_own_none: 'The library has no non-sponsored material that {@name} wrote or confirmed yet, so there are no openings to show.',
  cr_own_hint: 'In order of each video\'s interactions. A video\'s numbers come from far more than its opening: this shows how {@name} usually opens, not a formula. The labels follow plain rules on the words.',
  cr_times: '{x}× the usual interactions', cr_no_numbers: 'no numbers entered',
  cr_pattern_row: '{name}: {n}', cr_pattern_mid: ' ({m} with numbers, middle value {x}× the usual)',
  cr_ask_hooks: 'Ask the AI for openings in this manner', cr_ask: 'Ask the AI', cr_ask_again: 'Ask for more',
  cr_instruction_ph: 'What should change? Optional. For example: more casual', cr_working: 'The AI is writing…',
  cr_use: 'Use this', cr_used: 'Replaced with the AI\'s proposal.', cr_undo: 'Undo',
  cr_checks_clean: 'Rule checks: nothing found', cr_checks_n: 'Rule checks: {n} to look at', cr_was: ' (the line now: {n})', cr_was_clean: ' (none in the line now either)',
  cr_fit: '{inside} of {n} breaths within {@names} usual length', cr_fit_new: 'Never in {@names} material: {list}',
  cr_match: 'Voice match {from} → {to}', cr_one_breath: 'Every breath can be said in one go', cr_not_one_breath: 'One breath is still too long', cr_units: '{n} units',
  cr_note: 'An AI wrote these lines. The program checked only the rules and the speech habits; whether a line is good is for {@name} to judge. A line that is taken is not counted as {@names} own edit.',
  cr_left_out: '{n} more could not be used or exceeded the limit (empty, the same as the line, repeated, or past the cap) and are not listed.',
  cr_findings_more: '{n} more rule findings on this proposal are not listed.',
  cr_ai_reading: 'The AI\'s reading: {why}',
  cr_issue_TRANSLATIONESE: 'AI reading: translated phrasing', cr_issue_BOOKISH_MANDARIN: 'AI reading: written rather than spoken',
  cr_issue_GENERIC_XHS_SLOP: 'AI reading: generic marketing phrasing', cr_issue_CADENCE_TOO_LONG: 'AI reading: breath too long',
  cr_issue_UNNATURAL_CODESWITCH: 'AI reading: unnatural code-switching', cr_issue_STIFF_AD_TONE: 'AI reading: stiff ad tone',
  cr_issue_NOT_THEIR_VOICE: 'AI reading: unlike {@names} voice',
  cr_rules_agree_yes: ' (rule checks flagged this too)', cr_rules_agree_no: ' (rule checks did not flag this)',
  cr_flagged: 'Pointed at: “{p}”', cr_flagged_in_own: ' (in {@names} material: {c}× across {s} items)',
  cr_flagged_not_in_own: ' (0× in {@names} {of} items)',
  cr_flagged_missing: 'The words the AI pointed at do not stand in this line and were dropped.',
  cr_claim_says_ok: 'AI says {@name} says “{p}” · checked: {c}× in {s} of {of} items',
  cr_claim_says_miss: 'AI says {@name} says “{p}” · not found in {of} items (the AI\'s opinion)',
  cr_claim_says_none: 'AI says {@name} says “{p}” · no material to check yet (the AI\'s opinion)',
  cr_claim_never_ok: 'AI says {@name} never says “{p}” · checked: 0× in {of} items',
  cr_claim_never_bad: 'AI says {@name} never says “{p}” · contradicted: {c}× in {s} of {of} items',
  cr_claim_never_none: 'AI says {@name} never says “{p}” · no material to check yet (the AI\'s opinion)',
  cr_claim_opens_ok: 'AI says {@name} opens with “{p}” · checked: in {s} of {of} openings',
  cr_claim_opens_miss: 'AI says {@name} opens with “{p}” · not found in {of} openings (the AI\'s opinion)',
  cr_claim_opens_none: 'AI says {@name} opens with “{p}” · no openings to check yet (the AI\'s opinion)',
  cr_in_dna_uses: ' (also in {@names} voice profile)', cr_in_dna_banned: ' (also in {@names} never-say list)',
  cr_worse_head: 'Worse than the line now by the counts: {list}.',
  cr_worse_rules: 'rule findings {from} → {to}', cr_worse_hints: 'written-style hints {from} → {to}',
  cr_worse_voice_match: 'voice match {from} → {to}',
  cr_worse_length: 'breaths in usual length {fi}/{fb} → {ti}/{tb}',
  cr_worse_unfamiliar: 'unfamiliar words {from} → {to}',
  cr_critic_none: 'The AI picked out no line to change. That does not mean the script has no problems, only that it found none.',
  cr_line_no: 'Line {n}', cr_now: 'now',
  cr_stale: 'This line was changed afterwards, so these proposals no longer match it.', cr_save_first: 'The script is not saved yet. Please try again in a moment.',
  cr_mb_title: 'Ask an AI for proposals', cr_mb_intro: 'Copy the text below into the AI chat you use, then paste its whole reply back here. Proposals are run through the rule checks before they are shown, and the script is not changed by itself.',
  pat_question: 'a question', pat_number: 'a number', pat_time: 'starts from when', pat_turn: 'a turn', pat_viewer: 'speaks to the viewer', pat_plain: 'plain',
});

// The tool that is open, one at a time: { scriptId, kind, line (null for the critic), busy, result, hooks, instruction, applied }.
let craft = null;
const CRAFT_REFUSALS = ['not_found', 'bad_kind', 'bad_line', 'live_clip', 'empty_line'];   // answered with a sentence by the server

const craftLive = (line) => String(line.audio_mode || '').toUpperCase() === 'LIVE_CLIP';
const craftSep = () => (lang === 'zh' ? '、' : ', ');
const craftBoxOf = (tool) => (tool.kind === 'critic' ? document.getElementById('craft-critic')
  : $view.querySelector(`.line[data-i="${tool.line}"] [data-craft-box]`));

function craftFail(e) {
  if (CRAFT_REFUSALS.includes(e.code)) return toast((lang === 'zh' ? e.message_zh : e.detail) || t('err_default'));
  return showError(e);
}

/* Called at the end of paintScript: the editor was drawn again, so the buttons and the open tool are put back. */
function craftAttach() {
  if (!cur) return;
  const s = cur.script;
  const gone = craft && (craft.scriptId !== s.id || (craft.line !== null && craft.line >= s.lines.length)
    || (craft.result && craft.result.suggestions.some((item) => item.line_index >= s.lines.length)));
  if (gone) craft = null;
  const first = s.lines.findIndex((line) => !craftLive(line));
  $view.querySelectorAll('.line').forEach((el) => {
    const i = Number(el.dataset.i);
    if (craftLive(s.lines[i])) return;
    const tools = document.createElement('span');
    tools.className = 'craft-tools';
    tools.innerHTML = `${i === first ? `<button type="button" class="btn ghost" data-craft="hooks">${esc(t('cr_hooks'))}</button>` : ''}
      <button type="button" class="btn ghost" data-craft="rewrite">${esc(t('cr_rewrite'))}</button>
      <button type="button" class="btn ghost" data-craft="shorten">${esc(t('cr_shorten'))}</button>`;
    el.querySelector('.line-head .spacer').after(tools);
    const box = document.createElement('div');
    box.className = 'craft-box';
    box.dataset.craftBox = '';
    el.querySelector('[data-issues]').after(box);
  });
  const bar = document.getElementById('craft-bar');
  if (bar) {
    bar.innerHTML = `<div class="row"><button type="button" class="btn" data-craft="critic">${esc(t('cr_critic'))}</button>
      <span class="hint">${esc(t('cr_bar_hint'))}</span></div><div class="craft-box" id="craft-critic"></div>`;
    bar.onclick = craftClick;
  }
  const $lines = document.getElementById('lines');
  if ($lines) $lines.addEventListener('click', craftClick);     // #lines is new after every paint, so this is added once
  craftPaint();
}

function craftClick(e) {
  const open = e.target.closest('[data-craft]');
  if (open) {
    const card = open.closest('.line');
    return craftOpen(open.dataset.craft, card ? Number(card.dataset.i) : null);
  }
  const act = e.target.closest('[data-craft-do]');
  if (!act || !craft) return undefined;
  const what = act.dataset.craftDo;
  if (what === 'close') { craft = null; return craftPaint(); }
  if (what === 'ask') return craftAsk();
  if (what === 'use') return craftUse(Number(act.dataset.n));
  if (what === 'undo') return craftUndo(act.dataset.n === undefined ? null : Number(act.dataset.n));
  return undefined;
}

function craftOpen(kind, line) {
  const tool = { scriptId: cur.script.id, kind, line: kind === 'critic' ? null : line, busy: false, result: null, hooks: null, instruction: '', applied: null };
  craft = tool;
  craftPaint();
  if (kind === 'hooks') {
    api('GET', '/api/hooks').then((found) => { if (craft === tool) { tool.hooks = found; craftPaint(); } }).catch(showError);
  }
  if (kind === 'shorten' || kind === 'critic') craftAsk();     // nothing to choose first: the button said what it does
}

async function craftAsk() {
  const tool = craft;
  if (!tool || tool.busy) return;
  const typed = document.getElementById('cr-instruction');
  if (typed) tool.instruction = typed.value;
  tool.busy = true;
  craftPaint();
  try {
    await flushSave();                    // the server reads the stored script
    if (dirty) { toast(t('cr_save_first')); return; }
    const payload = { script_id: tool.scriptId, kind: tool.kind, line_index: tool.line, instruction: tool.instruction };
    const result = appState.settings.manual
      ? await manualBridge('suggest', payload, { title: t('cr_mb_title'), intro: t('cr_mb_intro') })
      : await api('POST', `/api/scripts/${tool.scriptId}/suggest`, payload);
    if (result) { tool.result = result; tool.applied = null; }      // undefined: the copy-and-paste dialog was closed
  } catch (e) { craftFail(e); } finally {
    tool.busy = false;
    if (craft === tool) craftPaint();
  }
}

/* Put a proposal into its line. The line must still read as it did when the proposals were made. */
function craftUse(n) {
  const tool = craft;
  const item = tool.result && tool.result.suggestions[n];
  if (!item || !cur || cur.script.id !== tool.scriptId) return;
  const line = cur.script.lines[item.line_index];
  if (!line || line.spoken_script_zh !== item.original) { toast(t('cr_stale')); tool.result = null; craftPaint(); return; }
  const before = { text: line.spoken_script_zh, english: line.english_partner_translation };
  line.spoken_script_zh = item.text;
  // The English meaning belongs to the words it was written for. A proposal that brings none leaves the field empty:
  // the old line's meaning under new words would mislead whoever follows the script in English. Undo puts it back.
  line.english_partner_translation = item.english || '';
  if (tool.kind === 'critic') item.used = { before };
  else { tool.result = null; tool.applied = { line: item.line_index, text: item.text, before }; }
  cur.findings = cur.findings.filter((f) => f.line_no !== item.line_index + 1);   // the old line's; fresh ones come after the save
  paintScript();
  scheduleSave();
}

function craftUndo(n) {
  const tool = craft;
  const item = n === null ? null : tool.result && tool.result.suggestions[n];
  const done = item ? { line: item.line_index, text: item.text, before: item.used && item.used.before } : tool.applied;
  if (!done || !done.before || !cur || cur.script.id !== tool.scriptId) return;
  const line = cur.script.lines[done.line];
  if (!line || line.spoken_script_zh !== done.text) { toast(t('cr_stale')); return; }
  line.spoken_script_zh = done.before.text;
  line.english_partner_translation = done.before.english;
  if (item) item.used = null; else craft = null;
  cur.findings = cur.findings.filter((f) => f.line_no !== done.line + 1);
  paintScript();
  scheduleSave();
}

function craftOwnHtml(found) {
  if (!found) return '';
  /* poAiOpeningsHtml (posted.js): openings that are still an AI's sentence, apart from the creator's own and labelled */
  if (!found.openers.length) return `<p class="hint" id="cr-own-none">${esc(t('cr_own_none'))}</p>${poAiOpeningsHtml(found)}`;
  const seen = new Set();       // the same opening from two items (a script added twice) is shown once, with its better result
  const distinct = found.openers.filter((o) => !seen.has(o.opening) && seen.add(o.opening));
  const rows = distinct.slice(0, 5).map((o) => `<div class="craft-own">
      <div>${esc(o.opening)}</div>
      <div class="meta">${esc(o.title)} · ${esc(o.times_usual !== null ? tf('cr_times', { x: o.times_usual }) : t('cr_no_numbers'))}
        ${o.patterns.map((p) => `<span class="tag">${esc(t('pat_' + p))}</span>`).join('')}</div></div>`).join('');
  const labels = found.patterns.map((p) => tf('cr_pattern_row', { name: t('pat_' + p.pattern), n: p.videos })
    + (p.times_usual !== null ? tf('cr_pattern_mid', { x: p.times_usual, m: p.with_numbers }) : '')).join(lang === 'zh' ? '；' : '; ');
  return `<div id="cr-own"><h4>${esc(t('cr_own_openings'))}</h4>${rows}
    <p class="hint">${esc(labels)}</p><p class="hint">${esc(t('cr_own_hint'))}</p></div>${poAiOpeningsHtml(found)}`;
}

function craftChecksHtml(item, result) {
  const total = (p) => p.errors + p.reviews;
  const found = total(item.problems);
  const was = total(item.problems_now);
  const chips = [`<span class="tag ${found ? 'warn' : 'ok'}" data-check="rules">${esc(found ? tf('cr_checks_n', { n: found }) : t('cr_checks_clean'))}${esc(was ? tf('cr_was', { n: was }) : t('cr_was_clean'))}</span>`];
  if (item.one_breath !== undefined) chips.push(`<span class="tag ${item.one_breath ? 'ok' : 'warn'}" data-check="breath">${esc(t(item.one_breath ? 'cr_one_breath' : 'cr_not_one_breath'))}</span>`);
  if (item.fit && item.fit.breaths) chips.push(`<span class="tag" data-check="fit">${esc(tf('cr_fit', { inside: item.fit.inside, n: item.fit.breaths }))}</span>`);
  if (item.fit && item.fit.unfamiliar.length) chips.push(`<span class="tag warn" data-check="new">${esc(tf('cr_fit_new', { list: item.fit.unfamiliar.join(craftSep()) }))}</span>`);
  if (item.voice_match !== null && result.voice_match_now !== null) {
    chips.push(`<span class="tag ${item.voice_match < result.voice_match_now ? 'warn' : ''}" data-check="match">${esc(tf('cr_match', { from: result.voice_match_now, to: item.voice_match }))}</span>`);
  }
  (item.patterns || []).forEach((p) => chips.push(`<span class="tag">${esc(t('pat_' + p))}</span>`));
  const issues = item.findings.map(wlIssueHtml).join('');      // wordlist.js: drawn as in the editor, hints set apart
  const more = item.findings_left_out ? `<p class="hint">${esc(tf('cr_findings_more', { n: item.findings_left_out }))}</p>` : '';
  return `<div class="craft-checks">${chips.join('')}</div>${issues ? `<div class="issues">${issues}</div>` : ''}${more}`;
}

function craftClaimText(c) {
  const args = { p: c.phrase, c: c.count, s: c.scripts, of: c.of };
  let base = '';
  if (c.claim === 'never_says') {
    base = tf(c.found === null ? 'cr_claim_never_none' : (c.holds ? 'cr_claim_never_ok' : 'cr_claim_never_bad'), args);
  } else if (c.claim === 'opens_with') {
    base = tf(c.found === null ? 'cr_claim_opens_none' : (c.holds ? 'cr_claim_opens_ok' : 'cr_claim_opens_miss'), args);
  } else {
    base = tf(c.found === null ? 'cr_claim_says_none' : (c.holds ? 'cr_claim_says_ok' : 'cr_claim_says_miss'), args);
  }
  const dna = c.in_dna === 'uses' ? t('cr_in_dna_uses') : (c.in_dna === 'banned' ? t('cr_in_dna_banned') : '');
  return base + dna;
}

function craftWorseHtml(worse) {
  if (!Array.isArray(worse) || !worse.length) return '';
  const total = (p) => (p ? p.errors + p.reviews : 0);
  const parts = worse.map((w) => {
    if (w.what === 'rules') return tf('cr_worse_rules', { from: total(w.from), to: total(w.to) });
    if (w.what === 'length') return tf('cr_worse_length', { fi: w.from[0], fb: w.from[1], ti: w.to[0], tb: w.to[1] });
    return tf('cr_worse_' + w.what, { from: w.from, to: w.to });
  });
  return `<div class="cr-note" data-critic="worse">${esc(tf('cr_worse_head', { list: parts.join(craftSep()) }))}</div>`;
}

function craftReadingHtml(item) {
  const r = item.reading;
  if (!r) return '';
  const chips = [];
  if (r.issue && I18N[lang]['cr_issue_' + r.issue]) {
    const agree = r.rules_agree === true ? t('cr_rules_agree_yes') : (r.rules_agree === false ? t('cr_rules_agree_no') : '');
    chips.push(`<span class="tag" data-critic="issue">${esc(t('cr_issue_' + r.issue) + agree)}</span>`);
  }
  if (r.flagged) {
    const lk = r.flagged_lookup;
    const rec = lk && lk.found !== null
      ? (lk.found ? tf('cr_flagged_in_own', { c: lk.count, s: lk.scripts }) : tf('cr_flagged_not_in_own', { of: lk.of }))
      : '';
    chips.push(`<span class="tag" data-critic="flagged">${esc(tf('cr_flagged', { p: r.flagged }) + rec)}</span>`);
  }
  (r.claims || []).forEach((c) => {
    chips.push(`<span class="tag ${c.holds === true ? 'ok' : 'warn'}" data-critic="claim">${esc(craftClaimText(c))}</span>`);
  });
  const missing = r.flagged_missing ? `<div class="hint" data-critic="flagged-missing">${esc(t('cr_flagged_missing'))}</div>` : '';
  return `${chips.length ? `<div class="craft-checks" data-critic="reading">${chips.join('')}</div>` : ''}${missing}`;
}

function craftWhyText(item) {
  const rObj = item.reason && typeof item.reason === 'object' ? item.reason : null;
  const plain = typeof item.reason === 'string' ? item.reason : (rObj && typeof rObj.text === 'string' ? rObj.text : '');
  const zh = item.why_zh || (rObj && rObj.why_zh) || plain;
  const en = item.why_en || (rObj && rObj.why_en) || plain;
  return lang === 'en' ? (en || zh) : (zh || en);
}

function craftItemHtml(item, n, result) {
  const where = esc(tf('cr_line_no', { n: item.line_index + 1 }));
  if (item.used) {
    return `<div class="craft-item used" data-n="${n}"><span class="meta">${where} · ${esc(t('cr_used'))}</span>
      <button type="button" class="btn ghost" data-craft-do="undo" data-n="${n}">${esc(t('cr_undo'))}</button></div>`;
  }
  const why = craftWhyText(item);
  const whyLine = why
    ? `<div class="meta" data-critic="why">${esc(result.kind === 'critic' ? tf('cr_ai_reading', { why }) : why)}</div>`
    : '';
  return `<div class="craft-item" data-n="${n}">
    ${result.kind === 'critic' ? `<div class="meta">${where} · ${esc(t('cr_now'))}：<del>${esc(item.original)}</del></div>` : ''}
    <div class="craft-text">${esc(item.text)} <span class="meta">${esc(tf('cr_units', { n: item.units }))}</span></div>
    ${whyLine}
    ${lang === 'en' && item.english ? `<div class="meta">${esc(item.english)}</div>` : ''}
    ${result.kind === 'critic' ? craftReadingHtml(item) + craftWorseHtml(item.worse) : ''}
    ${craftChecksHtml(item, result)}
    <div class="row"><button type="button" class="btn primary" data-craft-do="use" data-n="${n}">${esc(t('cr_use'))}</button></div></div>`;
}

function craftPaint() {
  $view.querySelectorAll('.craft-box').forEach((el) => { el.innerHTML = ''; el.classList.remove('open'); });
  if (!craft) return;
  const el = craftBoxOf(craft);
  if (!el) return;
  const result = craft.result;
  let body = '';
  if (craft.applied) {
    body = `<div class="craft-item used"><span class="meta">${esc(t('cr_used'))}</span>
      <button type="button" class="btn ghost" data-craft-do="undo">${esc(t('cr_undo'))}</button></div>`;
  } else {
    if (craft.kind === 'hooks') body += craftOwnHtml(craft.hooks);
    if (craft.kind === 'rewrite') {
      body += `<input type="text" id="cr-instruction" maxlength="300" placeholder="${esc(t('cr_instruction_ph'))}" value="${esc(craft.instruction)}" ${craft.busy ? 'disabled' : ''}>`;
    }
    body += craft.busy ? `<p class="hint" id="cr-working">${esc(t('cr_working'))}</p>`
      : `<div class="row"><button type="button" class="btn ${result ? '' : 'primary'}" data-craft-do="ask">${esc(t(result ? 'cr_ask_again' : (craft.kind === 'hooks' ? 'cr_ask_hooks' : 'cr_ask')))}</button></div>`;
    if (result) {
      body += result.suggestions.map((item, n) => craftItemHtml(item, n, result)).join('');
      if (result.kind === 'critic' && !result.suggestions.length) body += `<p class="hint" id="cr-none">${esc(t('cr_critic_none'))}</p>`;
      if (result.left_out) body += `<p class="hint">${esc(tf('cr_left_out', { n: result.left_out }))}</p>`;
      if (result.suggestions.length) body += `<p class="hint">${esc(t('cr_note'))}</p>`;
    }
  }
  el.classList.add('open');
  el.innerHTML = `<div class="row"><b>${esc(t('cr_' + craft.kind + '_title'))}</b><span class="spacer"></span>
    <button type="button" class="btn ghost" data-craft-do="close">${esc(t('cr_close'))}</button></div>${body}`;
}


/* ---------- script tone counts beside the creator's own material (/api/scripts/<id>/tone) ----------
   Collapsible section right under Voice match in #vm-box. Plain surface counts and measured sound reference
   only (studio/tone_analyzer.py); no composite or personality scores. */
Object.assign(I18N.zh, {
  tn_ed_title: '语气与用词对照',
  tn_ed_summary: '这篇 {sb} 口气 · 对照你已确认的 {n} 篇非商单素材（{b} 口气）',
  tn_ed_obs_n: '{n} 项差异提示',
  tn_ed_need_material: '至少需要 {need} 口气你写过或确认过的非商单文字才能对照语气计数（当前 {have} 口气）。',
  tn_ed_need_script: '脚本至少要有 {need} 口气才能对照语气计数。',
  tn_ed_breath: '气口长度',
  tn_ed_breath_v: '这篇中间值 {sm} 字（{pct}% 在你平常的 {p5}–{p95} 字之间）· 你的中间值 {om} 字',
  tn_ed_particles: '句尾语气词与尾词',
  tn_ed_colloq: '口语连接词',
  tn_ed_bookish: '书面连接词',
  tn_ed_redup: '叠词（AABB / AA的）',
  tn_ed_hedges: '留白与限定说法',
  tn_ed_hardsell: '常见硬广夸张词',
  tn_ed_punct: '问号与感叹号',
  tn_ed_punct_v: '这篇：问句 {sq}% / 感叹 {se}% · 你的非商单：问句 {oq}% / 感叹 {oe}%',
  tn_ed_rate_cmp: '这篇 {s}% · 你的非商单 {o}%',
  tn_ed_per100_cmp: '这篇每百口气 {s} 处 · 你的非商单 {o} 处',
  tn_ed_range: '（单篇 {low}–{high}）',
  tn_ed_unfamiliar: '未在你的非商单里见过：{list}',
  tn_ed_delivery: '你的录音实测参考',
  tn_ed_delivery_pace: '语速 {cpm} 字/分钟',
  tn_ed_delivery_pauses: '每百字停顿 {n} 次',
  tn_ed_delivery_loud: '响度跨度中间值 {db} dB',
  tn_ed_delivery_pitch: '基频中间值 {hz} Hz（起伏 {st} 半音）',
});

Object.assign(I18N.en, {
  tn_ed_title: 'Tone & wording counts',
  tn_ed_summary: 'This script: {sb} breaths · beside {n} confirmed non-sponsored item(s) ({b} breaths)',
  tn_ed_obs_n: '{n} noted difference(s)',
  tn_ed_need_material: 'Needs at least {need} breaths of non-sponsored text that {@name} wrote or confirmed (currently {have}).',
  tn_ed_need_script: 'A script needs at least {need} breaths for tone counts to be compared.',
  tn_ed_breath: 'Breath length',
  tn_ed_breath_v: 'This script median {sm} units ({pct}% within {@names} {p5}–{p95}) · {@names} median {om}',
  tn_ed_particles: 'Sentence-final particles & tags',
  tn_ed_colloq: 'Spoken connectors',
  tn_ed_bookish: 'Formal written connectors',
  tn_ed_redup: 'Reduplicated words',
  tn_ed_hedges: 'Hedges & caveats',
  tn_ed_hardsell: 'Hard-sell phrases',
  tn_ed_punct: 'Questions & exclamations',
  tn_ed_punct_v: 'This script: {sq}% questions / {se}% exclamations · {@names} material: {oq}% / {oe}%',
  tn_ed_rate_cmp: 'This script {s}% · {@names} material {o}%',
  tn_ed_per100_cmp: 'This script {s} per 100 breaths · {@names} material {o}',
  tn_ed_range: ' (scripts: {low}–{high})',
  tn_ed_unfamiliar: 'Not in {@names} material: {list}',
  tn_ed_delivery: '{@Names} measured delivery',
  tn_ed_delivery_pace: 'pace {cpm} units/min',
  tn_ed_delivery_pauses: '{n} pauses / 100 units',
  tn_ed_delivery_loud: 'loudness range median {db} dB',
  tn_ed_delivery_pitch: 'pitch median {hz} Hz (span {st} semitones)',
});

let toneOpen = false;
const toneState = { scriptId: null, updatedAt: null, data: null, reqId: 0 };

function tnRangeStr(r) {
  return r && r.scripts ? tf('tn_ed_range', { low: r.low, high: r.high }) : '';
}

function tnChipsHtml(used, unfamiliar, warnUsed) {
  const sep = craftSep();
  let html = (used || []).map((w) => ` <span class="tag ${warnUsed ? 'warn' : ''}">${esc(w)}</span>`).join('');
  if (unfamiliar && unfamiliar.length) {
    html += ` <span class="tag warn">${esc(tf('tn_ed_unfamiliar', { list: unfamiliar.join(sep) }))}</span>`;
  }
  return html;
}

function paintScriptToneBox(box, data) {
  if (!box || !data) return;
  if (!data.ready) {
    const need = data.needed || {};
    const msg = data.reason === 'too_little_material'
      ? tf('tn_ed_need_material', { need: need.reference_breaths, have: data.reference ? data.reference.breaths : 0 })
      : tf('tn_ed_need_script', { need: need.script_breaths });
    box.innerHTML = `<p class="hint vm-none" id="tone-cmp-empty"><b>${esc(t('tn_ed_title'))}</b> · ${esc(msg)}</p>`;
    return;
  }
  const c = data.comparisons || {};
  const obs = data.observations || [];
  const obsHtml = obs.map((o) => `<div class="cr-note" data-tone-obs="${esc(o.kind)}">${esc(lang === 'zh' ? o.message_zh : o.message_en)}</div>`).join('');
  const kvRows = [];
  const addKv = (key, bodyHtml) => { kvRows.push(`<dt>${esc(t(key))}</dt><dd>${bodyHtml}</dd>`); };

  if (c.breath_length) {
    const b = c.breath_length;
    addKv('tn_ed_breath', esc(tf('tn_ed_breath_v', { sm: b.script.median, pct: b.script.inside_pct, p5: b.own.p5, p95: b.own.p95, om: b.own.median }) + tnRangeStr(b.range)));
  }
  if (c.particles) {
    const p = c.particles;
    addKv('tn_ed_particles', esc(tf('tn_ed_rate_cmp', { s: p.script_rate_pct, o: p.own_rate_pct }) + tnRangeStr(p.range)) + tnChipsHtml(p.script_used, p.unfamiliar, false));
  }
  if (c.colloquial_connectors) {
    const cc = c.colloquial_connectors;
    addKv('tn_ed_colloq', esc(tf('tn_ed_rate_cmp', { s: cc.script_rate_pct, o: cc.own_rate_pct }) + tnRangeStr(cc.range)) + tnChipsHtml(cc.script_used, cc.unfamiliar, false));
  }
  if (c.bookish_connectors) {
    const bc = c.bookish_connectors;
    addKv('tn_ed_bookish', esc(tf('tn_ed_rate_cmp', { s: bc.script_rate_pct, o: bc.own_rate_pct }) + tnRangeStr(bc.range)) + tnChipsHtml(bc.script_used, [], true));
  }
  if (c.reduplications) {
    const rd = c.reduplications;
    addKv('tn_ed_redup', esc(tf('tn_ed_per100_cmp', { s: rd.script_per_100, o: rd.own_per_100 }) + tnRangeStr(rd.range)) + tnChipsHtml(rd.script_used, [], false));
  }
  if (c.hedges) {
    const hd = c.hedges;
    addKv('tn_ed_hedges', esc(tf('tn_ed_per100_cmp', { s: hd.script_per_100, o: hd.own_per_100 }) + tnRangeStr(hd.range)) + tnChipsHtml(hd.script_used, [], false));
  }
  if (c.hard_sell) {
    const hs = c.hard_sell;
    addKv('tn_ed_hardsell', esc(tf('tn_ed_per100_cmp', { s: hs.script_per_100, o: hs.own_per_100 }) + tnRangeStr(hs.range)) + tnChipsHtml(hs.script_used, [], true));
  }
  if (c.questions && c.exclamations) {
    addKv('tn_ed_punct', esc(tf('tn_ed_punct_v', {
      sq: c.questions.script_rate_pct, se: c.exclamations.script_rate_pct,
      oq: c.questions.own_rate_pct, oe: c.exclamations.own_rate_pct,
    })));
  }

  const del = data.delivery || {};
  const delParts = [];
  if (del.measured_speaking_rate) delParts.push(tf('tn_ed_delivery_pace', { cpm: del.measured_speaking_rate.speaking_rate_cpm }));
  if (del.pauses_per_100_units !== null && del.pauses_per_100_units !== undefined) delParts.push(tf('tn_ed_delivery_pauses', { n: del.pauses_per_100_units }));
  if (del.sound && del.sound.loudness_range_db) delParts.push(tf('tn_ed_delivery_loud', { db: del.sound.loudness_range_db.median }));
  if (del.sound && del.sound.pitch_hz) {
    const sp = del.sound.pitch_span_semitones;
    delParts.push(tf('tn_ed_delivery_pitch', { hz: del.sound.pitch_hz.median, st: sp ? sp.median : 0 }));
  }
  if (delParts.length) addKv('tn_ed_delivery', esc(delParts.join(' · ')));

  const wlNote = data.wordlist_note && data.wordlist_note[lang] ? data.wordlist_note[lang] : '';
  const summaryBadge = obs.length ? `<span class="tag warn">${esc(tf('tn_ed_obs_n', { n: obs.length }))}</span>` : '';

  box.innerHTML = `<div class="vm" id="tone-cmp-card" style="margin-top:8px">
    <details ${toneOpen ? 'open' : ''} id="tone-cmp-details">
      <summary><b>${esc(t('tn_ed_title'))}</b>
        <span class="meta"> · ${esc(tf('tn_ed_summary', { sb: data.script.breaths, n: data.reference.scripts, b: data.reference.breaths }))}</span>
        ${summaryBadge}</summary>
      ${obsHtml}
      <dl class="kv" style="margin-top:8px">${kvRows.join('')}</dl>
      ${wlNote ? `<p class="hint">${esc(wlNote)}</p>` : ''}
    </details></div>`;
  const details = document.getElementById('tone-cmp-details');
  if (details) details.ontoggle = () => { toneOpen = details.open; };
}

function syncScriptTone() {
  const box = document.getElementById('tone-cmp-box');
  if (!box || !cur || !cur.script || !cur.script.id) return;
  const sid = cur.script.id;
  const stamp = cur.script.updated_at;
  if (toneState.scriptId === sid && toneState.data) {
    paintScriptToneBox(box, toneState.data);
  }
  if (toneState.scriptId === sid && toneState.updatedAt === stamp && toneState.data) return;
  if (toneState.scriptId !== sid) toneState.data = null;
  toneState.scriptId = sid;
  toneState.updatedAt = stamp;
  const reqId = ++toneState.reqId;
  api('GET', '/api/scripts/' + encodeURIComponent(sid) + '/tone')
    .then((data) => {
      if (reqId !== toneState.reqId || !cur || !cur.script || cur.script.id !== sid) return;
      toneState.data = data;
      paintScriptToneBox(document.getElementById('tone-cmp-box'), data);
    })
    .catch(() => { /* optional panel; leave empty on transient error */ });
}
