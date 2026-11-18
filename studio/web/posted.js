'use strict';
/* A script that was posted joins the library, with its numbers (studio/posted.py). Loaded before pages.js.

   What this file puts on the pages:
   - the scripts list: which scripts are posted;
   - the script page: "mark as posted", what was recorded, "update numbers", and a notice when the script was edited
     after its text went into the library;
   - the library: where an item came from, its days and its link, and how its wording stands;
   - the openings tool: openings that are still an AI's sentence, kept apart from the creator's own.

   The numbers of a posted script count at once. Its wording does not count as the creator's own until they say so,
   or until the words of the posted video take the text's place: the text began here, often as an AI's draft. Every
   count shown ("3 of 8 lines are still as the AI wrote them") is counted by the server in code, not judged by an AI. */

Object.assign(I18N.zh, {
  po_title: '发布记录', po_tag: '已发布', po_card: '已发布 · {d}',
  po_mark: '标记为已发布',
  po_mark_hint: '视频发出去以后点这里：这篇脚本会带着数据进素材库，选题和热点的分析就能用上它。',
  po_date: '发布日期', po_link: '笔记链接', po_link_hint: '可不填。只当文字保存，工作台不会去打开它', po_link_ph: 'https://…',
  po_sponsored: '这条是商单', po_numbers: '数据', po_numbers_hint: '可以先空着，以后再补', po_read_on: '这些数据是哪天看的',
  po_goes: '保存后，这篇脚本现在的标题和口播（现场原声不算）会作为一条素材进入素材库。数据马上计入分析；它的文字先不算你自己的说法，等你确认。',
  po_save: '保存', po_cancel: '取消', po_marked: '已标记为已发布，并加入素材库。', po_saved: '已保存。',
  po_posted_on: '{d} 发布', po_to_library: '在素材库里看这一条',
  po_no_numbers: '还没填数据。',
  po_read: '数据是 {d} 看的：发布 {n} 天后。', po_read_same: '数据是 {d} 看的：发布当天。', po_read_plain: '数据是 {d} 看的。',
  po_compare: '数据会连着涨好些天：发布 2 天后看的和 60 天后看的，不能直接比。这里只记下是哪天看的，不折算，也不预估。',
  po_update_numbers: '更新数据',
  po_differs: '这篇脚本在发布之后又改过。素材库里的那一条保留的是发布时的文字，没有跟着变。',
  po_refresh: '用现在的脚本更新素材库里的文字',
  po_refresh_confirm: '素材库里那一条的标题和文字会换成脚本现在的样子，你在素材库里对它做过的修改会被盖掉。换过以后，它的文字要重新等你确认，才算你的说法。继续吗？',
  po_refreshed: '已更新素材库里的文字。',
  po_unmark_hint: '要取消「已发布」，到素材库里删除那一条就行，脚本本身不受影响。',
  po_stale: '这篇脚本在别处被改过了，和这一页显示的不一样。请先重新加载页面再操作。',
  po_save_first: '脚本还没保存成功，请稍后再试。',
  po_w_waiting: '它的文字还不算你自己的说法。',
  po_w_how: '数据已经计入分析。想让文字也算数，到素材库里确认，或者在那里附上这条视频的字幕。',
  po_w_confirmed: '你已经确认：它的文字算作你自己的说法。',
  po_w_spoken: '素材库里存的是这条视频里实际说的话（来自字幕文件或转写），算作你自己的说法。',
  po_w_transcript: '素材库里是这条视频的机器转写，还等你看一遍并确认。',
  po_c_all: '{n} 句全部还是 AI 写的原样，一句都没有改过。',
  po_c_some: '{n} 句里有 {k} 句还是 AI 写的原样，其余 {m} 句是你改过或自己写的。',
  po_c_none: '{n} 句都是你改过或自己写的，没有一句还是 AI 写的原样。',
  po_c_hand: '这篇是你在工作台里自己打的，{n} 句里没有 AI 写的句子。',
  po_c_hand_some: '这篇是你自己打的，不过 {n} 句里有 {k} 句用的是 AI 的建议。',
  po_wait_title: '这是从工作台的脚本发布的，文字还不算你自己的说法。',
  po_wait_body: '数据已经计入分析。它的文字暂时不会用来量你的说话习惯、学你的声音，也不会当作你的范文或你的开场，因为里面可能有 AI 写的句子。',
  po_wait_body_hand: '数据已经计入分析。它的文字暂时不会用来量你的说话习惯、学你的声音，也不会当作你的范文或你的开场：在工作台里写的稿子，都要你确认过才算你自己的说法。',
  po_wait_how: '想让文字也算数，有三个办法：确认「这就是我说的」；附上这条视频的字幕文件；或者在这台电脑上转写这条视频。后两种存下来的就是你实际说的话。',
  po_wait_all_ai: '注意：现在每一句都是 AI 写的。如果就这样确认，工作台会把 AI 的句子当成你的说话习惯来学。',
  po_confirm: '这就是我说的，是我自己的话',
  po_attach_subs: '附上字幕文件（.srt / .vtt）', po_attach_video: '在这台电脑上转写这条视频',
  po_attached: '已换成视频里实际说的话。', po_attach_working: '正在转写这条视频…', po_attach_failed: '没有成功：',
  po_no_transcriber: '这台电脑还没装好转写功能。请先到「开始」页的素材那一步安装，或者改用字幕文件。',
  po_unconfirm: '不再把它算作我的说法', po_unconfirmed: '已改回：它的文字不再算作你的说法。',
  po_from: '来自工作台的脚本', po_open_script: '打开脚本：{title}', po_script_gone: '原来的脚本已经删除',
  po_delete_unmarks: '它是从工作台的一篇脚本发布的：删除以后，那篇脚本不再显示「已发布」，脚本本身还在。',
  po_ai_openings: 'AI 写的开场（来自在这里写、已经发布的脚本）', po_ai_opening_tag: 'AI 写的，不算你的开场',
  po_ai_openings_hint: '这些开场还是 AI 写的原句，所以不列在你自己的开场里，也不会当作你的习惯交给 AI 去模仿。视频的数据是你的，所以照样给你看。',
});
Object.assign(I18N.en, {
  po_title: 'Posting record', po_tag: 'Posted', po_card: 'Posted · {d}',
  po_mark: 'Mark as posted',
  po_mark_hint: 'Use this once the video is out: the script joins the library with its numbers, so the topic and trend analysis can use it.',
  po_date: 'Date posted', po_link: 'Link to the note', po_link_hint: 'optional; kept as text only, the studio never opens it', po_link_ph: 'https://…',
  po_sponsored: 'This one is sponsored', po_numbers: 'Numbers', po_numbers_hint: 'can stay empty and be added later', po_read_on: 'Day these numbers were read',
  po_goes: 'On saving, the script\'s title and spoken lines as they are now (live clips left out) become one item in the library. The numbers count in the analysis at once; the wording does not count as {@names} own until it is confirmed.',
  po_save: 'Save', po_cancel: 'Cancel', po_marked: 'Marked as posted and added to the library.', po_saved: 'Saved.',
  po_posted_on: 'Posted on {d}', po_to_library: 'See its item in the library',
  po_no_numbers: 'No numbers entered yet.',
  po_read: 'Numbers read on {d}: {n} days after posting.', po_read_one: 'Numbers read on {d}: 1 day after posting.',
  po_read_same: 'Numbers read on {d}: the day it was posted.', po_read_plain: 'Numbers read on {d}.',
  po_compare: 'Numbers keep growing for days: a video read 2 days after posting and one read 60 days after are not comparable. Only the day they were read is recorded; nothing is adjusted or projected.',
  po_update_numbers: 'Update numbers',
  po_differs: 'This script was edited after it was posted. Its item in the library still holds the text as posted; it did not change with the script.',
  po_refresh: 'Update the library item from the script as it is now',
  po_refresh_confirm: 'The title and text of the library item will be replaced with the script as it is now, and edits made to the item in the library are overwritten. After that its wording waits to be confirmed again before it counts as {@names} own. Continue?',
  po_refreshed: 'The library item was updated.',
  po_unmark_hint: 'To undo "posted", remove its item in the library; the script itself is not touched.',
  po_stale: 'This script was changed somewhere else and is no longer what this page shows. Reload the page first.',
  po_save_first: 'The script is not saved yet. Please try again in a moment.',
  po_w_waiting: 'Its wording does not count as {@names} own yet.',
  po_w_how: 'Its numbers already count in the analysis. To make the wording count too, confirm it in the library, or attach the subtitle file of the video there.',
  po_w_confirmed: 'Confirmed: its wording counts as {@names} own.',
  po_w_spoken: 'The library holds what was actually said in the video (from its subtitle file or a transcript), which counts as {@names} own wording.',
  po_w_transcript: 'The library holds a machine transcript of the video that is still waiting to be read and confirmed.',
  po_c_all: 'All {n} lines are still exactly as the AI wrote them; none was changed.', po_c_all_one: 'Its 1 line is still exactly as the AI wrote it.',
  po_c_some: '{k} of {n} lines are still exactly as the AI wrote them; the rest was changed or written by {@name}.',
  po_c_none: 'All {n} lines were changed or written by {@name}; none is still as the AI wrote it.',
  po_c_none_one: 'Its 1 line was changed or written by {@name}; it is not as the AI wrote it.',
  po_c_hand: 'This script was typed by hand in the studio: none of its {n} lines was written by an AI.',
  po_c_hand_one: 'This script was typed by hand in the studio: its 1 line was not written by an AI.',
  po_c_hand_some: 'This script was typed by hand, but {k} of its {n} lines are AI proposals that were taken.',
  po_wait_title: 'Posted from a script written in the studio: its wording does not count as {@names} own yet.',
  po_wait_body: 'Its numbers already count in the analysis. For now its wording is not used to measure {@names} speech habits or to learn {@their} voice, nor as one of {@their} own examples or openings, because some of it may have been written by an AI.',
  po_wait_body_hand: 'Its numbers already count in the analysis. For now its wording is not used to measure {@names} speech habits or to learn {@their} voice, nor as one of {@their} own examples or openings: a script written in the studio counts as {@names} own wording only once that is confirmed.',
  po_wait_how: 'Three ways to make the wording count: confirm "this is what was said"; attach the subtitle file of the posted video; or transcribe the video on this computer. With the last two, what is kept is what was actually said.',
  po_wait_all_ai: 'Note: every line is the AI\'s. Confirming it as it is would teach the studio the AI\'s sentences as {@names} way of speaking.',
  po_confirm: 'This is what was said, in {@names} own words',
  po_attach_subs: 'Attach the subtitle file (.srt / .vtt)', po_attach_video: 'Transcribe the video on this computer',
  po_attached: 'Replaced with what was said in the video.', po_attach_working: 'Transcribing the video…', po_attach_failed: 'That did not work: ',
  po_no_transcriber: 'Transcription is not set up on this computer yet. Install it on the Start page, in the material step, or use a subtitle file.',
  po_unconfirm: 'Stop counting it as {@names} own wording', po_unconfirmed: 'Changed back: its wording no longer counts as {@names} own.',
  po_from: 'From a script in the studio', po_open_script: 'Open the script: {title}', po_script_gone: 'the script it came from was deleted',
  po_delete_unmarks: 'It was posted from a script in the studio: once it is removed, that script is no longer shown as posted. The script itself stays.',
  po_ai_openings: 'Openings an AI wrote (in scripts written here and posted)', po_ai_opening_tag: 'written by AI, not {@names} own opening',
  po_ai_openings_hint: 'These openings are still sentences an AI wrote, so they are not listed among {@names} own and are never handed to an AI as {@their} habit. The videos\' numbers are {@names} own, so they are shown all the same.',
});

