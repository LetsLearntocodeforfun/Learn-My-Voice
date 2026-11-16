'use strict';
/* Chinese-first single-page UI. No build step, no dependencies. */

const I18N = {
  zh: {
    brand: '脚本工作台', nav_scripts: '我的脚本', nav_voice: '我的声音', nav_settings: '设置', lang_btn: 'English',
    home_title: '我的脚本', home_sub: '写、改、念，都在这里。', new_script: '＋ 写新脚本',
    empty: '还没有脚本，点上面的按钮开始第一篇吧。', lines_n: '句', sec: '秒',
    no_ai: '还没连接 AI。可以先自己写，或者去「设置」连接。', go_settings: '去设置',
    new_title: '写新脚本', new_sub: '把想法随手写下来，中文英文都可以。',
    notes: '想拍什么？', notes_ph: '例如：周日去 Trader Joe\'s 补货，又忍不住拿了四盒小笼包，外面风特别大……',
    duration: '视频时长', format: '视频形式',
    ai_write: '✨ AI 帮我写初稿', self_write: '我自己写', writing: '正在写，大约半分钟…', back: '← 返回',
    example_voice: '现在用的是示例声音档案，不是你本人的说话习惯，AI 初稿会不像你。',
    fmt_VLOG_VOICEOVER: '日常 Vlog 旁白', fmt_COUPLE_VLOG: '双人日常', fmt_COUPLE_VLOG_partner: '和{@partner}的日常',
    fmt_FAVORITES_ROUNDUP: '好物合集', fmt_SINGLE_PRODUCT_SHARE: '单品分享',
    untitled: '未命名脚本', spoken_ph: '这一句要说的话，用 / 分开每一口气', shot_ph: '画面：拍什么', flower_ph: '花字（可不填）',
    english_ph: 'English meaning', add_line: '＋ 加一句', chars: '字', about: '约',
    written: '已写', budget: '建议', est: '预计念', saved: '已保存', saving: '保存中…', save_failed: '保存失败',
    prompter: '提词器', export: '导出', delete: '删除', confirm_delete: '确定删除这篇脚本吗？删除后无法恢复。',
    exp_voice: '口播文稿', exp_voice_d: '一口气一行，可用于提词或字幕文稿',
    exp_shots: '拍摄清单', exp_shots_d: '每一句对应的画面', exp_flower: '花字清单', exp_flower_d: '剪辑时要加的花字',
    all_good: '没有发现问题 👍', issues_n: '处需要看一下',
    start: '开始', pause: '暂停', speed: '速度', size: '字号', close: '关闭', prompter_empty: '脚本还是空的。',
    voice_title: '我的声音', voice_sub: 'AI 写稿和检查时参照的说话习惯。',
    v_essence: '整体感觉', v_rate: '语速', v_rate_u: '字/分钟', v_clause: '每口气字数', v_particles: '常用语气词',
    v_address: '怎么称呼大家', v_partner: '怎么称呼{@partner}', v_partner_any: '怎么称呼一起出镜的人',
    v_openers: '常用开头', v_connectors: '常用连接词',
    v_praise: '夸东西的说法', v_never: '从来不说',
    settings_title: '设置', settings_sub: '连接一次就好。密钥只保存在这台电脑上。',
    provider: 'AI 服务', api_key: '密钥（API Key）', key_saved_ph: '已保存，留空则继续使用', key_ph: '粘贴密钥',
    connect: '连接并读取模型', model: '模型', pick_model: '请选择模型', save: '保存', connected: '已连接',
    not_connected: '未连接', settings_saved: '已保存', current: '当前使用',
    err_ai_not_configured: '还没连接 AI，请先到「设置」。', err_notes_required: '先写一点想法吧。',
    err_key_required: '请先填写密钥。', err_default: '出错了，请再试一次。',
  },
  en: {
    brand: 'Learn My Voice', nav_scripts: 'Scripts', nav_voice: 'Voice', nav_settings: 'Settings', lang_btn: '中文',
    home_title: 'Scripts', home_sub: 'Write, edit and read from one place.', new_script: '+ New script',
    empty: 'No scripts yet. Use the button above to start one.', lines_n: 'lines', sec: 's',
    no_ai: 'AI is not connected. You can write by hand, or connect it in Settings.', go_settings: 'Open settings',
    new_title: 'New script', new_sub: 'Jot down the idea in Chinese or English.',
    notes: 'What is the video about?', notes_ph: 'e.g. Sunday Trader Joe\'s run, came home with four boxes of soup dumplings again, freezing wind outside…',
    duration: 'Length', format: 'Format',
    ai_write: '✨ Draft with AI', self_write: 'Write it myself', writing: 'Writing, about half a minute…', back: '← Back',
    example_voice: 'This is the bundled example voice profile, not {@names} real speech habits, so AI drafts will not sound like {@them}.',
    fmt_VLOG_VOICEOVER: 'Daily vlog voiceover', fmt_COUPLE_VLOG: 'Two-person vlog', fmt_COUPLE_VLOG_partner: 'Vlog with {@partner}',
    fmt_FAVORITES_ROUNDUP: 'Favorites roundup', fmt_SINGLE_PRODUCT_SHARE: 'Single product',
    untitled: 'Untitled', spoken_ph: 'Spoken line; separate breaths with /', shot_ph: 'Shot: what to film', flower_ph: 'On-screen text (optional)',
    english_ph: 'English meaning', add_line: '+ Add line', chars: 'units', about: '~',
    written: 'Written', budget: 'target', est: 'reads in', saved: 'Saved', saving: 'Saving…', save_failed: 'Save failed',
    prompter: 'Teleprompter', export: 'Export', delete: 'Delete', confirm_delete: 'Delete this script? This cannot be undone.',
    exp_voice: 'Voiceover text', exp_voice_d: 'One breath per line, for prompting or subtitle text',
    exp_shots: 'Shot list', exp_shots_d: 'What to film for each line', exp_flower: 'On-screen text list', exp_flower_d: 'Text overlays to add when editing',
    all_good: 'No issues found 👍', issues_n: 'things to check',
    start: 'Start', pause: 'Pause', speed: 'Speed', size: 'Size', close: 'Close', prompter_empty: 'The script is empty.',
    voice_title: '{@Names} voice', voice_sub: 'The speech habits used for drafting and checking.',
    v_essence: 'Overall feel', v_rate: 'Speaking rate', v_rate_u: 'units/min', v_clause: 'Units per breath', v_particles: 'Particles',
    v_address: 'Addresses viewers as', v_partner: 'Refers to {@partner} as', v_partner_any: 'Refers to an on-camera partner as',
    v_openers: 'Typical openers', v_connectors: 'Connectors',
    v_praise: 'How {@name} praises things', v_never: 'Never says',
    settings_title: 'Settings', settings_sub: 'Connect once. The key stays on this computer.',
    provider: 'AI service', api_key: 'API key', key_saved_ph: 'Saved; leave blank to keep it', key_ph: 'Paste key',
    connect: 'Connect and list models', model: 'Model', pick_model: 'Choose a model', save: 'Save', connected: 'connected',
    not_connected: 'not connected', settings_saved: 'Saved', current: 'In use',
    err_ai_not_configured: 'AI is not connected. Open Settings first.', err_notes_required: 'Write a few notes first.',
    err_key_required: 'Enter a key first.', err_default: 'Something went wrong. Please try again.',
  },
};

