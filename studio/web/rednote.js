'use strict';
/* Numbers and comments, beside the library (studio/rednote_analytics.py). Loaded before pages.js, which calls
   plPaintLibraryTools(box, samples) once the library is drawn; helpers from app.js and pages.js are used at call time.

   What this file puts on the library page, in one card:
   - numbers from a table the creator saved from the Creator Center: choose the file, see which column was taken for
     what (the four counts plus optional shares, new followers, cover click rate, watch time and completion rate) and
     what every row would change, then import exactly that. No row creates a library item;
   - for one video: an optional posted date (for items added by hand) and what its readings say about views per day;
   - what viewers wrote under it: pasted in (or read from a screenshot into the paste box first, by copy-and-paste or
     an optional vision AI call after an explicit privacy warning, and stored only when the creator clicks to count),
     counted locally by word lists or summarized by an AI carrying only the video's title, numbers and comment texts.

   Nobody on this project has seen a real export of the Creator Center, and the vision AI calls were written from
   provider documentation without being run against a live service. Every cell and comment goes through esc(). */

Object.assign(I18N.zh, {
  nc_title: '数据和评论',
  nc_sub: '用创作服务平台的数据表更新视频的数据，看一条视频平均每天涨多少播放，把观众的评论贴进来数一数。',
  nc_imp_pick: '从表格导入数据',
  nc_imp_hint: '在创作服务平台里把笔记数据存成表格（.csv、.tsv、.txt、.xlsx），在这里选它。文件只在这台电脑上读取，不会发到任何地方。先给你看预览，点了「导入」才会存。',
  nc_imp_title: '从表格导入数据：{name}', nc_imp_reading: '正在读取「{name}」…',
  nc_sheet: '读的是第一张工作表「{s}」。', nc_sheet_unnamed: '读的是第一张工作表。', nc_other_sheets: '没有读的工作表：{s}。',
  nc_col: '第 {n} 列', nc_col_named: '第 {n} 列「{h}」', nc_col_eg: '（如：{s}）', nc_sep: '、', nc_none: '没有这一列',
  nc_f_title: '标题在哪一列', nc_f_views: '播放量', nc_f_likes: '点赞', nc_f_collects: '收藏', nc_f_comments: '评论',
  nc_f_shares: '分享', nc_f_new_followers: '涨粉', nc_f_click_rate_pct: '封面点击率', nc_f_avg_watch_sec: '人均观看时长',
  nc_f_completion_rate_pct: '完播率', nc_f_read_on: '数据是哪天看的', nc_f_posted_on: '视频是哪天发布的',
  nc_guess_note: '这些列是工作台猜的，不一定对：还没有拿真实的导出表试过。请对照下面读出来的内容，不对就改。',
  nc_dated_day: '表里没有「数据是哪天看的」这一列，所以这些数据都记在今天（{d}）名下。',
  nc_dated_col: '这些数据按「{h}」这一列写的日期来记。',
  nc_posted_note: '发布日期只是照着读出来给你看，不会被当成看数据的日期。',
  nc_th_title: '标题', nc_th_read_on: '数据日期', nc_th_effect: '会改动什么', nc_empty_cell: '（空）', nc_was: '原 {n}',
  nc_sec_unit: '{n} 秒',
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
  nc_byhand_posted: '这条视频的发布日期（选填）',
  nc_byhand_save: '保存日期', nc_byhand_saved: '发布日期已保存。',
  nc_script_posted: '发布日期：{d}（来自脚本的发布记录）',
  nc_pd_since_reading: '{a} 到 {b}（{k} 天）多了 {n} 次播放，平均每天 {x} 次。',
  nc_pd_since_posted: '{p} 发布，到 {b} 是 {k} 天，一共 {n} 次播放，平均每天 {x} 次。',
  nc_pd_none: '这条视频的播放量只在一天看过，算不出每天涨多少。过几天再填一次数据，或者再导入一次表格，就能算了。',
  nc_pd_no_views: '这条视频还没有填播放量。',
  nc_pd_avg_note: '平均数说明不了哪一天：播放一般刚发布时涨得快，后来慢。这里只是把记下的数相除，不折算，也不预估。',
  nc_history: '记下的数据（最近 {n} 次）',
  nc_shot_manual: '从评论截图读字（复制粘贴）',
  nc_shot_ai: '让 AI 读评论截图', nc_shot_reading: '正在读取截图…',
  nc_shot_hint: '从截图读出的文字只会先放进下面的输入框，等你核对无误、点了「数一数这些评论」才会保存。工作台不存任何评论者的昵称。',
  nc_shot_mb_title: '从评论截图读出文字（复制粘贴）',
  nc_shot_mb_intro: '把第 1 步的指令复制到你常用的对话窗口，再附上评论区截图（注意：截图里会有评论者的昵称和头像，建议先裁掉左侧头像或打码），把返回的 JSON 贴回第 2 步。文字只会先放进评论框供你核对，不会直接入库。',
  nc_shot_warn_cloud: '发送前请确认：评论区截图里包含其他观众的昵称和头像（建议先裁剪遮挡），点「确认发送」会把所选图片发给你在「设置」里连接的 AI 服务。该识图接口按服务商文档编写，未在真实服务上联调。读出的评论只会先放进输入框供你核对，不会直接保存。',
  nc_shot_warn_local: '发送前请确认：评论区截图里包含其他观众的昵称和头像，点「确认发送」会把所选图片发给这台电脑上的本地模型服务。读出的评论只会先放进输入框供你核对，不会直接保存。',
  nc_shot_confirm_send: '确认发送截图',
  nc_shot_preview_banner: '已把截图里的 {n} 条评论放进下面的输入框（已去掉开头的昵称）。请先核对一遍，再点「数一数这些评论」保存。',
  nc_shot_too_many: '一次最多选 {max} 张截图。',
  nc_shot_too_big: '单张截图最多 {mb} MB，总大小最多 {total} MB。请先裁剪或压缩后再试。',
  nc_comments_ph: '把评论贴在这里，一行一条（开头的昵称会自动去掉，不会入库）…',
  nc_count_btn: '数一数这些评论', nc_model_btn: '让 AI 读一遍评论', nc_clear_btn: '清空已存评论',
  nc_clear_confirm: '要清空这条视频存下的所有评论和总结吗？', nc_clear_yes: '确认清空',
  nc_model_sends: '点「让 AI 读一遍评论」只会发送这条视频的标题、四项数据以及已存的评论正文（不含创作者档案、脚本正文或评论者昵称）。',
  nc_src_local: '按词表计数（未做语义判断）', nc_src_ai: 'AI 总结', nc_src_manual: '粘贴的 AI 回复',
  nc_sent_pos: '命中正面词 {n}%', nc_sent_neu: '未命中 {n}%', nc_sent_neg: '命中顾虑词 {n}%',
  nc_stored_list: '已存的评论正文（{n} 条，不含昵称）', nc_comment_likes: '赞 {n}',
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
  nc_f_shares: 'Shares', nc_f_new_followers: 'New followers', nc_f_click_rate_pct: 'Cover click rate',
  nc_f_avg_watch_sec: 'Avg watch time', nc_f_completion_rate_pct: 'Completion rate',
  nc_f_read_on: 'Day the numbers were read', nc_f_posted_on: 'Day the video was posted',
  nc_guess_note: 'The studio guessed these columns and may be wrong: this has not been tried on a real export. Check them against the rows below and change them if needed.',
  nc_dated_day: 'The table has no column for the day the numbers were read, so all of them are recorded as read today ({d}).',
  nc_dated_col: 'The numbers are recorded under the day given in column "{h}".',
  nc_posted_note: 'The day a video was posted is only shown as it was read. It is never taken for the day the numbers were read.',
  nc_th_title: 'Title', nc_th_read_on: 'Numbers read on', nc_th_effect: 'What it changes', nc_empty_cell: '(empty)', nc_was: 'was {n}',
  nc_sec_unit: '{n}s',
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
  nc_byhand_posted: 'Day this video was posted (optional)',
  nc_byhand_save: 'Save date', nc_byhand_saved: 'Posted date saved.',
  nc_script_posted: 'Posted on {d} (managed in its script\'s Posting record)',
  nc_pd_since_reading: 'From {a} to {b} ({k} days) it gained {n} views: {x} a day on average.',
  nc_pd_since_reading_one: 'From {a} to {b} (1 day) it gained {n} views.',
  nc_pd_since_posted: 'Posted on {p}; by {b}, {k} days later, it had {n} views: {x} a day on average.',
  nc_pd_since_posted_one: 'Posted on {p}; by {b}, 1 day later, it had {n} views.',
  nc_pd_none: 'The views of this video were read on one day only, so views per day cannot be worked out. Enter the numbers again on a later day, or import a table again.',
  nc_pd_no_views: 'No views are entered for this video yet.',
  nc_pd_avg_note: 'An average says nothing about any single day: views usually come fast at first and slowly later. The recorded numbers are only divided here; nothing is adjusted or projected.',
  nc_history: 'Numbers on record (the last {n} readings)', nc_history_one: 'Numbers on record (1 reading)',
  nc_shot_manual: 'Read screenshot by copy & paste',
  nc_shot_ai: 'Have AI read screenshot', nc_shot_reading: 'Reading screenshot…',
  nc_shot_hint: 'Text read from a screenshot is only placed into the box below first; nothing is saved until Count these comments is clicked. No commenter names are stored.',
  nc_shot_mb_title: 'Read comments from a screenshot (copy & paste)',
  nc_shot_mb_intro: 'Copy the prompt in step 1 into your chat window and attach the comment screenshot (note: screenshots show other people\'s nicknames and avatars—crop or mask them first if possible), then paste the JSON reply in step 2. Extracted text is only placed into the comment box for review.',
  nc_shot_warn_cloud: 'Before sending: comment screenshots contain other people\'s nicknames and avatars (crop them out first if possible). Clicking Confirm sends the chosen image(s) to the AI service configured in Settings. This vision call was written from provider documentation and has not been run against the live service. Extracted comments are only placed in the box below for review, not saved automatically.',
  nc_shot_warn_local: 'Before sending: comment screenshots contain other people\'s nicknames and avatars. Clicking Confirm sends the chosen image(s) to the local model server on this computer. Extracted comments are only placed in the box below for review, not saved automatically.',
  nc_shot_confirm_send: 'Confirm & send screenshot',
  nc_shot_preview_banner: 'Placed {n} comments from the screenshot into the box below (leading names stripped). Review them first, then click Count these comments to save.',
  nc_shot_preview_banner_one: 'Placed 1 comment from the screenshot into the box below (leading name stripped). Review it first, then click Count these comments to save.',
  nc_shot_too_many: 'Choose at most {max} screenshots at once.',
  nc_shot_too_big: 'Each screenshot can be at most {mb} MB ({total} MB in total). Crop or compress them first.',
  nc_comments_ph: 'Paste the comments here, one on each line (leading nicknames are stripped and never stored)…',
  nc_count_btn: 'Count these comments', nc_model_btn: 'Have an AI read the comments', nc_clear_btn: 'Clear saved comments',
  nc_clear_confirm: 'Remove all saved comments and the comment summary for this video?', nc_clear_yes: 'Confirm clear',
  nc_model_sends: 'Have an AI read the comments sends only this video\'s title, its four numbers, and the saved comment texts (no creator profile, script text, or commenter names).',
  nc_src_local: 'Counted by word lists (not judged)', nc_src_ai: 'Summarized by AI', nc_src_manual: 'Pasted AI reply',
  nc_sent_pos: 'Praise words {n}%', nc_sent_neu: 'Unmatched {n}%', nc_sent_neg: 'Concern words {n}%',
  nc_stored_list: 'Saved comment texts ({n}, no names stored)', nc_stored_list_one: 'Saved comment text (1, no names stored)',
  nc_comment_likes: '{n} likes', nc_comment_likes_one: '1 like',
});

