'use strict';
/* Chinese-first single-page UI. No build step, no dependencies. */
(function () {
  try {
    const url = new URL(location.href);
    if (url.searchParams.has('token')) {
      url.searchParams.delete('token');
      history.replaceState(null, '', url.pathname + url.search + url.hash);
    }
  } catch (e) { /* leave address unchanged if URL cannot be parsed */ }
})();

/* ---------- what is kept between runs: the language, the look, how the teleprompter was set, whether the references are open ----------
   The page keeps these for itself (localStorage), and the core keeps them as well (POST /api/kept). Inside the
   desktop app the page's address has a different port at every start, and what a page keeps for itself belongs
   to one address: there, the core's copy is the one that lasts. It arrives with the page, on <html data-kept>,
   and is put into the page's own memory here, before anything is drawn. After that, what changes in the page
   is sent to the core (keepWithCore). */
const THEMES = ['auto', 'light', 'dark'];
const ACCENTS = ['blue', 'purple', 'pink', 'orange', 'green', 'graphite'];
/* Each name with the words it may be; null for a whole number (the teleprompter's, which it checks itself).
   Must match PAGE_WORDS and PAGE_NUMBERS in studio/store.py: the core refuses anything else. */
const KEPT = { lang: ['zh', 'en'], theme: THEMES, accent: ACCENTS, bilingual: ['0', '1'], 'rf-open': ['0', '1'], 'tp-pace': null, 'tp-size': null, 'tp-mirror': null };
const KEPT_NAMES = Object.keys(KEPT);
const keepable = (name, value) => (KEPT[name] ? KEPT[name].includes(value) : /^-?[0-9]{1,4}$/.test(value));
const keptByCore = {};          // what the core is known to hold
const pageMemory = {};          // stands in where the browser lets a page keep nothing (private browsing)
function recall(name) {
  try {
    const held = localStorage.getItem(name);
    if (held !== null) return held;
  } catch (e) { /* not readable: the stand-in below */ }
  return pageMemory[name] !== undefined ? pageMemory[name] : null;
}
function remember(name, value) {
  pageMemory[name] = value;
  try { localStorage.setItem(name, value); } catch (e) { /* kept for as long as the page is open */ }
}
(function () {
  let kept = {};
  try { kept = JSON.parse(document.documentElement.getAttribute('data-kept') || '{}') || {}; } catch (e) { kept = {}; }
  KEPT_NAMES.forEach((name) => {
    if (typeof kept[name] !== 'string' || !keepable(name, kept[name])) return;
    keptByCore[name] = kept[name];
    remember(name, kept[name]);
  });
})();
/* Sends the core whatever differs from its copy. Called when something was chosen and whenever a page of the
   app is opened; it asks for nothing when nothing differs. Something this page would not accept itself is not
   sent, and what the core refuses all the same is not offered again. */
function keepWithCore() {
  if (keepWithCore.busy) { keepWithCore.again = true; return; }     // one request at a time; what changed meanwhile goes next
  const changed = {};
  KEPT_NAMES.forEach((name) => {
    const held = recall(name);
    if (held !== null && held !== keptByCore[name] && keepable(name, held)) changed[name] = held;
  });
  if (!Object.keys(changed).length) return;
  keepWithCore.busy = true;
  api('POST', '/api/kept', changed)
    .then((answer) => { Object.assign(keptByCore, changed, answer.kept || {}); })
    .catch((e) => { if (e.code === 'bad_value') Object.assign(keptByCore, changed); })
    .then(() => {
      keepWithCore.busy = false;
      if (keepWithCore.again) { keepWithCore.again = false; keepWithCore(); }
    });
}

/* ---------- the look: light or dark, and the accent colour ----------
   app.css reads both from <html>: data-theme is "light" or "dark", data-accent is absent for blue. "auto" is
   what the system has, and follows the system when that changes while the app is open. */
const systemDark = window.matchMedia ? window.matchMedia('(prefers-color-scheme: dark)') : null;
function lookNow() {
  const theme = recall('theme');
  const accent = recall('accent');
  return { theme: THEMES.includes(theme) ? theme : 'auto', accent: ACCENTS.includes(accent) ? accent : 'blue' };
}
function applyLook() {
  const look = lookNow();
  const dark = look.theme === 'dark' || (look.theme === 'auto' && !!systemDark && systemDark.matches);
  const root = document.documentElement;
  root.setAttribute('data-theme', dark ? 'dark' : 'light');
  if (look.accent === 'blue') root.removeAttribute('data-accent');
  else root.setAttribute('data-accent', look.accent);
}
function setLook(name, value) {
  remember(name, value);
  applyLook();
  keepWithCore();
}
applyLook();
if (systemDark) {
  if (systemDark.addEventListener) systemDark.addEventListener('change', applyLook);
  else if (systemDark.addListener) systemDark.addListener(applyLook);      // web views from before 2020
}

/* Line icons, drawn here so that nothing is loaded: 24 units square, a line of 1.5 (the .ic rule in app.css). */
const ICONS = {
  start: '<path d="M5.5 21V4"/><path d="M5.5 4.5h12l-2.5 4 2.5 4h-12"/>',
  scripts: '<path d="M6.5 3h7.5l4.5 4.5V21h-12z"/><path d="M14 3v4.5h4.5"/><path d="M9.5 12.5h6M9.5 16.5h6"/>',
  ideas: '<path d="M9.5 18.2h5M10.5 21h3"/><path d="M12 3a6 6 0 0 0-3.8 10.6c.8.7 1.3 1.5 1.3 2.4h5c0-.9.5-1.7 1.3-2.4A6 6 0 0 0 12 3z"/>',
  plan: '<rect x="4" y="5.5" width="16" height="14.5" rx="2"/><path d="M8 3.5v4M16 3.5v4M4 10h16M8.5 14h3M8.5 17h7"/>',
  trends: '<path d="M3.5 17l5.5-5.5 3.5 3.5L20 7.5"/><path d="M14.5 7.5H20V13"/>',
  library: '<path d="M4 4.5h16v4H4z"/><path d="M5.5 8.5v11h13v-11"/><path d="M10 12.5h4"/>',
  voice: '<path d="M4 10.5v3M8 7v10M12 4v16M16 7v10M20 10.5v3"/>',
  settings: '<path d="M4 7.5h9M18 7.5h2M4 16.5h2M11 16.5h9"/><circle cx="15.5" cy="7.5" r="2.5"/><circle cx="8.5" cy="16.5" r="2.5"/>',
  globe: '<circle cx="12" cy="12" r="8.5"/><path d="M3.5 12h17M12 3.5c2.7 2.8 2.7 14.2 0 17M12 3.5c-2.7 2.8-2.7 14.2 0 17"/>',
};
const icon = (name) => `<svg class="ic" viewBox="0 0 24 24" width="20" height="20" aria-hidden="true">${ICONS[name] || ''}</svg>`;
/* The app's mark beside its name: a voice, as a wave, in the colours of the app's icon (the .mark rules in app.css). */
const BRAND_MARK = '<svg class="mark" viewBox="0 0 28 28" width="28" height="28" aria-hidden="true"><rect width="28" height="28" rx="7"/>' +
  '<path d="M8 12v4M11 9.5v9M14 7v14M17 9.5v9M20 12v4"/></svg>';

