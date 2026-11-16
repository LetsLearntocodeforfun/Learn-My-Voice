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
  if (!match.available) {
    const need = match.needed || {};
    const text = match.reason === 'too_little_material'
      ? tf('vm_need_material', { need: need.reference_breaths, have: match.reference ? match.reference.breaths : 0 })
      : tf('vm_need_script', { need: need.script_breaths });
    el.innerHTML = `<p class="hint vm-none"><b>${esc(t('vm_title'))}</b> · ${esc(text)}</p>`;
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
    </details></div>`;
  const details = el.querySelector('details');
  details.ontoggle = () => { vmOpen = details.open; };
}
