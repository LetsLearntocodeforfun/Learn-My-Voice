'use strict';
/* Back up everything, and restore a backup. Loaded after wordlist.js and before pages.js (which boots the app);
   helpers from app.js and pages.js (t, tf, esc, toast, askConfirm, fmtDate, num) are used at call time.

   Two things live here:

   - Which data a page means. The server names the data it holds in the X-Data-Epoch header of every answer; the
     name changes when a backup is restored. This page remembers the name it first saw and sends it with every
     request. The server refuses a change that names other data (studio/backup.py), so a page that was open before
     a restore cannot write its old content into the restored data. And as soon as an answer carries another name
     than the one remembered, the page says so and offers to reload: what it shows is no longer what is stored.
     This is done in one place, around the browser's own request function, so that every request of every page
     script is covered, also one a later script sends without going through api().
   - bkPaintSettings(el): the section of the Settings page. It says what a backup holds and what it does not,
     makes one, and restores one: the file is sent to the app, which checks it and answers with what it holds;
     that is shown beside what is here now, and nothing is replaced until the creator has answered the question
     the page asks (askConfirm). After a restore the page loads again, so that nothing it still held is shown.

   Everything shown goes through esc(): the day and the version a backup file names are text from outside. */

Object.assign(I18N.zh, {
  bk_title: '备份和恢复',
  bk_intro: '把你在这里做的、教给工作台的一切存成一个文件。换电脑或者出了问题的时候，可以原样恢复回来。',
  bk_loading: '正在读取…', bk_failed: '读取失败，请刷新页面再试。',
  bk_make_title: '备份全部内容',
  bk_in: '文件里有：',
  bk_in_list: '{s} 篇脚本（连同它们的 AI 初稿）、素材库 {m} 条（连同数据）、热词 {k} 个、记住的修改 {l} 条、个人资料、声音档案和之前的 {h} 份、等你确认的声音提议、最近一次的选题建议，还有你对词表的改动和热点排序的侧重。',
  bk_out: '文件里没有：',
  bk_out_list: 'API 密钥和 AI 服务的连接设置（备份常常会发邮件、放网盘，所以里面从不带密钥）、本机转写安装的软件和模型（很大，可以重新安装）、等待转写的上传文件、导出过的文件，以及之前的自动备份。',
  bk_make_btn: '备份全部内容', bk_making: '正在备份…',
  bk_made_saved: '已备份：{name}（{size}）',
  bk_made_download: '备份文件已交给浏览器下载：{name}（{size}）。请确认它已经存好。',
  bk_left_out: '有 {n} 项没有放进备份，因为太大或太多：{names}',
  bk_restore_title: '恢复备份',
  bk_restore_hint: '选一个备份文件。工作台会先检查它，并告诉你里面有什么；在你确认之前，什么都不会被替换。恢复之前，现在的内容会先自动备份一份，所以恢复之后还能撤回。',
  bk_choose: '选择备份文件…', bk_checking: '正在检查这个文件…',
  bk_too_big: '这个文件超过了备份的大小上限（{size}），所以没有读取。',
  bk_prev_title: '这个备份里有什么',
  bk_col_file: '备份文件里', bk_col_now: '现在这里',
  bk_row_day: '备份日期', bk_row_version: '应用版本', bk_row_scripts: '脚本', bk_row_samples: '素材库',
  bk_row_trends: '热词', bk_row_lessons: '记住的修改', bk_row_voice: '声音档案', bk_row_earlier: '之前的声音档案',
  bk_row_creator: '个人资料',
  bk_yes: '有', bk_no: '没有', bk_voice_unreadable: '有，但读不出来',
  bk_version_source: '从源代码运行，没有版本号',
  bk_says: '日期和版本是文件自己写的；数量是工作台检查文件时数出来的。',
  bk_older: '这个备份是旧一些的版本做的，恢复时会更新到现在的格式。',
  bk_auto_kind: '这是一份恢复之前自动留下的备份。',
  bk_keys_note: '备份里从来没有 API 密钥。恢复不会改动这台电脑上的 AI 连接。',
  bk_restore_btn: '用这个备份替换现在的全部内容…', bk_cancel: '不恢复',
  bk_ask: '用 {d} 的备份替换现在这里的全部内容吗？\n现在的内容会先自动备份一份，之后可以在这一页恢复回来。',
  bk_ask_ok: '替换', bk_restoring: '正在恢复…',
  bk_busy: '有转写或别的任务正在进行。等它完成以后才能恢复备份。',
  bk_done: '已恢复 {d} 的备份。恢复之前的内容留作了自动备份，列在下面；恢复那一份就能撤回这一次。',
  bk_done_ai_kept: '备份里从来没有 API 密钥；这台电脑上的 AI 连接保持原样。',
  bk_done_ai_none: '备份里从来没有 API 密钥，这台电脑上现在也没有连接 AI：请在这一页上方重新连接，密钥需要重新填写。',
  bk_done_ok: '知道了',
  bk_auto_title: '恢复之前的自动备份',
  bk_auto_hint: '每次恢复之前，工作台会把当时的内容自动备份一份，只保留最近 {n} 份。',
  bk_auto_item: '{t} 恢复之前的内容（{size}）', bk_auto_btn: '查看这一份', bk_auto_none: '还没有。',
  bk_last: '上一次恢复：{t}，用的是 {d} 的备份。',
  bk_put_back: '上次关闭应用的时候，有一次恢复正进行到一半。工作台已经把所有内容放回了那次恢复之前的样子。',
  bk_stale: '这一页打开之后，应用里的数据已经被恢复的备份替换了。这一页上还没保存的改动不会保存。请刷新页面，查看现在的内容。',
  bk_reload: '刷新页面',
});
Object.assign(I18N.en, {
  bk_title: 'Backup and restore',
  bk_intro: 'Everything {@name} made here and taught the studio, kept in one file. It brings all of it back on another computer, or after something went wrong.',
  bk_loading: 'Reading…', bk_failed: 'This could not be read. Reload the page to try again.',
  bk_make_title: 'Back up everything',
  bk_in: 'In the file:',
  bk_in_list: '{s} scripts (each with the AI draft it began as), {m} library items (with their numbers), {k} tracked keywords, {l} remembered edits, the creator profile, the voice profile and {h} earlier ones, a voice proposal that is waiting, the latest topic ideas, and the changes to the word list and to how keywords are ranked.',
  bk_out: 'Not in the file:',
  bk_out_list: 'API keys and the AI connection (a backup gets mailed and put on cloud drives, so it never holds a key), what on-device transcription installed (large, and it can be set up again), uploads waiting to be transcribed, exported files, and earlier automatic backups.',
  bk_make_btn: 'Back up everything', bk_making: 'Backing up…',
  bk_made_saved: 'Backed up: {name} ({size})',
  bk_made_download: 'The backup was handed to the browser as a download: {name} ({size}). Check that it was saved.',
  bk_left_out: 'Left out of the backup because they are too large or too many ({n}): {names}',
  bk_restore_title: 'Restore a backup',
  bk_restore_hint: 'Choose a backup file. The studio checks it first and shows what it holds; nothing is replaced until you say yes. What is here now is first kept as an automatic backup, so a restore can be undone.',
  bk_choose: 'Choose a backup file…', bk_checking: 'Checking the file…',
  bk_too_big: 'This file is larger than a backup can be ({size}), so it was not read.',
  bk_prev_title: 'What this backup holds',
  bk_col_file: 'In the backup', bk_col_now: 'Here now',
  bk_row_day: 'Made on', bk_row_version: 'App version', bk_row_scripts: 'Scripts', bk_row_samples: 'Library items',
  bk_row_trends: 'Tracked keywords', bk_row_lessons: 'Remembered edits', bk_row_voice: 'Voice profile',
  bk_row_earlier: 'Earlier voice profiles', bk_row_creator: 'Creator profile',
  bk_yes: 'yes', bk_no: 'no', bk_voice_unreadable: 'there, but it cannot be read',
  bk_version_source: 'run from the source code, no version number',
  bk_says: 'The day and the version are what the file says of itself. The numbers were counted by the studio while it checked the file.',
  bk_older: 'This backup was made by an older version. It is brought up to date when it is restored.',
  bk_auto_kind: 'This is an automatic backup made before a restore.',
  bk_keys_note: 'A backup never holds an API key. A restore does not change the AI connection of this computer.',
  bk_restore_btn: 'Replace everything here with this backup…', bk_cancel: 'Do not restore',
  bk_ask: 'Replace everything that is here now with the backup of {d}?\nWhat is here now is first kept as an automatic backup, and can be brought back from this page.',
  bk_ask_ok: 'Replace', bk_restoring: 'Restoring…',
  bk_busy: 'A transcription or another task is running. A backup can be restored once it is done.',
  bk_done: 'The backup of {d} was restored. What was here before is kept as an automatic backup, listed below; restoring that one undoes this.',
  bk_done_ai_kept: 'A backup never holds an API key; the AI connection of this computer was left as it was.',
  bk_done_ai_none: 'A backup never holds an API key, and no AI is connected on this computer: connect one at the top of this page. The key has to be typed in again.',
  bk_done_ok: 'OK',
  bk_auto_title: 'Automatic backups made before a restore',
  bk_auto_hint: 'Before every restore the studio keeps what was there as an automatic backup. Only the newest {n} are kept.',
  bk_auto_item: 'What was here before the restore of {t} ({size})', bk_auto_btn: 'Look at this one', bk_auto_none: 'None yet.',
  bk_last: 'Last restore: {t}, from the backup of {d}.',
  bk_put_back: 'The app was closed in the middle of a restore. Everything was put back as it was before that restore.',
  bk_stale: 'The data in the app was replaced by a restored backup after this page was opened. Changes on this page that were not saved yet are not saved. Reload the page to see what is there now.',
  bk_reload: 'Reload the page',
});

