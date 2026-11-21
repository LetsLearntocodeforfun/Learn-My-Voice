'use strict';
/* A brand's brief for a sponsored video (studio/sponsored.py). Loaded before pages.js.

   Three places in the app use this file:
   - #/new (spPaintEntry): a quiet card that opens #/sponsored without adding sidebar items;
   - #/sponsored and #/sponsored/<id> (renderSponsored): add a brief (.txt, .md, .docx or pasted text; never PDF),
     read the privacy disclosure, translate the brief into plain words (via the configured AI service or manual
     copy-and-paste), inspect code-checked grounding ("not found in the brief") and advertising word-list flags,
     choose or write an angle and switch requirements on/off, then draft a SPONSORED script;
   - #/s/<id> (spAttach, spAfterSave): the checklist and guardrail panel beside a sponsored script, counting
     required phrases in spoken lines, forbidden phrases across the script, required tags/mentions, creator ticks,
     and compliance findings from the project's word list (with its caveat that it is not legal advice).

   Everything from data goes through esc(). No inline handlers, no form elements, no outside requests. */

Object.assign(I18N.zh, {
  sp_entry_title: '按品牌 Brief 写商单脚本',
  sp_entry_sub: '先把品牌的合作要求拆成大白话、核对原文出处与违禁词，选好你自己的切入点后再写初稿。写出来的脚本会固定标为「商单」，不会混进你的声音档案。',
  sp_entry_btn: '打开商单 Brief 工作台',
  sp_back_new: '← 返回新建脚本',
  sp_back_list: '← 返回 Brief 列表',
  sp_title: '商单 Brief',
  sp_sub: '把品牌发来的合作要求变成大白话清单和切入点，由你拍板后再写初稿。',
  sp_priv_title: '隐私与文件说明（发送前请先看）',
  sp_priv_conf: '品牌 Brief 通常是保密文本。保存在这里的 Brief 会存进当前数据文件夹的数据库（最多 {n} 份），并随备份一起打包；删除 Brief 时会覆盖数据库里的原文，之后的新备份不再包含它，但已经生成的脚本仍保持「商单」标记。',
  sp_priv_files: '支持直接粘贴文字，或选择 .txt、.md、.docx 文件（最大 {mb} MB，最多 {chars} 个字符）。不支持 PDF（请打开 PDF 复制文字贴进来，或另存为 .docx）。',
  sp_priv_dest_cloud: '当前 AI 服务：{p}（模型：{m}）。点击「解读 Brief」会把 Brief 全文、你在设置里填的创作者资料和声音档案发给该服务（不发历史脚本）。',
  sp_priv_dest_local: '当前本地模型：{p}（地址：{addr}，模型：{m}）。点击「解读 Brief」会把 Brief 全文、创作者资料和声音档案发给这台电脑上的模型服务。',
  sp_priv_dest_manual: '当前为复制粘贴模式：工作台不会主动联网发送。点击按钮后会把 Brief 全文、创作者资料和声音档案放进提示词框，只有你自己粘贴到聊天窗口时才会发出去。',
  sp_priv_dest_none: '当前还没有配置 AI 服务。你可以先把 Brief 保存下来查看词表提示，再去「设置」连接 AI 或切换到复制粘贴模式。',
  sp_priv_draft_note: '写脚本初稿时，提示词里只包含你确认过的切入点和要求清单（绝不包含 Brief 原文），连同创作者资料、声音档案、非商单范文和改稿经验一起发给上面写明的 AI 服务（或复制粘贴框）。',
  sp_add_h: '添加一份品牌 Brief',
  sp_title_label: '名称（可不填，默认取文件名或第一行）',
  sp_title_ph: '例如：秋冬保湿面霜合作 Brief',
  sp_file_btn: '选择 .txt / .md / .docx 文件',
  sp_file_drop: '或把文件拖到这里（不支持 PDF）',
  sp_paste_label: '或者直接粘贴 Brief 内容',
  sp_paste_ph: '把品牌给的合作要求贴在这里…',
  sp_add_btn: '保存这份 Brief',
  sp_adding: '正在保存…',
  sp_added_stripped: '已保存（自动去掉了 {n} 个不可见控制字符）',
  sp_added_ok: 'Brief 已保存',
  sp_file_too_big: '文件超过了 {mb} MB 的上限。请复制里面的文字直接粘贴。',
  sp_list_h: '已保存的 Brief',
  sp_empty: '还没有保存过品牌 Brief。在上面粘贴文字或选择文件即可开始。',
  sp_chars_n: '{n} 字',
  sp_state_unread: '待解读',
  sp_state_read: '已解读',
  sp_state_chosen: '已选切入点',
  sp_open_brief: '查看与操作',
  sp_open_script: '打开已生成的脚本：{title}',
  sp_del_confirm: '确定删除「{title}」吗？Brief 原文、AI 解读和勾选清单都会从数据库中抹掉；如果已经生成过脚本，脚本本身会保留并继续标为商单。',
  sp_cut_warn: '这份记录超出了 {n} 字上限，只显示并处理前 {n} 字。',
  sp_read_h: '第一步：把 Brief 拆成大白话与要求清单',
  sp_read_btn: '✨ 用 AI 解读这份 Brief',
  sp_read_again: '重新解读',
  sp_read_manual: '✨ 用复制粘贴模式解读 Brief',
  sp_reading_busy: '正在解读 Brief…',
  sp_import_read: '粘贴已有的解读 JSON',
  sp_mb_read_title: '解读品牌 Brief（复制粘贴模式）',
  sp_mb_read_intro: '这段提示词包含这份品牌 Brief 的全文、你的创作者资料和声音档案（不含历史脚本）。品牌 Brief 通常是保密内容，请确认你要粘贴到的聊天服务后再发送。',
  sp_mb_draft_title: '写商单脚本初稿（复制粘贴模式）',
  sp_mb_draft_intro: '这段提示词包含你确认的切入点和要求清单（不含 Brief 原文）、创作者资料、声音档案、非商单范文和改稿经验。',
  sp_brief_fold: 'Brief 原文与广告词表检查（{n} 字）',
  sp_flags_h: '广告用词词表在 Brief 原文里标出的词',
  sp_flags_none: '词表在这份 Brief 原文里没有标出条目。',
  sp_flags_count: '共 {n} 处',
  sp_flags_more: '另有 {n} 组词未展开。',
  sp_flag_line: '第 {n} 行：{text}',
  sp_digest_h: '大白话摘要',
  sp_brand: '品牌',
  sp_product: '产品',
  sp_name_not_in_brief: 'Brief 原文未出现这个写法',
  sp_points_h: '卖点换成平时说话的说法',
  sp_points_none: '没有列出卖点。',
  sp_in_brief: '原文已核对',
  sp_not_in_brief: '在 Brief 原文中没找到',
  sp_not_in_brief_hint: 'AI 引用的这句话在 Brief 里没找到，所以默认关闭，请核对原文后再决定要不要开：',
  sp_reqs_h: '第二步：核对要求清单（由你决定哪些算数）',
  sp_reqs_hint: '每一条都拿 AI 引用的句子去 Brief 原文里核对过：没找到的默认关掉。只有勾选开启的条目才会写进脚本提示词和编辑器核对清单。',
  sp_reqs_none: 'AI 没有提取到具体要求，你可以在下面自己添加。',
  sp_left_out_note: 'AI 回复里有部分超出上限或格式无效的条目未列入（要求 {r} 条、卖点 {p} 条、切入点 {w} 个）。',
  sp_kind_must_say: '口播必提',
  sp_kind_must_show: '画面必拍',
  sp_kind_must_not_say: '绝对别说',
  sp_kind_fact: '产品事实',
  sp_kind_tag: '必带话题 #',
  sp_kind_mention: '必 @ 账号',
  sp_kind_term: '合作条款 / 时长档期',
  sp_phrases_label: '程序按字面核对的词句（可取消不需要的）：',
  sp_phrase_not_in_brief: '不在 Brief 原文里',
  sp_own_h: '自己补充要求',
  sp_own_hint: 'Brief 里漏掉的、或者品牌临时在微信里补的要求，可以自己加进来。',
  sp_own_text_ph: '一句话写清楚要求，例如：口播里要提到「早八人救星」',
  sp_own_phrases_ph: '要按字面检查的词句（可不填，多个用逗号隔开，最多 6 个）',
  sp_own_add: '添加这条要求',
  sp_own_SourceTag: '自己加的',
  sp_ways_h: '第三步：选一个切入点（或者自己写）',
  sp_ways_hint: '选一个最像你平时视频开头的切入方式，或者直接写你自己的想法。写初稿时不会把 Brief 原文喂给 AI。',
  sp_way_pick: '用这个切入点',
  sp_way_opening: '开场草稿：',
  sp_way_bridge: '转到产品：',
  sp_way_own_label: '不用上面的，我自己写切入点',
  sp_way_own_ph: '写一两句：这条视频打算从什么日常场景说起、怎么自然带出产品…',
  sp_draft_h: '第四步：保存决定并生成商单初稿',
  sp_save_choice: '只保存上面的选择',
  sp_choice_saved: '切入点与要求清单已保存',
  sp_draft_btn: '✨ 按上面的选择写商单初稿',
  sp_draft_manual: '✨ 用复制粘贴模式写商单初稿',
  sp_drafting: '正在写初稿…',
  sp_confirm_redraft: '这份 Brief 已经生成过一篇脚本了。再生成一篇新的商单脚本，会把核对清单移到新脚本旁边（旧脚本本身还会保留，仍标为商单）。继续吗？',
  sp_ed_title: '商单要求与用词核对',
  sp_ed_brief_link: '查看原 Brief 与选择',
  sp_ed_deleted: '这篇脚本是从品牌 Brief 写出来的（原 Brief 已删除）。它继续固定标为「商单」：发布后不会进入核心声音样本，这里的修改也不会记入改稿经验。',
  sp_ed_loading: '正在核对商单要求…',
  sp_ed_said_ok: '必提已齐 {k}/{n}',
  sp_ed_said_miss: '必提还差 {m}/{n}',
  sp_ed_forbid_ok: '禁说词 0 处',
  sp_ed_forbid_hit: '出现禁说词 {n} 处',
  sp_ed_tags_ok: '话题/@ 已齐',
  sp_ed_tags_miss: '缺话题/@ {n} 个',
  sp_ed_check_left: '待你自查 {n} 项',
  sp_ed_check_done: '自查项已打勾',
  sp_ed_sec_say: '口播必提（程序按字面在口播台词里找）',
  sp_ed_line_hit: '第 {n} 句：「{w}」',
  sp_ed_line_span: '第 {a}–{b} 句：「{w}」',
  sp_ed_live_note: '（注意：这一句是现场原声 LIVE_CLIP）',
  sp_ed_missing_phrase: '还没提到：「{p}」',
  sp_ed_sec_forbid: '绝对别说（检查口播、花字、标题、正文、话题）',
  sp_ed_forbid_clean: '未出现',
  sp_ed_forbid_where: '{where}出现了「{p}」：{w}',
  sp_ed_forbid_more: '另有更多处未展开。',
  sp_where_spoken: '口播第 {n} 句',
  sp_where_onscreen: '花字第 {n} 句',
  sp_where_title: '备选标题',
  sp_where_caption: '发布正文',
  sp_where_hashtags: '发布话题',
  sp_ed_sec_tags: '必带话题与 @ 账号（检查发布文案）',
  sp_ed_no_post: '这篇脚本还没有填发布正文或话题，请在下方「发布文案」里补上。',
  sp_ed_tag_found: '{p}（已在{w}）',
  sp_ed_tag_missing: '缺少 {p}',
  sp_in_caption: '正文',
  sp_in_hashtags: '话题栏',
  sp_ed_sec_you: '需要你自己核对的项（画面、事实、无固定字面的要点、档期）',
  sp_ed_sec_wl: '本脚本的广告用词提示（项目自带词表）',
  sp_ed_wl_clean: '当前脚本没有触发广告用词词表中的条目。',
  sp_ed_left_out: '脚本很长，只检查了前一部分文字。',
});

