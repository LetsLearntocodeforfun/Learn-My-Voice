'use strict';
/* Numbers and comments, beside the library (studio/rednote_analytics.py). Loaded before pages.js, which calls
   plPaintLibraryTools(box, samples) once the library is drawn; helpers from app.js and pages.js are used at call time.

   What this file puts on the library page, in one card:
   - numbers from a table the creator saved from the Creator Center: choose the file, see which column was taken for
     what and what every row would change, then import exactly that. No row creates a library item;
   - for one video: what its readings say about views per day;
   - what viewers wrote under it, pasted in.

   What leaves the computer: nothing, for the table and for the views per day. The file goes to this app's own server
   on 127.0.0.1, which reads it and answers with a preview; nothing is stored until "Import" is clicked.

   Nobody on this project has seen a real export of the Creator Center, so the columns the server picks are only where
   the pickers start. Every cell shown here comes from a file or from somebody's comment and goes through esc(). */

Object.assign(I18N.zh, {
  nc_title: '数据和评论',
  nc_sub: '用创作服务平台的数据表更新视频的数据，看一条视频平均每天涨多少播放，把观众的评论贴进来数一数。',
  nc_imp_pick: '从表格导入数据',
  nc_imp_hint: '在创作服务平台里把笔记数据存成表格（.csv、.tsv、.txt、.xlsx），在这里选它。文件只在这台电脑上读取，不会发到任何地方。先给你看预览，点了「导入」才会存。',
  nc_imp_title: '从表格导入数据：{name}', nc_imp_reading: '正在读取「{name}」…',
  nc_sheet: '读的是第一张工作表「{s}」。', nc_sheet_unnamed: '读的是第一张工作表。', nc_other_sheets: '没有读的工作表：{s}。',
  nc_col: '第 {n} 列', nc_col_named: '第 {n} 列「{h}」', nc_col_eg: '（如：{s}）', nc_sep: '、', nc_none: '没有这一列',
  nc_f_title: '标题在哪一列', nc_f_views: '播放量', nc_f_likes: '点赞', nc_f_collects: '收藏', nc_f_comments: '评论',
  nc_f_read_on: '数据是哪天看的', nc_f_posted_on: '视频是哪天发布的',
  nc_guess_note: '这些列是工作台猜的，不一定对：还没有拿真实的导出表试过。请对照下面读出来的内容，不对就改。',
  nc_dated_day: '表里没有「数据是哪天看的」这一列，所以这些数据都记在今天（{d}）名下。',
  nc_dated_col: '这些数据按「{h}」这一列写的日期来记。',
  nc_posted_note: '发布日期只是照着读出来给你看，不会被当成看数据的日期。',
  nc_th_title: '标题', nc_th_read_on: '数据日期', nc_th_effect: '会改动什么', nc_empty_cell: '（空）', nc_was: '原 {n}',
  nc_e_update: '更新「{t}」的数据', nc_e_reading_only: '只记为 {d} 的数据：「{t}」已经有更新的数据了',
  nc_e_unmatched: '素材库里没有这个标题：不导入，也不新建',
  nc_s_no_title: '没有标题', nc_s_title_cut: '标题太长，没有读全', nc_s_over_limit: '超出一次 {max} 行的上限',
  nc_s_repeated: '这个标题在表里不止一行', nc_s_no_numbers: '没有读到数字', nc_s_bad_day: '没有读出日期',
  nc_s_future_day: '这个日期还没到', nc_s_ambiguous: '素材库里不止一条叫这个标题', nc_s_read_before_posted: '日期早于这条的发布日期',
  nc_c_read: '一共读了 {n} 行。', nc_c_update: '会更新 {n} 条素材的数据。',
  nc_c_reading_only: '{n} 行只记为它那天的数据，不改素材现在的数据。',
  nc_c_unmatched: '{n} 行的标题在素材库里找不到：不导入，也不会新建素材。',
  nc_c_left: '另外不导入的有 {n} 行（{why}）。',
  nc_why_header: '表头和它上面的内容 {n} 行', nc_why_no_title: '没有标题 {n} 行', nc_why_title_cut: '标题没有读全 {n} 行',
  nc_why_over_limit: '超出一次 {max} 行的上限 {n} 行', nc_why_repeated: '标题重复 {n} 行', nc_why_no_numbers: '没有数字 {n} 行',
  nc_why_bad_day: '没有读出日期 {n} 行', nc_why_future_day: '日期还没到 {n} 行', nc_why_ambiguous: '素材库里有重名 {n} 行',
  nc_why_read_before_posted: '日期早于发布日期 {n} 行',
  nc_unmatched_how: '找不到的那些：先把那条视频（视频文件、字幕或脚本）用同样的标题加进素材库，再导入一次。',
  nc_more_rows: '这个文件很长，只读了前 {max} 行。', nc_more_columns: '只读了前 {max} 列。',
  nc_go: '导入这 {n} 行', nc_going: '正在导入…', nc_cancel: '取消', nc_close: '关闭',
  nc_done: '已更新 {n} 条素材的数据', nc_done_readings: '，另有 {n} 行记为它那天的数据',
  nc_too_large: '这个文件超过了 {mb} MB 的导入上限。请删掉不需要的行或工作表，或者另存为 .csv，再试一次。',
  nc_pick: '看哪一条视频',
  nc_pd_since_reading: '{a} 到 {b}（{k} 天）多了 {n} 次播放，平均每天 {x} 次。',
  nc_pd_since_posted: '{p} 发布，到 {b} 是 {k} 天，一共 {n} 次播放，平均每天 {x} 次。',
  nc_pd_none: '这条视频的播放量只在一天看过，算不出每天涨多少。过几天再填一次数据，或者再导入一次表格，就能算了。',
  nc_pd_no_views: '这条视频还没有填播放量。',
  nc_pd_avg_note: '平均数说明不了哪一天：播放一般刚发布时涨得快，后来慢。这里只是把记下的数相除，不折算，也不预估。',
  nc_history: '记下的数据（最近 {n} 次）',
  nc_comments_ph: '把评论贴在这里，一行一条…',
  nc_count_btn: '数一数这些评论', nc_model_btn: '让 AI 读一遍评论',
  nc_sent_pos: '正面 {n}%', nc_sent_neu: '中性 {n}%', nc_sent_neg: '顾虑/负面 {n}%',
});
Object.assign(I18N.en, {
  nc_title: 'Numbers and comments',
  nc_sub: 'Update the numbers of {@names} videos from a Creator Center table, see how many views a video gained a day on average, and paste in what viewers wrote to have it counted.',
  nc_imp_pick: 'Import numbers from a table',
  nc_imp_hint: 'Save the note data in the Creator Center as a table (.csv, .tsv, .txt or .xlsx) and choose it here. The file is read on this computer and nothing is sent anywhere. A preview comes first; nothing is stored until Import is clicked.',
  nc_imp_title: 'Import numbers from a table: {name}', nc_imp_reading: 'Reading "{name}"…',
  nc_sheet: 'Read from the first sheet, "{s}".', nc_sheet_unnamed: 'Read from the first sheet.', nc_other_sheets: 'Sheets not read: {s}.',
  nc_col: 'Column {n}', nc_col_named: 'Column {n} "{h}"', nc_col_eg: ' (e.g. {s})', nc_sep: ', ', nc_none: 'No such column',
  nc_f_title: 'Column with the titles', nc_f_views: 'Views', nc_f_likes: 'Likes', nc_f_collects: 'Saves', nc_f_comments: 'Comments',
  nc_f_read_on: 'Day the numbers were read', nc_f_posted_on: 'Day the video was posted',
  nc_guess_note: 'The studio guessed these columns and may be wrong: this has not been tried on a real export. Check them against the rows below and change them if needed.',
  nc_dated_day: 'The table has no column for the day the numbers were read, so all of them are recorded as read today ({d}).',
  nc_dated_col: 'The numbers are recorded under the day given in column "{h}".',
  nc_posted_note: 'The day a video was posted is only shown as it was read. It is never taken for the day the numbers were read.',
  nc_th_title: 'Title', nc_th_read_on: 'Numbers read on', nc_th_effect: 'What it changes', nc_empty_cell: '(empty)', nc_was: 'was {n}',
  nc_e_update: 'Updates the numbers of "{t}"', nc_e_reading_only: 'Kept as the reading of {d} only: "{t}" already has newer numbers',
  nc_e_unmatched: 'No library item has this title: not imported, nothing created',
  nc_s_no_title: 'no title', nc_s_title_cut: 'title too long to be read whole', nc_s_over_limit: 'over the limit of {max} rows per import',
  nc_s_repeated: 'this title is on more than one row', nc_s_no_numbers: 'no number could be read', nc_s_bad_day: 'no day could be read',
  nc_s_future_day: 'that day has not come yet', nc_s_ambiguous: 'more than one library item has this title',
  nc_s_read_before_posted: 'the day is before this video was posted',
  nc_c_read: 'Read {n} rows.', nc_c_read_one: 'Read 1 row.',
  nc_c_update: 'The numbers of {n} library items will be updated.', nc_c_update_one: 'The numbers of 1 library item will be updated.',
  nc_c_reading_only: '{n} rows are kept as the reading of their own day only; the numbers the items carry stay.',
  nc_c_reading_only_one: '1 row is kept as the reading of its own day only; the numbers the item carries stay.',
  nc_c_unmatched: '{n} rows have a title no library item has: they are not imported and no item is created.',
  nc_c_unmatched_one: '1 row has a title no library item has: it is not imported and no item is created.',
  nc_c_left: 'Other rows not imported: {n} ({why}).',
  nc_why_header: 'header and what stands above it: {n}', nc_why_no_title: 'no title: {n}', nc_why_title_cut: 'title not read whole: {n}',
  nc_why_over_limit: 'over the limit of {max} rows per import: {n}', nc_why_repeated: 'title on more than one row: {n}',
  nc_why_no_numbers: 'no number: {n}', nc_why_bad_day: 'no day read: {n}', nc_why_future_day: 'day not come yet: {n}',
  nc_why_ambiguous: 'title shared by several library items: {n}', nc_why_read_before_posted: 'day before the video was posted: {n}',
  nc_unmatched_how: 'For the rows that match nothing: add the video (its file, its subtitles or its script) to the library under the same title, then import again.',
  nc_more_rows: 'The file is long: only its first {max} rows were read.', nc_more_columns: 'Only the first {max} columns were read.',
  nc_go: 'Import these {n} rows', nc_go_one: 'Import this 1 row', nc_going: 'Importing…', nc_cancel: 'Cancel', nc_close: 'Close',
  nc_done: 'Updated the numbers of {n} library items', nc_done_one: 'Updated the numbers of 1 library item',
  nc_done_readings: '; {n} more rows were kept as readings of their own day',
  nc_too_large: 'This file is larger than the {mb} MB that can be imported. Remove the rows or sheets that are not needed, or save it as .csv, and try again.',
  nc_pick: 'Video to look at',
  nc_pd_since_reading: 'From {a} to {b} ({k} days) it gained {n} views: {x} a day on average.',
  nc_pd_since_reading_one: 'From {a} to {b} (1 day) it gained {n} views.',
  nc_pd_since_posted: 'Posted on {p}; by {b}, {k} days later, it had {n} views: {x} a day on average.',
  nc_pd_since_posted_one: 'Posted on {p}; by {b}, 1 day later, it had {n} views.',
  nc_pd_none: 'The views of this video were read on one day only, so views per day cannot be worked out. Enter the numbers again on a later day, or import a table again.',
  nc_pd_no_views: 'No views are entered for this video yet.',
  nc_pd_avg_note: 'An average says nothing about any single day: views usually come fast at first and slowly later. The recorded numbers are only divided here; nothing is adjusted or projected.',
  nc_history: 'Numbers on record (the last {n} readings)', nc_history_one: 'Numbers on record (1 reading)',
  nc_comments_ph: 'Paste the comments here, one on each line…',
  nc_count_btn: 'Count these comments', nc_model_btn: 'Have an AI read the comments',
  nc_sent_pos: 'Positive {n}%', nc_sent_neu: 'Neutral {n}%', nc_sent_neg: 'Critical {n}%',
});