/* ---------- which data this page means ---------- */

const BK_HEADER = 'X-Data-Epoch';       // must match EPOCH_HEADER in studio/backup.py
/* epoch: the name of the data this page was opened on, once an answer has said it. replacing: this page is
   itself restoring a backup, and loads again when that is done. */
const bkData = { epoch: null, replacing: false, stale: false };
const bkPlain = window.fetch.bind(window);

window.fetch = async function bkRequest(input, init) {
  const address = typeof input === 'string' ? input : String((input && input.url) || '');
  if (address.indexOf('/api/') !== 0) return bkPlain(input, init);
  const options = Object.assign({}, init || {});
  if (bkData.epoch !== null) {
    const headers = new Headers(options.headers || {});
    headers.set(BK_HEADER, bkData.epoch);
    options.headers = headers;
  }
  const answer = await bkPlain(input, options);
  const now = answer.headers.get(BK_HEADER);
  if (now !== null) {
    if (bkData.epoch === null) bkData.epoch = now;
    else if (now !== bkData.epoch && !bkData.replacing) bkShowStale();
  }
  return answer;
};

/* The data this page shows was replaced. Said once, over the page, with one thing to do. */
function bkShowStale() {
  if (bkData.stale) return;
  bkData.stale = true;
  const box = document.createElement('div');
  box.id = 'bk-stale';
  box.innerHTML = `<div class="ask-box" role="alertdialog" aria-modal="true" aria-describedby="bk-stale-text">
      <p id="bk-stale-text">${esc(t('bk_stale'))}</p>
      <div class="ask-row"><button type="button" class="btn primary" id="bk-reload">${esc(t('bk_reload'))}</button></div>
    </div>`;
  document.body.appendChild(box);
  const button = document.getElementById('bk-reload');
  button.onclick = () => location.reload();
  button.focus();
}