const NC_MAX_FILE_BYTES = 8388608;   // must match MAX_FILE_BYTES in studio/keyword_import.py; tests/test_rednote_analytics.py checks it
const NC_MAX_SHOT_FILES = 4;
const NC_MAX_SHOT_BYTES = 4194304;
const NC_MAX_SHOT_TOTAL = 8388608;
const NC_LABEL_CHARS = 30;           // a header in a picker is cut to this; the server cuts the sample cells the same way
const NC_FIELDS = ['title', 'views', 'likes', 'collects', 'comments', 'shares', 'new_followers', 'click_rate_pct', 'avg_watch_sec', 'completion_rate_pct', 'read_on', 'posted_on'];   // FIELDS in studio/rednote_analytics.py
const NC_NUMBERS = ['views', 'likes', 'collects', 'comments'];
const NC_EXTRAS = ['shares', 'new_followers', 'click_rate_pct', 'avg_watch_sec', 'completion_rate_pct'];
const NC_RATES = ['click_rate_pct', 'completion_rate_pct'];
const ncUi = { open: false, sampleId: '', samples: [], file: null, preview: null, failed: null, seq: 0, draftText: '', shotCount: 0, pendingShots: null, confirmClear: false };

function ncResetImport() { ncUi.file = null; ncUi.preview = null; ncUi.failed = null; ncUi.seq += 1; }