const NC_MAX_FILE_BYTES = 8388608;   // must match MAX_FILE_BYTES in studio/keyword_import.py; tests/test_rednote_analytics.py checks it
const NC_LABEL_CHARS = 30;           // a header in a picker is cut to this; the server cuts the sample cells the same way
const NC_FIELDS = ['title', 'views', 'likes', 'collects', 'comments', 'read_on', 'posted_on'];   // FIELDS in studio/rednote_analytics.py
const NC_NUMBERS = ['views', 'likes', 'collects', 'comments'];
/* open: whether the card is unfolded. sampleId: the video the lower half is about. file: { name, base64 } of the chosen
   table, kept so that a picker change can ask the server again. preview: the server's last answer for it. failed: why
   the last request was refused (an Error from api(), or { key } when this page refused). seq: names the newest request;
   the answer to an older one is dropped. */
const ncUi = { open: false, sampleId: '', samples: [], file: null, preview: null, failed: null, seq: 0 };

function ncResetImport() { ncUi.file = null; ncUi.preview = null; ncUi.failed = null; ncUi.seq += 1; }

function ncWhy(e) {
  if (e && e.key) return tf(e.key, { mb: NC_MAX_FILE_BYTES / 1048576 });
  return errorMessage(e);
}

/* ---------- numbers from a table ---------- */
/* A column as a picker shows it: its place, its header when the file has one, and two cells from it. */
function ncColumnLabel(c) {
  const header = c.header.length > NC_LABEL_CHARS ? c.header.slice(0, NC_LABEL_CHARS) + '…' : c.header;
  const place = header ? tf('nc_col_named', { n: c.index + 1, h: header }) : tf('nc_col', { n: c.index + 1 });
  return place + (c.samples.length ? tf('nc_col_eg', { s: c.samples.join(t('nc_sep')) }) : '');
}