let lang = localStorage.getItem('lang') === 'en' ? 'en' : 'zh';
let appState = { settings: { configured: false, keys: {} }, dna_is_example: true, creator: null, video_formats: [], sample_count: 0, trend_count: 0, lookup_available: false };
const $view = document.getElementById('view');
const $nav = document.getElementById('nav');
const $toast = document.getElementById('toast');
const $prompter = document.getElementById('prompter');

/* The creator profile from setup arrives with /api/state as appState.creator; is_set is false until setup is done.
   English text names the creator and uses their pronoun; before setup it says "the creator" and "they".
   Chinese text speaks to the creator as 你, so it only ever needs {@partner}.
   Tokens in text: {@name} {@Name} {@names} {@Names} (possessive) {@them} {@their} {@partner}. */
const PRONOUN_FORMS = { she: ['her', 'her'], he: ['him', 'his'], they: ['them', 'their'] };   // [object, possessive]
function creatorProfile() { return appState.creator && appState.creator.is_set ? appState.creator : null; }
/* How the creator refers on camera to the person who appears with them; '' when nobody does or nothing is set. */
function partnerName() {
  const c = creatorProfile();
  return c && c.partner && typeof c.partner.called === 'string' ? c.partner.called.trim() : '';
}
function creatorWords() {
  const c = creatorProfile();
  const name = c ? String(c.name).trim() : '';
  const forms = PRONOUN_FORMS[c && Object.keys(PRONOUN_FORMS).includes(c.pronoun) ? c.pronoun : 'they'];
  return {
    name: name || 'the creator', Name: name || 'The creator',
    names: (name || 'the creator') + "'s", Names: (name || 'The creator') + "'s",
    them: forms[0], their: forms[1], partner: partnerName(),
  };
}

