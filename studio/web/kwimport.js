'use strict';
/* Keyword exports from analytics tools (新红, 千瓜, 灰豚) on the Trends page: choose a .csv, .tsv, .txt or .xlsx
   file, see what was found in it, pick the column with the keywords and the one with the number, import.
   Loaded after discover.js and before pages.js (which boots the app); helpers from pages.js are used at call time.

   What leaves the computer: nothing. The file goes to this app's own server on 127.0.0.1, which reads it and
   answers with a preview. Nothing is stored until "Import" is clicked, and no AI service is involved.

   Nobody on this project has seen a real export from those tools, so the columns the server picks are only
   where the two pickers start. Every cell shown here comes from a file and goes through esc(). */

Object.assign(I18N.zh, {
  ki_pick: '从表格导入',
  ki_hint: '可以选数据分析工具导出的关键词表（.csv、.tsv、.txt、.xlsx）。文件只在这台电脑上读取，不会发到任何地方。',
  ki_title: '从表格导入：{name}', ki_reading: '正在读取「{name}」…',
  ki_sheet: '读的是第一张工作表「{s}」。', ki_sheet_unnamed: '读的是第一张工作表。', ki_other_sheets: '没有读的工作表：{s}。',
  ki_kw_col: '关键词在哪一列', ki_heat_col: '热度数字在哪一列', ki_no_heat: '没有数字',
  ki_col: '第 {n} 列', ki_col_named: '第 {n} 列「{h}」', ki_col_eg: '（如：{s}）', ki_sep: '、',
  ki_guess_note: '这两列是工作台猜的，不一定对。请对照下面读出来的内容，不对就改。',
  ki_first: '前 {n} 行读出来是这样：', ki_th_kw: '关键词', ki_th_heat: '热度', ki_empty_cell: '（空）',
  ki_skip_empty: '没有关键词', ki_skip_too_long: '超过 {max} 个字符', ki_skip_duplicate: '重复', ki_skip_over_limit: '超出上限',
  ki_read: '一共读了 {n} 行。', ki_will: '会导入 {n} 个关键词。', ki_left: '不导入的有 {n} 行（{why}）。',
  ki_why_header: '表头和它上面的标题 {n} 行', ki_why_empty: '没有关键词 {n} 行', ki_why_too_long: '超过 {max} 个字符 {n} 行',
  ki_why_duplicate: '重复 {n} 行', ki_why_over_limit: '超出一次 {max} 个的上限 {n} 行',
  ki_more_rows: '这个文件很长，只读了前 {max} 行。', ki_more_columns: '只读了前 {max} 列。',
  ki_go: '导入 {n} 个关键词', ki_going: '正在导入…', ki_cancel: '取消', ki_close: '关闭',
  ki_done: '已导入 {n} 个关键词', ki_done_left: '，另有 {n} 个超出一次 {max} 个的上限，没有导入',
  ki_too_large: '这个文件超过了 {mb} MB 的导入上限。请删掉不需要的行或工作表，或者另存为 .csv，再试一次。',
});
Object.assign(I18N.en, {
  ki_pick: 'Import a spreadsheet',
  ki_hint: 'A keyword list exported from an analytics tool (.csv, .tsv, .txt or .xlsx). The file is read on this computer and nothing is sent anywhere.',
  ki_title: 'Import a spreadsheet: {name}', ki_reading: 'Reading "{name}"…',
  ki_sheet: 'Read from the first sheet, "{s}".', ki_sheet_unnamed: 'Read from the first sheet.', ki_other_sheets: 'Sheets not read: {s}.',
  ki_kw_col: 'Column with the keywords', ki_heat_col: 'Column with the heat number', ki_no_heat: 'No number',
  ki_col: 'Column {n}', ki_col_named: 'Column {n} "{h}"', ki_col_eg: ' (e.g. {s})', ki_sep: ', ',
  ki_guess_note: 'The studio guessed these two columns and may be wrong. Check them against the rows below and change them if needed.',
  ki_first: 'How the first {n} rows are read:', ki_first_one: 'How the first row is read:',
  ki_th_kw: 'Keyword', ki_th_heat: 'Heat', ki_empty_cell: '(empty)',
  ki_skip_empty: 'no keyword', ki_skip_too_long: 'over {max} characters', ki_skip_duplicate: 'repeated', ki_skip_over_limit: 'over the limit',
  ki_read: 'Read {n} rows.', ki_read_one: 'Read 1 row.',
  ki_will: '{n} keywords will be imported.', ki_will_one: '1 keyword will be imported.',
  ki_left: 'Rows not imported: {n} ({why}).',
  ki_why_header: 'header and titles above it: {n}', ki_why_empty: 'no keyword: {n}', ki_why_too_long: 'over {max} characters: {n}',
  ki_why_duplicate: 'repeated: {n}', ki_why_over_limit: 'over the limit of {max} per import: {n}',
  ki_more_rows: 'The file is long: only its first {max} rows were read.', ki_more_columns: 'Only the first {max} columns were read.',
  ki_go: 'Import {n} keywords', ki_go_one: 'Import 1 keyword', ki_going: 'Importing…', ki_cancel: 'Cancel', ki_close: 'Close',
  ki_done: 'Imported {n} keywords', ki_done_one: 'Imported 1 keyword',
  ki_done_left: '; {n} more were over the limit of {max} per import and were left out',
  ki_too_large: 'This file is larger than the {mb} MB that can be imported. Remove the rows or sheets that are not needed, or save it as .csv, and try again.',
});