Object.assign(I18N.en, {
  sp_entry_title: 'Writing from a brand\'s brief?',
  sp_entry_sub: 'Put the collaboration brief into plain words first, check every requirement against the brief\'s text and the advertising word list, then pick {@names} own way in before drafting. Scripts drafted here stay marked as sponsored so brand wording never enters {@names} voice profile.',
  sp_entry_btn: 'Open sponsored briefs',
  sp_back_new: '← Back to new script',
  sp_back_list: '← Back to briefs',
  sp_title: 'Sponsored briefs',
  sp_sub: 'Turn a brand\'s brief into a plain-language summary, a verified checklist and a way in chosen by {@name} before drafting.',
  sp_priv_title: 'Privacy & file types (read before sending)',
  sp_priv_conf: 'A brand\'s brief is confidential text. Saved briefs live in this folder\'s database (up to {n}) and are included in backups. Deleting a brief overwrites its text in the database so later backups no longer carry it, while any script drafted from it stays marked as sponsored.',
  sp_priv_files: 'Paste text directly, or upload a .txt, .md or .docx file (up to {mb} MB, at most {chars} characters). PDFs are not supported — open a PDF and copy the text, or save it as .docx.',
  sp_priv_dest_cloud: 'Configured AI service: {p} (model: {m}). Translating/reading sends the brief\'s text, the creator profile from setup and {@names} voice profile to that service (never past scripts).',
  sp_priv_dest_local: 'Configured local model: {p} (at {addr}, model: {m}). Translating/reading sends the brief\'s text, the creator profile and {@names} voice profile to that local server on this computer.',
  sp_priv_dest_manual: 'Copy-and-paste mode is on: nothing is sent over the network by the app. Translating/reading puts the brief\'s text, the creator profile and {@names} voice profile into the prompt box, and it goes only where you paste it.',
  sp_priv_dest_none: 'No AI service is configured yet. You can still save the brief here to see word-list flags, then connect an AI or switch to copy-and-paste mode in Settings.',
  sp_priv_draft_note: 'When drafting the script, only the way in and requirements accepted below are put into the prompt — never the raw brief text — together with the creator profile, {@names} voice profile, non-sponsored examples and remembered lessons, sent to the same service (or prompt box).',
  sp_add_h: 'Add a brand\'s brief',
  sp_title_label: 'Title (optional; defaults to the file name or first line)',
  sp_title_ph: 'e.g. Autumn moisturizer brief',
  sp_file_btn: 'Choose .txt / .md / .docx file',
  sp_file_drop: 'or drop a file here (no PDF)',
  sp_paste_label: 'Or paste the brief\'s text',
  sp_paste_ph: 'Paste the brand\'s collaboration brief here…',
  sp_add_btn: 'Save this brief',
  sp_adding: 'Saving…',
  sp_added_stripped: 'Saved ({n} invisible control characters removed)',
  sp_added_ok: 'Brief saved',
  sp_file_too_big: 'This file is over the {mb} MB limit. Copy the text out of it and paste it instead.',
  sp_list_h: 'Saved briefs',
  sp_empty: 'No brand briefs saved yet. Paste text or choose a file above to start.',
  sp_chars_n: '{n} chars',
  sp_state_unread: 'Not read yet',
  sp_state_read: 'Read',
  sp_state_chosen: 'Way in chosen',
  sp_open_brief: 'Open',
  sp_open_script: 'Open drafted script: {title}',
  sp_del_confirm: 'Delete "{title}"? The brief\'s text, AI reading and checklist will be overwritten in the database. Any script already drafted from it stays and remains marked as sponsored.',
  sp_cut_warn: 'This record exceeded the {n}-character limit; only the first {n} characters are shown and checked.',
  sp_read_h: 'Step 1: Read the brief into plain words and requirements',
  sp_read_btn: '✨ Read this brief with AI',
  sp_read_again: 'Read again',
  sp_read_manual: '✨ Read this brief (copy & paste)',
  sp_reading_busy: 'Reading the brief…',
  sp_import_read: 'Paste an existing reading JSON',
  sp_mb_read_title: 'Read a brand\'s brief (copy & paste)',
  sp_mb_read_intro: 'This prompt contains the full text of the brand\'s brief, the creator profile from setup and {@names} voice profile (none of {@their} past scripts). A brief is confidential: check which chat service you paste it into.',
  sp_mb_draft_title: 'Draft a sponsored script (copy & paste)',
  sp_mb_draft_intro: 'This prompt contains only the way in and requirements accepted from the brief (never the brief\'s raw text), plus the creator profile, {@names} voice profile, non-sponsored examples and remembered lessons.',
  sp_brief_fold: 'Brief text & advertising word-list check ({n} chars)',
  sp_flags_h: 'Wording in the brief flagged by the project\'s word list',
  sp_flags_none: 'Nothing in the brief\'s text was flagged by the active word list.',
  sp_flags_count: '{n} places',
  sp_flags_more: '{n} more flagged wordings are not listed.',
  sp_flag_line: 'Line {n}: {text}',
  sp_digest_h: 'Plain-language digest',
  sp_brand: 'Brand',
  sp_product: 'Product',
  sp_name_not_in_brief: 'exact spelling not found in brief',
  sp_points_h: 'Selling points in everyday words',
  sp_points_none: 'No selling points listed.',
  sp_in_brief: 'Found in brief',
  sp_not_in_brief: 'Not found in the brief',
  sp_not_in_brief_hint: 'The passage the AI cited was not found in the brief, so this starts switched off. Check the brief before enabling it:',
  sp_reqs_h: 'Step 2: Check the requirements ({@name} decides what counts)',
  sp_reqs_hint: 'Every requirement\'s cited quote was looked up in the brief: anything not found starts switched off. Only switched-on items go into the draft prompt and the editor checklist.',
  sp_reqs_none: 'The AI listed no requirements. Add any of {@names} own below.',
  sp_left_out_note: 'Some excess or unusable entries in the AI reply were left out (requirements: {r}, points: {p}, ways in: {w}).',
  sp_kind_must_say: 'Must say',
  sp_kind_must_show: 'Must show',
  sp_kind_must_not_say: 'Must not say',
  sp_kind_fact: 'Product fact',
  sp_kind_tag: 'Required #tag',
  sp_kind_mention: 'Required @mention',
  sp_kind_term: 'Term / length / date',
  sp_phrases_label: 'Exact phrases checked by code (uncheck any that are not required word-for-word):',
  sp_phrase_not_in_brief: 'not in brief',
  sp_own_h: 'Add {@names} own requirement',
  sp_own_hint: 'Add anything missed or sent separately by the brand.',
  sp_own_text_ph: 'One sentence saying what is required, e.g. Mention "24-hour moisture"',
  sp_own_phrases_ph: 'Exact phrases to check (optional, comma-separated, up to 6)',
  sp_own_add: 'Add requirement',
  sp_own_SourceTag: 'Added by {@name}',
  sp_ways_h: 'Step 3: Pick a way in (or write {@names} own)',
  sp_ways_hint: 'Choose one angle for how the product enters an ordinary video, or write {@names} own. Drafting never sends the raw brief to the AI.',
  sp_way_pick: 'Use this way in',
  sp_way_opening: 'Opening sketch: ',
  sp_way_bridge: 'Bridge to product: ',
  sp_way_own_label: 'Write {@names} own way in instead',
  sp_way_own_ph: 'One or two sentences: what everyday moment the video starts from and how the product comes in…',
  sp_draft_h: 'Step 4: Save choice & draft sponsored script',
  sp_save_choice: 'Save choice only',
  sp_choice_saved: 'Choice and checklist saved',
  sp_draft_btn: '✨ Draft sponsored script from this choice',
  sp_draft_manual: '✨ Draft sponsored script (copy & paste)',
  sp_drafting: 'Drafting script…',
  sp_confirm_redraft: 'A script was already drafted from this brief. Drafting another moves the checklist to the new script (the earlier script stays and remains marked as sponsored). Continue?',
  sp_ed_title: 'Sponsored checklist & word-list guardrails',
  sp_ed_brief_link: 'View brief & choice',
  sp_ed_deleted: 'This script was drafted from a brand\'s brief that has since been deleted. It remains marked as sponsored: posting it will not add its wording to {@names} core voice material, and edits here are not remembered as lessons.',
  sp_ed_loading: 'Checking sponsored requirements…',
  sp_ed_said_ok: 'Must-say {k}/{n}',
  sp_ed_said_miss: 'Missing {m}/{n} must-say',
  sp_ed_forbid_ok: '0 forbidden hits',
  sp_ed_forbid_hit: '{n} forbidden hits',
  sp_ed_tags_ok: 'Tags/@ present',
  sp_ed_tags_miss: '{n} tags/@ missing',
  sp_ed_check_left: '{n} for {@name} to check',
  sp_ed_check_done: 'Manual checks ticked',
  sp_ed_sec_say: 'Required mentions in spoken lines (counted by code)',
  sp_ed_line_hit: 'Line {n}: "{w}"',
  sp_ed_line_span: 'Lines {a}–{b}: "{w}"',
  sp_ed_live_note: ' (in a LIVE_CLIP line)',
  sp_ed_missing_phrase: 'Missing: "{p}"',
  sp_ed_sec_forbid: 'Things not to say (checked across spoken lines, on-screen text, titles, caption & hashtags)',
  sp_ed_forbid_clean: 'None found',
  sp_ed_forbid_where: '{where} has "{p}": {w}',
  sp_ed_forbid_more: 'More hits are not listed.',
  sp_where_spoken: 'Spoken line {n}',
  sp_where_onscreen: 'On-screen text line {n}',
  sp_where_title: 'Title options',
  sp_where_caption: 'Caption',
  sp_where_hashtags: 'Hashtags',
  sp_ed_sec_tags: 'Required hashtags & @mentions (checked in post copy)',
  sp_ed_no_post: 'Post copy is empty; add caption and hashtags in Post copy below.',
  sp_ed_tag_found: '{p} (in {w})',
  sp_ed_tag_missing: 'Missing {p}',
  sp_in_caption: 'caption',
  sp_in_hashtags: 'hashtags',
  sp_ed_sec_you: 'For {@name} to check (shots, facts, unphrased points, terms)',
  sp_ed_sec_wl: 'Advertising word-list flags on this script',
  sp_ed_wl_clean: 'No compliance entry from the active word list matched this script.',
  sp_ed_left_out: 'Long script: only the first part of its text was checked.',
});