const t = (k) => {
  const s = I18N[lang][k] !== undefined ? I18N[lang][k] : (I18N.zh[k] !== undefined ? I18N.zh[k] : k);
  if (s.indexOf('{@') === -1) return s;
  const words = creatorWords();
  return s.replace(/\{@(\w+)\}/g, (token, word) => (words[word] !== undefined ? words[word] : token));
};
/* COUPLE_VLOG means a video with the on-camera partner from the creator profile, whoever that is. Its label names
   that person, and it stops being offered once the profile says nobody appears with the creator. */
const formatLabel = (v) => t(v === 'COUPLE_VLOG' && partnerName() ? 'fmt_COUPLE_VLOG_partner' : 'fmt_' + v);
const formatChoices = (current) => appState.video_formats.filter(
  (v) => v !== 'COUPLE_VLOG' || v === current || !creatorProfile() || partnerName());

const esc = (s) => String(s === null || s === undefined ? '' : s)
  .replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

function toast(msg) {
  $toast.textContent = msg;
  $toast.classList.add('show');
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => $toast.classList.remove('show'), 3200);
}

async function api(method, path, body) {
  const opts = { method, headers: {} };
  if (body !== undefined) { opts.headers['Content-Type'] = 'application/json'; opts.body = JSON.stringify(body); }
  const res = await fetch(path, opts);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) { const e = new Error(data.message || data.error || 'error'); e.code = data.error; e.reason = data.reason; e.detail = data.message; e.message_zh = data.message_zh; throw e; }
  return data;
}

function showError(e) {
  if (e.code === 'ai_failed') {
    const key = 'ai_' + e.reason;
    const known = I18N[lang][key] !== undefined;
    const detail = (!known || e.reason === 'http' || e.reason === 'other') ? (e.detail || '') : '';
    return toast(t(known ? key : 'ai_other') + detail);
  }
  const key = 'err_' + e.code;
  toast(I18N[lang][key] ? t(key) : t('err_default'));
}