const KI_MAX_FILE_BYTES = 8388608;   // must match MAX_FILE_BYTES in studio/keyword_import.py; tests/test_keyword_import.py checks it
const KI_LABEL_CHARS = 30;           // a header in a picker is cut to this; the server cuts the sample cells the same way
/* file: { name, base64 } of the chosen file, kept so that a picker change can ask the server again.
   preview: the server's last answer for it. failed: why the last request was refused (an Error from api(), or
   { key } when this page refused). seq: names the newest request; the answer to an older one is dropped. */
const kiUi = { file: null, preview: null, failed: null, seq: 0 };

function kiReset() { kiUi.file = null; kiUi.preview = null; kiUi.failed = null; kiUi.seq += 1; }

/* Why a file was refused, in the interface language. The import requests answer a refusal in both languages. */
function kiWhy(e) {
  if (e && e.key) return tf(e.key, { mb: KI_MAX_FILE_BYTES / 1048576 });
  if (e && e.message_zh) return lang === 'zh' ? e.message_zh : e.message;
  return t('err_default');
}

/* The control beside the paste box. renderTrends puts this into its page and then calls kiBind(). */
function kiControlHtml() {
  return `<label class="btn" for="ki-file">${esc(t('ki_pick'))}</label>
    <input type="file" id="ki-file" accept=".csv,.tsv,.txt,.xlsx" hidden>
    <span class="hint ki-hint">${esc(t('ki_hint'))}</span>`;
}

/* A column as a picker shows it: its place, its header when the file has one, and two cells from it. */
function kiColumnLabel(c) {
  const header = c.header.length > KI_LABEL_CHARS ? c.header.slice(0, KI_LABEL_CHARS) + '…' : c.header;
  const place = header ? tf('ki_col_named', { n: c.index + 1, h: header }) : tf('ki_col', { n: c.index + 1 });
  return place + (c.samples.length ? tf('ki_col_eg', { s: c.samples.join(t('ki_sep')) }) : '');
}

function kiCardHtml() {
  const f = kiUi.file;
  const p = kiUi.preview;
  if (!f) return '';
  const title = `<h3>${esc(tf('ki_title', { name: f.name }))}</h3>`;
  const failed = kiUi.failed ? `<div class="banner" id="ki-error" role="alert">${esc(kiWhy(kiUi.failed))}</div>` : '';
  if (!p) {
    return `<div class="card ki-card" id="ki-card">${title}
      ${failed || `<p class="meta" id="ki-reading">${esc(tf('ki_reading', { name: f.name }))}</p>`}
      ${failed ? `<div class="row"><button class="btn" id="ki-cancel">${esc(t('ki_close'))}</button></div>` : ''}</div>`;
  }
  const option = (c, chosen) => `<option value="${c.index}"${c.index === chosen ? ' selected' : ''}>${esc(kiColumnLabel(c))}</option>`;
  const heatOptions = `<option value=""${p.heat_column === null ? ' selected' : ''}>${esc(t('ki_no_heat'))}</option>`
    + p.columns.filter((c) => c.index !== p.keyword_column).map((c) => option(c, p.heat_column)).join('');
  const sheet = p.kind !== 'xlsx' ? '' : `<p class="meta">${esc(p.sheet ? tf('ki_sheet', { s: p.sheet }) : t('ki_sheet_unnamed'))}${
    p.other_sheets.length ? ' ' + esc(tf('ki_other_sheets', { s: p.other_sheets.join(t('ki_sep')) })) : ''}</p>`;
  const max = p.limits.keyword_chars;
  const rows = p.rows.map((r) => `<tr${r.skipped ? ' class="ki-off"' : ''}>
      <td>${r.keyword ? esc(r.keyword) : `<span class="meta">${esc(t('ki_empty_cell'))}</span>`}${
    r.skipped ? ` <span class="tag warn">${esc(tf('ki_skip_' + r.skipped, { max }))}</span>` : ''}</td>
      <td>${r.heat === null ? '–' : esc(num(r.heat))}</td></tr>`).join('');
  const s = p.skipped;
  const why = [['header', s.header, max], ['empty', s.empty, max], ['too_long', s.too_long, max], ['duplicate', s.duplicate, max],
    ['over_limit', s.over_limit, p.limits.keywords]].filter((x) => x[1]).map((x) => tf('ki_why_' + x[0], { n: x[1], max: x[2] }));
  const left = p.rows_read - p.would_import;
  const counts = [tf(countKey('ki_read', p.rows_read), { n: p.rows_read }), tf(countKey('ki_will', p.would_import), { n: p.would_import })];
  if (left) counts.push(tf('ki_left', { n: left, why: why.join(t('ki_sep')) }));
  if (p.more_rows) counts.push(tf('ki_more_rows', { max: num(p.limits.rows) }));
  if (p.more_columns) counts.push(tf('ki_more_columns', { max: p.limits.columns }));
  return `<div class="card ki-card" id="ki-card">${title}${sheet}${failed}
    <div class="ki-pickers">
      <label for="ki-kw">${esc(t('ki_kw_col'))}
        <select id="ki-kw">${p.columns.map((c) => option(c, p.keyword_column)).join('')}</select></label>
      <label for="ki-heat">${esc(t('ki_heat_col'))}
        <select id="ki-heat">${heatOptions}</select></label>
    </div>
    <p class="hint">${esc(t('ki_guess_note'))}</p>
    <p class="meta">${esc(tf(countKey('ki_first', p.rows.length), { n: p.rows.length }))}</p>
    <table class="perf ki-table"><thead><tr><th>${esc(t('ki_th_kw'))}</th><th>${esc(t('ki_th_heat'))}</th></tr></thead>
      <tbody>${rows}</tbody></table>
    <p class="meta" id="ki-counts">${esc(counts.join(lang === 'zh' ? '' : ' '))}</p>
    <div class="row" style="margin-top:12px">
      <button class="btn primary" id="ki-go"${p.would_import ? '' : ' disabled'}>${esc(tf(countKey('ki_go', p.would_import), { n: p.would_import }))}</button>
      <button class="btn" id="ki-cancel">${esc(t('ki_cancel'))}</button></div>
  </div>`;
}