function ncWhy(e) {
  if (e && e.key) return tf(e.key, { mb: NC_MAX_FILE_BYTES / 1048576 });
  return errorMessage(e);
}

function ncFormatMetric(v, f) {
  if (v === null || v === undefined) return '–';
  if (NC_RATES.includes(f)) return num(v) + '%';
  if (f === 'avg_watch_sec') return tf('nc_sec_unit', { n: num(v) });
  return num(v);
}

/* ---------- numbers from a table ---------- */
function ncColumnLabel(c) {
  const header = c.header.length > NC_LABEL_CHARS ? c.header.slice(0, NC_LABEL_CHARS) + '…' : c.header;
  const place = header ? tf('nc_col_named', { n: c.index + 1, h: header }) : tf('nc_col', { n: c.index + 1 });
  return place + (c.samples.length ? tf('nc_col_eg', { s: c.samples.join(t('nc_sep')) }) : '');
}

function ncEffectHtml(r, p) {
  if (r.effect === 'update') return esc(tf('nc_e_update', { t: r.sample_title }));
  if (r.effect === 'reading_only') return esc(tf('nc_e_reading_only', { t: r.sample_title, d: r.read_on }));
  if (r.effect === 'unmatched') return `<span class="tag warn">${esc(t('nc_e_unmatched'))}</span>`;
  return `<span class="tag">${esc(tf('nc_s_' + r.why, { max: p.limits.import_rows }))}</span>`;
}

