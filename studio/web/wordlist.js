'use strict';
/* The word list behind the wording findings. Loaded after kwimport.js and before pages.js (which boots the app);
   helpers from pages.js (tf, countKey) are used at call time.

   Three things live here:
   - wlIssueHtml(f): one finding, as the editor and the line tools show it. A finding that comes from the word list
     carries what it rests on. A hint that a line may sound written (category AI_TELL) is drawn differently and is
     labelled as a hint: it is not a rule, and nothing on a page counts it as one.
   - wlNoteHtml(notice): what is said once wherever findings are listed (the editor, and the Trends and Ideas pages,
     which mark keywords with the same list). The lists are this project's own and no statement of any rule, the
     day they were reviewed, and how many shipped entries are switched off.
   - wlPaintSettings(el): the section of the Settings page where entries are switched off and on and the creator's
     own phrases are kept (GET and PUT /api/wordlist).

   What is said about an entry comes from the server, which takes it from studio/lexicon.json: nothing about a law
   is written in this file. Everything shown goes through esc(), a phrase the creator typed included: it is text
   here and on the server, and is never made into a pattern. */

Object.assign(I18N.zh, {
  wl_tell_label: '只是提示', wl_hints_n: '{n} 条听感提示',
  wl_no_finding: '没有提示，不等于这样说就没问题。', wl_reviewed: '词表上次整理：{d}。',
  wl_off_n: '词表里有 {n} 条被关掉了，这些说法现在不会提示。',
  wl_unreadable: '保存的词表改动读不出来，所以现在用的是自带的词表：关掉的条目和你自己加的说法都没有生效。在「设置」的词表里再改一次（开关任意一条，或加一条说法），就会重新存好；原来没读出来的改动不会恢复。',
  wl_manage: '查看和修改词表',
  wl_title: '用词提示的词表',
  wl_intro: '写稿和检查时，工作台按这份词表提示可能有问题的广告用词，还有念出来像书面稿的说法。每一条都可以关掉，你也可以加自己的说法。你在这里做的改动只保存在这台电脑上，不会写进给 AI 的提示词。',
  wl_loading: '正在读取词表…', wl_failed: '词表读取失败，请刷新页面再试。',
  wl_count: '{n} 条', wl_count_off: '{n} 条，其中 {off} 条已关掉',
  wl_read_on: '这份文本是 {d} 读的。', wl_source_link: '原文 ↗',
  wl_group_none: '没有核对过出处的广告用词',
  wl_group_none_d: '这几条来自常见做法。小红书自己的规则和化妆品命名的规定都没有读过，所以这里不引用任何出处。',
  wl_group_tells: '念出来像书面稿的说法',
  wl_tells_d: '这一类只是提示：从不标红，不算违规，也不拦任何操作。它只提醒这一句可能像写出来的，而不像说出来的。',
  wl_tells_credit: '线索参照：{title}。',
  wl_group_tells_own: '本项目自己加的线索',
  wl_group_tells_own_d: '这几条没有出处，是本项目自己加的，同样只是提示。',
  wl_sev_error: '会标红', wl_sev_review: '请你确认', wl_sev_hint: '只是提示',
  wl_basis: '依据：{where}：{says}', wl_by_analogy: '法条里没有点名这些说法，是比照列入的。',
  wl_no_basis: '没有核对过出处。', wl_pattern_no: '参照第 {n} 类写法。',
  wl_is_off: '已关掉', wl_turn_off: '关掉', wl_turn_on: '重新打开',
  wl_own_title: '你自己加的说法',
  wl_own_hint: '想再确认一下的说法可以加在这里：它出现在口播台词里时会黄色提醒，并注明是你自己加的。照你写的字面去找，不分大小写和全角半角，空格和标点都算数。',
  wl_own_differs: '这和「你坚决不说的话」不是一回事：那一份一出现就标红，字中间夹了空格或标点也认得出，还会写进提示词让 AI 别写。',
  wl_never_link: '到「开始」第一步「{step}」里修改',
  wl_own_ph: '例如：包邮', wl_own_add: '添加', wl_own_none: '还没有加。', wl_own_remove: '删除',
  wl_own_limits: '最多 {n} 条，每条最多 {c} 个字符。',
});
Object.assign(I18N.en, {
  wl_tell_label: 'Hint only', wl_hints_n: '{n} hints on how it sounds', wl_hints_n_one: '1 hint on how it sounds',
  wl_no_finding: 'No finding does not mean the wording is allowed.', wl_reviewed: 'List last reviewed: {d}.',
  wl_off_n: '{n} entries of the list are switched off, so that wording is not flagged now.',
  wl_off_n_one: '1 entry of the list is switched off, so that wording is not flagged now.',
  wl_unreadable: 'The stored changes to the word list could not be read, so the shipped list is in use: nothing is switched off and no phrase of {@names} own is applied. Changing the list once in Settings (switching any entry off or on, or adding a phrase) stores it afresh; the changes that could not be read are not brought back.',
  wl_manage: 'See and change the list',
  wl_title: 'The word list behind the wording findings',
  wl_intro: 'While a script is written and checked, the studio flags advertising wording that may be a problem, and wording that sounds written when read aloud, from this list. Any entry can be switched off, and phrases can be added. What is changed here stays on this computer and is not put into any prompt for an AI.',
  wl_loading: 'Reading the word list…', wl_failed: 'The word list could not be read. Reload the page to try again.',
  wl_count: '{n} entries', wl_count_one: '1 entry', wl_count_off: '{n} entries, {off} switched off',
  wl_read_on: 'This text was read on {d}.', wl_source_link: 'source ↗',
  wl_group_none: 'Advertising wording with no source checked',
  wl_group_none_d: 'These entries rest on common practice. Xiaohongshu\'s own rules and the guidance on naming cosmetics were not read, so nothing is cited for them.',
  wl_group_tells: 'Wording that sounds written when spoken',
  wl_tells_d: 'Hints only: never shown in red, never counted as a rule being broken, and they stop nothing. They only say that a line may sound written rather than spoken.',
  wl_tells_credit: 'The clues follow: {title}.',
  wl_group_tells_own: 'Clues added by this project',
  wl_group_tells_own_d: 'These have no source: this project added them. They are hints only as well.',
  wl_sev_error: 'shown in red', wl_sev_review: 'asks for a check', wl_sev_hint: 'hint only',
  wl_basis: 'Basis: {where}: {says}', wl_by_analogy: 'The law does not name this wording; it is listed by analogy.',
  wl_no_basis: 'No source was checked.', wl_pattern_no: 'After pattern {n}.',
  wl_is_off: 'switched off', wl_turn_off: 'Switch off', wl_turn_on: 'Switch back on',
  wl_own_title: '{@Names} own phrases',
  wl_own_hint: 'Wording to look at twice can be added here: when it appears in a spoken line it is flagged for a check and marked as coming from {@names} own list. It is looked for exactly as written, whatever the letter case or width; spaces and punctuation count.',
  wl_own_differs: 'This is not the list of phrases never to be used. Those are shown in red as soon as they appear, are recognised with spaces or punctuation between their characters, and go into the prompts so that an AI does not write them.',
  wl_never_link: 'Edit those under Start, step 1, "{step}"',
  wl_own_ph: 'e.g. 包邮', wl_own_add: 'Add', wl_own_none: 'None added yet.', wl_own_remove: 'Remove',
  wl_own_limits: 'Up to {n} phrases, each up to {c} characters.',
});