const SP_PHRASE_KINDS = ['must_say', 'must_not_say', 'tag', 'mention'];
const SP_KINDS = ['must_say', 'must_show', 'must_not_say', 'fact', 'tag', 'mention', 'term'];

/* Local form state for adding a brief and for editing a brief's choice. */
const spAddForm = { title: '', text: '' };
const spDecision = {
  briefId: null, readingAt: null, wayId: '', ownWay: '',
  reqOn: {}, reqPhrases: {}, ownReqs: [],
  newKind: 'must_say', newText: '', newPhrases: '',
  targetSec: 60, videoFormat: 'VLOG_VOICEOVER',
};
/* State for the checklist panel beside the script editor (#/s/<id>). */
const spEdUi = { scriptId: null, page: null, data: null, loading: false, seq: 0, open: true };

async function spFileToBase64(file) {
  const bytes = new Uint8Array(await file.arrayBuffer());
  let binary = '';
  const step = 0x8000;
  for (let i = 0; i < bytes.length; i += step) {
    binary += String.fromCharCode.apply(null, bytes.subarray(i, i + step));
  }
  return btoa(binary);
}

function spSplitPhrases(raw) {
  return String(raw || '').split(/[,，\n]+/).map((s) => s.trim()).filter(Boolean);
}

function spServiceLine(srv) {
  if (!srv) return '';
  if (srv.where === 'manual') return t('sp_priv_dest_manual');
  if (srv.where === 'local') {
    return tf('sp_priv_dest_local', { p: srv.provider, addr: srv.address || 'localhost', m: srv.model || '–' });
  }
  if (srv.where === 'cloud' && srv.configured) {
    return tf('sp_priv_dest_cloud', { p: srv.provider, m: srv.model || '–' });
  }
  return t('sp_priv_dest_none');
}

function spPrivacyHtml(limits, service, includeDraftNote) {
  const lim = limits || { briefs: 30, file_bytes: 8 * 1024 * 1024, brief_chars: 20000 };
  const mb = Math.round((lim.file_bytes || 8388608) / (1024 * 1024));
  return `<div class="card soft" id="sp-privacy">
    <h3>${esc(t('sp_priv_title'))}</h3>
    <p class="hint" style="margin:6px 0">${esc(tf('sp_priv_conf', { n: lim.briefs || 30 }))}</p>
    <p class="hint" style="margin:6px 0">${esc(tf('sp_priv_files', { mb, chars: num(lim.brief_chars || 20000) }))}</p>
    <p class="hint" style="margin:6px 0" data-sp-dest><b>${esc(spServiceLine(service))}</b>${
      !service || (!service.configured && service.where !== 'manual')
        ? ` <a href="#/settings">${esc(t('go_settings'))}</a>` : ''}</p>
    ${includeDraftNote ? `<p class="hint" style="margin:6px 0 0">${esc(t('sp_priv_draft_note'))}</p>` : ''}
  </div>`;
}