/* What a row would do, in words. The server decides it (plan in studio/rednote_analytics.py); this only words it. */
function ncEffectHtml(r, p) {
  if (r.effect === 'update') return esc(tf('nc_e_update', { t: r.sample_title }));
  if (r.effect === 'reading_only') return esc(tf('nc_e_reading_only', { t: r.sample_title, d: r.read_on }));
  if (r.effect === 'unmatched') return `<span class="tag warn">${esc(t('nc_e_unmatched'))}</span>`;
  return `<span class="tag">${esc(tf('nc_s_' + r.why, { max: p.limits.import_rows }))}</span>`;
}

function ncNumberCell(r, f) {
  if (r[f] === null) return '–';
  const was = r.now && r.now[f] !== null && r.now[f] !== r[f] && r.effect === 'update'
    ? `<br><span class="meta">${esc(tf('nc_was', { n: num(r.now[f]) }))}</span>` : '';
  return esc(num(r[f])) + was;
}

function ncImportCardHtml() {
  const f = ncUi.file;
  const p = ncUi.preview;
  if (!f) return '';
  const title = `<h3>${esc(tf('nc_imp_title', { name: f.name }))}</h3>`;
  const failed = ncUi.failed ? `<div class="banner" id="nc-imp-error" role="alert">${esc(ncWhy(ncUi.failed))}</div>` : '';
  if (!p) {
    return `<div class="card ki-card" id="nc-imp-card">${title}
      ${failed || `<p class="meta" id="nc-imp-reading">${esc(tf('nc_imp_reading', { name: f.name }))}</p>`}
      ${failed ? `<div class="row"><button type="button" class="btn" id="nc-imp-cancel">${esc(t('nc_close'))}</button></div>` : ''}</div>`;
  }
  const picker = (field) => {
    const chosen = p.fields[field];
    const none = field === 'title' ? '' : `<option value=""${chosen === null ? ' selected' : ''}>${esc(t('nc_none'))}</option>`;
    const options = p.columns.map((c) => `<option value="${c.index}"${c.index === chosen ? ' selected' : ''}>${esc(ncColumnLabel(c))}</option>`).join('');
    return `<label for="nc-f-${field}">${esc(t('nc_f_' + field))}
        <select id="nc-f-${field}" data-nc-field="${field}">${none}${options}</select></label>`;
  };
  const sheet = p.kind !== 'xlsx' ? '' : `<p class="meta">${esc(p.sheet ? tf('nc_sheet', { s: p.sheet }) : t('nc_sheet_unnamed'))}${
    p.other_sheets.length ? ' ' + esc(tf('nc_other_sheets', { s: p.other_sheets.join(t('nc_sep')) })) : ''}</p>`;
  const readHeader = p.fields.read_on === null ? '' : (p.headers[p.fields.read_on] || tf('nc_col', { n: p.fields.read_on + 1 }));
  const dated = p.dated_by === 'column' ? tf('nc_dated_col', { h: readHeader }) : tf('nc_dated_day', { d: p.import_day });
  const rows = p.rows.map((r) => `<tr${r.effect === 'update' || r.effect === 'reading_only' ? '' : ' class="ki-off"'} data-nc-row="${r.row}" data-nc-effect="${esc(r.why || r.effect)}">
      <td>${r.title ? esc(r.title) : `<span class="meta">${esc(t('nc_empty_cell'))}</span>`}</td>
      ${NC_NUMBERS.map((n) => `<td style="white-space:nowrap">${ncNumberCell(r, n)}</td>`).join('')}
      <td style="white-space:nowrap">${r.read_on ? esc(r.read_on) : '–'}</td>
      <td style="text-align:left">${ncEffectHtml(r, p)}</td></tr>`).join('');
  const c = p.counts;
  const max = p.limits.import_rows;
  const why = ['header', 'no_title', 'title_cut', 'over_limit', 'repeated', 'no_numbers', 'bad_day', 'future_day', 'ambiguous', 'read_before_posted']
    .filter((k) => c[k]).map((k) => tf('nc_why_' + k, { n: c[k], max }));
  const left = p.rows_read - c.update - c.reading_only - c.unmatched;
  const counts = [tf(countKey('nc_c_read', p.rows_read), { n: p.rows_read }), tf(countKey('nc_c_update', c.update), { n: c.update })];
  if (c.reading_only) counts.push(tf(countKey('nc_c_reading_only', c.reading_only), { n: c.reading_only }));
  if (c.unmatched) counts.push(tf(countKey('nc_c_unmatched', c.unmatched), { n: c.unmatched }));
  if (left) counts.push(tf('nc_c_left', { n: left, why: why.join(t('nc_sep')) }));
  if (p.more_rows) counts.push(tf('nc_more_rows', { max: num(p.limits.rows) }));
  if (p.more_columns) counts.push(tf('nc_more_columns', { max: p.limits.columns }));
  return `<div class="card ki-card" id="nc-imp-card">${title}${sheet}${failed}
    <div class="ki-pickers">${NC_FIELDS.map(picker).join('')}</div>
    <p class="hint">${esc(t('nc_guess_note'))}</p>
    <p class="meta" id="nc-imp-dated">${esc(dated)}${p.fields.posted_on === null ? '' : ' ' + esc(t('nc_posted_note'))}</p>
    <div style="overflow-x:auto;max-height:420px;overflow-y:auto;margin-top:8px">
    <table class="perf" id="nc-imp-table"><thead><tr><th>${esc(t('nc_th_title'))}</th>${NC_NUMBERS.map((n) => `<th style="white-space:nowrap">${esc(t('nc_f_' + n))}</th>`).join('')}
      <th style="white-space:nowrap">${esc(t('nc_th_read_on'))}</th><th style="text-align:left">${esc(t('nc_th_effect'))}</th></tr></thead>
      <tbody>${rows}</tbody></table></div>
    <p class="meta" id="nc-imp-counts">${esc(counts.join(lang === 'zh' ? '' : ' '))}</p>
    ${c.unmatched ? `<p class="hint" id="nc-imp-unmatched">${esc(t('nc_unmatched_how'))}</p>` : ''}
    <div class="row" style="margin-top:12px">
      <button type="button" class="btn primary" id="nc-imp-go"${p.would_change ? '' : ' disabled'}>${esc(tf(countKey('nc_go', p.would_change), { n: p.would_change }))}</button>
      <button type="button" class="btn" id="nc-imp-cancel">${esc(t('nc_cancel'))}</button></div>
  </div>`;
}