/* ---------- the section of the Settings page ---------- */

/* info: the last answer of GET /api/backup. working: '' or what is under way ('making', 'checking', 'restoring').
   made: what the last backup made here was answered with. preview: a checked backup waiting for the creator's
   answer. problem: the sentence of the last refusal. seq names the newest request for info.
   Kept here and not read off the page: the settings page is drawn again on every click in it. */
const bkUi = { info: null, working: '', made: null, preview: null, problem: '', seq: 0 };
const BK_DONE = 'bk-restored';          // in sessionStorage: what this tab restored just before it loaded again

function bkSize(bytes) {
  const n = Number(bytes) || 0;
  if (n >= 1048576) return (n / 1048576).toFixed(1) + ' MB';
  if (n >= 1024) return Math.round(n / 1024) + ' KB';
  return n + ' B';
}

function bkSentence(e) {
  return (lang === 'zh' ? e.message_zh : e.detail) || e.detail || t('err_default');
}

async function bkPaintSettings(el) {
  if (!el) return;
  if (bkUi.info) bkDraw(el);
  else el.innerHTML = `<p class="hint" id="bk-loading">${esc(t('bk_loading'))}</p>`;
  const seq = ++bkUi.seq;
  let info;
  try { info = await api('GET', '/api/backup'); } catch (e) {
    if (!bkUi.info && el.isConnected) el.innerHTML = `<p class="hint" id="bk-failed">${esc(t('bk_failed'))}</p>`;
    return;
  }
  if (seq !== bkUi.seq) return;
  const same = bkUi.info && JSON.stringify(bkUi.info) === JSON.stringify(info);
  bkUi.info = info;
  if (!same && el.isConnected) bkDraw(el);
}