/* Entry card on #/new (no sidebar link). */
function spPaintEntry(el) {
  if (!el) return;
  el.innerHTML = `<div class="card soft" style="margin:14px 0 18px">
    <div class="row">
      <div style="flex:1;min-width:220px">
        <h3 style="margin:0 0 4px">${esc(t('sp_entry_title'))}</h3>
        <p class="hint" style="margin:0">${esc(t('sp_entry_sub'))}</p>
      </div>
      <a class="btn" href="#/sponsored" id="sp-open-btn">${esc(t('sp_entry_btn'))}</a>
    </div>
  </div>`;
}

/* Sync spDecision from a brief payload when opening a brief or receiving a new reading. */
function spSyncDecision(p) {
  const r = p.reading;
  if (!r) {
    spDecision.briefId = p.id;
    spDecision.readingAt = null;
    return;
  }
  if (spDecision.briefId === p.id && spDecision.readingAt === r.created_at) return;
  spDecision.briefId = p.id;
  spDecision.readingAt = r.created_at;
  spDecision.reqOn = {};
  spDecision.reqPhrases = {};
  spDecision.ownReqs = [];
  spDecision.newKind = 'must_say';
  spDecision.newText = '';
  spDecision.newPhrases = '';

  const c = p.choice && p.choice.reading_at === r.created_at ? p.choice : null;
  if (c) {
    const byId = {};
    (c.requirements || []).forEach((item) => {
      if (item.source === 'own') {
        spDecision.ownReqs.push({ kind: item.kind, text: item.text, phrases: (item.phrases || []).slice() });
      } else {
        byId[item.id] = item;
      }
    });
    (r.requirements || []).forEach((req) => {
      const chosen = byId[req.id];
      spDecision.reqOn[req.id] = Boolean(chosen);
      spDecision.reqPhrases[req.id] = chosen
        ? (chosen.phrases || []).slice()
        : (req.phrases || []).filter((ph) => ph.in_brief).map((ph) => ph.text);
    });
    if (c.way && c.way.source === 'own') {
      spDecision.wayId = 'own';
      spDecision.ownWay = c.way.idea || '';
    } else if (c.way && c.way.id) {
      spDecision.wayId = c.way.id;
      spDecision.ownWay = '';
    }
  } else {
    (r.requirements || []).forEach((req) => {
      spDecision.reqOn[req.id] = Boolean(req.on);
      spDecision.reqPhrases[req.id] = (req.phrases || []).filter((ph) => ph.in_brief).map((ph) => ph.text);
    });
    spDecision.wayId = (r.ways && r.ways.length) ? r.ways[0].id : 'own';
    spDecision.ownWay = '';
  }
  const fmts = formatChoices(spDecision.videoFormat);
  if (fmts.length && !fmts.includes(spDecision.videoFormat)) spDecision.videoFormat = fmts[0];
}

function spBuildChoiceBody() {
  const way = spDecision.wayId === 'own'
    ? { own: spDecision.ownWay }
    : { id: spDecision.wayId };
  const requirements = Object.keys(spDecision.reqOn)
    .filter((id) => spDecision.reqOn[id])
    .map((id) => ({ id, phrases: spDecision.reqPhrases[id] || [] }));
  const own = spDecision.ownReqs.map((item) => ({
    kind: item.kind, text: item.text, phrases: item.phrases || [],
  }));
  return { reading_at: spDecision.readingAt, way, requirements, own };
}

function spPassageHtml(found, foundPassage, rawQuote) {
  if (found && foundPassage) {
    const b = foundPassage.before ? `${esc(foundPassage.before)}` : '';
    const a = foundPassage.after ? `${esc(foundPassage.after)}` : '';
    return `<div class="card soft" style="font-size:13px;margin:6px 0;padding:8px 10px;white-space:pre-wrap"><span class="tag ok" style="margin-right:6px">${esc(t('sp_in_brief'))}</span>${b}<mark>${esc(foundPassage.text)}</mark>${a}</div>`;
  }
  return `<div class="banner" style="margin:6px 0" data-sp-ungrounded><span class="tag warn">${esc(t('sp_not_in_brief'))}</span> ${esc(t('sp_not_in_brief_hint'))} ${rawQuote ? `<span>「${esc(rawQuote)}」</span>` : ''}</div>`;
}

function spBriefFlagsHtml(flags, notice) {
  if (!flags || !Array.isArray(flags.groups) || !flags.groups.length) {
    return `<div id="sp-brief-flags"><p class="hint">${esc(t('sp_flags_none'))}</p>${wlNoteHtml(notice)}</div>`;
  }
  const items = flags.groups.map((g) => {
    const places = (g.places || []).map((pl) =>
      `<div class="meta">${esc(tf('sp_flag_line', { n: pl.line_no, text: pl.text }))}</div>`).join('');
    return `<div class="card soft" style="margin-top:10px;padding:10px 12px;margin-bottom:0">${wlIssueHtml(g)}<div class="meta" style="margin-top:4px">${esc(tf('sp_flags_count', { n: g.count }))}</div>${places}</div>`;
  }).join('');
  const more = flags.left_out ? `<p class="hint">${esc(tf('sp_flags_more', { n: flags.left_out }))}</p>` : '';
  return `<div id="sp-brief-flags"><h4 style="margin:12px 0 6px">${esc(t('sp_flags_h'))}</h4>${items}${more}${wlNoteHtml(notice)}</div>`;
}

function spBi(zhText, enText) {
  if (lang === 'en') return enText || zhText || '';
  return zhText || enText || '';
}