/* Ask the server what the file holds. `fields` is undefined for the first look, when the server guesses, and holds the
   creator's choice for every field after that. This request stores nothing. */
async function ncAsk(fields) {
  const seq = ++ncUi.seq;
  const f = ncUi.file;
  try {
    const body = { name: f.name, file_base64: f.base64 };
    if (fields) body.fields = fields;
    const preview = await api('POST', '/api/rednote/import/preview', body);
    if (seq !== ncUi.seq) return;
    ncUi.preview = preview; ncUi.failed = null;
  } catch (e) {
    if (seq !== ncUi.seq) return;
    ncUi.failed = e;
  }
  ncPaintImport();
}

async function ncChoose(file) {
  ncResetImport();
  ncUi.file = { name: file.name, base64: '' };
  /* Refused here, before it is sent: a request over the server's own size limit is not read at all, and the browser
     may then see only a closed connection instead of the reason. */
  if (file.size > NC_MAX_FILE_BYTES) { ncUi.failed = { key: 'nc_too_large' }; ncPaintImport(); return; }
  ncPaintImport();
  const seq = ncUi.seq;
  let base64;
  try { base64 = await fileToBase64(file); } catch (e) { if (seq === ncUi.seq) { ncUi.failed = e; ncPaintImport(); } return; }
  if (seq !== ncUi.seq) return;               // cancelled, or another file was chosen, while this one was being read
  ncUi.file.base64 = base64;
  await ncAsk();
}