const I18N = {
  zh: {
    brand: '脚本工作台', nav_scripts: '我的脚本', nav_voice: '我的声音', nav_settings: '设置', lang_btn: 'English',
    home_title: '我的脚本', home_sub: '写、改、念，都在这里。', new_script: '＋ 写新脚本',
    empty: '还没有脚本，点上面的按钮开始第一篇吧。', lines_n: '句', sec: '秒',
    no_ai: '还没连接 AI。可以先自己写，或者去「设置」连接。', go_settings: '去设置',
    new_title: '写新脚本', new_sub: '把想法随手写下来，中文英文都可以。',
    notes: '想拍什么？', notes_ph: '例如：周日去 Trader Joe\'s 补货，又忍不住拿了四盒小笼包，外面风特别大……',
    duration: '视频时长', format: '视频形式',
    ai_write: '✨ AI 帮我写初稿', self_write: '我自己写', import_script_json: '导入脚本 JSON', writing: '正在写，大约半分钟…', back: '← 返回',
    example_voice: '现在用的是示例声音档案，不是你本人的说话习惯，AI 初稿会不像你。',
    fmt_VLOG_VOICEOVER: '日常 Vlog 旁白', fmt_COUPLE_VLOG: '双人日常', fmt_COUPLE_VLOG_partner: '和{@partner}的日常',
    fmt_FAVORITES_ROUNDUP: '好物合集', fmt_SINGLE_PRODUCT_SHARE: '单品分享',
    untitled: '未命名脚本', spoken_ph: '这一句要说的话，用 / 分开每一口气', shot_ph: '画面：拍什么', flower_ph: '花字（可不填）',
    english_ph: 'English meaning', add_line: '＋ 加一句', chars: '字', about: '约',
    written: '已写', budget: '建议', est: '预计念', saved: '已保存', saving: '保存中…', save_failed: '保存失败',
    prompter: '提词器', export: '导出', delete: '删除', confirm_delete: '确定删除这篇脚本吗？删除后无法恢复。',
    exp_voice: '口播文稿', exp_voice_d: '一口气一行，可用于提词或字幕文稿',
    exp_shots: '拍摄清单', exp_shots_d: '每一句对应的画面', exp_flower: '花字清单', exp_flower_d: '剪辑时要加的花字',
    all_good: '这些检查没有发现问题', issues_n: '处需要看一下', issues_more: '另有 {n} 处没有列出',
    close: '关闭', ask_ok: '确定', ask_cancel: '取消',
    exp_saved: '已存到「下载」文件夹：{name}', exp_saved_else: '「下载」文件夹写不进去，已存到：{path}', exp_show: '在文件夹中显示',
    link_not_opened: '这个链接没能在浏览器里打开，可以复制后自己打开：', link_copy: '复制链接',
    voice_title: '我的声音', voice_sub: 'AI 写稿和检查时参照的说话习惯。',
    v_essence: '整体感觉', v_rate: '语速', v_rate_u: '字/分钟', v_clause: '每口气字数', v_particles: '常用语气词',
    v_address: '怎么称呼大家', v_partner: '怎么称呼{@partner}', v_partner_any: '怎么称呼一起出镜的人',
    v_openers: '常用开头', v_connectors: '常用连接词',
    v_praise: '夸东西的说法', v_never: '从来不说',
    settings_title: '设置', settings_sub: '连接一次就好。密钥只保存在这台电脑上。',
    look_title: '外观', look_theme: '明暗', look_auto: '跟随系统', look_light: '浅色', look_dark: '深色', look_accent: '强调色',
    accent_blue: '蓝色', accent_purple: '紫色', accent_pink: '粉色', accent_orange: '橙色', accent_green: '绿色', accent_graphite: '石墨色',
    provider: 'AI 服务', api_key: '密钥（API Key）', key_saved_ph: '已保存，留空则继续使用', key_ph: '粘贴密钥',
    connect: '连接并读取模型', model: '模型', pick_model: '请选择模型', save: '保存', connected: '已连接',
    not_connected: '未连接', settings_saved: '已保存', current: '当前使用',
    err_ai_not_configured: '还没连接 AI，请先到「设置」。', err_notes_required: '先写一点想法吧。',
    err_key_required: '请先填写密钥。', err_bad_model: '模型名称格式不正确。',
    err_unauthorized: '工作台已重启，这个页面的访问钥匙已失效。请先复制需要保留的内容，再重新加载或从启动链接重新打开。',
    reload_btn: '重新加载',
    key_unreadable_warn: '这里保存过密钥，但在这台电脑上无法解开（可能是在另一台电脑或账号上保存的，或应用的主密钥文件已变更）。请重新粘贴密钥。',
    key_plaintext_warn: '无法读取或创建主密钥文件（{path}），密钥已按仅本人可读（0600）直接保存在 config.json 中。',
    err_stale_script: '这篇脚本刚刚在别处被改过了，这次没有覆盖保存。请刷新后再改。',
    dna_unreadable_warn: '保存的声音档案文件损坏或无法读取，当前暂时使用示例档案。你可以在下方恢复以前保存的档案，或重新学习。',
    v_history_title: '以前保存的声音档案', v_history_sub: '换新档案时旧档案都会留着，随时可以换回来。',
    v_history_restore: '恢复这份档案', v_history_restored: '已恢复以前的声音档案',
    v_history_confirm: '要用这份以前的档案替换当前正在使用的声音档案吗？（当前档案也会留在这里）',
    err_default: '出错了，请再试一次。',
  },
  en: {
    brand: 'Learn My Voice', nav_scripts: 'Scripts', nav_voice: 'Voice', nav_settings: 'Settings', lang_btn: '中文',
    home_title: 'Scripts', home_sub: 'Write, edit and read from one place.', new_script: '+ New script',
    empty: 'No scripts yet. Use the button above to start one.', lines_n: 'lines', sec: 's',
    no_ai: 'AI is not connected. You can write by hand, or connect it in Settings.', go_settings: 'Open settings',
    new_title: 'New script', new_sub: 'Jot down the idea in Chinese or English.',
    notes: 'What is the video about?', notes_ph: 'e.g. Sunday Trader Joe\'s run, came home with four boxes of soup dumplings again, freezing wind outside…',
    duration: 'Length', format: 'Format',
    ai_write: '✨ Draft with AI', self_write: 'Write it myself', import_script_json: 'Import script JSON', writing: 'Writing, about half a minute…', back: '← Back',
    example_voice: 'This is the bundled example voice profile, not {@names} real speech habits, so AI drafts will not sound like {@them}.',
    fmt_VLOG_VOICEOVER: 'Daily vlog voiceover', fmt_COUPLE_VLOG: 'Two-person vlog', fmt_COUPLE_VLOG_partner: 'Vlog with {@partner}',
    fmt_FAVORITES_ROUNDUP: 'Favorites roundup', fmt_SINGLE_PRODUCT_SHARE: 'Single product',
    untitled: 'Untitled', spoken_ph: 'Spoken line; separate breaths with /', shot_ph: 'Shot: what to film', flower_ph: 'On-screen text (optional)',
    english_ph: 'English meaning', add_line: '+ Add line', chars: 'units', about: '~',
    written: 'Written', budget: 'target', est: 'reads in', saved: 'Saved', saving: 'Saving…', save_failed: 'Save failed',
    prompter: 'Teleprompter', export: 'Export', delete: 'Delete', confirm_delete: 'Delete this script? This cannot be undone.',
    exp_voice: 'Voiceover text', exp_voice_d: 'One breath per line, for prompting or subtitle text',
    exp_shots: 'Shot list', exp_shots_d: 'What to film for each line', exp_flower: 'On-screen text list', exp_flower_d: 'Text overlays to add when editing',
    all_good: 'These checks found nothing', issues_n: 'things to check', issues_more: '{n} more are not listed',
    close: 'Close', ask_ok: 'OK', ask_cancel: 'Cancel',
    exp_saved: 'Saved to the Downloads folder: {name}', exp_saved_else: 'The Downloads folder could not be written to. Saved to: {path}',
    exp_show: 'Show in folder',
    link_not_opened: 'This link could not be opened in the browser. Copy it and open it there: ', link_copy: 'Copy link',
    voice_title: '{@Names} voice', voice_sub: 'The speech habits used for drafting and checking.',
    v_essence: 'Overall feel', v_rate: 'Speaking rate', v_rate_u: 'units/min', v_clause: 'Units per breath', v_particles: 'Particles',
    v_address: 'Addresses viewers as', v_partner: 'Refers to {@partner} as', v_partner_any: 'Refers to an on-camera partner as',
    v_openers: 'Typical openers', v_connectors: 'Connectors',
    v_praise: 'How {@name} praises things', v_never: 'Never says',
    settings_title: 'Settings', settings_sub: 'Connect once. The key stays on this computer.',
    look_title: 'Appearance', look_theme: 'Mode', look_auto: 'Auto', look_light: 'Light', look_dark: 'Dark', look_accent: 'Accent colour',
    accent_blue: 'Blue', accent_purple: 'Purple', accent_pink: 'Pink', accent_orange: 'Orange', accent_green: 'Green', accent_graphite: 'Graphite',
    provider: 'AI service', api_key: 'API key', key_saved_ph: 'Saved; leave blank to keep it', key_ph: 'Paste key',
    connect: 'Connect and list models', model: 'Model', pick_model: 'Choose a model', save: 'Save', connected: 'connected',
    not_connected: 'not connected', settings_saved: 'Saved', current: 'In use',
    err_ai_not_configured: 'AI is not connected. Open Settings first.', err_notes_required: 'Write a few notes first.',
    err_key_required: 'Enter a key first.', err_bad_model: 'Invalid model name.',
    err_unauthorized: 'The studio restarted and this page\'s launch key is no longer valid. Copy any unsaved text, then reload or reopen from the launch link.',
    reload_btn: 'Reload',
    key_unreadable_warn: 'A key for this service is saved, but cannot be opened on this computer (it may have been saved on another computer or account, or the app\'s key file changed). Paste it again to reconnect.',
    key_plaintext_warn: 'The key file could not be made or read ({path}), so the key is stored as owner-only plain text in config.json.',
    err_stale_script: 'This script was changed somewhere else (another tab or an AI app). Reload to see the newer version.',
    dna_unreadable_warn: 'The saved voice profile file could not be read, so the example profile is currently in use. Restore an earlier profile below or learn again.',
    v_history_title: 'Earlier voice profiles', v_history_sub: 'Replaced profiles are kept here and can be restored at any time.',
    v_history_restore: 'Restore', v_history_restored: 'Restored earlier voice profile',
    v_history_confirm: 'Replace the active voice profile with this earlier one? (The current profile will also be kept here.)',
    err_default: 'Something went wrong. Please try again.',
  },
};