function spRenderList(data) {
  const briefs = data.briefs || [];
  const limits = data.limits || {};
  const maxBytes = limits.file_bytes || (8 * 1024 * 1024);
  const cards = briefs.map((b) => {
    const stateKey = b.has_choice ? 'sp_state_chosen' : (b.has_reading ? 'sp_state_read' : 'sp_state_unread');
    const stateCls = b.has_choice ? 'ok' : (b.has_reading ? '' : 'warn');
    const scriptLink = b.script
      ? `<a class="btn" href="#/s/${esc(b.script.id)}">${esc(tf('sp_open_script', { title: b.script.title }))}</a>` : '';
    return `<div class="card" data-brief-card="${esc(b.id)}">
      <div class="row">
        <div>
          <h3>${esc(b.title || t('untitled'))}</h3>
          <div class="meta">${b.source_name ? `${esc(b.source_name)} · ` : ''}${esc(tf('sp_chars_n', { n: num(b.chars) }))} · ${esc(fmtDate(b.updated_at))}
            <span class="tag ${stateCls}" style="margin-left:6px">${esc(t(stateKey))}</span></div>
        </div>
        <span class="spacer"></span>
        ${scriptLink}
        <a class="btn primary" href="#/sponsored/${esc(b.id)}" data-sp-open="${esc(b.id)}">${esc(t('sp_open_brief'))}</a>
        <button type="button" class="btn ghost" data-sp-del="${esc(b.id)}" aria-label="${esc(t('delete'))}">✕</button>
      </div>
    </div>`;
  }).join('');

  $view.innerHTML = `<div class="wrap">
    <a href="#/new" class="meta" id="sp-back-new">${esc(t('sp_back_new'))}</a>
    <h1>${esc(t('sp_title'))}</h1>
    <p class="sub">${esc(t('sp_sub'))}</p>
    ${spPrivacyHtml(limits, data.service, false)}
    <div class="card" id="sp-add-card">
      <h3>${esc(t('sp_add_h'))}</h3>
      <label class="field" for="sp-title-in">${esc(t('sp_title_label'))}</label>
      <input type="text" id="sp-title-in" maxlength="${Number(limits.title_chars || 80)}" value="${esc(spAddForm.title)}" placeholder="${esc(t('sp_title_ph'))}">
      <div class="row" style="margin-top:14px">
        <label class="btn" for="sp-file-in" id="sp-file-btn" tabindex="0" role="button">${esc(t('sp_file_btn'))}</label>
        <input type="file" id="sp-file-in" accept=".txt,.md,.docx,text/plain,text/markdown,application/vnd.openxmlformats-officedocument.wordprocessingml.document" hidden>
        <span class="hint">${esc(t('sp_file_drop'))}</span>
      </div>
      <label class="field" for="sp-text-in">${esc(t('sp_paste_label'))}</label>
      <textarea id="sp-text-in" rows="6" placeholder="${esc(t('sp_paste_ph'))}">${esc(spAddForm.text)}</textarea>
      <div class="row" style="margin-top:12px">
        <button type="button" class="btn primary big" id="sp-add-btn">${esc(t('sp_add_btn'))}</button>
      </div>
    </div>
    <h2 class="sec-h">${esc(t('sp_list_h'))}</h2>
    <div id="sp-list">${cards || `<div class="empty">${esc(t('sp_empty'))}</div>`}</div>
  </div>`;

  const $title = document.getElementById('sp-title-in');
  const $text = document.getElementById('sp-text-in');
  const $file = document.getElementById('sp-file-in');
  const $fileBtn = document.getElementById('sp-file-btn');
  const $card = document.getElementById('sp-add-card');
  autoGrow($text);
  $title.oninput = () => { spAddForm.title = $title.value; };
  $text.oninput = () => { spAddForm.text = $text.value; autoGrow($text); };
  $fileBtn.onkeydown = (e) => {
    if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); $file.click(); }
  };

  const uploadFile = async (file) => {
    if (!file) return;
    if (file.size > maxBytes) {
      toast(tf('sp_file_too_big', { mb: Math.round(maxBytes / (1024 * 1024)) }));
      return;
    }
    const $btn = document.getElementById('sp-add-btn');
    await busy($btn, t('sp_adding'), async () => {
      const fileBase64 = await spFileToBase64(file);
      const created = await api('POST', '/api/sponsored', {
        title: $title.value, name: file.name, file_base64: fileBase64,
      });
      spAddForm.title = '';
      spAddForm.text = '';
      toast(created.invisible_removed ? tf('sp_added_stripped', { n: created.invisible_removed }) : t('sp_added_ok'));
      location.hash = '#/sponsored/' + created.id;
    });
  };

  $file.onchange = (e) => {
    const f = e.target.files && e.target.files[0];
    $file.value = '';
    if (f) uploadFile(f);
  };
  $card.addEventListener('dragenter', (e) => { e.preventDefault(); $card.classList.add('drop-active'); });
  $card.addEventListener('dragover', (e) => { e.preventDefault(); $card.classList.add('drop-active'); });
  $card.addEventListener('dragleave', (e) => {
    if (!$card.contains(e.relatedTarget)) $card.classList.remove('drop-active');
  });
  $card.addEventListener('drop', (e) => {
    e.preventDefault();
    $card.classList.remove('drop-active');
    if (e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0]) uploadFile(e.dataTransfer.files[0]);
  });

  document.getElementById('sp-add-btn').onclick = (e) => busy(e.currentTarget, t('sp_adding'), async () => {
    const created = await api('POST', '/api/sponsored', { title: $title.value, text: $text.value });
    spAddForm.title = '';
    spAddForm.text = '';
    toast(created.invisible_removed ? tf('sp_added_stripped', { n: created.invisible_removed }) : t('sp_added_ok'));
    location.hash = '#/sponsored/' + created.id;
  });

  document.getElementById('sp-list').onclick = async (e) => {
    const del = e.target.closest('[data-sp-del]');
    if (!del) return;
    const item = briefs.find((x) => x.id === del.dataset.spDel);
    if (!item) return;
    if (!(await askConfirm(tf('sp_del_confirm', { title: item.title || t('untitled') }), { ok: t('delete'), danger: true }))) return;
    try {
      const next = await api('DELETE', '/api/sponsored/' + item.id);
      spRenderList(next);
    } catch (err) { showError(err); }
  };
}