function bkVoiceText(value) {
  return t(value === 'own' ? 'bk_yes' : (value === 'unreadable' ? 'bk_voice_unreadable' : 'bk_no'));
}

/* What a checked backup holds, beside what is here now. */
function bkPreviewHtml(preview) {
  const f = preview.file;
  const here = appState.build && !appState.build.from_source && appState.build.version ? appState.build.version : t('bk_version_source');
  const rows = [
    ['bk_row_day', f.created_on, ''],
    ['bk_row_version', f.app_version || t('bk_version_source'), here],
    ['bk_row_scripts', num(f.holds.scripts), num(preview.now.scripts)],
    ['bk_row_samples', num(f.holds.samples), num(preview.now.samples)],
    ['bk_row_trends', num(f.holds.trends), num(preview.now.trends)],
    ['bk_row_lessons', num(f.holds.lessons), num(preview.now.lessons)],
    ['bk_row_voice', bkVoiceText(f.holds.voice_profile), bkVoiceText(preview.now.voice_profile)],
    ['bk_row_earlier', num(f.holds.earlier_profiles), num(preview.now.earlier_profiles)],
    ['bk_row_creator', t(f.holds.creator ? 'bk_yes' : 'bk_no'), t(preview.now.creator ? 'bk_yes' : 'bk_no')],
  ];
  const busy = bkUi.info && bkUi.info.busy;
  return `<div class="bk-preview" id="bk-preview">
      <h4>${esc(t('bk_prev_title'))}</h4>
      <table class="perf bk-table"><thead><tr><th></th><th>${esc(t('bk_col_file'))}</th><th>${esc(t('bk_col_now'))}</th></tr></thead>
        <tbody>${rows.map((r) => `<tr data-bk-row="${esc(r[0])}"><td>${esc(t(r[0]))}</td><td>${esc(r[1])}</td><td>${esc(r[2])}</td></tr>`).join('')}</tbody></table>
      <p class="hint" id="bk-says">${esc(t('bk_says'))}${f.older ? ' ' + esc(t('bk_older')) : ''}${f.kind === 'before_restore' ? ' ' + esc(t('bk_auto_kind')) : ''}</p>
      <p class="hint" id="bk-keys-note">${esc(t('bk_keys_note'))}</p>
      ${busy ? `<div class="banner" id="bk-busy">${esc(t('bk_busy'))}</div>` : ''}
      <div class="row"><button type="button" class="btn primary danger" id="bk-restore-btn" ${busy || bkUi.working ? 'disabled' : ''}>${
    esc(t(bkUi.working === 'restoring' ? 'bk_restoring' : 'bk_restore_btn'))}</button>
        <button type="button" class="btn" id="bk-cancel" ${bkUi.working ? 'disabled' : ''}>${esc(t('bk_cancel'))}</button></div>
    </div>`;
}

function bkDoneHtml() {
  let done = null;
  try { done = JSON.parse(sessionStorage.getItem(BK_DONE) || 'null'); } catch (e) { done = null; }
  if (!done || typeof done.day !== 'string') return '';
  const connected = appState.settings && appState.settings.configured;
  return `<div class="banner" id="bk-done" role="status">${esc(tf('bk_done', { d: done.day }))}
      <span id="bk-done-ai">${esc(t(connected ? 'bk_done_ai_kept' : 'bk_done_ai_none'))}</span>
      <button type="button" class="btn" id="bk-done-ok">${esc(t('bk_done_ok'))}</button></div>`;
}