let lang = recall('lang') === 'en' ? 'en' : 'zh';
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

/* `action` (optional) is { label, run }: one button beside the message, and the message then stays longer. */
function toast(msg, action) {
  $toast.textContent = msg;
  if (action) {
    const button = document.createElement('button');
    button.type = 'button';
    button.textContent = action.label;
    button.onclick = () => { $toast.classList.remove('show'); action.run(); };
    $toast.appendChild(button);
  }
  $toast.classList.toggle('acts', !!action);
  $toast.classList.add('show');
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => $toast.classList.remove('show'), action ? 9000 : 3200);
}

/* A question with two answers, asked inside the page. Resolves true for yes; false for Cancel, Escape and a click
   beside the box. The browser's own confirm() is not used: inside the desktop app the system's web view may answer
   it with "no" without showing anything, and then nothing could be deleted there. `o.ok` is the label of the yes
   button. `o.danger` says that yes removes something: the focus then starts on Cancel, so Enter alone removes nothing. */
function askConfirm(message, o) {
  const opts = o || {};
  if (askConfirm.close) askConfirm.close(false);            // one question at a time: an earlier one counts as "no"
  return new Promise((resolve) => {
    let $ask = document.getElementById('ask');
    if (!$ask) { $ask = document.createElement('div'); $ask.id = 'ask'; document.body.appendChild($ask); }
    const before = document.activeElement;
    $ask.innerHTML = `<div class="ask-box" role="alertdialog" aria-modal="true" aria-describedby="ask-text">
      <p id="ask-text">${esc(message)}</p>
      <div class="ask-row"><button type="button" class="btn" id="ask-no">${esc(t('ask_cancel'))}</button>
        <button type="button" class="btn primary${opts.danger ? ' danger' : ''}" id="ask-yes">${esc(opts.ok || t('ask_ok'))}</button></div>
    </div>`;
    $ask.hidden = false;
    const $yes = document.getElementById('ask-yes');
    const $no = document.getElementById('ask-no');
    const onKey = (e) => {
      if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); done(false); }
      else if (e.key === 'Tab') { e.preventDefault(); (document.activeElement === $yes ? $no : $yes).focus(); }   // stays in the box
    };
    function done(answer) {
      askConfirm.close = null;
      document.removeEventListener('keydown', onKey, true);
      $ask.hidden = true;
      $ask.innerHTML = '';
      if (before && before.focus && document.contains(before)) before.focus();
      resolve(answer);
    }
    askConfirm.close = done;
    document.addEventListener('keydown', onKey, true);
    $yes.onclick = () => done(true);
    $no.onclick = () => done(false);
    $ask.onclick = (e) => { if (e.target === $ask) done(false); };
    (opts.danger ? $no : $yes).focus();
  });
}