/* ---------- a finding, and the note that goes with a list of findings ---------- */

/* A hint that a line may sound written. It is not counted among the things to fix. */
const wlIsHint = (f) => f.category === 'AI_TELL';

/* One finding. For a finding from the word list the server sends its message in two parts (studio/checks.py):
   what was matched and why, and what that rests on. The second is set apart, smaller, under the first. */
function wlIssueHtml(f) {
  const zh = lang === 'zh';
  const basis = zh ? f.basis_zh : f.basis_en;
  const lead = basis ? (zh ? f.lead_zh : f.lead_en) : (zh ? f.message_zh : f.message_en);
  const hint = wlIsHint(f);
  return `<div class="issue ${hint ? 'tell' : (f.severity === 'error' ? 'error' : '')}"${hint ? ' data-wl-hint' : ''}>${
    hint ? `<span class="issue-kind">${esc(t('wl_tell_label'))}</span>` : ''}${esc(lead)}${
    basis ? `<small data-wl-basis>${esc(basis)}</small>` : ''}</div>`;
}

/* How many hints there are, to stand beside the count of things to check; '' when there is none. */
function wlHintsNote(n) {
  return n ? ' · ' + esc(tf(countKey('wl_hints_n', n), { n })) : '';
}

/* Said once wherever findings are listed. `notice` is `wordlist` from /api/lint or /api/trends. */
function wlNoteHtml(notice) {
  if (!notice) return '';
  const said = [esc(lang === 'zh' ? notice.disclaimer_zh : notice.disclaimer_en), esc(t('wl_no_finding'))];
  if (notice.reviewed_on) said.push(esc(tf('wl_reviewed', { d: notice.reviewed_on })));
  if (notice.switched_off) {
    said.push(`<b data-wl-off="${Number(notice.switched_off)}">${esc(tf(countKey('wl_off_n', notice.switched_off), { n: notice.switched_off }))}</b>`);
  }
  if (notice.unreadable) said.push(`<b data-wl-unreadable>${esc(t('wl_unreadable'))}</b>`);
  return `<p class="hint wl-note">${said.join(' ')} <a href="#/settings" data-wl-manage>${esc(t('wl_manage'))}</a></p>`;
}