function ncNumberCell(r, f) {
  if (r[f] === null || r[f] === undefined) return '–';
  const was = r.now && r.now[f] !== null && r.now[f] !== undefined && r.now[f] !== r[f] && r.effect === 'update'
    ? `<br><span class="meta">${esc(tf('nc_was', { n: ncFormatMetric(r.now[f], f) }))}</span>` : '';
  return esc(ncFormatMetric(r[f], f)) + was;
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
  const shownMetrics = NC_NUMBERS.concat(NC_EXTRAS.filter((k) => p.fields[k] !== null));
  const sheet = p.kind !== 'xlsx' ? '' : `<p class="meta">${esc(p.sheet ? tf('nc_sheet', { s: p.sheet }) : t('nc_sheet_unnamed'))}${
    p.other_sheets.length ? ' ' + esc(tf('nc_other_sheets', { s: p.other_sheets.join(t('nc_sep')) })) : ''}</p>`;
  const readHeader = p.fields.read_on === null ? '' : (p.headers[p.fields.read_on] || tf('nc_col', { n: p.fields.read_on + 1 }));
  const dated = p.dated_by === 'column' ? tf('nc_dated_col', { h: readHeader }) : tf('nc_dated_day', { d: p.import_day });
  const rows = p.rows.map((r) => `<tr${r.effect === 'update' || r.effect === 'reading_only' ? '' : ' class="ki-off"'} data-nc-row="${r.row}" data-nc-effect="${esc(r.why || r.effect)}">
      <td>${r.title ? esc(r.title) : `<span class="meta">${esc(t('nc_empty_cell'))}</span>`}</td>
      ${shownMetrics.map((n) => `<td style="white-space:nowrap">${ncNumberCell(r, n)}</td>`).join('')}
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
    <table class="perf" id="nc-imp-table"><thead><tr><th>${esc(t('nc_th_title'))}</th>${shownMetrics.map((n) => `<th style="white-space:nowrap">${esc(t('nc_f_' + n))}</th>`).join('')}
      <th style="white-space:nowrap">${esc(t('nc_th_read_on'))}</th><th style="text-align:left">${esc(t('nc_th_effect'))}</th></tr></thead>
      <tbody>${rows}</tbody></table></div>
    <p class="meta" id="nc-imp-counts">${esc(counts.join(lang === 'zh' ? '' : ' '))}</p>
    ${c.unmatched ? `<p class="hint" id="nc-imp-unmatched">${esc(t('nc_unmatched_how'))}</p>` : ''}
    <div class="row" style="margin-top:12px">
      <button type="button" class="btn primary" id="nc-imp-go"${p.would_change ? '' : ' disabled'}>${esc(tf(countKey('nc_go', p.would_change), { n: p.would_change }))}</button>
      <button type="button" class="btn" id="nc-imp-cancel">${esc(t('nc_cancel'))}</button></div>
  </div>`;
}

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
  if (file.size > NC_MAX_FILE_BYTES) { ncUi.failed = { key: 'nc_too_large' }; ncPaintImport(); return; }
  ncPaintImport();
  const seq = ncUi.seq;
  let base64;
  try { base64 = await fileToBase64(file); } catch (e) { if (seq === ncUi.seq) { ncUi.failed = e; ncPaintImport(); } return; }
  if (seq !== ncUi.seq) return;
  ncUi.file.base64 = base64;
  await ncAsk();
}