/* ---------- inside the desktop app: files and links go through the core ---------- */
let lastExport = null;      // what the core answered the last time it saved an export itself

/* In a browser an export is a download, and the browser deals with it. Inside the desktop app the core puts the
   file into the Downloads folder itself and the page says where it went: what the system's web view does with a
   download differs from system to system, and a file it can display it may show in place of the app. */
async function exportScript(id, kind) {
  const path = `/api/scripts/${id}/export/${kind}`;
  if (!appState.desktop) { location.href = `${path}?lang=${lang}`; return; }
  try {
    const saved = await api('POST', path + '/save', { lang });
    lastExport = saved;
    toast(saved.in_downloads ? tf('exp_saved', { name: saved.name }) : tf('exp_saved_else', { path: saved.path }),
      { label: t('exp_show'), run: () => api('POST', '/api/outside/show', { path: saved.path }).catch(showError) });
  } catch (e) {
    toast((lang === 'zh' && e.message_zh) || e.detail || t('err_default'));
  }
}

/* Inside the desktop app a link to another site is handed to the core, which opens it in the system's browser.
   Left to the system's web view, such a link asks for a new window, and may open nothing or open inside the app. */
async function openOutside(url) {
  let opened = false;
  try { opened = !!(await api('POST', '/api/outside/open', { url })).opened; } catch (e) { opened = false; }
  if (!opened) {
    toast(t('link_not_opened') + url, { label: t('link_copy'),
      run: () => { if (navigator.clipboard) navigator.clipboard.writeText(url).catch(() => {}); } });
  }
}
document.addEventListener('click', (e) => {
  if (!appState.desktop || e.defaultPrevented || e.button) return;
  const a = e.target && e.target.closest ? e.target.closest('a[href]') : null;
  if (!a) return;
  let url;
  try { url = new URL(a.getAttribute('href'), location.href); } catch (err) { return; }
  if (url.origin === location.origin || (url.protocol !== 'http:' && url.protocol !== 'https:')) return;
  e.preventDefault();
  openOutside(url.href);
});

const STUDIO_TOKEN = document.documentElement.getAttribute('data-token') || '';
function studioHeaders(extra) {
  const h = Object.assign({}, extra || {});
  if (STUDIO_TOKEN) h['X-Studio-Token'] = STUDIO_TOKEN;
  return h;
}

function showSessionExpired() {
  if (!$view || document.getElementById('auth-expired')) return;
  const box = document.createElement('div');
  box.id = 'auth-expired';
  box.className = 'banner';
  box.innerHTML = '<div style="margin-bottom:6px"><strong>工作台已经重新启动：</strong>这个页面的访问钥匙已失效，刚才修改的内容可能还没有保存。请先把需要留下的文字复制出来，再重新加载页面。</div>' +
    '<div style="margin-bottom:10px"><strong>The studio was started again:</strong> this page&#39;s key is no longer valid and recent changes may not have been saved. Copy what you need to keep, then reload.</div>' +
    '<button type="button" class="btn primary" id="auth-reload-btn">' + esc(I18N.zh.reload_btn + ' / ' + I18N.en.reload_btn) + '</button>';
  $view.prepend(box);
  const btn = document.getElementById('auth-reload-btn');
  if (btn) btn.onclick = () => location.reload();
}

async function api(method, path, body, extraOpts) {
  const opts = { method, headers: studioHeaders() };
  if (extraOpts && extraOpts.keepalive) opts.keepalive = true;
  if (body !== undefined) { opts.headers['Content-Type'] = 'application/json'; opts.body = JSON.stringify(body); }
  const res = await fetch(path, opts);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    if (res.status === 401 && !(extraOpts && extraOpts.keepalive)) showSessionExpired();
    const e = new Error(data.message || data.error || 'error');
    e.status = res.status; e.code = data.error || (res.status === 401 ? 'unauthorized' : 'error');
    e.reason = data.reason; e.detail = data.message; e.message_zh = data.message_zh;
    throw e;
  }
  return data;
}

function errorMessage(e) {
  if (!e) return t('err_default');
  if (e.code === 'unauthorized') return t('err_unauthorized');
  if (e.code === 'ai_failed') {
    if (lang === 'zh' && e.message_zh) return (e.reason === 'http' ? t('ai_http') : '') + e.message_zh;
    if (e.message_zh && e.detail && e.reason !== 'http') return e.detail;
    const key = 'ai_' + e.reason;
    const known = I18N[lang][key] !== undefined;
    const detail = (!known || e.reason === 'http' || e.reason === 'other') ? (e.detail || '') : '';
    return t(known ? key : 'ai_other') + detail;
  }
  const key = 'err_' + e.code;
  if (e.message_zh) return lang === 'zh' ? e.message_zh : (e.detail || (I18N[lang][key] ? t(key) : t('err_default')));
  return I18N[lang][key] ? t(key) : (e.detail || t('err_default'));
}