/* Must match studio/textstats.py: 1 CJK char, 1 English word or 1 digit = 1 unit; [cues] are not spoken. */
function spokenUnits(text) {
  const s = String(text || '').replace(/\[[^\]]*\]|［[^］]*］|【[^】]*】/g, '');
  const count = (re) => (s.match(re) || []).length;
  return count(/[\u3400-\u4dbf\u4e00-\u9fff]/g) + count(/[A-Za-z][A-Za-z'\u2019-]*/g) + count(/\d/g);
}

function autoGrow(el) { el.style.height = 'auto'; el.style.height = el.scrollHeight + 'px'; }

function fmtDate(ts) {
  const d = new Date(ts * 1000);
  return d.toLocaleDateString(lang === 'zh' ? 'zh-CN' : 'en-US', { month: 'short', day: 'numeric' }) +
    ' ' + d.toLocaleTimeString(lang === 'zh' ? 'zh-CN' : 'en-US', { hour: '2-digit', minute: '2-digit' });
}

/* ---------- navigation ---------- */
function renderNav(route) {
  const item = (hash, key, on, mark) => `<a href="${hash}" class="${on ? 'on' : ''}">${esc(t(key))}${mark ? '<i class="dot"></i>' : ''}</a>`;
  $nav.innerHTML = `<div class="brand">${esc(t('brand'))}</div>` +
    item('#/start', 'nav_start', route === 'start', appState.setup_next && appState.setup_next !== 'done') +
    item('#/', 'nav_scripts', route === 'home' || route === 'new' || route === 'script') +
    item('#/ideas', 'nav_ideas', route === 'ideas') +
    item('#/trends', 'nav_trends', route === 'trends') +
    item('#/library', 'nav_library', route === 'library') +
    item('#/voice', 'nav_voice', route === 'voice') +
    item('#/settings', 'nav_settings', route === 'settings') +
    `<div class="grow"></div><button class="lang" id="lang-btn">${esc(t('lang_btn'))}</button>`;
  document.getElementById('lang-btn').onclick = () => {
    lang = lang === 'zh' ? 'en' : 'zh';
    localStorage.setItem('lang', lang);
    router();
  };
  document.documentElement.lang = lang === 'zh' ? 'zh-CN' : 'en';
  document.title = t('brand');
}

async function router() {
  await flushSave();
  const hash = location.hash || '#/';
  const m = hash.match(/^#\/s\/([0-9a-f]{12})$/);
  try {
    appState = await api('GET', '/api/state');
    /* Nothing set up yet: open the guided setup once per session. After that the home page only points to it. */
    if (hash === '#/' && appState.setup_next === 'creator' && !sessionStorage.getItem('start-shown')) {
      sessionStorage.setItem('start-shown', '1');
      location.hash = '#/start';
      return;
    }
    if (m) { renderNav('script'); await loadTrendSuggestions(); await renderScript(m[1]); }
    else if (hash === '#/start') { renderNav('start'); await renderSetup(); }
    else if (hash === '#/new') { renderNav('new'); await loadTrendSuggestions(); renderNew(); }
    else if (hash === '#/ideas') { renderNav('ideas'); await renderIdeas(); }
    else if (hash === '#/trends') { renderNav('trends'); await renderTrends(); }
    else if (hash === '#/library') { renderNav('library'); await renderLibrary(); }
    else if (hash === '#/voice') { renderNav('voice'); await renderVoice(); }
    else if (hash === '#/settings') { renderNav('settings'); renderSettings(); }
    else { renderNav('home'); await renderHome(); }
  } catch (e) {
    if (e.code === 'not_found') { location.hash = '#/'; return; }
    showError(e);
  }
}

/* ---------- home ---------- */
async function renderHome() {
  const { scripts } = await api('GET', '/api/scripts');
  const cards = scripts.map((s) => `
    <a class="card" href="#/s/${esc(s.id)}">
      <h3>${esc(s.title)}</h3>
      <div>${esc(s.preview)}</div>
      <div class="meta">${esc(fmtDate(s.updated_at))} · ${s.line_count} ${esc(t('lines_n'))} · ${s.target_sec}${esc(t('sec'))}</div>
    </a>`).join('');
  const next = appState.setup_next;
  const setupBanner = next && next !== 'done'
    ? `<div class="banner">${esc(t('st_home_next'))}${esc(t('st_step_' + next))} <a href="#/start">${esc(t('st_home_go'))}</a></div>` : '';
  const banner = setupBanner || (appState.settings.configured ? '' :
    `<div class="banner">${esc(t('no_ai'))} <a href="#/settings">${esc(t('go_settings'))}</a></div>`);
  $view.innerHTML = `<div class="wrap">
    <div class="row"><div><h1>${esc(t('home_title'))}</h1><p class="sub">${esc(t('home_sub'))}</p></div>
      <div class="spacer"></div><a class="btn primary big" href="#/new">${esc(t('new_script'))}</a></div>
    ${banner}<div id="dv-box"></div>${cards || `<div class="empty">${esc(t('empty'))}</div>`}</div>`;
  /* One line, typed or spoken, to topic ideas (discover.js). Offered here once an AI is connected. */
  if (appState.settings.configured) dvPaintBox(document.getElementById('dv-box'), true);
}

/* ---------- new script ---------- */
const draft = { notes: '', target_sec: 60, video_format: 'VLOG_VOICEOVER', keywords: [] };

function renderNew() {
  const chips = (values, current, attr, label) => values.map((v) =>
    `<button type="button" class="chip ${v === current ? 'on' : ''}" data-${attr}="${esc(v)}">${esc(label(v))}</button>`).join('');
  const configured = appState.settings.configured;
  $view.innerHTML = `<div class="wrap">
    <a href="#/" class="meta">${esc(t('back'))}</a>
    <h1>${esc(t('new_title'))}</h1><p class="sub">${esc(t('new_sub'))}</p>
    ${configured && appState.dna_is_example ? `<div class="banner">${esc(t('example_voice'))}</div>` : ''}
    ${configured ? '' : `<div class="banner">${esc(t('no_ai'))} <a href="#/settings">${esc(t('go_settings'))}</a></div>`}
    <label class="field" for="notes">${esc(t('notes'))}</label>
    <textarea id="notes" rows="5" placeholder="${esc(t('notes_ph'))}">${esc(draft.notes)}</textarea>
    <label class="field">${esc(t('duration'))}</label>
    <div class="chips">${chips([30, 60, 90], draft.target_sec, 'sec', (v) => v + t('sec'))}</div>
    <label class="field">${esc(t('format'))}</label>
    <div class="chips">${chips(formatChoices(draft.video_format), draft.video_format, 'fmt', formatLabel)}</div>
    <div id="kw-box"></div>
    <div class="row" style="margin-top:28px">
      <button class="btn primary big" id="ai-btn" ${configured ? '' : 'disabled'}>${esc(t(appState.settings.manual ? 'ai_write_manual' : 'ai_write'))}</button>
      <button class="btn big" id="self-btn">${esc(t('self_write'))}</button>
    </div></div>`;
  const $notes = document.getElementById('notes');
  $notes.oninput = () => { draft.notes = $notes.value; autoGrow($notes); };
  keywordPicker(document.getElementById('kw-box'), draft.keywords, (list) => { draft.keywords = list; });
  $view.querySelectorAll('[data-sec]').forEach((b) => { b.onclick = () => { draft.target_sec = Number(b.dataset.sec); renderNew(); }; });
  $view.querySelectorAll('[data-fmt]').forEach((b) => { b.onclick = () => { draft.video_format = b.dataset.fmt; renderNew(); }; });
  const create = async (useAi, btn) => {
    const label = btn.textContent;
    btn.disabled = true;
    if (useAi && !appState.settings.manual) btn.textContent = t('writing');
    try {
      const body = Object.assign({ lang }, draft);
      const script = useAi ? await aiRun('draft', body) : await api('POST', '/api/scripts', body);
      if (!script) { btn.disabled = false; btn.textContent = label; return; }  // copy-and-paste was cancelled
      draft.notes = ''; draft.keywords = [];
      location.hash = '#/s/' + script.id;
    } catch (e) { showError(e); btn.disabled = false; btn.textContent = label; }
  };
  document.getElementById('ai-btn').onclick = (e) => create(true, e.currentTarget);
  document.getElementById('self-btn').onclick = (e) => create(false, e.currentTarget);
}

/* ---------- script workspace ---------- */
let cur = null;          // { script, findings, stats }
let saveTimer = null;
let dirty = false;
let lintSeq = 0;

function scheduleSave() {
  dirty = true;
  setSaved('saving');
  clearTimeout(saveTimer);
  saveTimer = setTimeout(saveNow, 500);
}

async function flushSave() {
  clearTimeout(saveTimer);
  if (dirty) await saveNow();
}

const lintBody = (s) => ({ lines: s.lines, target_sec: s.target_sec, keywords: s.keywords || [], post: s.post });

async function saveNow() {
  if (!cur) return;
  dirty = false;
  const s = cur.script;
  const seq = ++lintSeq;
  try {
    await api('PUT', '/api/scripts/' + s.id, { title: s.title, target_sec: s.target_sec, lines: s.lines, keywords: s.keywords || [] });
    const lint = await api('POST', '/api/lint', lintBody(s));
    if (seq !== lintSeq || !cur || cur.script.id !== s.id) return;
    cur.findings = lint.findings;
    cur.stats = lint.stats;
    cur.coverage = lint.keyword_coverage;
    paintChecks();
    if (!dirty) setSaved('saved');
  } catch (e) { dirty = true; setSaved('save_failed'); }
}

function setSaved(key) { const el = document.getElementById('saved'); if (el) el.textContent = t(key); }

function lineHtml(line, i) {
  const en = lang === 'en' ? `<div class="en"><input type="text" data-f="english_partner_translation"
      placeholder="${esc(t('english_ph'))}" value="${esc(line.english_partner_translation)}"></div>` : '';
  return `<div class="card line" data-i="${i}">
    <div class="line-head"><span class="no">${i + 1}</span><span class="meta" data-count></span><span class="spacer"></span>
      <button class="btn ghost" data-act="up" aria-label="up">↑</button>
      <button class="btn ghost" data-act="down" aria-label="down">↓</button>
      <button class="btn ghost" data-act="del" aria-label="${esc(t('delete'))}">✕</button></div>
    <textarea class="spoken" rows="1" data-f="spoken_script_zh" placeholder="${esc(t('spoken_ph'))}">${esc(line.spoken_script_zh)}</textarea>
    <div class="issues" data-issues></div>
    <div class="extras">
      <input type="text" data-f="visual_broll_en_zh" placeholder="${esc(t('shot_ph'))}" value="${esc(line.visual_broll_en_zh)}">
      <input type="text" data-f="onscreen_flower_text_zh" placeholder="${esc(t('flower_ph'))}" value="${esc(line.onscreen_flower_text_zh)}">
    </div>${en}</div>`;
}

function paintCounts() {
  const cpm = cur.stats && cur.stats.speaking_rate_cpm;
  $view.querySelectorAll('.line').forEach((el) => {
    const units = spokenUnits(cur.script.lines[Number(el.dataset.i)].spoken_script_zh);
    const secs = cpm ? ` · ${t('about')}${Math.max(1, Math.round(units / cpm * 60))}${t('sec')}` : '';
    el.querySelector('[data-count]').textContent = units ? `${units}${lang === 'zh' ? '' : ' '}${t('chars')}${secs}` : '';
  });
}

function paintChecks() {
  const msg = (f) => (lang === 'zh' ? f.message_zh : f.message_en);
  const chip = (f) => `<div class="issue ${f.severity === 'error' ? 'error' : ''}">${esc(msg(f))}</div>`;
  $view.querySelectorAll('.line').forEach((el) => {
    const no = Number(el.dataset.i) + 1;
    el.querySelector('[data-issues]').innerHTML = cur.findings.filter((f) => f.line_no === no).map(chip).join('');
  });
  const kwBox = document.getElementById('kw-box');
  if (kwBox && kwBox.kw) kwBox.kw.setCoverage(cur.coverage);
  const st = cur.stats || {};
  const whole = cur.findings.filter((f) => f.line_no === null);
  const lineIssues = cur.findings.length - whole.length;
  const ratio = st.budget ? st.units / st.budget : 0;
  const cls = ratio > 1.1 ? 'over' : (ratio < 0.9 ? 'under' : '');
  const el = document.getElementById('summary');
  if (!el) return;
  el.innerHTML = `<div class="row meta">
      <span>${esc(t('written'))} <b>${st.units || 0}</b> ${esc(t('chars'))}${st.budget ? ` / ${esc(t('budget'))} ${st.budget}` : ''}</span>
      ${st.estimated_sec ? `<span>${esc(t('est'))} ${st.estimated_sec}${esc(t('sec'))}</span>` : ''}
      <span class="spacer"></span>
      <span>${lineIssues ? `${lineIssues} ${esc(t('issues_n'))}` : esc(t('all_good'))}</span></div>
    <div class="meter"><i class="${cls}" style="width:${Math.min(100, Math.round(ratio * 100))}%"></i></div>
    <div class="issues" style="margin-top:8px">${whole.map(chip).join('')}</div>`;
  paintCounts();
}

function paintScript() {
  const s = cur.script;
  const secs = [30, 60, 90].includes(s.target_sec) ? [30, 60, 90] : [30, 60, 90, s.target_sec];
  $view.innerHTML = `<div class="wrap">
    <div class="row"><a href="#/" class="meta">${esc(t('back'))}</a><span class="spacer"></span>
      <span class="saved" id="saved">${esc(t('saved'))}</span>
      <button class="btn" id="prompter-btn">${esc(t('prompter'))}</button>
      <div class="menu"><button class="btn" id="export-btn">${esc(t('export'))} ▾</button>
        <div class="pop" id="export-pop" hidden>
          <a href="#" data-export="jianying">${esc(t('exp_kit'))}<small>${esc(t('exp_kit_d'))}</small></a>
          <a href="#" data-export="srt">${esc(t('exp_srt'))}<small>${esc(t('exp_srt_d'))}</small></a>
          <a href="#" data-export="voiceover">${esc(t('exp_voice'))}<small>${esc(t('exp_voice_d'))}</small></a>
          <a href="#" data-export="shots">${esc(t('exp_shots'))}<small>${esc(t('exp_shots_d'))}</small></a>
          <a href="#" data-export="flower">${esc(t('exp_flower'))}<small>${esc(t('exp_flower_d'))}</small></a>
        </div></div>
      <button class="btn ghost" id="delete-btn">${esc(t('delete'))}</button></div>
    <input type="text" class="title-input" id="title" value="${esc(s.title)}" placeholder="${esc(t('untitled'))}">
    <div class="chips" style="margin:10px 0 4px">${secs.map((v) =>
      `<button type="button" class="chip ${v === s.target_sec ? 'on' : ''}" data-sec="${v}">${v}${esc(t('sec'))}</button>`).join('')}</div>
    <div id="kw-box"></div>
    <div class="summary" id="summary"></div>
    <div id="lines">${s.lines.map(lineHtml).join('')}</div>
    <button class="btn" id="add-line">${esc(t('add_line'))}</button>
    <div id="post-box"></div><div id="basis-box"></div><div id="edits-box"></div></div>`;
  $view.querySelectorAll('textarea').forEach(autoGrow);
  keywordPicker(document.getElementById('kw-box'), s.keywords || [], (list) => { s.keywords = list; scheduleSave(); });
  paintPost(document.getElementById('post-box'), s.post);
  paintBasis(document.getElementById('basis-box'), s);
  paintEdits(document.getElementById('edits-box'), s);
  paintChecks();

  document.getElementById('title').oninput = (e) => { s.title = e.target.value; scheduleSave(); };
  $view.querySelectorAll('[data-sec]').forEach((b) => { b.onclick = () => { s.target_sec = Number(b.dataset.sec); paintScript(); scheduleSave(); }; });
  document.getElementById('add-line').onclick = () => {
    s.lines.push({ audio_mode: 'VOICEOVER', spoken_script_zh: '', visual_broll_en_zh: '', onscreen_flower_text_zh: '', english_partner_translation: '' });
    paintScript(); scheduleSave();
    const all = $view.querySelectorAll('.spoken'); all[all.length - 1].focus();
  };
  const $lines = document.getElementById('lines');
  $lines.oninput = (e) => {
    const card = e.target.closest('.line');
    if (!card || !e.target.dataset.f) return;
    s.lines[Number(card.dataset.i)][e.target.dataset.f] = e.target.value;
    if (e.target.tagName === 'TEXTAREA') autoGrow(e.target);
    paintCounts(); scheduleSave();
  };
  $lines.onclick = (e) => {
    const act = e.target.dataset.act;
    if (!act) return;
    const i = Number(e.target.closest('.line').dataset.i);
    if (act === 'del') { s.lines.splice(i, 1); if (!s.lines.length) s.lines.push({ audio_mode: 'VOICEOVER', spoken_script_zh: '' }); }
    else {
      const j = act === 'up' ? i - 1 : i + 1;
      if (j < 0 || j >= s.lines.length) return;
      const tmp = s.lines[i]; s.lines[i] = s.lines[j]; s.lines[j] = tmp;
    }
    cur.findings = [];  // line numbers changed; fresh results arrive after save
    paintScript(); scheduleSave();
  };
  const $pop = document.getElementById('export-pop');
  document.getElementById('export-btn').onclick = () => { $pop.hidden = !$pop.hidden; };
  $pop.onclick = async (e) => {
    const a = e.target.closest('[data-export]');
    if (!a) return;
    e.preventDefault(); $pop.hidden = true;
    await flushSave();
    location.href = `/api/scripts/${s.id}/export/${a.dataset.export}?lang=${lang}`;
  };
  document.getElementById('delete-btn').onclick = async () => {
    if (!confirm(t('confirm_delete'))) return;
    clearTimeout(saveTimer); dirty = false;
    await api('DELETE', '/api/scripts/' + s.id);
    cur = null; location.hash = '#/';
  };
  document.getElementById('prompter-btn').onclick = openPrompter;
}

async function renderScript(id) {
  const script = await api('GET', '/api/scripts/' + id);
  const lint = await api('POST', '/api/lint', lintBody(script));
  cur = { script, findings: lint.findings, stats: lint.stats, coverage: lint.keyword_coverage };
  paintScript();
}

/* ---------- teleprompter ---------- */
const prompter = { running: false, speed: 40, size: 44, raf: 0, last: 0, pos: 0 };

function openPrompter() {
  const clauses = [];
  cur.script.lines.forEach((l) => {
    if (String(l.audio_mode || '').toUpperCase() === 'LIVE_CLIP') return;
    String(l.spoken_script_zh || '').replace(/\[[^\]]*\]|［[^］]*］|【[^】]*】/g, '').split(/[\/\n]/)
      .map((c) => c.trim()).filter(Boolean).forEach((c) => clauses.push(c));
  });
  $prompter.innerHTML = `<div class="bar">
      <button class="btn" data-p="toggle">${esc(t('start'))}</button>
      <span>${esc(t('speed'))}</span><button class="btn" data-p="slower">−</button><button class="btn" data-p="faster">＋</button>
      <span>${esc(t('size'))}</span><button class="btn" data-p="smaller">−</button><button class="btn" data-p="bigger">＋</button>
      <span class="spacer"></span><button class="btn" data-p="close">${esc(t('close'))}</button></div>
    <div class="scroll" style="font-size:${prompter.size}px">${
      clauses.length ? clauses.map((c) => `<p>${esc(c)}</p>`).join('') : `<p>${esc(t('prompter_empty'))}</p>`}</div>`;
  $prompter.hidden = false;
  const $scroll = $prompter.querySelector('.scroll');
  prompter.pos = 0; $scroll.scrollTop = 0;
  const step = () => {
    const now = performance.now();
    prompter.pos += prompter.speed * (now - prompter.last) / 1000;
    prompter.last = now;
    $scroll.scrollTop = prompter.pos;
  };
  const setRunning = (on) => {
    prompter.running = on;
    $prompter.querySelector('[data-p="toggle"]').textContent = t(on ? 'pause' : 'start');
    clearInterval(prompter.raf);
    if (on) { prompter.pos = $scroll.scrollTop; prompter.last = performance.now(); prompter.raf = setInterval(step, 16); }
  };
  const close = () => { setRunning(false); $prompter.hidden = true; document.removeEventListener('keydown', onKey); };
  const onKey = (e) => {
    if (e.key === 'Escape') close();
    if (e.key === ' ') { e.preventDefault(); setRunning(!prompter.running); }
  };
  document.addEventListener('keydown', onKey);
  $prompter.onclick = (e) => {
    const p = e.target.dataset.p;
    if (p === 'toggle') setRunning(!prompter.running);
    else if (p === 'slower') prompter.speed = Math.max(10, prompter.speed - 10);
    else if (p === 'faster') prompter.speed = Math.min(200, prompter.speed + 10);
    else if (p === 'smaller' || p === 'bigger') {
      prompter.size = Math.max(24, Math.min(96, prompter.size + (p === 'bigger' ? 6 : -6)));
      $scroll.style.fontSize = prompter.size + 'px';
    } else if (p === 'close') close();
  };
}

/* ---------- voice ---------- */
async function renderVoice() {
  const { dna, is_example } = await api('GET', '/api/dna');
  const get = (path, d) => path.split('.').reduce((o, k) => (o && o[k] !== undefined ? o[k] : undefined), dna) ?? d;
  const hasCjk = (s) => /[\u4e00-\u9fff]/.test(s);
  const clean = (x) => {
    let s = typeof x === 'string' ? x.trim() : '';
    if (lang !== 'zh') return s;
    if (!hasCjk(s)) return '';                       // English-only note
    const cut = s.replace(/\s*\([^)]*[A-Za-z]{3}[^)]*\)\s*$/, '');  // trailing English parenthetical
    return hasCjk(cut) ? cut : s;
  };
  const tags = (list, cls) => Array.from(new Set((list || []).map(clean).filter(Boolean)))
    .map((s) => `<span class="tag ${cls || ''}">${esc(s)}</span>`).join('');
  const clause = get('cadence_and_syntax.ideal_clause_length_chars', {});
  const particles = (get('lexical_fingerprint.modal_particles.primary', []) || []).map((p) => p.particle);
  const openers = (get('lexical_fingerprint.signature_openers', []) || []).map((o) => o.verbatim_sample || o.pattern_zh);
  const praise = [].concat(...Object.values(get('lexical_fingerprint.genuine_praise_lexicon', {}) || {}).filter(Array.isArray));
  const never = [].concat(get('persona_summary.audience_address_terms.never_uses', []) || [],
    get('anti_voice_blacklist.banned_phrases_zh', []) || []);
  const row = (key, html) => (html ? `<dt>${esc(t(key))}</dt><dd>${html}</dd>` : '');
  /* Named after the partner in the creator profile; gone once the profile says nobody appears; neutral before setup. */
  const partnerKey = creatorProfile() ? (partnerName() ? 'v_partner' : '') : 'v_partner_any';
  $view.innerHTML = `<div class="wrap"><h1>${esc(t('voice_title'))}</h1><p class="sub">${esc(t('voice_sub'))}</p>
    ${is_example ? `<div class="banner">${esc(t('example_voice'))}</div>` : ''}
    <div class="card"><dl class="kv">
      ${row('v_essence', esc(get(lang === 'zh' ? 'persona_summary.one_line_essence_zh' : 'persona_summary.one_line_essence_en', '')))}
      ${row('v_rate', esc(get('cadence_and_syntax.target_speaking_rate_cpm', '')) + ' ' + esc(t('v_rate_u')))}
      ${row('v_clause', clause.min !== undefined ? `${esc(clause.min)}–${esc(clause.hard_max)}` : '')}
      ${row('v_particles', tags(particles))}
      ${row('v_address', tags(get('persona_summary.audience_address_terms.uses', [])))}
      ${partnerKey ? row(partnerKey, tags(get('persona_summary.husband_and_family_references.spoken_nicknames_zh_en', []))) : ''}
      ${row('v_openers', tags(openers))}
      ${row('v_connectors', tags(get('lexical_fingerprint.discourse_connectors.high_frequency', [])))}
      ${row('v_praise', tags(praise))}
      ${row('v_never', tags(never, 'no'))}
    </dl></div><div id="voice-actions"></div></div>`;
  paintVoiceActions(dna, is_example);
}