function bkDraw(el) {
  const info = bkUi.info;
  const h = info.holds;
  const made = bkUi.made;
  let madeLine = '';
  if (made) {
    madeLine = `<p class="meta" id="bk-made">${esc(tf(made.path ? 'bk_made_saved' : 'bk_made_download', { name: made.name, size: bkSize(made.size) }))}</p>`;
    if (made.left_out && made.left_out.length) {
      madeLine += `<p class="hint" id="bk-left-out">${esc(tf('bk_left_out', { n: made.left_out.length, names: made.left_out.map((x) => x.name).join(', ') }))}</p>`;
    }
  }
  const automatic = info.automatic.map((a) => `<div class="bk-auto" data-bk-auto="${esc(a.name)}">
      <span>${esc(tf('bk_auto_item', { t: fmtDate(a.made_at), size: bkSize(a.size) }))}</span>
      <button type="button" class="btn" data-bk-auto-btn="${esc(a.name)}" ${bkUi.working ? 'disabled' : ''}>${esc(t('bk_auto_btn'))}</button></div>`).join('')
    || `<p class="hint" id="bk-auto-none">${esc(t('bk_auto_none'))}</p>`;
  const last = info.last_restore && typeof info.last_restore.at === 'number'
    ? `<p class="meta" id="bk-last">${esc(tf('bk_last', { t: fmtDate(info.last_restore.at), d: info.last_restore.created_on || '' }))}</p>` : '';
  el.innerHTML = `<h2>${esc(t('bk_title'))}</h2>
    <p class="hint">${esc(t('bk_intro'))}</p>
    ${info.put_back_at_start ? `<div class="banner" id="bk-put-back" role="alert">${esc(t('bk_put_back'))}</div>` : ''}
    ${bkDoneHtml()}
    <div class="card" id="bk-make">
      <h3>${esc(t('bk_make_title'))}</h3>
      <p class="hint" id="bk-in"><b>${esc(t('bk_in'))}</b> ${esc(tf('bk_in_list', { s: num(h.scripts), m: num(h.samples), k: num(h.trends), l: num(h.lessons), h: num(h.earlier_profiles) }))}</p>
      <p class="hint" id="bk-out"><b>${esc(t('bk_out'))}</b> ${esc(t('bk_out_list'))}</p>
      <div class="row" style="margin-top:12px"><button type="button" class="btn primary" id="bk-make-btn" ${bkUi.working ? 'disabled' : ''}>${
    esc(t(bkUi.working === 'making' ? 'bk_making' : 'bk_make_btn'))}</button></div>
      ${madeLine}
    </div>
    <div class="card" id="bk-restore">
      <h3>${esc(t('bk_restore_title'))}</h3>
      <p class="hint">${esc(t('bk_restore_hint'))}</p>
      <div class="row" style="margin-top:12px"><label class="btn" for="bk-file" id="bk-choose">${esc(t('bk_choose'))}</label>
        <input type="file" id="bk-file" accept=".${esc(info.extension)}" hidden ${bkUi.working ? 'disabled' : ''}>
        ${bkUi.working === 'checking' ? `<span class="meta" id="bk-checking">${esc(t('bk_checking'))}</span>` : ''}</div>
      ${bkUi.problem ? `<div class="banner" id="bk-problem" role="alert">${esc(bkUi.problem)}</div>` : ''}
      ${bkUi.preview ? bkPreviewHtml(bkUi.preview) : ''}
      <h4>${esc(t('bk_auto_title'))}</h4>
      <p class="hint">${esc(tf('bk_auto_hint', { n: info.keep_automatic }))}</p>
      <div id="bk-autos">${automatic}</div>
      ${last}
    </div>`;

  const again = () => { if (el.isConnected) bkDraw(el); };
  const refresh = async () => {
    try { bkUi.info = await api('GET', '/api/backup'); } catch (e) { /* the section stays as it is */ }
    again();
  };
  const ok = document.getElementById('bk-done-ok');
  if (ok) ok.onclick = () => { sessionStorage.removeItem(BK_DONE); again(); };

  document.getElementById('bk-make-btn').onclick = async () => {
    bkUi.working = 'making'; bkUi.problem = ''; again();
    try {
      if (appState.desktop) {
        /* Inside the desktop app the core puts the file where exports go and says where that is (see exportScript). */
        const saved = await api('POST', '/api/backup/save', {});
        bkUi.made = saved;
        toast(saved.in_downloads ? tf('exp_saved', { name: saved.name }) : tf('exp_saved_else', { path: saved.path }),
          { label: t('exp_show'), run: () => api('POST', '/api/outside/show', { path: saved.path }).catch(showError) });
      } else {
        const out = await api('POST', '/api/backup', {});
        const res = await fetch('/api/backup/' + out.id + '/file', { method: 'GET', headers: studioHeaders() });
        if (!res.ok) {
          const data = await res.json().catch(() => ({}));
          const err = new Error(data.message || data.error || 'error');
          err.code = data.error; err.detail = data.message; err.message_zh = data.message_zh; throw err;
        }
        const blob = await res.blob();
        const url = URL.createObjectURL(blob);
        const link = document.createElement('a');
        link.href = url;
        link.download = out.name;
        document.body.appendChild(link);
        link.click();
        link.remove();
        setTimeout(() => URL.revokeObjectURL(url), 1000);
        bkUi.made = out;
      }
    } catch (e) { bkUi.problem = bkSentence(e); toast(bkUi.problem); }
    bkUi.working = '';
    again();
  };

  /* Send a backup file to the app to be checked. Nothing is replaced by this. */
  const check = async (send) => {
    bkUi.working = 'checking'; bkUi.problem = ''; bkUi.preview = null; again();
    try { bkUi.preview = await send(); } catch (e) { bkUi.problem = bkSentence(e); }
    bkUi.working = '';
    await refresh();
  };
  document.getElementById('bk-file').onchange = (e) => {
    const file = e.target.files && e.target.files[0];
    if (!file) return;
    if (file.size > info.limits.max_bytes) {
      bkUi.preview = null; bkUi.problem = tf('bk_too_big', { size: bkSize(info.limits.max_bytes) }); again();
      return;
    }
    check(async () => {
      const res = await fetch('/api/restore/file', { method: 'PUT', headers: studioHeaders({ 'Content-Type': 'application/octet-stream' }), body: file });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) { const err = new Error(data.message || data.error || 'error'); err.code = data.error; err.detail = data.message; err.message_zh = data.message_zh; throw err; }
      return data;
    });
  };
  el.querySelectorAll('[data-bk-auto-btn]').forEach((b) => {
    b.onclick = () => check(() => api('POST', '/api/restore/automatic', { name: b.dataset.bkAutoBtn }));
  });

  const cancel = document.getElementById('bk-cancel');
  if (cancel) cancel.onclick = async () => {
    const id = bkUi.preview.restore_id;
    bkUi.preview = null; again();
    try { await api('DELETE', '/api/restore/' + id); } catch (e) { /* it is forgotten later by itself */ }
  };
  const go = document.getElementById('bk-restore-btn');
  if (go) go.onclick = async () => {
    const preview = bkUi.preview;
    if (!(await askConfirm(tf('bk_ask', { d: preview.file.created_on }), { ok: t('bk_ask_ok'), danger: true }))) return;
    bkUi.working = 'restoring'; bkUi.problem = ''; again();
    bkData.replacing = true;            // the answer names the new data: this page is about to load again on it
    try {
      await api('POST', '/api/restore', { restore_id: preview.restore_id });
    } catch (e) {
      bkData.replacing = false;
      bkUi.working = ''; bkUi.problem = bkSentence(e);
      if (e.code === 'restore_not_found') bkUi.preview = null;
      await refresh();
      return;
    }
    /* Everything this page still holds (an open script, a form, a list) is from before. It loads again. */
    sessionStorage.setItem(BK_DONE, JSON.stringify({ day: preview.file.created_on }));
    location.hash = '#/settings';
    location.reload();
  };
}