function showError(e) {
  if (e && e.code === 'unauthorized') {
    showSessionExpired();
    return toast(t('err_unauthorized'), { label: t('reload_btn'), run: () => location.reload() });
  }
  toast(errorMessage(e));
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
  const item = (hash, key, glyph, on, mark) => `<a href="${hash}" class="${on ? 'on' : ''}"${on ? ' aria-current="page"' : ''}>` +
    `${icon(glyph)}<span>${esc(t(key))}</span>${mark ? '<i class="dot"></i>' : ''}</a>`;
  $nav.innerHTML = `<div class="brand">${BRAND_MARK}<span>${esc(t('brand'))}</span></div>` +
    item('#/start', 'nav_start', 'start', route === 'start', appState.setup_next && appState.setup_next !== 'done') +
    item('#/', 'nav_scripts', 'scripts', route === 'home' || route === 'new' || route === 'script' || route === 'sponsored') +
    item('#/plan', 'nav_plan', 'plan', route === 'plan', (appState.plan_count || 0) > 0) +
    item('#/ideas', 'nav_ideas', 'ideas', route === 'ideas') +
    item('#/trends', 'nav_trends', 'trends', route === 'trends') +
    item('#/library', 'nav_library', 'library', route === 'library') +
    item('#/voice', 'nav_voice', 'voice', route === 'voice') +
    item('#/autopilot', 'nav_autopilot', 'trends', route === 'autopilot') +
    item('#/settings', 'nav_settings', 'settings', route === 'settings') +
    `<div class="grow"></div><button type="button" class="lang" id="lang-btn">${icon('globe')}<span>${esc(t('lang_btn'))}</span></button>`;
  document.getElementById('lang-btn').onclick = () => {
    lang = lang === 'zh' ? 'en' : 'zh';
    remember('lang', lang);
    keepWithCore();
    router();
  };
  document.documentElement.lang = lang === 'zh' ? 'zh-CN' : 'en';
  document.title = t('brand');
}