async function ncImport() {
  const f = ncUi.file;
  const p = ncUi.preview;
  let out;
  try {
    out = await api('POST', '/api/rednote/import', { name: f.name, file_base64: f.base64, fields: p.fields, fingerprint: p.fingerprint });
  } catch (e) {
    ncUi.failed = e;
    if (e.code === 'import_changed') { await ncAsk(p.fields); ncUi.failed = e; }
    ncPaintImport();
    return;
  }
  ncResetImport();
  toast(tf(countKey('nc_done', out.changed), { n: out.changed }) + (out.readings_only ? tf('nc_done_readings', { n: out.readings_only }) : ''));
  if (location.hash === '#/library') await renderLibrary();
}

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

/* ---------- one video: posted day & views per day ---------- */
function ncPostedRowHtml(detail) {
  if (!detail) return '';
  if (detail.from_script) {
    return detail.posted_on ? `<p class="meta" style="margin:8px 0 0">${esc(tf('nc_script_posted', { d: detail.posted_on }))}</p>` : '';
  }
  return `<div class="row" style="margin-top:10px;align-items:flex-end;gap:8px;flex-wrap:wrap">
    <label for="nc-posted-on" style="margin:0"><span class="meta">${esc(t('nc_byhand_posted'))}</span><br>
      <input type="date" id="nc-posted-on" value="${esc(detail.posted_on || '')}" style="width:170px"></label>
    <button type="button" class="btn" id="nc-posted-save">${esc(t('nc_byhand_save'))}</button>
  </div>`;
}