function spRenderDetail(p) {
  spSyncDecision(p);
  const srv = p.service || {};
  const isManual = srv.where === 'manual';
  const canRead = isManual || Boolean(srv.configured);
  const r = p.reading;

  let readingHtml = '';
  if (r) {
    const d = r.digest || {};
    const brandBadge = d.brand
      ? `<span class="tag ${d.brand_in_brief ? 'ok' : 'warn'}">${esc(t('sp_brand'))}: ${esc(d.brand)}${d.brand_in_brief ? '' : ` (${esc(t('sp_name_not_in_brief'))})`}</span>` : '';
    const prodBadge = d.product
      ? `<span class="tag ${d.product_in_brief ? 'ok' : 'warn'}">${esc(t('sp_product'))}: ${esc(d.product)}${d.product_in_brief ? '' : ` (${esc(t('sp_name_not_in_brief'))})`}</span>` : '';
    const sumPrimary = spBi(d.summary_zh, d.summary_en);
    const sumSecondary = lang === 'zh' ? d.summary_en : d.summary_zh;

    const pointsHtml = (r.points || []).length
      ? (r.points || []).map((pt) => `<div class="card soft" style="margin-top:10px;padding:10px 12px;margin-bottom:0">
          <div><b>${esc(spBi(pt.plain_zh, pt.plain_en))}</b></div>
          ${pt.plain_en && pt.plain_zh ? `<div class="meta">${esc(lang === 'zh' ? pt.plain_en : pt.plain_zh)}</div>` : ''}
          ${spPassageHtml(pt.found, pt.passage, pt.quote)}
          ${(pt.flags || []).map(wlIssueHtml).join('')}
        </div>`).join('')
      : `<p class="hint">${esc(t('sp_points_none'))}</p>`;

    const reqRows = (r.requirements || []).map((req) => {
      const checked = Boolean(spDecision.reqOn[req.id]);
      const keptPhrases = spDecision.reqPhrases[req.id] || [];
      const phraseControls = SP_PHRASE_KINDS.includes(req.kind) && (req.phrases || []).length
        ? `<div style="margin-top:6px"><div class="meta">${esc(t('sp_phrases_label'))}</div>
            <div class="row" style="gap:8px;margin-top:4px">${(req.phrases || []).map((ph, idx) => {
              const phOn = keptPhrases.includes(ph.text);
              return `<label class="tag ${ph.in_brief ? '' : 'warn'}" style="cursor:pointer">
                <input type="checkbox" data-sp-phrase="${esc(req.id)}" data-sp-phtext="${esc(ph.text)}" data-sp-phidx="${idx}" ${phOn ? 'checked' : ''} ${checked ? '' : 'disabled'}>
                ${esc(ph.text)}${ph.in_brief ? '' : ` (${esc(t('sp_phrase_not_in_brief'))})`}
              </label>`;
            }).join('')}</div>
            ${(req.phrases || []).map((ph) => (ph.flags || []).map(wlIssueHtml).join('')).join('')}
          </div>`
        : '';
      return `<div class="card soft" style="margin-top:10px;padding:10px 12px;margin-bottom:0" data-sp-req-row="${esc(req.id)}">
        <label class="row" style="cursor:pointer;align-items:flex-start">
          <input type="checkbox" data-sp-req="${esc(req.id)}" ${checked ? 'checked' : ''} style="margin-top:4px">
          <span style="flex:1">
            <span class="tag">${esc(t('sp_kind_' + req.kind))}</span>
            <b>${esc(spBi(req.text, req.text_en))}</b>
            ${req.text_en && req.text ? `<span class="meta" style="display:block">${esc(lang === 'zh' ? req.text_en : req.text)}</span>` : ''}
          </span>
        </label>
        ${spPassageHtml(req.found, req.passage, req.quote)}
        ${phraseControls}
      </div>`;
    }).join('');

    const ownRows = spDecision.ownReqs.map((item, idx) => `<div class="card soft" style="margin-top:10px;padding:10px 12px;margin-bottom:0" data-sp-own-row="${idx}">
      <div class="row">
        <span class="tag">${esc(t('sp_kind_' + item.kind))}</span>
        <span class="tag ok">${esc(t('sp_own_SourceTag'))}</span>
        <b>${esc(item.text)}</b>
        ${(item.phrases || []).map((ph) => `<span class="tag">${esc(ph)}</span>`).join('')}
        <span class="spacer"></span>
        <button type="button" class="btn ghost" data-sp-own-del="${idx}" aria-label="${esc(t('delete'))}">✕</button>
      </div>
    </div>`).join('');

    const lo = r.left_out || {};
    const leftNote = (lo.requirements || lo.points || lo.ways)
      ? `<p class="hint">${esc(tf('sp_left_out_note', { r: lo.requirements || 0, p: lo.points || 0, w: lo.ways || 0 }))}</p>` : '';

    const wayCards = (r.ways || []).map((w) => {
      const picked = spDecision.wayId === w.id;
      return `<div class="card soft" style="margin-top:10px;padding:10px 12px;margin-bottom:0" data-sp-way-card="${esc(w.id)}">
        <label class="row" style="cursor:pointer;align-items:flex-start">
          <input type="radio" name="sp-way" value="${esc(w.id)}" ${picked ? 'checked' : ''} style="margin-top:4px">
          <span style="flex:1">
            <b>${esc(spBi(w.title_zh, w.title_en))}</b>
            ${w.title_en && w.title_zh ? ` <span class="meta">(${esc(lang === 'zh' ? w.title_en : w.title_zh)})</span>` : ''}
            <span style="display:block;margin-top:4px">${esc(spBi(w.idea_zh, w.idea_en))}</span>
            ${w.opening_zh ? `<span class="meta" style="display:block;margin-top:4px">${esc(t('sp_way_opening'))}${esc(w.opening_zh)}${lang === 'en' && w.opening_en ? ' — ' + esc(w.opening_en) : ''}</span>` : ''}
            ${w.bridge_zh ? `<span class="meta" style="display:block">${esc(t('sp_way_bridge'))}${esc(w.bridge_zh)}${lang === 'en' && w.bridge_en ? ' — ' + esc(w.bridge_en) : ''}</span>` : ''}
          </span>
        </label>
        ${(w.flags || []).length ? `<div class="issues" style="margin-top:6px">${(w.flags || []).map(wlIssueHtml).join('')}</div>` : ''}
      </div>`;
    }).join('');

    const fmts = formatChoices(spDecision.videoFormat);
    const secChips = [30, 60, 90].map((v) =>
      `<button type="button" class="chip ${v === spDecision.targetSec ? 'on' : ''}" data-sp-sec="${v}">${v}${esc(t('sec'))}</button>`).join('');
    const fmtChips = fmts.map((v) =>
      `<button type="button" class="chip ${v === spDecision.videoFormat ? 'on' : ''}" data-sp-fmt="${esc(v)}">${esc(formatLabel(v))}</button>`).join('');

    readingHtml = `
      <div class="card" id="sp-digest">
        <h3>${esc(t('sp_digest_h'))}</h3>
        <div style="margin:6px 0">${brandBadge}${prodBadge}</div>
        <p style="margin:6px 0">${esc(sumPrimary)}</p>
        ${sumSecondary && sumSecondary !== sumPrimary ? `<p class="meta" style="margin:4px 0 0">${esc(sumSecondary)}</p>` : ''}
        <h4 style="margin:16px 0 6px">${esc(t('sp_points_h'))}</h4>
        ${pointsHtml}
      </div>

      <div class="card" id="sp-reqs">
        <h3>${esc(t('sp_reqs_h'))}</h3>
        <p class="hint">${esc(t('sp_reqs_hint'))}</p>
        ${leftNote}
        <div id="sp-req-list">${reqRows || `<p class="hint">${esc(t('sp_reqs_none'))}</p>`}</div>
        <div style="margin-top:14px;padding-top:12px;border-top:1px solid var(--line)" id="sp-own-sec">
          <h4>${esc(t('sp_own_h'))}</h4>
          <p class="hint">${esc(t('sp_own_hint'))}</p>
          <div id="sp-own-list">${ownRows}</div>
          <div class="row" style="margin-top:10px;align-items:flex-end">
            <div style="min-width:160px">
              <select id="sp-own-kind">${SP_KINDS.map((k) =>
                `<option value="${esc(k)}" ${k === spDecision.newKind ? 'selected' : ''}>${esc(t('sp_kind_' + k))}</option>`).join('')}</select>
            </div>
            <div style="flex:1;min-width:220px">
              <input type="text" id="sp-own-text" maxlength="${Number((p.limits && p.limits.text_chars) || 300)}" value="${esc(spDecision.newText)}" placeholder="${esc(t('sp_own_text_ph'))}">
            </div>
          </div>
          <div id="sp-own-ph-wrap" style="margin-top:8px" ${SP_PHRASE_KINDS.includes(spDecision.newKind) ? '' : 'hidden'}>
            <input type="text" id="sp-own-phrases" value="${esc(spDecision.newPhrases)}" placeholder="${esc(t('sp_own_phrases_ph'))}">
          </div>
          <div class="row" style="margin-top:8px">
            <button type="button" class="btn" id="sp-own-add-btn">${esc(t('sp_own_add'))}</button>
          </div>
        </div>
      </div>

      <div class="card" id="sp-ways">
        <h3>${esc(t('sp_ways_h'))}</h3>
        <p class="hint">${esc(t('sp_ways_hint'))}</p>
        ${wayCards}
        <div class="card soft" style="margin-top:10px;padding:10px 12px;margin-bottom:0" data-sp-way-card="own">
          <label class="row" style="cursor:pointer">
            <input type="radio" name="sp-way" value="own" id="sp-way-own-radio" ${spDecision.wayId === 'own' ? 'checked' : ''}>
            <b>${esc(t('sp_way_own_label'))}</b>
          </label>
          <textarea id="sp-way-own-text" rows="3" maxlength="${Number((p.limits && p.limits.own_way_chars) || 600)}" placeholder="${esc(t('sp_way_own_ph'))}" style="margin-top:8px">${esc(spDecision.ownWay)}</textarea>
        </div>
      </div>

      <div class="card" id="sp-draft-card">
        <h3>${esc(t('sp_draft_h'))}</h3>
        <p class="hint">${esc(t('sp_priv_draft_note'))}</p>
        <label class="field">${esc(t('duration'))}</label>
        <div class="chips">${secChips}</div>
        <label class="field">${esc(t('format'))}</label>
        <div class="chips">${fmtChips}</div>
        <div class="row" style="margin-top:18px">
          <button type="button" class="btn primary big" id="sp-draft-btn" ${canRead ? '' : 'disabled'}>${esc(t(isManual ? 'sp_draft_manual' : 'sp_draft_btn'))}</button>
          <button type="button" class="btn big" id="sp-save-choice-btn">${esc(t('sp_save_choice'))}</button>
        </div>
      </div>`;
  }

  $view.innerHTML = `<div class="wrap">
    <div class="row">
      <a href="#/sponsored" class="meta" id="sp-back-list">${esc(t('sp_back_list'))}</a>
      <span class="spacer"></span>
      ${p.script ? `<a class="btn primary" href="#/s/${esc(p.script.id)}" id="sp-script-link">${esc(tf('sp_open_script', { title: p.script.title }))}</a>` : ''}
      <button type="button" class="btn ghost" id="sp-del-one">${esc(t('delete'))}</button>
    </div>
    <h1>${esc(p.title || t('untitled'))}</h1>
    <p class="sub">${p.source_name ? `${esc(p.source_name)} · ` : ''}${esc(tf('sp_chars_n', { n: num(p.chars) }))}</p>
    ${p.brief_cut ? `<div class="banner">${esc(tf('sp_cut_warn', { n: num(p.chars) }))}</div>` : ''}
    ${spPrivacyHtml(p.limits, srv, true)}

    <div class="card" id="sp-read-card">
      <h3>${esc(t('sp_read_h'))}</h3>
      <div class="row" style="margin-top:10px">
        <button type="button" class="btn ${r ? '' : 'primary big'}" id="sp-read-btn" ${canRead ? '' : 'disabled'}>${esc(t(isManual ? 'sp_read_manual' : (r ? 'sp_read_again' : 'sp_read_btn')))}</button>
        <button type="button" class="btn" id="sp-import-read-btn">${esc(t('sp_import_read'))}</button>
      </div>
    </div>

    <details class="card" id="sp-brief-details" ${r ? '' : 'open'}>
      <summary style="cursor:pointer;font-weight:600">${esc(tf('sp_brief_fold', { n: num(p.chars) }))}</summary>
      <div class="card soft" id="sp-brief-raw" style="margin-top:10px;padding:10px 12px;max-height:320px;overflow-y:auto;white-space:pre-wrap">${esc(p.brief)}</div>
      ${spBriefFlagsHtml(p.flags, p.wordlist)}
    </details>

    ${readingHtml}
  </div>`;

  $view.querySelectorAll('textarea').forEach(autoGrow);

  document.getElementById('sp-del-one').onclick = async () => {
    if (!(await askConfirm(tf('sp_del_confirm', { title: p.title || t('untitled') }), { ok: t('delete'), danger: true }))) return;
    try {
      await api('DELETE', '/api/sponsored/' + p.id);
      location.hash = '#/sponsored';
    } catch (err) { showError(err); }
  };

  document.getElementById('sp-read-btn').onclick = (e) => busy(e.currentTarget, t('sp_reading_busy'), async () => {
    const next = isManual
      ? await manualBridge('sponsor', { sponsor_id: p.id }, { title: t('sp_mb_read_title'), intro: t('sp_mb_read_intro') })
      : await api('POST', `/api/sponsored/${p.id}/read`, { service: { provider: srv.provider, model: srv.model } });
    if (!next) return;
    spDecision.readingAt = null;
    spRenderDetail(next);
  });

  document.getElementById('sp-import-read-btn').onclick = async () => {
    try {
      const next = await manualBridge('sponsor', { sponsor_id: p.id },
        { replyOnly: true, title: t('sp_mb_read_title'), intro: t('sp_mb_read_intro') });
      if (!next) return;
      spDecision.readingAt = null;
      spRenderDetail(next);
    } catch (err) { showError(err); }
  };

  if (!r) return;

  const $reqs = document.getElementById('sp-reqs');
  $reqs.onchange = (e) => {
    const reqCb = e.target.closest('[data-sp-req]');
    if (reqCb) {
      const id = reqCb.dataset.spReq;
      spDecision.reqOn[id] = reqCb.checked;
      $reqs.querySelectorAll(`[data-sp-phrase="${id}"]`).forEach((box) => { box.disabled = !reqCb.checked; });
      return;
    }
    const phCb = e.target.closest('[data-sp-phrase]');
    if (phCb) {
      const id = phCb.dataset.spPhrase;
      const list = [];
      $reqs.querySelectorAll(`[data-sp-phrase="${id}"]`).forEach((box) => {
        if (box.checked) list.push(box.dataset.spPhtext);
      });
      spDecision.reqPhrases[id] = list;
    }
  };

  const $ownKind = document.getElementById('sp-own-kind');
  const $ownText = document.getElementById('sp-own-text');
  const $ownPhrases = document.getElementById('sp-own-phrases');
  const $ownWrap = document.getElementById('sp-own-ph-wrap');
  $ownKind.onchange = () => {
    spDecision.newKind = $ownKind.value;
    $ownWrap.hidden = !SP_PHRASE_KINDS.includes(spDecision.newKind);
  };
  $ownText.oninput = () => { spDecision.newText = $ownText.value; };
  $ownPhrases.oninput = () => { spDecision.newPhrases = $ownPhrases.value; };

  document.getElementById('sp-own-add-btn').onclick = () => {
    const text = $ownText.value.trim();
    if (!text) return;
    const phrases = SP_PHRASE_KINDS.includes($ownKind.value) ? spSplitPhrases($ownPhrases.value) : [];
    spDecision.ownReqs.push({ kind: $ownKind.value, text, phrases });
    spDecision.newText = '';
    spDecision.newPhrases = '';
    spRenderDetail(p);
  };

  document.getElementById('sp-own-list').onclick = (e) => {
    const del = e.target.closest('[data-sp-own-del]');
    if (!del) return;
    spDecision.ownReqs.splice(Number(del.dataset.spOwnDel), 1);
    spRenderDetail(p);
  };

  const $ways = document.getElementById('sp-ways');
  $ways.onchange = (e) => {
    if (e.target.name === 'sp-way') spDecision.wayId = e.target.value;
  };
  const $ownWay = document.getElementById('sp-way-own-text');
  $ownWay.onfocus = () => {
    spDecision.wayId = 'own';
    const radio = document.getElementById('sp-way-own-radio');
    if (radio) radio.checked = true;
  };
  $ownWay.oninput = () => { spDecision.ownWay = $ownWay.value; autoGrow($ownWay); };

  $view.querySelectorAll('[data-sp-sec]').forEach((b) => {
    b.onclick = () => { spDecision.targetSec = Number(b.dataset.spSec); spRenderDetail(p); };
  });
  $view.querySelectorAll('[data-sp-fmt]').forEach((b) => {
    b.onclick = () => { spDecision.videoFormat = b.dataset.spFmt; spRenderDetail(p); };
  });

  document.getElementById('sp-save-choice-btn').onclick = (e) => busy(e.currentTarget, e.currentTarget.textContent, async () => {
    const updated = await api('PUT', `/api/sponsored/${p.id}/choice`, spBuildChoiceBody());
    toast(t('sp_choice_saved'));
    spRenderDetail(updated);
  });

  document.getElementById('sp-draft-btn').onclick = (e) => busy(e.currentTarget, t('sp_drafting'), async () => {
    const replace = Boolean(p.script);
    if (replace && !(await askConfirm(t('sp_confirm_redraft'), { danger: true }))) return;
    const saved = await api('PUT', `/api/sponsored/${p.id}/choice`, spBuildChoiceBody());
    const freshSrv = saved.service || srv;
    const draftPayload = {
      target_sec: spDecision.targetSec,
      video_format: spDecision.videoFormat,
      replace,
      lang,
    };
    const script = freshSrv.where === 'manual'
      ? await manualBridge('sponsor_draft', Object.assign({ sponsor_id: p.id }, draftPayload),
        { title: t('sp_mb_draft_title'), intro: t('sp_mb_draft_intro') })
      : await api('POST', `/api/sponsored/${p.id}/draft`, Object.assign({
        service: { provider: freshSrv.provider, model: freshSrv.model },
      }, draftPayload));
    if (!script) { spRenderDetail(saved); return; }
    location.hash = '#/s/' + script.id;
  });
}