function wlPaintNote(el, notice) {
  if (el) el.innerHTML = wlNoteHtml(notice);
}

/* ---------- the section of the Settings page ---------- */

/* data: the last answer of /api/wordlist. open: which groups the creator folded or unfolded, by key; a group
   they have not touched is drawn open when it holds an entry that is off. typed: what stands in the box for a
   new phrase. seq names the newest request, so the answer to an older one is dropped.
   Kept here and not read off the page: the settings page is drawn again on every click in it. */
const wlUi = { data: null, open: {}, typed: '', seq: 0 };

async function wlPaintSettings(el) {
  if (!el) return;
  if (wlUi.data) wlDraw(el);
  else el.innerHTML = `<p class="hint" id="wl-loading">${esc(t('wl_loading'))}</p>`;
  const seq = ++wlUi.seq;
  let data;
  try { data = await api('GET', '/api/wordlist'); } catch (e) {
    if (!wlUi.data && el.isConnected) el.innerHTML = `<p class="hint" id="wl-failed">${esc(t('wl_failed'))}</p>`;
    return;
  }
  if (seq !== wlUi.seq) return;
  /* Drawn again only when something differs from what is on the page: a redraw takes the cursor out of the box. */
  const same = wlUi.data && JSON.stringify(wlUi.data) === JSON.stringify(data);
  wlUi.data = data;
  if (!same && el.isConnected) wlDraw(el);
}

/* The link to a text the list rests on. The address comes from the list; only an https address becomes a link. */
function wlSourceLinkHtml(source) {
  return /^https:\/\//.test(source.url || '')
    ? ` <a href="${esc(source.url)}" target="_blank" rel="noopener noreferrer">${esc(t('wl_source_link'))}</a>` : '';
}

/* The entries in groups by what they rest on: each text that was read, then the advertising wording for which
   nothing was read, then the hints by their source, then the hints this project added itself. */