function ncPerDayHtml(d) {
  if (!d) return '';
  const lines = [];
  const r = d.since_reading;
  const p = d.since_posted;
  if (r) lines.push(tf(countKey('nc_pd_since_reading', r.days), { a: r.from, b: r.to, k: r.days, n: num(r.views), x: num(r.per_day) }));
  if (p) lines.push(tf(countKey('nc_pd_since_posted', p.days), { p: p.posted_on, b: p.to, k: p.days, n: num(p.views), x: num(p.per_day) }));
  const viewed = d.history.some((h) => h.views !== null);
  if (!r && !p) lines.push(t(viewed ? 'nc_pd_none' : 'nc_pd_no_views'));
  const histCols = NC_NUMBERS.concat(NC_EXTRAS.filter((k) => d.history.some((h) => h[k] !== null && h[k] !== undefined)));
  const history = d.history.length ? `<details><summary class="meta">${esc(tf(countKey('nc_history', d.history.length), { n: d.history.length }))}</summary>
    <table class="perf"><thead><tr><th>${esc(t('nc_th_read_on'))}</th>${histCols.map((n) => `<th>${esc(t('nc_f_' + n))}</th>`).join('')}</tr></thead>
    <tbody>${d.history.map((h) => `<tr><td>${esc(h.read_on)}</td>${histCols.map((n) => `<td>${esc(ncFormatMetric(h[n], n))}</td>`).join('')}</tr>`).join('')}</tbody></table></details>` : '';
  return `<p id="nc-per-day" style="margin:10px 0 0">${esc(lines.join(lang === 'zh' ? '' : ' '))}</p>
    ${r || p ? `<p class="hint">${esc(t('nc_pd_avg_note'))}</p>` : ''}${history}`;
}