/* Import exactly what the preview on the page shows: the server is handed the preview's fingerprint and refuses when
   the same file would now be read differently. */
async function ncImport() {
  const f = ncUi.file;
  const p = ncUi.preview;
  let out;
  try {
    out = await api('POST', '/api/rednote/import', { name: f.name, file_base64: f.base64, fields: p.fields, fingerprint: p.fingerprint });
  } catch (e) {
    ncUi.failed = e;
    if (e.code === 'import_changed') { await ncAsk(p.fields); ncUi.failed = e; }     // show what it would do now, and why it was not done
    ncPaintImport();
    return;
  }
  ncResetImport();
  toast(tf(countKey('nc_done', out.changed), { n: out.changed }) + (out.readings_only ? tf('nc_done_readings', { n: out.readings_only }) : ''));
  if (location.hash === '#/library') await renderLibrary();
}

/* Draw the preview card from ncUi, and wire it. Does nothing while the card is not on the page. */
function ncPaintImport() {
  const $box = document.getElementById('nc-imp-box');
  if (!$box) return;
  $box.innerHTML = ncImportCardHtml();
  const $cancel = document.getElementById('nc-imp-cancel');
  if ($cancel) $cancel.onclick = () => { ncResetImport(); ncPaintImport(); };
  const $go = document.getElementById('nc-imp-go');
  if (!$go) return;
  const selects = Array.from($box.querySelectorAll('[data-nc-field]'));
  selects.forEach(($sel) => {
    $sel.onchange = () => {
      const fields = {};
      selects.forEach(($s) => { fields[$s.dataset.ncField] = $s.value === '' ? null : Number($s.value); });
      /* A column holds one thing: giving it to this field takes it from the field that had it. */
      const picked = fields[$sel.dataset.ncField];
      NC_FIELDS.forEach((k) => { if (k !== $sel.dataset.ncField && picked !== null && fields[k] === picked) fields[k] = null; });
      if (fields.title === null) fields.title = ncUi.preview.fields.title;
      selects.forEach(($s) => { $s.disabled = true; });
      $go.disabled = true;
      ncAsk(fields);
    };
  });
  $go.onclick = () => busy($go, t('nc_going'), ncImport);
}