function wlGroups(data) {
  const zh = lang === 'zh';
  const groups = [];
  const found = {};
  const sourceOf = (e) => (e.group === 'compliance' ? (e.basis && e.basis.source) : e.source);
  const known = (e) => (data.sources[sourceOf(e)] ? sourceOf(e) : '');
  const about = (source) => `${esc(zh ? source.note_zh : source.note_en)} ${source.read_on ? esc(tf('wl_read_on', { d: source.read_on })) : ''}${wlSourceLinkHtml(source)}`;
  data.entries.forEach((e) => {
    const hint = e.group === 'ai_tells';
    const key = (hint ? 'tell-' : 'rule-') + known(e);
    if (!found[key]) {
      const source = data.sources[known(e)];
      let title;
      let aboutHtml;
      if (source && !hint) { title = zh ? source.title_zh : source.title_en; aboutHtml = about(source); }
      else if (source) {
        title = t('wl_group_tells');
        aboutHtml = `${esc(t('wl_tells_d'))} ${esc(tf('wl_tells_credit', { title: zh ? source.title_zh : source.title_en }))} ${about(source)}`;
      } else if (hint) { title = t('wl_group_tells_own'); aboutHtml = esc(t('wl_group_tells_own_d')); }
      else { title = t('wl_group_none'); aboutHtml = esc(t('wl_group_none_d')); }
      found[key] = { key, title, aboutHtml, entries: [], order: (hint ? 2 : 0) + (source ? 0 : 1) };
      groups.push(found[key]);
    }
    found[key].entries.push(e);
  });
  return groups.sort((a, b) => a.order - b.order);      // a stable sort: groups of one kind stay in the order of the list
}

function wlEntryHtml(e) {
  const zh = lang === 'zh';
  const rule = e.group === 'compliance';
  const sev = !rule ? ['', 'wl_sev_hint'] : (e.severity === 'error' ? ['no', 'wl_sev_error'] : ['warn', 'wl_sev_review']);
  const why = rule ? (zh ? e.why_zh : e.why_en) : '';
  const note = rule ? (zh ? e.note_zh : e.note_en) : '';
  let basis;
  if (rule && e.basis) {
    basis = tf('wl_basis', { where: zh ? e.basis.where_zh : e.basis.where_en, says: zh ? e.basis.says_zh : e.basis.says_en })
      + (e.basis.by_analogy ? (zh ? '' : ' ') + t('wl_by_analogy') : '');
  } else if (!rule && e.source && e.pattern_no !== null && e.pattern_no !== undefined) {
    basis = tf('wl_pattern_no', { n: e.pattern_no });
  } else {
    basis = t('wl_no_basis');
  }
  return `<div class="wl-entry ${e.on ? '' : 'off'}" data-wl-entry="${esc(e.id)}">
      <div class="wl-text">
        <div class="wl-shows">${esc(e.shows)} <span class="tag ${sev[0]}">${esc(t(sev[1]))}</span>${
    e.on ? '' : ` <span class="tag warn" data-wl-off-tag>${esc(t('wl_is_off'))}</span>`}</div>
        ${why ? `<div class="meta">${esc(why)}${note ? esc(zh ? `（${note}）` : ` (${note})`) : ''}</div>` : ''}
        <div class="meta" data-wl-entry-basis>${esc(basis)}</div>
      </div>
      <button type="button" class="btn" data-wl-toggle="${esc(e.id)}">${esc(t(e.on ? 'wl_turn_off' : 'wl_turn_on'))}</button>
    </div>`;
}

function wlOwnHtml(data) {
  const full = data.phrases.length >= data.limits.phrases;
  const chips = data.phrases.map((p, i) => `<span class="kw" data-wl-phrase>${esc(p)}<button type="button" data-wl-del="${i}" aria-label="${esc(t('wl_own_remove'))}">✕</button></span>`).join('')
    || `<span class="hint" id="wl-own-none">${esc(t('wl_own_none'))}</span>`;
  return `<h3>${esc(t('wl_own_title'))}</h3>
    <p class="hint">${esc(t('wl_own_hint'))}</p>
    <div class="chips" id="wl-phrases" style="margin-top:10px">${chips}</div>
    <div class="row kw-row" style="margin-top:8px">
      <input type="text" id="wl-input" maxlength="${Number(data.limits.phrase_chars)}" placeholder="${esc(t('wl_own_ph'))}" value="${esc(wlUi.typed)}" ${full ? 'disabled' : ''}>
      <button type="button" class="btn" id="wl-add" ${full ? 'disabled' : ''}>${esc(t('wl_own_add'))}</button>
      <span class="hint">${esc(tf('wl_own_limits', { n: data.limits.phrases, c: data.limits.phrase_chars }))}</span></div>
    <p class="hint" id="wl-differs">${esc(t('wl_own_differs'))} <a href="#/start" id="wl-to-never">${esc(tf('wl_never_link', { step: t('st_step_creator') }))}</a></p>`;
}