const PO_METRICS = ['views', 'likes', 'collects', 'comments'];     // must match METRIC_FIELDS in studio/store.py
// The form open in the script page's posting record: null, 'mark' or 'numbers'. Kept here and not read off the
// page, with what was typed into it: adding or moving a line draws the whole editor again.
const poUi = { scriptId: null, form: null, values: {}, error: '', busy: false };
const poItems = {};     // library item id -> its "posted" facts as last drawn
const poJobs = {};      // library item id -> { text } while its video is being transcribed
let poFocus = null;     // the library item to scroll to when the library is drawn next

const poToday = () => {
  const d = new Date();
  const two = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${two(d.getMonth() + 1)}-${two(d.getDate())}`;
};
/* What the server said about a refused request, in the interface language. */
const poSay = (e) => (lang === 'zh' ? e.message_zh : e.detail) || e.detail || t('err_default');
const poFail = (e) => toast(poSay(e));
const poIsLink = (url) => /^https?:\/\//i.test(String(url || ''));

/* ---------- the sentences about numbers and about wording ---------- */
function poReadText(p) {
  if (!p.has_numbers || !p.metrics_on) return t('po_no_numbers');
  const n = p.read_after_days;
  if (typeof n !== 'number') return tf('po_read_plain', { d: p.metrics_on });
  if (n === 0) return tf('po_read_same', { d: p.metrics_on });
  return tf(countKey('po_read', n), { d: p.metrics_on, n });
}

/* How many of the item's lines are still exactly as an AI wrote them. The server counts; this only words it. */
function poCountText(p) {
  const n = p.lines;
  const k = p.lines_by_ai;
  if (typeof n !== 'number' || typeof k !== 'number' || !n) return '';
  const vars = { n, k, m: n - k };
  if (!p.drafted) return tf(k ? 'po_c_hand_some' : countKey('po_c_hand', n), vars);
  if (k === n) return tf(countKey('po_c_all', n), vars);
  return tf(k ? 'po_c_some' : countKey('po_c_none', n), vars);
}

function poWordingText(p) {
  if (p.wording === 'waiting') return [t('po_w_waiting'), poCountText(p), t('po_w_how')].filter(Boolean).join(' ');
  if (p.wording === 'confirmed') return [t('po_w_confirmed'), poCountText(p)].filter(Boolean).join(' ');
  return t(p.wording === 'transcript_waiting' ? 'po_w_transcript' : 'po_w_spoken');
}

/* ---------- the scripts list ---------- */
function poCardTag(s) {
  return s.posted ? ` <span class="tag ok" data-po-card>${esc(s.posted_on ? tf('po_card', { d: s.posted_on }) : t('po_tag'))}</span>` : '';
}

/* ---------- the script page: the posting record ---------- */
function poFormHtml() {
  const v = poUi.values;
  const today = poToday();
  const first = poUi.form === 'mark';
  const metric = (f) => `<label>${esc(t('m_' + f))}<input type="number" min="0" inputmode="numeric" data-po-v="${f}" value="${esc(v[f] === undefined || v[f] === null ? '' : v[f])}"></label>`;
  return `${first ? `<label class="field" for="po-date">${esc(t('po_date'))}</label>
    <input type="date" id="po-date" data-po-v="posted_on" max="${today}" value="${esc(v.posted_on || '')}">
    <label class="field" for="po-link">${esc(t('po_link'))} <span class="hint">· ${esc(t('po_link_hint'))}</span></label>
    <input type="text" id="po-link" data-po-v="note_url" inputmode="url" spellcheck="false" placeholder="${esc(t('po_link_ph'))}" value="${esc(v.note_url || '')}">
    <label class="check" style="margin-top:14px"><input type="checkbox" id="po-sponsored" data-po-v="is_sponsored" ${v.is_sponsored ? 'checked' : ''}> ${esc(t('po_sponsored'))}</label>` : ''}
    <label class="field">${esc(t('po_numbers'))} <span class="hint">· ${esc(t('po_numbers_hint'))}</span></label>
    <div class="metrics">${PO_METRICS.map(metric).join('')}</div>
    <label class="field" for="po-read">${esc(t('po_read_on'))}</label>
    <input type="date" id="po-read" data-po-v="metrics_on" max="${today}" value="${esc(v.metrics_on || '')}">
    <p class="hint">${esc(t('po_compare'))}</p>
    ${first ? `<p class="hint">${esc(t('po_goes'))}</p>` : ''}
    <div class="row" style="margin-top:12px"><button type="button" class="btn primary" data-po="save">${esc(t('po_save'))}</button>
      <button type="button" class="btn" data-po="cancel">${esc(t('po_cancel'))}</button></div>`;
}

function poRecordHtml(p) {
  /* The link is built from what was stored: text that goes through esc() into the address and into what is shown. */
  const note = poIsLink(p.note_url)
    ? `<p class="po-note">${esc(t('po_link'))}: <a id="po-note" href="${esc(p.note_url)}" target="_blank" rel="noopener noreferrer">${esc(p.note_url)}</a></p>` : '';
  const numbers = p.has_numbers ? PO_METRICS.map((f) => `${esc(t('m_' + f))} <b>${esc(num(p[f]))}</b>`).join(' · ') + '<br>' : '';
  return `<p id="po-when">${esc(tf('po_posted_on', { d: p.posted_on || '' }))} ·
      <a href="#/library" data-po="to-library" data-po-sample="${esc(p.sample_id)}">${esc(t('po_to_library'))}</a></p>
    ${note}
    <p id="po-numbers">${numbers}${esc(poReadText(p))}</p>
    <p class="hint">${esc(t('po_compare'))}</p>
    <p id="po-wording">${esc(poWordingText(p))}</p>
    ${p.text_differs ? `<div class="banner" id="po-differs">${esc(t('po_differs'))}
      <div class="row" style="margin-top:8px"><button type="button" class="btn" data-po="refresh">${esc(t('po_refresh'))}</button></div></div>` : ''}
    <div class="row"><button type="button" class="btn" data-po="numbers">${esc(t('po_update_numbers'))}</button></div>
    <p class="hint">${esc(t('po_unmark_hint'))}</p>`;
}

/* Called from paintScript (app.js). `script.posted` is null, or what was recorded when the script was marked. */
function paintPosted(el, script) {
  if (!el) return;
  if (poUi.scriptId !== script.id) Object.assign(poUi, { scriptId: script.id, form: null, values: {}, error: '', busy: false });
  const p = script.posted || null;
  let body;
  if (poUi.form) body = poFormHtml();
  else if (p) body = poRecordHtml(p);
  else {
    body = `<p class="hint">${esc(t('po_mark_hint'))}</p>
      <div class="row"><button type="button" class="btn" data-po="mark">${esc(t('po_mark'))}</button></div>`;
  }
  el.innerHTML = `<div class="card soft" id="po-card">
    <h3>${esc(t('po_title'))}${p ? ` <span class="tag ok" id="po-tag">${esc(t('po_tag'))}</span>` : ''}</h3>
    ${poUi.error ? `<div class="banner" id="po-error" role="alert">${esc(poUi.error)}</div>` : ''}${body}</div>`;
}

function poPaint() {
  if (cur) paintPosted(document.getElementById('po-box'), cur.script);
}

/* Called from saveNow (app.js) after a save: the record is asked for again, so that "edited after it was posted"
   shows up as soon as it is true, and a record whose library item was removed elsewhere stops being shown. */
async function poAfterSave(s) {
  if (!s.posted || poUi.form || poUi.busy) return;
  try {
    const fresh = await api('GET', '/api/scripts/' + s.id);
    if (!cur || cur.script !== s || poUi.form || poUi.busy) return;
    s.posted = fresh.posted;
    poPaint();
  } catch (e) { /* the record keeps what it showed; the next save asks again */ }
}

/* The stored script, when it is the script on this page; otherwise null, after saying why. What goes into the
   library is the stored script, so it has to be what the creator is looking at: everything typed is saved first,
   and a script that was changed somewhere else in the meantime is not marked from here. */
async function poStored(s) {
  await flushSave();
  if (dirty) { toast(t('po_save_first')); return null; }
  const fresh = await api('GET', '/api/scripts/' + s.id);
  const spoken = (script) => JSON.stringify(script.lines.map((line) => [String(line.audio_mode || ''), String(line.spoken_script_zh || '')]));
  if (spoken(fresh) !== spoken(s)) { toast(t('po_stale')); return null; }
  return fresh;
}

async function poSave() {
  if (poUi.busy || !cur) return;
  const s = cur.script;
  const v = poUi.values;
  const numbers = {};
  PO_METRICS.forEach((f) => { numbers[f] = v[f] === undefined || v[f] === null ? '' : v[f]; });
  poUi.busy = true;
  poUi.error = '';
  try {
    let out;
    if (poUi.form === 'mark') {
      const fresh = await poStored(s);
      if (!fresh) return;
      out = await api('POST', `/api/scripts/${s.id}/posted`, Object.assign({
        posted_on: v.posted_on || '', note_url: v.note_url || '', is_sponsored: !!v.is_sponsored,
        metrics_on: v.metrics_on || '', base_updated_at: fresh.updated_at,
      }, numbers));
      toast(t('po_marked'));
    } else {
      out = await api('PUT', `/api/scripts/${s.id}/posted`, Object.assign({ metrics_on: v.metrics_on || '' }, numbers));
      toast(t('po_saved'));
    }
    s.posted = out.posted;
    Object.assign(poUi, { form: null, values: {} });
  } catch (e) {
    poUi.error = poSay(e);
    toast(poUi.error);
    if (e.code === 'not_posted') { s.posted = null; poUi.form = null; }     // its library item was removed somewhere else
  } finally {
    poUi.busy = false;
    poPaint();
  }
}

async function poRefresh() {
  if (poUi.busy || !cur) return;
  const s = cur.script;
  /* While the question is open another script may have been opened: the answer then belongs to nothing. */
  if (!(await askConfirm(t('po_refresh_confirm'))) || poUi.busy || !cur || cur.script.id !== s.id) return;
  poUi.busy = true;
  poUi.error = '';
  try {
    const fresh = await poStored(s);
    if (!fresh) return;
    const out = await api('PUT', `/api/scripts/${s.id}/posted`, { refresh_text: true, base_updated_at: fresh.updated_at });
    s.posted = out.posted;
    toast(t('po_refreshed'));
  } catch (e) {
    poUi.error = poSay(e);
    toast(poUi.error);
    if (e.code === 'not_posted') s.posted = null;
  } finally {
    poUi.busy = false;
    poPaint();
  }
}

/* ---------- the library: an item that came from a script ---------- */
function poItemInner(id, p) {
  const today = poToday();
  const from = p.script_id
    ? `<a href="#/s/${esc(p.script_id)}" data-po-script>${esc(tf('po_open_script', { title: p.script_title || '' }))}</a>`
    : `<span class="meta" data-po-gone>${esc(t('po_script_gone'))}</span>`;
  const note = poIsLink(p.note_url)
    ? `<div class="po-note">${esc(t('po_link'))}: <a href="${esc(p.note_url)}" target="_blank" rel="noopener noreferrer" data-po-note>${esc(p.note_url)}</a></div>` : '';
  const waiting = p.wording === 'waiting' || p.wording === 'transcript_waiting';      // the note above the text says it
  const state = waiting ? '' : `<p class="hint" data-po-state>${esc(poWordingText(p))}${p.wording === 'confirmed'
    ? ` <a href="#" data-po="unconfirm" data-po-sample="${esc(id)}">${esc(t('po_unconfirm'))}</a>` : ''}</p>`;
  return `<div class="po-line"><span class="tag ok">${esc(t('po_from'))}</span>${from}</div>${note}
    <div class="po-fields">
      <label>${esc(t('po_date'))}<input type="date" data-po-f="posted_on" max="${today}" value="${esc(p.posted_on || '')}"></label>
      ${p.has_numbers ? `<label>${esc(t('po_read_on'))}<input type="date" data-po-f="metrics_on" max="${today}" value="${esc(p.metrics_on || '')}"></label>` : '<span></span>'}
      <label class="po-wide">${esc(t('po_link'))}<input type="text" data-po-f="note_url" inputmode="url" spellcheck="false" placeholder="${esc(t('po_link_ph'))}" value="${esc(p.note_url || '')}"></label>
    </div>
    <p class="hint" data-po-read>${esc(poReadText(p))} ${esc(t('po_compare'))}</p>${state}`;
}

/* Called from sampleHtml (pages.js): the block under an item's numbers. Nothing for an item that came in by hand. */
function poSampleHtml(s) {
  if (!s.posted) { delete poItems[s.id]; return ''; }
  poItems[s.id] = s.posted;
  if (poFocus === s.id) {
    poFocus = null;
    setTimeout(() => {
      const card = document.querySelector(`.sample[data-id="${s.id}"]`);
      if (card) card.scrollIntoView({ block: 'center' });
    }, 0);
  }
  return `<div class="po-item" data-po-item>${poItemInner(s.id, s.posted)}</div>`;
}

/* Called from materialRowHtml (setup.js): the same item in the short list of the setup page. */
function poRowTag(x) {
  if (!x.posted) { delete poItems[x.id]; return ''; }
  poItems[x.id] = x.posted;
  return `<span class="tag ok" data-po-row>${esc(t('po_tag'))}</span>`;
}

/* Called from reviewNoteHtml (setup.js) for a posted script whose wording waits: why it waits, how many of its lines
   an AI wrote, and the three ways to make it count. The existing confirm button and its handler do the confirming. */
function poReviewNoteHtml(x, withButton) {
  const p = x.posted;
  const job = poJobs[x.id];
  const allAi = !!(p.drafted && p.lines && p.lines_by_ai === p.lines);
  /* Why it waits. A script typed by hand with no AI line in it is not told that an AI may have written some of it. */
  const why = t(!p.drafted && p.lines && !p.lines_by_ai ? 'po_wait_body_hand' : 'po_wait_body');
  const button = withButton === false ? '' : `<button type="button" class="btn" data-confirm>${esc(t('po_confirm'))}</button>`;
  const attach = job ? `<span class="meta" data-po-job>${esc(job.text)}</span>` : `
      <label class="btn">${esc(t('po_attach_subs'))}<input type="file" accept=".srt,.vtt" hidden data-po-attach="subtitle" data-po-sample="${esc(x.id)}"></label>
      <label class="btn">${esc(t('po_attach_video'))}<input type="file" accept=".mp4,.mov,.m4v,.m4a,.mp3,.wav" hidden data-po-attach="video" data-po-sample="${esc(x.id)}"></label>`;
  return `<div class="banner review-note" data-po-wording><b>${esc(t('po_wait_title'))}</b> <span data-po-count>${esc(poCountText(p))}</span>
    ${allAi ? `<div data-po-all-ai><b>${esc(t('po_wait_all_ai'))}</b></div>` : ''}
    <div data-po-why>${esc(why)} ${esc(t('po_wait_how'))}</div>
    <div class="row" style="margin-top:8px;flex-wrap:wrap">${button}${attach}</div></div>`;
}

const poWaits = (x) => !!(x && x.posted && x.posted.wording === 'waiting');
/* The words on the confirm button of the setup page: what is confirmed differs for a posted script and a transcript. */
const poConfirmLabel = (x) => t(poWaits(x) ? 'po_confirm' : 'rn_confirm');

/* The question asked before an item is removed: removing the item of a posted script also un-marks that script. */
function poDeleteSampleText(id, key) {
  const p = poItems[id];
  return t(key) + (p && p.script_id ? '\n\n' + t('po_delete_unmarks') : '');
}

/* Called by the library page after an edit to an item was saved (pages.js): a number typed there takes the day it
   was read with it, and an edit to the text changes how many lines are an AI's, so both are drawn again. */
function poAfterSampleSave(card, saved) {
  if (!card || !saved || !saved.posted) return;
  poItems[saved.id] = saved.posted;
  const block = card.querySelector('[data-po-item]');
  if (block) block.innerHTML = poItemInner(saved.id, saved.posted);
  const note = card.querySelector('[data-po-wording]');
  if (note && poWaits(saved)) note.outerHTML = poReviewNoteHtml(saved, true);
}

async function poField(input) {
  const card = input.closest('.sample');
  if (!card) return;
  try {
    poAfterSampleSave(card, await api('PUT', '/api/samples/' + card.dataset.id, { [input.dataset.poF]: input.value }));
    toast(t('po_saved'));
  } catch (e) {
    poFail(e);
    await router();     // the page goes back to what is stored
  }
}

/* The words of the posted video, for the item its script already has: a subtitle file, or the video itself, which
   is transcribed on this computer through the same upload and the same job as any video. */
async function poAttach(input) {
  const file = input.files && input.files[0];
  const id = input.dataset.poSample;
  const kind = input.dataset.poAttach;
  input.value = '';
  if (!file) return;
  if (kind === 'subtitle') {
    try {
      await api('POST', '/api/samples', { subtitle_text: await readTextFile(file), source_name: file.name, attach_to: id });
      toast(t('po_attached'));
    } catch (e) {
      if (e.code === 'bad_subtitles') toast(tf('mt_bad_subs', { name: file.name })); else poFail(e);
    }
    await router();
    return;
  }
  const ears = await api('GET', '/api/transcriber');
  if (!(ears.supported && ears.installed && ears.model_ready)) { toast(t('po_no_transcriber')); return; }
  poJobs[id] = { text: t('po_attach_working') };
  await router();
  try {
    const res = await fetch('/api/uploads?name=' + encodeURIComponent(file.name), { method: 'PUT', headers: { 'Content-Type': 'application/octet-stream' }, body: file });
    const up = await res.json().catch(() => ({}));
    if (!res.ok) throw Object.assign(new Error(up.message || up.error || 'upload'), { detail: up.message, message_zh: up.message_zh });
    let job = (await api('POST', '/api/videos', { upload_id: up.upload_id, attach_to: id })).job;
    while (job.status === 'running') {
      await new Promise((resolve) => { setTimeout(resolve, 800); });
      job = (await api('GET', '/api/jobs/' + job.id)).job;
    }
    if (job.status !== 'done') throw Object.assign(new Error('failed'), { detail: job.error && job.error.message, message_zh: job.error && job.error.message_zh });
    toast(t('po_attached'));
  } catch (e) {
    toast(t('po_attach_failed') + poSay(e));
  }
  delete poJobs[id];
  if (location.hash === '#/library' || location.hash === '#/start') await router();
}

/* ---------- the openings tool: openings that are still an AI's sentence ---------- */
/* Called from craftOwnHtml (craft.js). `found.written_by_ai` is there only when there is one (studio/hooks.py). */
function poAiOpeningsHtml(found) {
  const list = (found && found.written_by_ai) || [];
  if (!list.length) return '';
  const rows = list.slice(0, 5).map((o) => `<div class="craft-own" data-po-ai-opening>
      <div>${esc(o.opening)}</div>
      <div class="meta">${esc(o.title)} · ${esc(o.times_usual !== null ? tf('cr_times', { x: o.times_usual }) : t('cr_no_numbers'))}
        <span class="tag warn">${esc(t('po_ai_opening_tag'))}</span></div></div>`).join('');
  return `<div id="po-ai-openings"><h4>${esc(t('po_ai_openings'))}</h4>${rows}<p class="hint">${esc(t('po_ai_openings_hint'))}</p></div>`;
}

/* ---------- what the buttons and fields of this file do ---------- */
async function poAct(what, el) {
  if (what === 'mark' || what === 'numbers') {
    if (!cur) return;
    const p = cur.script.posted || {};
    const today = poToday();
    const values = what === 'mark'
      ? { posted_on: today, note_url: '', is_sponsored: !!cur.script.mode && cur.script.mode !== 'NORMAL_ORGANIC', metrics_on: today }
      : { metrics_on: today };
    if (what === 'numbers') PO_METRICS.forEach((f) => { values[f] = p[f] === null || p[f] === undefined ? '' : p[f]; });
    Object.assign(poUi, { form: what, values, error: '' });
    poPaint();
  } else if (what === 'cancel') {
    Object.assign(poUi, { form: null, values: {}, error: '' });
    poPaint();
  } else if (what === 'save') {
    await poSave();
  } else if (what === 'refresh') {
    await poRefresh();
  } else if (what === 'unconfirm') {
    await api('PUT', '/api/samples/' + el.dataset.poSample, { needs_review: true });
    toast(t('po_unconfirmed'));
    await router();
  }
}

document.addEventListener('click', (e) => {
  const el = e.target.closest('[data-po]');
  if (!el) return;
  if (el.dataset.po === 'to-library') { poFocus = el.dataset.poSample; return; }      // the link itself goes there
  e.preventDefault();
  poAct(el.dataset.po, el).catch(poFail);
});

const poTyped = (e) => {
  const el = e.target.closest('[data-po-v]');
  if (el) poUi.values[el.dataset.poV] = el.type === 'checkbox' ? el.checked : el.value;
};
document.addEventListener('input', poTyped);
document.addEventListener('change', (e) => {
  poTyped(e);
  const attach = e.target.closest('[data-po-attach]');
  if (attach) { poAttach(attach).catch(poFail); return; }
  const field = e.target.closest('[data-po-f]');
  if (field) poField(field).catch(poFail);
});