async function router() {
  await flushSave();
  const hash = location.hash || '#/';
  const m = hash.match(/^#\/(?:s|script)\/([0-9a-f]{12})$/);
  const sp = hash.match(/^#\/sponsored(?:\/([0-9a-f]{12}))?$/);
  try {
    appState = await api('GET', '/api/state');
    keepWithCore();       // what was set since the last page was opened (in the teleprompter, say) goes to the core
    /* The core was started by a script that is trying an installer of the desktop app: the page checks its own
       window (windowcheck.js) and reports to the core. Never set when a person starts the app. */
    if (appState.window_check && !router.checking) {
      router.checking = true;
      const check = document.createElement('script');
      check.src = '/windowcheck.js';
      document.body.appendChild(check);
    }
    /* Nothing set up yet: open the guided setup once per session. After that the home page only points to it. */
    if (hash === '#/' && appState.setup_next === 'creator' && !sessionStorage.getItem('start-shown')) {
      sessionStorage.setItem('start-shown', '1');
      location.hash = '#/start';
      return;
    }
    if (m) { renderNav('script'); await loadTrendSuggestions(); await renderScript(m[1]); }
    else if (hash === '#/start') { renderNav('start'); await renderSetup(); }
    else if (hash === '#/new') { renderNav('new'); await loadTrendSuggestions(); renderNew(); }
    else if (sp) { renderNav('sponsored'); await renderSponsored(sp[1] || null); }
    else if (hash === '#/plan') { renderNav('plan'); await renderPlan(); }
    else if (hash === '#/ideas') { renderNav('ideas'); await renderIdeas(); }
    else if (hash === '#/trends') { renderNav('trends'); await renderTrends(); }
    else if (hash === '#/library') { renderNav('library'); await renderLibrary(); }
    else if (hash === '#/voice') { renderNav('voice'); await renderVoice(); }
    else if (hash === '#/autopilot') { renderNav('autopilot'); await renderAutopilot(); }
    else if (hash === '#/settings') { renderNav('settings'); renderSettings(); }
    else { renderNav('home'); await renderHome(); }
    if (typeof upCheckOnStartup === 'function') upCheckOnStartup();
  } catch (e) {
    if (e.code === 'not_found') { location.hash = '#/'; return; }
    showError(e);
  }
}

/* ---------- home ---------- */
async function renderHome() {
  const { scripts } = await api('GET', '/api/scripts');
  const cardHtml = (s) => `
    <div class="card script-card" data-script-card="${esc(s.id)}">
      <a class="script-card-main" href="#/s/${esc(s.id)}">
        <h3>${esc(s.title)}</h3>
        <div>${esc(s.preview)}</div>
        <div class="meta">${esc(fmtDate(s.updated_at))} · ${s.line_count} ${esc(t('lines_n'))} · ${s.target_sec}${esc(t('sec'))}${s.mode === 'SPONSORED' ? ` · <span class="tag">${esc(t('hm_filter_sponsored'))}</span>` : ''}${poCardTag(s)}</div>
      </a>
      <div class="script-card-side">
        <button type="button" class="btn ghost" data-dup-script="${esc(s.id)}">${esc(t('dup_btn'))}</button>
      </div>
    </div>`;
  const next = appState.setup_next;
  const setupBanner = next && next !== 'done'
    ? `<div class="banner">${esc(t('st_home_next'))}${esc(t('st_step_' + next))} <a href="#/start">${esc(t('st_home_go'))}</a></div>` : '';
  const banner = setupBanner || (appState.settings.configured ? '' :
    `<div class="banner">${esc(t('no_ai'))} <a href="#/settings">${esc(t('go_settings'))}</a></div>`);
  $view.innerHTML = `<div class="wrap">
    <div class="row"><div><h1>${esc(t('home_title'))}</h1><p class="sub">${esc(t('home_sub'))}</p></div>
      <div class="spacer"></div><a class="btn primary big" href="#/new">${esc(t('new_script'))}</a></div>
    ${banner}<div id="dv-box"></div>${rvHomeListHtml(scripts)}</div>`;
  rvAttachHome(cardHtml);
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
    <div id="sp-entry"></div>
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
      <button class="btn big" id="import-script-json">${esc(t('import_script_json'))}</button>
    </div></div>`;
  spPaintEntry(document.getElementById('sp-entry'));
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
  document.getElementById('import-script-json').onclick = async () => {
    try {
      const script = await manualBridge('draft', Object.assign({ lang }, draft),
        { replyOnly: true, title: t('mb_import_script_title'), intro: t('mb_import_script_intro') });
      if (!script) return;
      draft.notes = ''; draft.keywords = [];
      location.hash = '#/s/' + script.id;
    } catch (e) { showError(e); }
  };
}

/* ---------- script workspace ---------- */
let cur = null;          // { script, findings, leftOut, stats, coverage, voice, wordlist }
let saveTimer = null;
let saveInFlight = null;
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
  if (saveInFlight) await saveInFlight;
  if (dirty) await saveNow();
}

const lintBody = (s) => ({ lines: s.lines, target_sec: s.target_sec, keywords: s.keywords || [], post: s.post });

function saveNow(opts) {
  const run = saveNowInner(opts);
  const tracked = run.finally(() => { if (saveInFlight === tracked) saveInFlight = null; });
  saveInFlight = tracked;
  return tracked;
}

async function saveNowInner(opts) {
  if (!cur) return;
  dirty = false;
  const s = cur.script;
  const seq = ++lintSeq;
  const payload = { title: s.title, target_sec: s.target_sec, lines: s.lines, keywords: s.keywords || [] };
  const hadBeforeCritic = !!cur.beforeCritic;
  if (hadBeforeCritic) { payload.before_critic = true; cur.beforeCritic = false; }
  if (s.post && typeof s.post === 'object') payload.post = s.post;
  if (typeof s.updated_at === 'number') payload.base_updated_at = s.updated_at;
  try {
    const saved = await api('PUT', '/api/scripts/' + s.id, payload, opts);
    if (saved && typeof saved.updated_at === 'number') s.updated_at = saved.updated_at;
    if (opts && opts.keepalive) return;
    const lint = await api('POST', '/api/lint', lintBody(s));
    if (seq !== lintSeq || !cur || cur.script.id !== s.id) return;
    cur.findings = lint.findings;
    cur.leftOut = lint.findings_left_out || 0;
    cur.stats = lint.stats;
    cur.coverage = lint.keyword_coverage;
    cur.voice = lint.voice_match;
    cur.wordlist = lint.wordlist;
    paintChecks();
    poAfterSave(s);     // posted.js: a posted script that was edited says so
    rfAfterSave(s);     // refs.js: a new title or keyword changes which of the creator's material is on the same topic
    spAfterSave(s);     // sponsored.js: re-check required mentions, forbidden wording and tags on the saved script
    if (typeof rcAfterSave === 'function') rcAfterSave(s);
    if (typeof rvAfterSave === 'function') rvAfterSave(s);
    pkAfterSave(s);     // packaging.js: refresh pre-filming readiness gate and packaging checks
    if (!dirty) setSaved('saved');
  } catch (e) {
    if (hadBeforeCritic && cur) cur.beforeCritic = true;
    dirty = true;
    setSaved('save_failed');
    if (opts && opts.keepalive) return;
    if (e && (e.code === 'stale_script' || e.code === 'unauthorized')) showError(e);
    else toast((e && ((lang === 'zh' && e.message_zh) || e.detail)) || t('save_failed'));
  }
}

function setSaved(key) { const el = document.getElementById('saved'); if (el) el.textContent = t(key); }

function isBilingual() { return recall('bilingual') === '1'; }

function toggleBilingual() {
  remember('bilingual', isBilingual() ? '0' : '1');
  keepWithCore();
  if (cur) paintScript();
}

function lineClauses(text) {
  return String(text || '').split('/').map((c) => c.trim()).filter(Boolean);
}

function missingEnglishGlossCount(lines) {
  return (lines || []).filter((l) => l && l.audio_mode !== 'LIVE_CLIP'
    && String(l.spoken_script_zh || '').trim()
    && !String(l.english_partner_translation || '').trim()).length;
}

function lineHtml(line, i, all) {
  const total = Array.isArray(all) ? all.length : (cur && cur.script ? cur.script.lines.length : 1);
  const canSplit = lineClauses(line.spoken_script_zh).length >= 2;
  const canMerge = i < total - 1;
  const head = `<div class="line-head"><span class="no">${i + 1}</span><span class="meta" data-count></span><span class="spacer"></span>
      <button type="button" class="btn ghost" data-act="split" ${canSplit ? '' : 'hidden'}>${esc(t('ln_split'))}</button>
      ${canMerge ? `<button type="button" class="btn ghost" data-act="merge">${esc(t('ln_merge'))}</button>` : ''}
      <button type="button" class="btn ghost" data-act="up" aria-label="up" ${i === 0 ? 'disabled' : ''}>↑</button>
      <button type="button" class="btn ghost" data-act="down" aria-label="down" ${i === total - 1 ? 'disabled' : ''}>↓</button>
      <button type="button" class="btn ghost" data-act="del" aria-label="${esc(t('delete'))}">✕</button></div>`;
  if (isBilingual()) {
    return `<div class="card line bilingual" data-i="${i}">
      ${head}
      <div class="bi-grid">
        <div class="bi-col" data-bi-col="zh">
          <div class="meta bi-label">${esc(t('bi_col_zh'))}</div>
          <textarea class="spoken" rows="1" data-f="spoken_script_zh" placeholder="${esc(t('spoken_ph'))}">${esc(line.spoken_script_zh)}</textarea>
          <input type="text" data-f="onscreen_flower_text_zh" placeholder="${esc(t('flower_ph'))}" value="${esc(line.onscreen_flower_text_zh)}">
          <div class="issues" data-issues></div>
        </div>
        <div class="bi-col" data-bi-col="en">
          <div class="meta bi-label">${esc(t('bi_col_en'))}</div>
          <textarea class="en-gloss" rows="2" data-f="english_partner_translation" data-english-gloss placeholder="${esc(t('english_ph'))}">${esc(line.english_partner_translation)}</textarea>
          <input type="text" data-f="visual_broll_en_zh" placeholder="${esc(t('shot_ph'))}" value="${esc(line.visual_broll_en_zh)}">
        </div>
      </div></div>`;
  }
  const en = lang === 'en' ? `<div class="en"><input type="text" data-f="english_partner_translation"
      placeholder="${esc(t('english_ph'))}" value="${esc(line.english_partner_translation)}"></div>` : '';
  return `<div class="card line" data-i="${i}">
    ${head}
    <textarea class="spoken" rows="1" data-f="spoken_script_zh" placeholder="${esc(t('spoken_ph'))}">${esc(line.spoken_script_zh)}</textarea>
    <div class="issues" data-issues></div>
    <div class="extras">
      <input type="text" data-f="visual_broll_en_zh" placeholder="${esc(t('shot_ph'))}" value="${esc(line.visual_broll_en_zh)}">
      <input type="text" data-f="onscreen_flower_text_zh" placeholder="${esc(t('flower_ph'))}" value="${esc(line.onscreen_flower_text_zh)}">
    </div>${en}</div>`;
}

function paintBilingualBadge() {
  const el = document.getElementById('bi-badge');
  if (!el || !cur || !cur.script) return;
  if (!isBilingual()) { el.innerHTML = ''; return; }
  const missing = missingEnglishGlossCount(cur.script.lines);
  if (missing > 0) {
    const label = missing === 1 ? t('bi_missing_one') : t('bi_missing').replace('{n}', String(missing));
    el.innerHTML = `<span class="tag warn" data-bi-missing="${missing}">${esc(label)}</span>`;
  } else {
    el.innerHTML = `<span class="tag ok" data-bi-missing="0">${esc(t('bi_complete'))}</span>`;
  }
}

function paintCounts() {
  const cpm = cur.stats && cur.stats.speaking_rate_cpm;
  $view.querySelectorAll('.line').forEach((el) => {
    const line = cur.script.lines[Number(el.dataset.i)];
    if (!line) return;
    const units = spokenUnits(line.spoken_script_zh);
    const secs = cpm ? ` · ${t('about')}${Math.max(1, Math.round(units / cpm * 60))}${t('sec')}` : '';
    el.querySelector('[data-count]').textContent = units ? `${units}${lang === 'zh' ? '' : ' '}${t('chars')}${secs}` : '';
    const splitBtn = el.querySelector('[data-act="split"]');
    if (splitBtn) splitBtn.hidden = lineClauses(line.spoken_script_zh).length < 2;
  });
  paintBilingualBadge();
}

function paintChecks() {
  const chip = wlIssueHtml;      // wordlist.js: one finding; one from the word list says what it rests on
  $view.querySelectorAll('.line').forEach((el) => {
    const no = Number(el.dataset.i) + 1;
    el.querySelector('[data-issues]').innerHTML = cur.findings.filter((f) => f.line_no === no).map(chip).join('');
  });
  const kwBox = document.getElementById('kw-box');
  if (kwBox && kwBox.kw) kwBox.kw.setCoverage(cur.coverage);
  const st = cur.stats || {};
  const whole = cur.findings.filter((f) => f.line_no === null);
  const hints = cur.findings.filter(wlIsHint).length;      // a hint that a line may sound written is not a thing to fix
  const lineIssues = cur.findings.length - whole.length - hints;
  /* The server lists at most 500 findings about lines and says how many more there were (studio/checks.py). */
  const leftOut = cur.leftOut ? ` · <span id="issues-more">${esc(tf('issues_more', { n: cur.leftOut }))}</span>` : '';
  const ratio = st.budget ? st.units / st.budget : 0;
  const cls = ratio > 1.1 ? 'over' : (ratio < 0.9 ? 'under' : '');
  const el = document.getElementById('summary');
  if (!el) return;
  el.innerHTML = `<div class="row meta">
      <span>${esc(t('written'))} <b>${st.units || 0}</b> ${esc(t('chars'))}${st.budget ? ` / ${esc(t('budget'))} ${st.budget}` : ''}</span>
      ${st.estimated_sec ? `<span>${esc(t('est'))} ${st.estimated_sec}${esc(t('sec'))}</span>` : ''}
      <span class="spacer"></span>
      <span>${lineIssues ? `${lineIssues} ${esc(t('issues_n'))}` : esc(t('all_good'))}${wlHintsNote(hints)}${leftOut}</span></div>
    <div class="meter"><i class="${cls}" style="width:${Math.min(100, Math.round(ratio * 100))}%"></i></div>
    <div class="issues" style="margin-top:8px">${whole.map(chip).join('')}</div>`;
  paintVoiceMatch(document.getElementById('vm-box'), cur.voice);   // craft.js
  wlPaintNote(document.getElementById('wl-note'), cur.wordlist);   // wordlist.js: said once for all the findings on this page
  if (typeof spPaintEditor === 'function' && cur.script && cur.script.mode === 'SPONSORED') spPaintEditor();
  paintCounts();
}

function paintScript() {
  const s = cur.script;
  const secs = [30, 60, 90].includes(s.target_sec) ? [30, 60, 90] : [30, 60, 90, s.target_sec];
  $view.innerHTML = `<div class="wrap">
    <div class="row"><a href="#/" class="meta">${esc(t('back'))}</a><span class="spacer"></span>
      <span class="saved" id="saved">${esc(t('saved'))}</span>
      <button type="button" class="btn ${isBilingual() ? 'primary' : ''}" id="bilingual-btn" data-bilingual-toggle aria-pressed="${isBilingual() ? 'true' : 'false'}">${esc(t('bi_btn'))}</button>
      <button type="button" class="btn" id="rev-open-btn" data-rv-toggle>${esc(t('rv_btn'))}</button>
      <button type="button" class="btn" id="rev-save-btn" data-rv-quick-save>${esc(t('rv_save_btn'))}</button>
      <button type="button" class="btn" id="dup-script-btn" data-dup-current>${esc(t('dup_btn'))}</button>
      <button class="btn" id="prompter-btn">${esc(t('prompter'))}</button>
      <div class="menu"><button class="btn" id="export-btn">${esc(t('export'))} ▾</button>
        <div class="pop" id="export-pop" hidden>
          <a href="#" data-export="jianying">${esc(t('exp_kit'))}<small>${esc(t('exp_kit_d'))}</small></a>
          <a href="#" data-export="srt">${esc(t('exp_srt'))}<small>${esc(t('exp_srt_d'))}</small></a>
          <a href="#" data-export="voiceover">${esc(t('exp_voice'))}<small>${esc(t('exp_voice_d'))}</small></a>
          <a href="#" data-export="shots">${esc(t('exp_shots'))}<small>${esc(t('exp_shots_d'))}</small></a>
          <a href="#" data-export="flower">${esc(t('exp_flower'))}<small>${esc(t('exp_flower_d'))}</small></a>
          <a href="#" data-export="fcpxml">${esc(t('exp_fcpxml'))}<small>${esc(t('exp_fcpxml_d'))}</small></a>
          <a href="#" data-export="prompter">${esc(t('tp_exp'))}<small>${esc(t('tp_exp_d'))}</small></a>
        </div></div>
      <button class="btn ghost" id="delete-btn">${esc(t('delete'))}</button></div>
    <input type="text" class="title-input" id="title" value="${esc(s.title)}" placeholder="${esc(t('untitled'))}">
    <div class="chips" style="margin:10px 0 4px">${secs.map((v) =>
      `<button type="button" class="chip ${v === s.target_sec ? 'on' : ''}" data-sec="${v}">${v}${esc(t('sec'))}</button>`).join('')}</div>
    <div id="kw-box"></div>
    <div id="pk-ready-box"></div>
    <div class="summary" id="summary"></div>
    <div id="wl-note"></div>
    <div id="vm-box"></div>
    <div id="sp-box"></div>
    <div id="rc-box"></div>
    <div id="craft-bar"></div>
    <div id="rv-box"></div>
    <div id="rf-box"></div>
    <div id="bi-badge" class="bi-badge"></div>
    <div id="lines">${s.lines.map(lineHtml).join('')}</div>
    <button class="btn" id="add-line">${esc(t('add_line'))}</button>
    <div id="post-box"></div><div id="po-box"></div><div id="basis-box"></div><div id="edits-box"></div></div>`;
  $view.querySelectorAll('textarea').forEach(autoGrow);
  keywordPicker(document.getElementById('kw-box'), s.keywords || [], (list) => { s.keywords = list; scheduleSave(); });
  paintPost(document.getElementById('post-box'), s.post);
  paintPosted(document.getElementById('po-box'), s);     // posted.js: mark as posted, and what was recorded
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
    const btn = e.target.closest('[data-act]');
    if (!btn) return;
    const act = btn.dataset.act;
    const i = Number(btn.closest('.line').dataset.i);
    const joinField = (a, b, sep) => {
      const ta = String(a || '').trim();
      const tb = String(b || '').trim();
      return ta && tb ? `${ta}${sep}${tb}` : (ta || tb);
    };
    if (act === 'del') {
      s.lines.splice(i, 1);
      if (!s.lines.length) s.lines.push({ audio_mode: 'VOICEOVER', spoken_script_zh: '' });
    } else if (act === 'split') {
      const clauses = lineClauses(s.lines[i].spoken_script_zh);
      if (clauses.length < 2) return;
      const mid = Math.ceil(clauses.length / 2);
      const orig = s.lines[i];
      orig.spoken_script_zh = clauses.slice(0, mid).join(' / ');
      s.lines.splice(i + 1, 0, {
        audio_mode: orig.audio_mode || 'VOICEOVER',
        beat_type: orig.beat_type || '',
        time_range_sec: '',
        spoken_script_zh: clauses.slice(mid).join(' / '),
        delivery_cue: orig.delivery_cue || '',
        english_partner_translation: '',
        visual_broll_en_zh: '',
        onscreen_flower_text_zh: '',
      });
    } else if (act === 'merge') {
      if (i < 0 || i >= s.lines.length - 1) return;
      const curLine = s.lines[i];
      const nextLine = s.lines[i + 1];
      curLine.spoken_script_zh = joinField(curLine.spoken_script_zh, nextLine.spoken_script_zh, ' / ');
      curLine.english_partner_translation = joinField(curLine.english_partner_translation, nextLine.english_partner_translation, ' / ');
      curLine.visual_broll_en_zh = joinField(curLine.visual_broll_en_zh, nextLine.visual_broll_en_zh, ' · ');
      curLine.onscreen_flower_text_zh = joinField(curLine.onscreen_flower_text_zh, nextLine.onscreen_flower_text_zh, ' / ');
      s.lines.splice(i + 1, 1);
    } else {
      const j = act === 'up' ? i - 1 : i + 1;
      if (j < 0 || j >= s.lines.length) return;
      const tmp = s.lines[i]; s.lines[i] = s.lines[j]; s.lines[j] = tmp;
    }
    s.lines.forEach((l, idx) => { l.line_no = idx + 1; });
    cur.findings = [];  // line numbers changed; fresh results arrive after save
    cur.leftOut = 0;
    paintScript(); scheduleSave();
  };
  const $pop = document.getElementById('export-pop');
  document.getElementById('export-btn').onclick = () => { $pop.hidden = !$pop.hidden; };
  $pop.onclick = async (e) => {
    const a = e.target.closest('[data-export]');
    if (!a) return;
    e.preventDefault(); $pop.hidden = true;
    await flushSave();
    exportScript(s.id, a.dataset.export);
  };
  document.getElementById('delete-btn').onclick = async () => {
    if (!(await askConfirm(t('confirm_delete'), { ok: t('delete'), danger: true }))) return;
    clearTimeout(saveTimer); dirty = false;
    try {
      await api('DELETE', '/api/scripts/' + s.id);
      cur = null; location.hash = '#/';
    } catch (e) { showError(e); }
  };
  document.getElementById('bilingual-btn').onclick = toggleBilingual;
  document.getElementById('prompter-btn').onclick = openPrompter;   // in prompter.js
  craftAttach();    // craft.js: the per-line writing tools and whichever of them is open
  rvAttach();       // revisions.js: version checkpoints, breath-by-breath diffs, safe restore & duplicate
  rfAttach();       // refs.js: the creator's own material beside the lines, put back as it was before this redraw
  spAttach();       // sponsored.js: the brand-brief checklist beside a sponsored script
  if (typeof rcAttach === 'function') rcAttach();
  pkAttach();       // packaging.js: pre-filming readiness strip + RedNote 3:4 packaging lab
}

async function renderScript(id) {
  const script = await api('GET', '/api/scripts/' + id);
  const lint = await api('POST', '/api/lint', lintBody(script));
  cur = { script, findings: lint.findings, leftOut: lint.findings_left_out || 0, stats: lint.stats, coverage: lint.keyword_coverage,
    voice: lint.voice_match, wordlist: lint.wordlist };
  paintScript();
}

/* ---------- voice ---------- */
async function renderVoice() {
  const { dna, is_example, unreadable } = await api('GET', '/api/dna');
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
  const bannerHtml = unreadable
    ? `<div class="banner">${esc(t('dna_unreadable_warn'))}</div>`
    : (is_example ? `<div class="banner">${esc(t('example_voice'))}</div>` : '');
  $view.innerHTML = `<div class="wrap"><h1>${esc(t('voice_title'))}</h1><p class="sub">${esc(t('voice_sub'))}</p>
    ${bannerHtml}
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
    </dl></div><div id="tone-box"></div><div id="voice-actions"></div></div>`;
  if (typeof plPaintToneCard === 'function') plPaintToneCard(document.getElementById('tone-box'));
  paintVoiceActions(dna, is_example);
}