/* ---------- one video: views per day ---------- */
function ncPerDayHtml(d) {
  if (!d) return '';
  const lines = [];
  const r = d.since_reading;
  const p = d.since_posted;
  if (r) lines.push(tf(countKey('nc_pd_since_reading', r.days), { a: r.from, b: r.to, k: r.days, n: num(r.views), x: num(r.per_day) }));
  if (p) lines.push(tf(countKey('nc_pd_since_posted', p.days), { p: p.posted_on, b: p.to, k: p.days, n: num(p.views), x: num(p.per_day) }));
  const viewed = d.history.some((h) => h.views !== null);
  if (!r && !p) lines.push(t(viewed ? 'nc_pd_none' : 'nc_pd_no_views'));
  const history = d.history.length ? `<details><summary class="meta">${esc(tf(countKey('nc_history', d.history.length), { n: d.history.length }))}</summary>
    <table class="perf"><thead><tr><th>${esc(t('nc_th_read_on'))}</th>${NC_NUMBERS.map((n) => `<th>${esc(t('nc_f_' + n))}</th>`).join('')}</tr></thead>
    <tbody>${d.history.map((h) => `<tr><td>${esc(h.read_on)}</td>${NC_NUMBERS.map((n) => `<td>${h[n] === null ? '–' : esc(num(h[n]))}</td>`).join('')}</tr>`).join('')}</tbody></table></details>` : '';
  return `<p id="nc-per-day" style="margin:10px 0 0">${esc(lines.join(lang === 'zh' ? '' : ' '))}</p>
    ${r || p ? `<p class="hint">${esc(t('nc_pd_avg_note'))}</p>` : ''}${history}`;
}