async function renderSponsored(briefId) {
  if (briefId) {
    const p = await api('GET', '/api/sponsored/' + briefId);
    spRenderDetail(p);
  } else {
    const list = await api('GET', '/api/sponsored');
    spRenderList(list);
  }
}

/* ---------- beside the script editor (#/s/<id>) ---------- */
function spWhereLabel(where, lineNo) {
  if (where === 'spoken') return tf('sp_where_spoken', { n: lineNo || '?' });
  if (where === 'onscreen') return tf('sp_where_onscreen', { n: lineNo || '?' });
  if (where === 'title') return t('sp_where_title');
  if (where === 'caption') return t('sp_where_caption');
  return t('sp_where_hashtags');
}

function spPaintEditor() {
  const el = document.getElementById('sp-box');
  if (!el || !cur || !cur.script) return;
  if (cur.script.mode !== 'SPONSORED') {
    el.innerHTML = '';
    return;
  }
  const d = spEdUi.data;
  if (!d) {
    el.innerHTML = `<div class="card soft"><p class="hint" style="margin:0">${esc(t('sp_ed_loading'))}</p></div>`;
    return;
  }
  if (!d.brief) {
    el.innerHTML = `<div class="card soft" data-sp-panel="deleted">
      <div class="row"><span class="tag warn">${esc(t('sponsored'))}</span>
        <span class="hint" style="margin:0">${esc(t('sp_ed_deleted'))}</span></div>
    </div>`;
    return;
  }
  const c = d.counts || {};
  const sayTotal = (c.said || 0) + (c.not_said || 0);
  const badges = [];
  if (sayTotal) {
    badges.push(`<span class="tag ${c.not_said ? 'warn' : 'ok'}" data-sp-count="say">${esc(
      c.not_said ? tf('sp_ed_said_miss', { m: c.not_said, n: sayTotal }) : tf('sp_ed_said_ok', { k: c.said, n: sayTotal })
    )}</span>`);
  }
  if ((d.must_not_say || []).length) {
    badges.push(`<span class="tag ${c.forbidden ? 'no' : 'ok'}" data-sp-count="forbid">${esc(
      c.forbidden ? tf('sp_ed_forbid_hit', { n: c.forbidden }) : t('sp_ed_forbid_ok')
    )}</span>`);
  }
  if ((d.tags || []).length) {
    badges.push(`<span class="tag ${c.tags_missing ? 'warn' : 'ok'}" data-sp-count="tags">${esc(
      c.tags_missing ? tf('sp_ed_tags_miss', { n: c.tags_missing }) : t('sp_ed_tags_ok')
    )}</span>`);
  }
  if ((d.for_you || []).length) {
    badges.push(`<span class="tag ${c.to_check ? 'warn' : 'ok'}" data-sp-count="you">${esc(
      c.to_check ? tf('sp_ed_check_left', { n: c.to_check }) : t('sp_ed_check_done')
    )}</span>`);
  }

  const saySec = (d.must_say || []).length ? `<div style="margin-top:14px;padding-top:12px;border-top:1px solid var(--line)">
    <h4>${esc(t('sp_ed_sec_say'))}</h4>
    ${(d.must_say || []).map((item) => {
      const hits = (item.hits || []).map((h) => {
        const loc = h.line_no === h.to_line_no
          ? tf('sp_ed_line_hit', { n: h.line_no, w: h.written })
          : tf('sp_ed_line_span', { a: h.line_no, b: h.to_line_no, w: h.written });
        return `<span class="tag ok" data-sp-hit>${esc(loc + (h.live_clip ? t('sp_ed_live_note') : ''))}</span>`;
      }).join('');
      const miss = (item.missing || []).map((ph) =>
        `<span class="tag warn" data-sp-missing>${esc(tf('sp_ed_missing_phrase', { p: ph }))}</span>`).join('');
      return `<div class="card soft" style="margin-top:10px;padding:10px 12px;margin-bottom:0"><div><b>${esc(spBi(item.text, item.text_en))}</b></div><div style="margin-top:4px">${hits}${miss}</div></div>`;
    }).join('')}
  </div>` : '';

  const forbidSec = (d.must_not_say || []).length ? `<div style="margin-top:14px;padding-top:12px;border-top:1px solid var(--line)">
    <h4>${esc(t('sp_ed_sec_forbid'))}</h4>
    ${(d.must_not_say || []).map((item) => {
      const hits = (item.findings || []).map((f) =>
        `<div class="issue error" data-sp-forbidden>${esc(tf('sp_ed_forbid_where', {
          where: spWhereLabel(f.where, f.line_no), p: f.phrase, w: f.written,
        }))}</div>`).join('');
      const clean = !hits ? `<span class="tag ok">${esc(t('sp_ed_forbid_clean'))}</span>` : '';
      const more = item.more ? `<div class="meta">${esc(t('sp_ed_forbid_more'))}</div>` : '';
      return `<div class="card soft" style="margin-top:10px;padding:10px 12px;margin-bottom:0"><div><b>${esc(spBi(item.text, item.text_en))}</b></div><div class="issues" style="margin-top:4px">${hits}${clean}</div>${more}</div>`;
    }).join('')}
  </div>` : '';

  const tagsSec = (d.tags || []).length ? `<div style="margin-top:14px;padding-top:12px;border-top:1px solid var(--line)">
    <h4>${esc(t('sp_ed_sec_tags'))}</h4>
    ${!d.has_post ? `<p class="hint">${esc(t('sp_ed_no_post'))}</p>` : ''}
    ${(d.tags || []).map((item) => {
      const chips = (item.wanted || []).map((w) => {
        if (w.found) {
          const places = (w.where || []).map((loc) => t(loc === 'caption' ? 'sp_in_caption' : 'sp_in_hashtags')).join(' / ');
          return `<span class="tag ok">${esc(tf('sp_ed_tag_found', { p: w.phrase, w: places }))}</span>`;
        }
        return `<span class="tag warn" data-sp-tag-missing>${esc(tf('sp_ed_tag_missing', { p: w.phrase }))}</span>`;
      }).join('');
      return `<div class="card soft" style="margin-top:10px;padding:10px 12px;margin-bottom:0"><div><b>${esc(spBi(item.text, item.text_en))}</b></div><div style="margin-top:4px">${chips}</div></div>`;
    }).join('')}
  </div>` : '';

  const youSec = (d.for_you || []).length ? `<div style="margin-top:14px;padding-top:12px;border-top:1px solid var(--line)">
    <h4>${esc(t('sp_ed_sec_you'))}</h4>
    ${(d.for_you || []).map((item) => `<div class="card soft" style="margin-top:10px;padding:10px 12px;margin-bottom:0">
      <label class="row" style="cursor:pointer">
        <input type="checkbox" data-sp-tick="${esc(item.id)}" ${item.ticked ? 'checked' : ''}>
        <span class="tag">${esc(t('sp_kind_' + item.kind))}</span>
        <span>${esc(spBi(item.text, item.text_en))}</span>
      </label>
    </div>`).join('')}
  </div>` : '';

  const complianceFindings = (cur.findings || []).filter((f) => f.category === 'COMPLIANCE_RISK');
  const wlSec = `<div style="margin-top:14px;padding-top:12px;border-top:1px solid var(--line)" id="sp-ed-wl">
    <h4>${esc(t('sp_ed_sec_wl'))}</h4>
    ${complianceFindings.length
      ? `<div class="issues" style="margin-top:6px">${complianceFindings.map(wlIssueHtml).join('')}</div>`
      : `<p class="hint">${esc(t('sp_ed_wl_clean'))}</p>`}
    ${wlNoteHtml(cur.wordlist)}
  </div>`;

  el.innerHTML = `<details class="card" id="sp-editor-panel" data-sp-panel="active" ${spEdUi.open ? 'open' : ''}>
    <summary class="row" style="cursor:pointer">
      <span class="tag warn" style="margin:0">${esc(t('sponsored'))}</span>
      <b>${esc(t('sp_ed_title'))}</b>
      <span class="meta">· ${esc(d.brief.title || t('untitled'))}</span>
      <span>${badges.join('')}</span>
      <span class="spacer"></span>
      <a href="#/sponsored/${esc(d.brief.id)}" id="sp-ed-brief-link">${esc(t('sp_ed_brief_link'))}</a>
    </summary>
    ${d.text_left_out ? `<p class="hint">${esc(t('sp_ed_left_out'))}</p>` : ''}
    ${saySec}${forbidSec}${tagsSec}${youSec}${wlSec}
  </details>`;

  const $panel = document.getElementById('sp-editor-panel');
  if ($panel) $panel.ontoggle = () => { spEdUi.open = $panel.open; };
  el.onchange = async (e) => {
    const tickBox = e.target.closest('[data-sp-tick]');
    if (!tickBox || !cur) return;
    tickBox.disabled = true;
    try {
      const updated = await api('PUT', `/api/scripts/${cur.script.id}/sponsor/ticks`, {
        id: tickBox.dataset.spTick, ticked: tickBox.checked,
      });
      if (cur && spEdUi.scriptId === cur.script.id) {
        spEdUi.data = updated;
        spPaintEditor();
      }
    } catch (err) {
      showError(err);
      tickBox.disabled = false;
    }
  };
}