/* Ask the server what the file holds. `columns` is empty for the first look, when the server guesses, and holds
   the creator's choice after that. This request stores nothing. */
async function kiAsk(columns) {
  const seq = ++kiUi.seq;
  const f = kiUi.file;
  try {
    const preview = await api('POST', '/api/trends/import/preview', Object.assign({ name: f.name, file_base64: f.base64 }, columns));
    if (seq !== kiUi.seq) return;
    kiUi.preview = preview; kiUi.failed = null;
  } catch (e) {
    if (seq !== kiUi.seq) return;
    kiUi.failed = e;
  }
  kiPaint();
}

async function kiChoose(file) {
  kiReset();
  kiUi.file = { name: file.name, base64: '' };
  /* Refused here, before it is sent. The server would refuse it too, but a request over its own size limit is not
     read at all, and the browser may then see only a closed connection instead of the reason. */
  if (file.size > KI_MAX_FILE_BYTES) { kiUi.failed = { key: 'ki_too_large' }; kiPaint(); return; }
  kiPaint();
  const seq = kiUi.seq;
  let base64;
  try { base64 = await fileToBase64(file); } catch (e) { if (seq === kiUi.seq) { kiUi.failed = e; kiPaint(); } return; }
  if (seq !== kiUi.seq) return;               // cancelled, or another file was chosen, while this one was being read
  kiUi.file.base64 = base64;
  await kiAsk({});
}

async function kiImport() {
  const f = kiUi.file;
  const p = kiUi.preview;
  let out;
  try {
    out = await api('POST', '/api/trends/import', { name: f.name, file_base64: f.base64, keyword_column: p.keyword_column, heat_column: p.heat_column });
  } catch (e) { kiUi.failed = e; kiPaint(); return; }
  kiReset();
  toast(tf(countKey('ki_done', out.added), { n: out.added }) + (out.left_out ? tf('ki_done_left', { n: out.left_out, max: p.limits.keywords }) : ''));
  if (location.hash === '#/trends') await renderTrends(out);
}

/* Draw the card under the paste box from kiUi, and wire it. Does nothing while another page is showing: the
   state is kept, and the card is back when Trends is opened again. */
function kiPaint() {
  const $file = document.getElementById('ki-file');
  if (!$file) return;
  let $box = document.getElementById('ki-box');
  if (!$box) {
    $box = document.createElement('div');
    $box.id = 'ki-box';
    $file.closest('.card').insertAdjacentElement('afterend', $box);
  }
  $box.innerHTML = kiCardHtml();
  const $cancel = document.getElementById('ki-cancel');
  if ($cancel) $cancel.onclick = () => { kiReset(); kiPaint(); };
  const $kw = document.getElementById('ki-kw');
  if (!$kw) return;
  const $heat = document.getElementById('ki-heat');
  const $go = document.getElementById('ki-go');
  const repick = () => {
    const keyword = Number($kw.value);
    /* The number cannot come from the column the keywords are in: moving the keywords there means "no number". */
    const heat = $heat.value === '' || Number($heat.value) === keyword ? null : Number($heat.value);
    $kw.disabled = true; $heat.disabled = true; $go.disabled = true;
    kiAsk({ keyword_column: keyword, heat_column: heat });
  };
  $kw.onchange = repick;
  $heat.onchange = repick;
  $go.onclick = () => busy($go, t('ki_going'), kiImport);
}

/* Called by renderTrends once its page is in place. */
function kiBind() {
  const $file = document.getElementById('ki-file');
  if (!$file) return;
  $file.onchange = () => {
    const file = $file.files[0];
    $file.value = '';                         // so that choosing the same file again is a change too
    if (file) kiChoose(file);
  };
  kiPaint();
}