function ncPlaceExtractedLines(out) {
  if (!out || !out.text) return;
  ncUi.draftText = ncUi.draftText && ncUi.draftText.trim() ? (ncUi.draftText.trim() + '\n' + out.text) : out.text;
  ncUi.shotCount = out.count || out.lines.length || 1;
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
  if (!document.body.contains(box)) return;
  const sent = detail && detail.sentiment;
  const src = (detail && detail.sentiment_source) || 'local';
  const srcKey = src === 'ai' ? 'nc_src_ai' : (src === 'manual' ? 'nc_src_manual' : 'nc_src_local');
  const pct = (sent && sent.sentiment_breakdown_pct) || { positive: 0, neutral: 0, negative: 0 };
  const qs = (sent && sent.audience_questions) || [];
  const storedComments = (detail && detail.comments) || [];
  const canVisionAi = !!(appState.settings && appState.settings.configured && !appState.settings.manual);
  const shotWarnKey = (appState.settings && appState.settings.provider === 'local') ? 'nc_shot_warn_local' : 'nc_shot_warn_cloud';
  const pendingShotHtml = ncUi.pendingShots && ncUi.pendingShots.length ? `<div class="card" id="nc-shot-confirm-card" style="margin-top:8px">
      <p class="meta" style="margin:0 0 6px">${esc(ncUi.pendingShots.map((f) => f.name).join(t('nc_sep')))}</p>
      <p style="margin:0 0 10px">${esc(t(shotWarnKey))}</p>
      <div class="row" style="gap:8px;flex-wrap:wrap">
        <button type="button" class="btn primary" id="nc-shot-send">${esc(t('nc_shot_confirm_send'))}</button>
        <button type="button" class="btn" id="nc-shot-cancel">${esc(t('nc_cancel'))}</button>
      </div>
    </div>` : '';
  const clearConfirmHtml = ncUi.confirmClear ? `<div class="banner" id="nc-clear-box" style="margin-top:8px">
      <span>${esc(t('nc_clear_confirm'))}</span>
      <div class="row" style="margin-top:8px;gap:8px">
        <button type="button" class="btn" id="nc-clear-yes">${esc(t('nc_clear_yes'))}</button>
        <button type="button" class="btn ghost" id="nc-clear-no">${esc(t('nc_cancel'))}</button>
      </div>
    </div>` : '';
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
    ${ncPostedRowHtml(detail)}
    ${ncPerDayHtml(detail && detail.per_day)}
    <div class="row" style="margin-top:14px;flex-wrap:wrap;gap:8px">
      <button type="button" class="btn" id="nc-shot-manual">${esc(t('nc_shot_manual'))}</button>
      ${canVisionAi ? `<label class="btn" for="nc-shot-file" id="nc-shot-ai-lbl">${esc(t('nc_shot_ai'))}</label>
        <input type="file" id="nc-shot-file" accept=".png,.jpg,.jpeg,.webp" multiple hidden>` : ''}
      <span class="hint ki-hint">${esc(t('nc_shot_hint'))}</span>
    </div>
    ${pendingShotHtml}
    ${ncUi.shotCount ? `<div class="banner" id="nc-shot-banner" role="status" style="margin-top:8px">${esc(tf(countKey('nc_shot_preview_banner', ncUi.shotCount), { n: ncUi.shotCount }))}</div>` : ''}
    <textarea id="nc-comments-in" rows="4" style="margin-top:10px" placeholder="${esc(t('nc_comments_ph'))}">${esc(ncUi.draftText)}</textarea>
    <div class="row" style="margin-top:10px;flex-wrap:wrap;gap:8px">
      <button type="button" class="btn primary" id="nc-count">${esc(t('nc_count_btn'))}</button>
      ${storedComments.length ? `<button type="button" class="btn" id="nc-model">${esc(t('nc_model_btn'))}</button>
        <button type="button" class="btn ghost" id="nc-clear-comments">${esc(t('nc_clear_btn'))}</button>` : ''}
    </div>
    ${clearConfirmHtml}
    ${storedComments.length ? `<p class="hint" style="margin-top:6px">${esc(t('nc_model_sends'))}</p>` : ''}
    ${sent && sent.comment_count > 0 ? `<div class="card" id="nc-sent-card" style="margin-top:14px">
      <div class="row" style="flex-wrap:wrap;gap:6px">
        <span class="tag">${esc(t(srcKey))}</span>
        <span class="tag ok">${esc(tf('nc_sent_pos', { n: pct.positive }))}</span>
        <span class="tag">${esc(tf('nc_sent_neu', { n: pct.neutral }))}</span>
        <span class="tag ${pct.negative > 20 ? 'no' : ''}">${esc(tf('nc_sent_neg', { n: pct.negative }))}</span>
      </div>
      <p style="margin:8px 0">${esc(lang === 'zh' ? sent.summary_zh : (sent.summary_en || sent.summary_zh))}</p>
      ${qs.map((q) => `<div class="meta">${esc(lang === 'zh' ? q.question_zh : (q.question_en || q.question_zh))}</div>`).join('')}
      ${storedComments.length ? `<details style="margin-top:8px"><summary class="meta">${esc(tf(countKey('nc_stored_list', storedComments.length), { n: storedComments.length }))}</summary>
        ${storedComments.map((c) => `<div class="meta" style="margin-top:4px">${esc(c.text)}${c.likes ? ' · ' + esc(tf(countKey('nc_comment_likes', c.likes), { n: num(c.likes) })) : ''}</div>`).join('')}</details>` : ''}
    </div>` : ''}
  </details>`;

  const $card = document.getElementById('nc-card');
  $card.ontoggle = () => { ncUi.open = $card.open; };
  const $file = document.getElementById('nc-imp-file');
  $file.onchange = () => {
    const file = $file.files[0];
    $file.value = '';
    if (file) ncChoose(file);
  };
  ncPaintImport();
  const $sel = document.getElementById('nc-sample');
  $sel.onchange = () => { ncUi.sampleId = $sel.value; ncUi.draftText = ''; ncUi.shotCount = 0; ncUi.pendingShots = null; ncUi.confirmClear = false; ncDraw(); };
  const $postedSave = document.getElementById('nc-posted-save');
  if ($postedSave) {
    $postedSave.onclick = async () => {
      try {
        const val = document.getElementById('nc-posted-on').value;
        await api('PUT', '/api/samples/' + ncUi.sampleId + '/posted-on', { posted_on: val });
        toast(t('nc_byhand_saved'));
        await ncDraw();
      } catch (e) { showError(e); }
    };
  }
  const $in = document.getElementById('nc-comments-in');
  $in.oninput = () => { ncUi.draftText = $in.value; };
  document.getElementById('nc-shot-manual').onclick = async () => {
    try {
      const out = await manualBridge('comment_ocr', {}, { title: t('nc_shot_mb_title'), intro: t('nc_shot_mb_intro') });
      if (!out) return;
      ncPlaceExtractedLines(out);
      await ncDraw();
    } catch (e) { showError(e); }
  };
  const $shotFile = document.getElementById('nc-shot-file');
  if ($shotFile) {
    $shotFile.onchange = async () => {
      const files = Array.from($shotFile.files || []);
      $shotFile.value = '';
      if (!files.length) return;
      if (files.length > NC_MAX_SHOT_FILES) { toast(tf('nc_shot_too_many', { max: NC_MAX_SHOT_FILES })); return; }
      let sumBytes = 0;
      for (const f of files) {
        sumBytes += f.size;
        if (f.size > NC_MAX_SHOT_BYTES || sumBytes > NC_MAX_SHOT_TOTAL) {
          toast(tf('nc_shot_too_big', { mb: NC_MAX_SHOT_BYTES / 1048576, total: NC_MAX_SHOT_TOTAL / 1048576 }));
          return;
        }
      }
      ncUi.pendingShots = files;
      await ncDraw();
    };
  }
  const $shotCancel = document.getElementById('nc-shot-cancel');
  if ($shotCancel) $shotCancel.onclick = () => { ncUi.pendingShots = null; ncDraw(); };
  const $shotSend = document.getElementById('nc-shot-send');
  if ($shotSend) {
    $shotSend.onclick = () => busy($shotSend, t('nc_shot_reading'), async () => {
      const files = ncUi.pendingShots || [];
      try {
        const images = [];
        for (const f of files) images.push({ name: f.name, data_base64: await fileToBase64(f) });
        const out = await api('POST', '/api/rednote/comments/ocr', { images, confirmed_privacy: true });
        ncUi.pendingShots = null;
        ncPlaceExtractedLines(out);
        await ncDraw();
      } catch (e) {
        showError(e);
      }
    });
  }
  document.getElementById('nc-count').onclick = async () => {
    try {
      await api('POST', '/api/samples/' + ncUi.sampleId + '/comments', { text: document.getElementById('nc-comments-in').value, replace: false });
      ncUi.draftText = '';
      ncUi.shotCount = 0;
      await ncDraw();
    } catch (e) { showError(e); }
  };
  const $clear = document.getElementById('nc-clear-comments');
  if ($clear) $clear.onclick = () => { ncUi.confirmClear = true; ncDraw(); };
  const $clearNo = document.getElementById('nc-clear-no');
  if ($clearNo) $clearNo.onclick = () => { ncUi.confirmClear = false; ncDraw(); };
  const $clearYes = document.getElementById('nc-clear-yes');
  if ($clearYes) {
    $clearYes.onclick = async () => {
      try {
        await api('DELETE', '/api/samples/' + ncUi.sampleId + '/comments');
        ncUi.confirmClear = false;
        await ncDraw();
      } catch (e) { showError(e); }
    };
  }
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

async function plPaintLibraryTools(box, samples) {
  if (!box) return;
  ncUi.samples = samples || [];
  if (!ncUi.samples.length) { box.innerHTML = ''; return; }
  if (!ncUi.samples.some((s) => s.id === ncUi.sampleId)) ncUi.sampleId = ncUi.samples[0].id;
  await ncDraw();
}