function wlDraw(el) {
  const data = wlUi.data;
  const groups = wlGroups(data).map((g) => {
    const off = g.entries.filter((e) => !e.on).length;
    const open = wlUi.open[g.key] !== undefined ? wlUi.open[g.key] : off > 0;
    const count = off ? tf('wl_count_off', { n: g.entries.length, off }) : tf(countKey('wl_count', g.entries.length), { n: g.entries.length });
    return `<details class="card wl-group" data-wl-group="${esc(g.key)}" ${open ? 'open' : ''}>
      <summary><b>${esc(g.title)}</b> <span class="meta">· ${esc(count)}</span></summary>
      <p class="hint">${g.aboutHtml}</p>
      ${g.entries.map(wlEntryHtml).join('')}</details>`;
  }).join('');
  el.innerHTML = `<h2>${esc(t('wl_title'))}</h2>
    <p class="hint">${esc(t('wl_intro'))}</p>
    <p class="hint" id="wl-disclaimer">${esc(lang === 'zh' ? data.disclaimer_zh : data.disclaimer_en)} ${esc(t('wl_no_finding'))} ${esc(tf('wl_reviewed', { d: data.reviewed_on }))}</p>
    ${data.unreadable ? `<div class="banner" id="wl-unreadable" role="alert">${esc(t('wl_unreadable'))}</div>` : ''}
    ${data.switched_off ? `<div class="banner" id="wl-off-count">${esc(tf(countKey('wl_off_n', data.switched_off), { n: data.switched_off }))}</div>` : ''}
    ${groups}
    <div class="card wl-own" id="wl-own">${wlOwnHtml(data)}</div>`;

  el.querySelectorAll('details[data-wl-group]').forEach((d) => { d.ontoggle = () => { wlUi.open[d.dataset.wlGroup] = d.open; }; });
  const $input = el.querySelector('#wl-input');
  $input.oninput = () => { wlUi.typed = $input.value; };
  $input.onkeydown = (e) => {
    if (e.key !== 'Enter' || e.isComposing || e.keyCode === 229) return;     // Enter also confirms Chinese input
    e.preventDefault(); wlAddTyped(el);
  };
  el.onclick = (e) => {
    const toggle = e.target.closest('[data-wl-toggle]');
    const del = e.target.closest('[data-wl-del]');
    if (toggle) {
      /* Every entry that is to be off after this click: the others as they are, this one the other way round. */
      const id = toggle.dataset.wlToggle;
      wlSave(el, { off: data.entries.filter((x) => (x.id === id ? x.on : !x.on)).map((x) => x.id) });
    } else if (del) {
      wlSave(el, { phrases: data.phrases.filter((_, i) => i !== Number(del.dataset.wlDel)) });
    } else if (e.target.closest('#wl-add')) {
      wlAddTyped(el);
    } else if (e.target.closest('#wl-to-never')) {
      setupUi.open = 'creator';       // the phrases never to be used are in the first step of the guided setup
    }
  };
}

/* Store a change and draw the list as the server then has it. A refusal comes with a sentence in both languages. */
async function wlSave(el, body) {
  wlUi.seq += 1;                      // an answer still on its way to an earlier request is out of date now
  try {
    wlUi.data = await api('PUT', '/api/wordlist', body);
  } catch (e) {
    toast((lang === 'zh' ? e.message_zh : e.detail) || t('err_default'));
    return false;
  }
  toast(t('saved'));
  if (el.isConnected) wlDraw(el);
  return true;
}

async function wlAddTyped(el) {
  const typed = el.querySelector('#wl-input').value.trim();
  if (!typed) return;
  wlUi.typed = '';                    // the box is drawn empty once the phrase is stored
  if (!(await wlSave(el, { phrases: wlUi.data.phrases.concat([typed]) }))) { wlUi.typed = typed; return; }
  const $input = el.querySelector('#wl-input');
  if ($input && !$input.disabled) $input.focus();
}