async function spLoadForScript(scriptId) {
  const seq = ++spEdUi.seq;
  spEdUi.loading = true;
  try {
    const data = await api('GET', `/api/scripts/${scriptId}/sponsor`);
    if (seq !== spEdUi.seq || !cur || cur.script.id !== scriptId) return;
    spEdUi.data = data;
  } catch (err) {
    if (seq !== spEdUi.seq) return;
  } finally {
    if (seq === spEdUi.seq) {
      spEdUi.loading = false;
      spPaintEditor();
    }
  }
}

function spAttach() {
  if (!cur || !cur.script) return;
  const s = cur.script;
  if (s.mode !== 'SPONSORED') {
    spEdUi.scriptId = null;
    spEdUi.page = null;
    spEdUi.data = null;
    spPaintEditor();
    return;
  }
  if (spEdUi.scriptId !== s.id || spEdUi.page !== cur) {
    if (spEdUi.scriptId !== s.id) spEdUi.open = true;
    spEdUi.scriptId = s.id;
    spEdUi.page = cur;
    spEdUi.data = null;
    spPaintEditor();
    spLoadForScript(s.id);
  } else {
    spPaintEditor();
  }
}

function spAfterSave(s) {
  if (!s || s.mode !== 'SPONSORED') return;
  spLoadForScript(s.id);
}