/* ---------- the card ---------- */
async function ncDraw() {
  const box = document.getElementById('rn-lib-box');
  if (!box) return;
  const samples = ncUi.samples;
  let detail = null;
  if (ncUi.sampleId) {
    try { detail = await api('GET', '/api/samples/' + ncUi.sampleId + '/comments'); } catch (e) { detail = null; }
  }
  if (!document.body.contains(box)) return;      // another page was opened while the answer was on its way
  const sent = detail && detail.sentiment;
  const pct = (sent && sent.sentiment_breakdown_pct) || { positive: 0, neutral: 0, negative: 0 };
  const qs = (sent && sent.audience_questions) || [];
  box.innerHTML = `<details class="card soft" id="nc-card"${ncUi.open ? ' open' : ''}>
    <summary>${esc(t('nc_title'))}</summary>
    <p class="hint">${esc(t('nc_sub'))}</p>
    <div class="row" style="margin-top:12px">
      <label class="btn" for="nc-imp-file">${esc(t('nc_imp_pick'))}</label>
      <input type="file" id="nc-imp-file" accept=".csv,.tsv,.txt,.xlsx" hidden>
      <span class="hint ki-hint">${esc(t('nc_imp_hint'))}</span>
    </div>
    <div id="nc-imp-box"></div>
    <label class="field" for="nc-sample">${esc(t('nc_pick'))}</label>
    <select id="nc-sample">${samples.map((s) =>
      `<option value="${esc(s.id)}"${s.id === ncUi.sampleId ? ' selected' : ''}>${esc(s.title)}</option>`).join('')}</select>
    ${ncPerDayHtml(detail && detail.per_day)}
    <textarea id="nc-comments-in" rows="4" style="margin-top:14px" placeholder="${esc(t('nc_comments_ph'))}"></textarea>
    <div class="row" style="margin-top:10px">
      <button type="button" class="btn" id="nc-count">${esc(t('nc_count_btn'))}</button>
      ${(detail && detail.comments && detail.comments.length) ? `<button type="button" class="btn" id="nc-model">${esc(t('nc_model_btn'))}</button>` : ''}
    </div>
    ${sent && sent.comment_count > 0 ? `<div class="card" style="margin-top:14px">
      <div class="row">
        <span class="tag ok">${esc(tf('nc_sent_pos', { n: pct.positive }))}</span>
        <span class="tag">${esc(tf('nc_sent_neu', { n: pct.neutral }))}</span>
        <span class="tag ${pct.negative > 20 ? 'no' : ''}">${esc(tf('nc_sent_neg', { n: pct.negative }))}</span>
      </div>
      <p style="margin:8px 0">${esc(lang === 'zh' ? sent.summary_zh : (sent.summary_en || sent.summary_zh))}</p>
      ${qs.map((q) => `<div class="meta">${esc(q.question_zh)}</div>`).join('')}
    </div>` : ''}
  </details>`;

  const $card = document.getElementById('nc-card');
  $card.ontoggle = () => { ncUi.open = $card.open; };
  const $file = document.getElementById('nc-imp-file');
  $file.onchange = () => {
    const file = $file.files[0];
    $file.value = '';                         // so that choosing the same file again is a change too
    if (file) ncChoose(file);
  };
  ncPaintImport();
  const $sel = document.getElementById('nc-sample');
  $sel.onchange = () => { ncUi.sampleId = $sel.value; ncDraw(); };
  document.getElementById('nc-count').onclick = async () => {
    try {
      await api('POST', '/api/samples/' + ncUi.sampleId + '/comments', { text: document.getElementById('nc-comments-in').value, replace: false });
      await ncDraw();
    } catch (e) { showError(e); }
  };
  const $model = document.getElementById('nc-model');
  if ($model) {
    $model.onclick = async () => {
      try {
        if (!appState.settings.configured || appState.settings.manual) {
          if (!(await manualBridge('sentiment', { sample_id: ncUi.sampleId }))) return;
        } else {
          $model.disabled = true;
          await api('POST', '/api/samples/' + ncUi.sampleId + '/sentiment/ai', {});
        }
        await ncDraw();
      } catch (e) {
        $model.disabled = false;
        showError(e);
      }
    };
  }
}

/* Called by renderLibrary (pages.js) once the library is in place. Nothing is shown for an empty library: a table can
   only change items that are there. */
async function plPaintLibraryTools(box, samples) {
  if (!box) return;
  ncUi.samples = samples || [];
  if (!ncUi.samples.length) { box.innerHTML = ''; return; }
  if (!ncUi.samples.some((s) => s.id === ncUi.sampleId)) ncUi.sampleId = ncUi.samples[0].id;
  await ncDraw();
}
