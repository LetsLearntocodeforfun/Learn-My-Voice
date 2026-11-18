'use strict';
/* Pages added after the first version: settings (with local models), library, ideas, voice actions.
   Loaded after app.js; boots the app at the end. */

Object.assign(I18N.zh, {
  nav_ideas: '选题建议', nav_library: '素材库',
  lib_title: '素材库', lib_sub: '把以前手写的脚本放进来，AI 会照着你的习惯写。填上数据，还能帮你分析选题。',
  upload: '上传脚本文件', upload_hint: '支持 .txt、.md、.docx，可以一次选多个',
  paste_title: '或者直接粘贴一篇', paste_ph: '把脚本内容粘贴到这里', add_to_lib: '加入素材库',
  lib_empty: '素材库还是空的。', imported_n: '已加入 {n} 篇', import_failed: '「{name}」读取失败',
  m_views: '播放', m_likes: '点赞', m_collects: '收藏', m_comments: '评论', sponsored: '商单',
  show_text: '查看 / 修改内容', lib_stats: '共 {n} 篇', confirm_delete_sample: '确定从素材库删除这一篇吗？', title_ph: '视频标题',
  learn_btn: '✨ 从素材库学习我的声音', learn_again: '重新学习', learning: '正在学习，可能要一两分钟…', learned: '学好了！',
  learn_need_samples: '先到「素材库」放几篇你写的脚本（建议 5 篇以上），再回来学习。',
  learn_source: '根据 {n} 篇脚本学习', rate_label: '我的语速（字/分钟）',
  rate_hint: '语速没法从文字里量出来。念一段一分钟的口播，数一下字数，填在这里。',
  rate_unconfirmed: '现在是默认值，还没确认', rate_saved: '语速已更新',
  ideas_title: '选题建议', ideas_sub: '根据你以前视频的内容和数据，建议接下来拍什么。',
  go_library: '去素材库',
  ideas_few_metrics: '只有 {n} 篇填了数据，建议会比较粗略。数据越多越准。',
  top_title: '表现最好的视频', interactions: '互动', rate: '互动率', use_idea: '用这个写脚本', evidence: '依据',
  kind_BUILD_ON_WINNER: '延续表现好的', kind_ADJACENT_EXPERIMENT: '新尝试',
  conf_low: '把握：低', conf_medium: '把握：中', conf_high: '把握：高', generated_at: '生成于',
  local_models: '本地模型', local_url: '本地模型地址',
  local_hint: '需要先在这台电脑上打开 Ollama 或 LM Studio，并下载好模型。内容不会离开这台电脑。',
  err_no_samples: '素材库里还没有可用的脚本。', err_bad_file: '这个文件读不了，请换成 .txt 或 .docx。',
  err_empty_text: '内容是空的。', err_bad_rate: '语速请填 100 到 500 之间的数字。', err_no_profile: '请先学习你的声音。',
});
Object.assign(I18N.en, {
  nav_ideas: 'Ideas', nav_library: 'Library',
  lib_title: 'Library', lib_sub: 'Add scripts {@name} wrote by hand so AI writes in {@their} habits. Add numbers to get topic analysis.',
  upload: 'Upload script files', upload_hint: '.txt, .md, .docx; several at once is fine',
  paste_title: 'Or paste one', paste_ph: 'Paste the script here', add_to_lib: 'Add to library',
  lib_empty: 'The library is empty.', imported_n: 'Added {n}', import_failed: 'Could not read "{name}"',
  m_views: 'Views', m_likes: 'Likes', m_collects: 'Collects', m_comments: 'Comments', sponsored: 'Sponsored',
  show_text: 'View / edit text', lib_stats: '{n} scripts', confirm_delete_sample: 'Remove this script from the library?', title_ph: 'Video title',
  learn_btn: '✨ Learn {@names} voice from the library', learn_again: 'Learn again', learning: 'Learning, this can take a minute or two…', learned: 'Done!',
  learn_need_samples: 'Add a few of {@names} scripts to the Library first (5 or more is better).',
  learn_source: 'Learned from {n} scripts', rate_label: 'Speaking rate (units/min)',
  rate_hint: 'Rate cannot be measured from text. Read one minute of voiceover aloud, count the characters, and enter it here.',
  rate_unconfirmed: 'Currently a default, not confirmed', rate_saved: 'Rate updated',
  ideas_title: 'Ideas', ideas_sub: 'What to film next, based on the content and numbers of {@names} past videos.',
  go_library: 'Open library',
  ideas_few_metrics: 'Only {n} videos have numbers, so suggestions will be rough. More data gives better results.',
  top_title: 'Best-performing videos', interactions: 'Interactions', rate: 'Rate', use_idea: 'Write this script', evidence: 'Based on',
  kind_BUILD_ON_WINNER: 'Builds on a winner', kind_ADJACENT_EXPERIMENT: 'Experiment',
  conf_low: 'Confidence: low', conf_medium: 'Confidence: medium', conf_high: 'Confidence: high', generated_at: 'Generated',
  local_models: 'Local model', local_url: 'Local server address',
  local_hint: 'Ollama or LM Studio must be running on this computer with a model downloaded. Nothing leaves the computer.',
  err_no_samples: 'There are no usable scripts in the library yet.', err_bad_file: 'This file could not be read. Use .txt or .docx.',
  err_empty_text: 'The text is empty.', err_bad_rate: 'Enter a rate between 100 and 500.', err_no_profile: 'Learn {@names} voice first.',
});

Object.assign(I18N.zh, {
  manual_name: 'ChatGPT 订阅（复制粘贴）', no_key_needed: '不需要密钥',
  manual_explain: '没有 API 密钥也能用。每次需要 AI 时，工作台会准备好一段提示词：把它粘贴给 ChatGPT，再把 ChatGPT 的回复粘贴回来就行。',
  ai_write_manual: '✨ 用 ChatGPT 写初稿',
  mb_title: '用 ChatGPT 完成这一步', mb_intro: '提示词里包含你填的个人资料（名字、账号名、内容方向、自我介绍、一起出镜的人、不说的话）、你的声音档案和相关的脚本内容，都会随提示词一起发给 ChatGPT。',
  mb_step1: '第一步：复制提示词，发给 ChatGPT', mb_copy: '复制提示词', mb_open: '打开 ChatGPT ↗', mb_size: '共 {n} 个字符',
  mb_step2: '第二步：把 ChatGPT 的完整回复粘贴到这里', mb_reply_ph: '粘贴回复（一段 JSON）',
  mb_done: '完成', mb_cancel: '取消', mb_copied: '已复制', mb_copy_failed: '复制失败，请点一下提示词，全选后手动复制',
  mcp_title: '进阶：让 ChatGPT / Codex 直接使用工作台（MCP）',
  mcp_explain: '把下面的配置加到支持 MCP 的 AI 应用里，它就能直接读取你的个人资料和声音档案、写脚本、检查并保存到这里。具体步骤见项目的 README。',
  exp_kit: '剪映素材包', exp_kit_d: '字幕、花字、口播文稿、拍摄清单，一次打包',
  exp_srt: '字幕文件（.srt）', exp_srt_d: '时间按语速估算，导入剪映后需要微调',
  exp_voice_d: '一口气一行，可用于提词，或剪映的「文稿匹配」',
  err_manual_mode: '现在是复制粘贴模式，请用页面上的按钮操作。', err_bad_task: '出错了，请再试一次。',
  build_version: '版本 {v}（{c}，{d}）', build_source: '正在从源代码运行，没有版本号。',
  build_desktop: ' 桌面应用，自带 Python {p}。',
  ai_not_configured: '还没连接 AI，请先到「设置」。', ai_manual_mode: '现在是复制粘贴模式。',
  ai_local_address: '本地模型地址必须在这台电脑上（127.0.0.1 或 localhost）。',
  ai_http: 'AI 服务返回了错误：', ai_network: '连不上 AI 服务。请检查网络，或确认本地模型已经启动。',
  ai_bad_response: 'AI 服务的回复无法识别，请再试一次。',
  ai_bad_json: '回复的格式不对。请确认粘贴的是完整回复，或者再试一次。',
  ai_empty_result: '回复里没有可用的内容，请再试一次。', ai_no_samples: '素材库里还没有可用的脚本。',
  ai_unknown_provider: '不支持这个 AI 服务。', ai_other: 'AI 调用失败：',
});
Object.assign(I18N.en, {
  manual_name: 'ChatGPT subscription (copy & paste)', no_key_needed: 'no key needed',
  manual_explain: 'Works without an API key. Whenever AI is needed, the studio prepares a prompt: paste it into ChatGPT, then paste ChatGPT\'s reply back.',
  ai_write_manual: '✨ Draft with ChatGPT',
  mb_title: 'Finish this step with ChatGPT', mb_intro: 'The prompt contains the creator profile from setup (name, account name, niches, self-description, on-camera partner, refused phrases), {@names} voice profile and the relevant script text. All of it is sent to ChatGPT with the prompt.',
  mb_step1: 'Step 1: copy the prompt and send it to ChatGPT', mb_copy: 'Copy prompt', mb_open: 'Open ChatGPT ↗', mb_size: '{n} characters',
  mb_step2: 'Step 2: paste ChatGPT\'s full reply here', mb_reply_ph: 'Paste the reply (a block of JSON)',
  mb_done: 'Done', mb_cancel: 'Cancel', mb_copied: 'Copied', mb_copy_failed: 'Copy failed. Click the prompt, select all and copy by hand.',
  mcp_title: 'Advanced: let ChatGPT / Codex use the studio directly (MCP)',
  mcp_explain: 'Add this configuration to an AI app that supports MCP and it can read the creator profile and {@names} voice profile, write scripts, check them and save them here. See the project README for steps.',
  exp_kit: 'CapCut / Jianying kit', exp_kit_d: 'Subtitles, on-screen text, voiceover text and shot list in one zip',
  exp_srt: 'Subtitle file (.srt)', exp_srt_d: 'Timing is estimated from speaking rate; adjust after importing',
  exp_voice_d: 'One breath per line, for prompting or Jianying script matching (文稿匹配)',
  err_manual_mode: 'Copy-and-paste mode is on; use the buttons on the page.', err_bad_task: 'Something went wrong. Please try again.',
  build_version: 'Version {v} ({c}, {d})', build_source: 'Running from the source code; there is no version number.',
  build_desktop: ' Desktop app, with its own Python {p}.',
  ai_not_configured: 'AI is not connected. Open Settings first.', ai_manual_mode: 'Copy-and-paste mode is on.',
  ai_local_address: 'The local model address must be on this computer (127.0.0.1 or localhost).',
  ai_http: 'The AI service returned an error: ', ai_network: 'Could not reach the AI service. Check the network, or that the local model is running.',
  ai_bad_response: 'The AI service sent a reply that could not be read. Please try again.',
  ai_bad_json: 'The reply is not in the expected format. Make sure the whole reply was pasted, or try again.',
  ai_empty_result: 'The reply had nothing usable in it. Please try again.', ai_no_samples: 'There are no usable scripts in the library yet.',
  ai_unknown_provider: 'That AI service is not supported.', ai_other: 'AI call failed: ',
});

Object.assign(I18N.zh, {
  nav_trends: '热点',
  tr_title: '热点关键词', tr_sub: '把你在小红书看到的热词放进来，工作台会结合你自己视频的数据排个先后。',
  tr_no_fetch: '工作台不会自己去平台抓数据：热词要么是你贴进来的，要么是能联网的 AI 查到的（会标明「未核实」）。',
  tr_paste: '粘贴关键词', tr_paste_ph: '一行一个，后面可以带热度数字，例如：\n秋冬护肤, 125000\n早餐吃什么 12.5万\n周末去哪儿',
  tr_add: '加入', tr_added: '已加入 {n} 个', tr_skipped: '，有 {n} 行没法识别',
  tr_ai_title: '让能联网的 AI 帮我查热词',
  tr_ai_hint: '需要一个能联网搜索的 AI（例如打开了搜索功能的 ChatGPT）。查到的词会标成「AI 查到 · 未核实」并附上来源链接，请点开确认后再用。',
  tr_focus: '想查哪个方向？（可不填）', tr_focus_ph: '例如：秋冬护肤、周末去哪儿', tr_ai_btn: '准备提示词',
  tr_dropped_source: '，{n} 个没有来源链接，没有加入', tr_dropped_old: '，{n} 个来源太旧，没有加入',
  tr_weight: '排序侧重', tr_w_trend: '更看热度', tr_w_balanced: '均衡', tr_w_mine: '更看我的数据',
  tr_empty: '还没有热词。把看到的热词贴到上面试试。',
  tr_score: '综合分', tr_heat: '热度', tr_heat_ph: '未填',
  tr_age_today: '今天看到', tr_age_days: '{n} 天前看到', tr_stale: '可能过时了',
  tr_src_ai: 'AI 查到 · 未核实', tr_src_link: '来源 ↗',
  tr_flag_voice: '不像你的说法', tr_flag_compliance: '可能是违规用词',
  tr_mine_none: '你还没拍过相关内容', tr_mine_no_numbers: '有 {n} 条相关视频，但都没填数据',
  tr_mine_lift: '{n} 条有数据的相关视频，互动约为你平时的 {x} 倍',
  tr_use: '用它写脚本', tr_confirm_delete: '不再跟踪「{k}」吗？',
  tr_how: '分数怎么算：热度（越热、越新分越高，每 {d} 天减半）加上你自己相关视频的表现，按上面选的侧重加权。这些权重是凭经验定的，还没有用真实结果校准过，只能当参考。',
  tr_baseline: '你平时一条视频的互动：{n}（{v} 条有数据的非商单视频的中位数）。',
  tr_no_baseline: '还没有填了数据的非商单视频，所以现在只按热度排。',
  kw_label: '目标关键词', kw_hint: '想让这篇带上的热词或搜索词，最多 8 个',
  kw_ph: '输入一个关键词', kw_add: '添加', kw_suggest: '正在跟踪的热词：', kw_none: '还没选关键词',
  kw_missing: '还没用到', kw_in_spoken: '口播', kw_in_title: '备选标题', kw_in_caption: '正文', kw_in_hashtags: '话题',
  post_title: '发布文案', post_hint: 'AI 写初稿时一起写的。这里可以看、可以复制，不能修改。',
  post_titles: '备选标题', post_covers: '封面文字', post_caption: '正文', post_hashtags: '话题',
  post_copy: '复制', post_copy_failed: '复制失败，请选中文字后手动复制。',
  idea_trends: '相关热词',
  mb_intro_trends: '提示词里有你在设置里填的内容方向、你这次填的方向、素材库里非商单素材的标题（最多 20 个）和已经在跟踪的热词。不包含你的名字、账号名、自我介绍、声音档案，也不包含脚本正文。这些会随提示词一起发给 AI。',
  err_no_keywords: '没有识别到关键词。一行写一个试试。', err_bad_weight: '出错了，请再试一次。',
  ai_no_focus: '还不知道你做哪方面的内容：请填一下想查的方向，或者先往素材库放几篇脚本。',
  ai_cannot_browse: '这个 AI 说它不能联网搜索，所以什么都没加。请换一个能联网的 AI。',
});
Object.assign(I18N.en, {
  nav_trends: 'Trends',
  tr_title: 'Trend keywords', tr_sub: 'Add keywords you see trending on Xiaohongshu. The studio ranks them against how {@names} own videos performed.',
  tr_no_fetch: 'The studio never fetches platform data itself. Keywords are either pasted in by you or found by a web-searching AI, and those are marked as unverified.',
  tr_paste: 'Paste keywords', tr_paste_ph: 'One per line, optionally followed by a heat number, e.g.\n秋冬护肤, 125000\n早餐吃什么 12.5万\n周末去哪儿',
  tr_add: 'Add', tr_added: 'Added {n}', tr_skipped: '; lines that could not be read: {n}',
  tr_ai_title: 'Ask a web-searching AI to look up keywords',
  tr_ai_hint: 'This needs an AI that can search the web (for example ChatGPT with search turned on). What it finds is marked "AI-found · unverified" with a source link. Open the link to check before relying on it.',
  tr_focus: 'Which area? (optional)', tr_focus_ph: 'e.g. autumn skincare, weekend trips', tr_ai_btn: 'Prepare the prompt',
  tr_dropped_source: '; not added for lack of a source link: {n}', tr_dropped_old: '; not added because the source was too old: {n}',
  tr_weight: 'Rank by', tr_w_trend: 'Mostly trend', tr_w_balanced: 'Balanced', tr_w_mine: 'Mostly {@names} results',
  tr_empty: 'No keywords yet. Paste some above to start.',
  tr_score: 'Score', tr_heat: 'Heat', tr_heat_ph: 'not set',
  tr_age_today: 'seen today', tr_age_days: 'seen {n} days ago', tr_age_days_one: 'seen 1 day ago', tr_stale: 'may be out of date',
  tr_src_ai: 'AI-found · unverified', tr_src_link: 'source ↗',
  tr_flag_voice: 'not how {@name} talks', tr_flag_compliance: 'risky ad wording',
  tr_mine_none: '{@name} has no video on this yet',
  tr_mine_no_numbers: '{n} related videos, none with numbers', tr_mine_no_numbers_one: '1 related video, without numbers',
  tr_mine_lift: '{n} related videos with numbers, about {x}× {@their} usual interactions',
  tr_mine_lift_one: '1 related video with numbers, about {x}× {@their} usual interactions',
  tr_use: 'Write a script with it', tr_confirm_delete: 'Stop tracking "{k}"?',
  tr_how: 'How the score works: trend strength (hotter and more recent scores higher; it halves every {d} days) plus how {@names} own videos on the topic performed, weighted as chosen above. The weights are judgement calls that have not been tuned against real outcomes, so treat the score as a guide.',
  tr_baseline: '{@Names} usual video gets {n} interactions (median of {v} non-sponsored videos with numbers).',
  tr_baseline_one: '{@Names} usual video gets {n} interactions (from the only non-sponsored video with numbers).',
  tr_no_baseline: 'No non-sponsored video has numbers yet, so the ranking currently reflects trend strength only.',
  kw_label: 'Target keywords', kw_hint: 'trend or search terms to work into this script, up to 8',
  kw_ph: 'Type a keyword', kw_add: 'Add', kw_suggest: 'Tracked trends:', kw_none: 'No keywords chosen',
  kw_missing: 'not used yet', kw_in_spoken: 'spoken', kw_in_title: 'title options', kw_in_caption: 'caption', kw_in_hashtags: 'hashtags',
  post_title: 'Post copy', post_hint: 'Written by the AI together with the draft. It can be read and copied here, not edited.',
  post_titles: 'Title options', post_covers: 'Cover text', post_caption: 'Caption', post_hashtags: 'Hashtags',
  post_copy: 'Copy', post_copy_failed: 'Copy failed. Select the text and copy it by hand.',
  idea_trends: 'Trend keywords',
  mb_intro_trends: 'The prompt contains the niches entered in setup, the area typed here, the titles of up to 20 non-sponsored items from the library and the keywords already tracked. It does not contain the name, account name or self-description from setup, the voice profile, or any script text. All of it is sent to the AI with the prompt.',
  err_no_keywords: 'No keywords were recognised. Put one on each line.', err_bad_weight: 'Something went wrong. Please try again.',
  ai_no_focus: 'Nothing says what {@names} content is about yet. Type an area to look up, or add scripts to the Library first.',
  ai_cannot_browse: 'That AI said it cannot search the web, so nothing was added. Use one that can.',
});

/* Errors from PUT /api/creator (codes raised by studio/creator.py). err_name_required is defined with the setup pages.
   The number 50 must match MAX_NEVER in studio/creator.py. */
Object.assign(I18N.zh, {
  err_never_uncheckable: '有一条不说的话没法检查：只由括号或省略号这类符号组成的内容对不上任何台词，请写成具体的词句。',
  err_too_many_never: '不说的话最多可以列 50 条。',
});
Object.assign(I18N.en, {
  err_never_uncheckable: 'One of the refused phrases cannot be checked: text made only of brackets or an ellipsis can never match a line. Use actual words.',
  err_too_many_never: 'The list of refused phrases can hold at most 50.',
});

const tf = (key, vars) => t(key).replace(/\{(\w+)\}/g, (_, k) => (vars[k] !== undefined ? vars[k] : ''));
const num = (v) => (v === null || v === undefined ? '–' : Number(v).toLocaleString(lang === 'zh' ? 'zh-CN' : 'en-US'));
/* Text key for a count: the "_one" variant when the count is 1 and this language has one (English singulars). */
const countKey = (key, n) => (n === 1 && I18N[lang][key + '_one'] !== undefined ? key + '_one' : key);

async function busy(btn, label, fn) {
  const original = btn.textContent;
  btn.disabled = true; btn.textContent = label;
  try { return await fn(); } catch (e) { showError(e); return undefined; }
  finally { if (btn.isConnected) { btn.disabled = false; btn.textContent = original; } }
}

/* ---------- AI: direct call, or copy-and-paste with a chat subscription ---------- */
const AI_ENDPOINTS = { draft: '/api/generate', voice: '/api/voice/learn' };

/* Resolves with the server result, or undefined if she cancelled the copy-and-paste dialog. */
async function aiRun(task, payload) {
  if (appState.settings.manual) return manualBridge(task, payload);
  return api('POST', AI_ENDPOINTS[task], payload);
}

async function copyText(text, fallbackEl) {
  try { await navigator.clipboard.writeText(text); return true; } catch (e) { /* fall through */ }
  fallbackEl.focus(); fallbackEl.select();
  try { return document.execCommand('copy'); } catch (e) { return false; }
}

/* `options` (all optional): `title` and `intro` replace the dialog's own; `skip` is the label of a third button,
   which resolves with { skipped: true } for a step the creator may leave out. */
async function manualBridge(task, payload, options) {
  const o = options || {};
  const { prompt, brief_id: briefId } = await api('POST', '/api/manual/prompt', Object.assign({ task }, payload));
  const $modal = document.getElementById('modal');
  return new Promise((resolve) => {
    $modal.innerHTML = `<div class="sheet" role="dialog" aria-modal="true">
      <h2>${esc(o.title || t('mb_title'))}</h2>
      <p class="hint">${esc(o.intro || t(task === 'trends' ? 'mb_intro_trends' : 'mb_intro'))}</p>
      <label class="field" for="mb-prompt">${esc(t('mb_step1'))}</label>
      <textarea id="mb-prompt" class="mono" rows="5" readonly></textarea>
      <div class="row" style="margin-top:8px">
        <button class="btn primary" id="mb-copy">${esc(t('mb_copy'))}</button>
        <a class="btn" href="https://chatgpt.com/" target="_blank" rel="noopener">${esc(t('mb_open'))}</a>
        <span class="meta">${esc(tf('mb_size', { n: prompt.length.toLocaleString() }))}</span></div>
      <label class="field" for="mb-reply">${esc(t('mb_step2'))}</label>
      <textarea id="mb-reply" class="mono" rows="6" placeholder="${esc(t('mb_reply_ph'))}"></textarea>
      <div class="row" style="margin-top:14px">
        <button class="btn primary big" id="mb-done">${esc(t('mb_done'))}</button>
        ${o.skip ? `<button class="btn" id="mb-skip">${esc(o.skip)}</button>` : ''}
        <button class="btn" id="mb-cancel">${esc(t('mb_cancel'))}</button></div>
    </div>`;
    $modal.hidden = false;
    const $prompt = document.getElementById('mb-prompt');
    $prompt.value = prompt;
    const close = (value) => { $modal.hidden = true; $modal.innerHTML = ''; resolve(value); };
    document.getElementById('mb-copy').onclick = async () => {
      toast(t((await copyText(prompt, $prompt)) ? 'mb_copied' : 'mb_copy_failed'));
    };
    document.getElementById('mb-cancel').onclick = () => close(undefined);
    if (o.skip) document.getElementById('mb-skip').onclick = () => close({ skipped: true });
    document.getElementById('mb-done').onclick = async (e) => {
      const btn = e.currentTarget;
      btn.disabled = true;
      try {
        const reply = document.getElementById('mb-reply').value;
        close(await api('POST', '/api/manual/reply', Object.assign({ task, reply }, payload, briefId ? { brief_id: briefId } : {})));
      } catch (err) { showError(err); btn.disabled = false; }
    };
  });
}

/* ---------- settings ---------- */
const LOCAL_PRESETS = { Ollama: 'http://127.0.0.1:11434/v1', 'LM Studio': 'http://127.0.0.1:1234/v1' };
const settingsForm = { provider: '', models: [], model: '', localUrl: '' };

/* Which build this is, for someone reporting a problem: the version of a release package, or "from source". */
function buildLine() {
  const b = appState.build;
  /* Inside the desktop app the line also says so, and which Python the app carries (studio/buildinfo.py). */
  const desktop = appState.desktop && appState.desktop.python ? tf('build_desktop', { p: appState.desktop.python }) : '';
  if (!b || b.from_source || !b.version) return t('build_source') + desktop;
  return tf('build_version', { v: b.version, c: String(b.commit || '').slice(0, 7), d: b.built_on || '' }) + desktop;
}

function renderSettings() {
  const st = appState.settings;
  if (!settingsForm.provider) { settingsForm.provider = st.provider || 'manual'; settingsForm.model = st.model || ''; }
  if (!settingsForm.localUrl) settingsForm.localUrl = st.local_base_url || LOCAL_PRESETS.Ollama;
  const p = settingsForm.provider;
  const isLocal = p === 'local';
  const isManual = p === 'manual';
  const names = { manual: t('manual_name'), gemini: 'Google Gemini', openai: 'OpenAI', local: t('local_models') };
  const status = (k) => (k === 'manual' ? t('no_key_needed') : t(st.keys[k] ? 'connected' : 'not_connected'));
  const models = settingsForm.models.length ? settingsForm.models : (st.provider === p && st.model ? [st.model] : []);
  let access;
  if (isManual) {
    access = `<div class="card" style="margin-top:16px"><p style="margin:0">${esc(t('manual_explain'))}</p></div>`;
  } else if (isLocal) {
    access = `<label class="field" for="key">${esc(t('local_url'))}</label>
       <input type="text" id="key" autocomplete="off" spellcheck="false" value="${esc(settingsForm.localUrl)}">
       <div class="chips" style="margin-top:8px">${Object.keys(LOCAL_PRESETS).map((k) =>
         `<button type="button" class="chip" data-preset="${esc(k)}">${esc(k)}</button>`).join('')}</div>
       <p class="hint">${esc(t('local_hint'))}</p>`;
  } else {
    access = `<label class="field" for="key">${esc(t('api_key'))}</label>
       <input type="password" id="key" autocomplete="off" placeholder="${esc(t(st.keys[p] ? 'key_saved_ph' : 'key_ph'))}">`;
  }
  const modelPart = isManual ? '' : `
    <div class="row" style="margin-top:14px"><button class="btn" id="connect">${esc(t('connect'))}</button></div>
    <label class="field" for="model">${esc(t('model'))}</label>
    <select id="model" ${models.length ? '' : 'disabled'}>
      <option value="">${esc(t('pick_model'))}</option>
      ${models.map((m) => `<option value="${esc(m)}" ${m === settingsForm.model ? 'selected' : ''}>${esc(m)}</option>`).join('')}
    </select>`;
  const current = st.configured
    ? `<div class="card meta" id="current">${esc(t('current'))}: ${esc(names[st.provider] || st.provider)}${st.manual ? '' : ' · ' + esc(st.model)}</div>` : '';
  const mcp = appState.mcp;
  const mcpCard = mcp ? `<details class="card" style="margin-top:32px"><summary>${esc(t('mcp_title'))}</summary>
      <p class="hint">${esc(t('mcp_explain'))}</p>
      <textarea id="mcp-snippet" class="mono" rows="6" readonly></textarea></details>` : '';
  /* Light or dark or as the system has it, and the accent colour. A choice shows at once; nothing here needs Save. */
  const look = lookNow();
  const pick = (name, value, on, cls, body, label) => `<button type="button" class="${cls}${on ? ' on' : ''}" data-pick="${name}:${value}"` +
    ` aria-pressed="${on ? 'true' : 'false'}"${label ? ` title="${esc(label)}" aria-label="${esc(label)}"` : ''}>${body}</button>`;
  const lookCard = `<h2 class="sec-h">${esc(t('look_title'))}</h2>
    <div class="card" id="look-card">
      <div class="look-row"><span>${esc(t('look_theme'))}</span>
        <div class="seg" role="group" aria-label="${esc(t('look_theme'))}">${THEMES.map((k) =>
          pick('theme', k, k === look.theme, '', esc(t('look_' + k)))).join('')}</div></div>
      <div class="look-row"><span>${esc(t('look_accent'))}</span>
        <div class="swatches" role="group" aria-label="${esc(t('look_accent'))}">${ACCENTS.map((k) =>
          pick('accent', k, k === look.accent, 'swatch', '', t('accent_' + k))).join('')}</div></div>
    </div>`;
  $view.innerHTML = `<div class="wrap"><h1>${esc(t('settings_title'))}</h1><p class="sub">${esc(t('settings_sub'))}</p>
    ${current}
    <label class="field">${esc(t('provider'))}</label>
    <div class="chips">${Object.keys(names).map((k) =>
      `<button type="button" class="chip ${k === p ? 'on' : ''}" data-provider="${k}">${esc(names[k])}
       <span class="hint">· ${esc(status(k))}</span></button>`).join('')}</div>
    ${access}${modelPart}
    <div class="row" style="margin-top:24px"><button class="btn primary big" id="save">${esc(t('save'))}</button></div>
    ${lookCard}
    <div id="wl-box"></div>
    ${mcpCard}
    <p class="hint" id="build-line" style="margin-top:28px">${esc(buildLine())}</p></div>`;
  wlPaintSettings(document.getElementById('wl-box'));     // wordlist.js: the word list behind the wording findings
  document.querySelectorAll('#look-card [data-pick]').forEach((button) => {
    button.onclick = () => {
      const name = button.dataset.pick.split(':')[0];
      setLook(name, button.dataset.pick.split(':')[1]);
      document.querySelectorAll(`#look-card [data-pick^="${name}:"]`).forEach((other) => {
        other.classList.toggle('on', other === button);
        other.setAttribute('aria-pressed', other === button ? 'true' : 'false');
      });
    };
  });

  if (mcp) {
    document.getElementById('mcp-snippet').value =
      /* --data-dir: the AI app then works on the data this app is showing, wherever that folder is. */
      `# Codex: ~/.codex/config.toml\n[mcp_servers.learn_my_voice]\ncommand = ${JSON.stringify(mcp.python)}\nargs = [${
        [mcp.script].concat(mcp.data_dir ? ['--data-dir', mcp.data_dir] : []).map((x) => JSON.stringify(x)).join(', ')}]`;
  }
  const $key = document.getElementById('key');
  const accessBody = () => {
    if (isManual) return { provider: p };
    return isLocal ? { provider: p, base_url: $key.value.trim() } : { provider: p, key: $key.value };
  };
  if (isLocal) $key.oninput = () => { settingsForm.localUrl = $key.value; };
  $view.querySelectorAll('[data-preset]').forEach((b) => {
    b.onclick = () => { settingsForm.localUrl = LOCAL_PRESETS[b.dataset.preset]; settingsForm.models = []; renderSettings(); };
  });
  $view.querySelectorAll('[data-provider]').forEach((b) => {
    b.onclick = () => { settingsForm.provider = b.dataset.provider; settingsForm.models = []; settingsForm.model = ''; renderSettings(); };
  });
  if (!isManual) {
    const $model = document.getElementById('model');
    $model.onchange = () => { settingsForm.model = $model.value; };
    document.getElementById('connect').onclick = async (e) => {
      e.currentTarget.disabled = true;
      try {
        const out = await api('POST', '/api/settings/models', accessBody());
        appState.settings = out.settings; settingsForm.models = out.models;
        if (!out.models.includes(settingsForm.model)) settingsForm.model = out.models.length === 1 ? out.models[0] : '';
      } catch (err) { showError(err); }
      renderSettings();
    };
  }
  document.getElementById('save').onclick = async () => {
    if (!isManual && !settingsForm.model) return toast(t('pick_model'));
    try {
      const out = await api('POST', '/api/settings', Object.assign(accessBody(), { model: settingsForm.model }));
      appState.settings = out.settings; toast(t('settings_saved')); renderSettings();
    } catch (err) { showError(err); }
  };
}

/* ---------- library ---------- */
async function readTextFile(file) {
  const bytes = await file.arrayBuffer();
  try { return new TextDecoder('utf-8', { fatal: true }).decode(bytes); }
  catch (e) { return new TextDecoder('gb18030').decode(bytes); }   // older Chinese-encoded .txt
}

async function fileToBase64(file) {
  const bytes = new Uint8Array(await file.arrayBuffer());
  let binary = '';
  for (let i = 0; i < bytes.length; i += 0x8000) binary += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
  return btoa(binary);
}

async function importFiles(files) {
  let added = 0;
  for (const file of files) {
    const title = file.name.replace(/\.[^.]+$/, '');
    try {
      const body = /\.docx$/i.test(file.name)
        ? { title, docx_base64: await fileToBase64(file) }
        : { title, text: await readTextFile(file) };
      await api('POST', '/api/samples', body);
      added += 1;
    } catch (e) { toast(tf('import_failed', { name: file.name })); }
  }
  return added;
}

function sampleHtml(s) {
  const metric = (f) => `<label>${esc(t('m_' + f))}<input type="number" min="0" inputmode="numeric" data-f="${f}" value="${s[f] === null ? '' : esc(s[f])}"></label>`;
  const kind = ['script', 'subtitle', 'video'].includes(s.kind) ? s.kind : 'script';
  const timed = s.timing && s.timing.speaking_rate_cpm
    ? ` · ${tf('mt_timed', { d: fmtSpan(s.timing.spoken_sec), r: s.timing.speaking_rate_cpm })}` : '';
  return `<div class="card sample" data-id="${esc(s.id)}">
    <div class="row">
      <input type="text" class="sample-title" data-f="title" value="${esc(s.title)}" placeholder="${esc(t('title_ph'))}">
      <label class="check"><input type="checkbox" data-f="is_sponsored" ${s.is_sponsored ? 'checked' : ''}> ${esc(t('sponsored'))}</label>
      <button class="btn ghost" data-del aria-label="${esc(t('delete'))}">✕</button>
    </div>
    <div class="metrics">${['views', 'likes', 'collects', 'comments'].map(metric).join('')}</div>
    ${poSampleHtml(s)}${s.needs_review ? reviewNoteHtml(s) : ''}
    <details ${s.needs_review ? 'open' : ''}><summary><span class="tag ${kind === 'script' ? '' : 'ok'}">${esc(t('mt_kind_' + kind))}</span>
      ${esc(t('show_text'))} · ${spokenUnits(s.text)}${lang === 'zh' ? '' : ' '}${esc(t('chars'))}${esc(timed)}</summary>
      <textarea data-f="text" rows="8">${esc(s.text)}</textarea></details>
  </div>`;
}

async function renderLibrary() {
  const { samples } = await api('GET', '/api/samples');
  $view.innerHTML = `<div class="wrap"><h1>${esc(t('lib_title'))}</h1><p class="sub">${esc(t('lib_sub'))}</p>
    <div class="card">
      <div class="row"><label class="btn primary" for="files">${esc(t('upload'))}</label>
        <input type="file" id="files" multiple accept=".txt,.md,.docx,text/plain" hidden>
        <span class="hint">${esc(t('upload_hint'))}</span></div>
      <p class="hint">${esc(t('lib_more_kinds'))} <a href="#/start" id="lib-to-material">${esc(t('lib_more_kinds_go'))}</a></p>
      <label class="field" for="paste">${esc(t('paste_title'))}</label>
      <textarea id="paste" rows="4" placeholder="${esc(t('paste_ph'))}"></textarea>
      <div class="row" style="margin-top:10px"><button class="btn" id="paste-add">${esc(t('add_to_lib'))}</button></div>
    </div>
    <p class="meta">${samples.length ? esc(tf('lib_stats', { n: samples.length })) : ''}</p>
    <div id="samples">${samples.map(sampleHtml).join('') || `<div class="empty">${esc(t('lib_empty'))}</div>`}</div></div>`;

  document.getElementById('lib-to-material').onclick = () => { setupUi.open = 'material'; };
  document.getElementById('files').onchange = async (e) => {
    const added = await importFiles(Array.from(e.target.files));
    if (added) toast(tf('imported_n', { n: added }));
    renderLibrary();
  };
  const $paste = document.getElementById('paste');
  $paste.oninput = () => autoGrow($paste);
  document.getElementById('paste-add').onclick = async () => {
    try {
      await api('POST', '/api/samples', { text: $paste.value });
      toast(tf('imported_n', { n: 1 }));
      renderLibrary();
    } catch (err) { showError(err); }
  };
  const $list = document.getElementById('samples');
  $list.onchange = async (e) => {
    const card = e.target.closest('.sample');
    const field = e.target.dataset.f;
    if (!card || !field) return;
    const value = e.target.type === 'checkbox' ? e.target.checked : e.target.value;
    /* posted.js draws an item that came from a posted script again: a number typed here takes the day it was read with it */
    try { poAfterSampleSave(card, await api('PUT', '/api/samples/' + card.dataset.id, { [field]: value })); toast(t('saved')); }
    catch (err) { showError(err); }
  };
  $list.onclick = async (e) => {
    const card = e.target.closest('.sample');
    if (!card) return;
    if (e.target.closest('[data-confirm]')) {
      try {
        /* save what is in the box first, so what gets confirmed is what the creator is looking at */
        await api('PUT', '/api/samples/' + card.dataset.id, { text: card.querySelector('[data-f="text"]').value, needs_review: false });
        toast(t('rn_confirmed'));
        renderLibrary();
      } catch (err) { showError(err); }
      return;
    }
    if (!e.target.closest('[data-del]')) return;
    if (!(await askConfirm(poDeleteSampleText(card.dataset.id, 'confirm_delete_sample'), { ok: t('delete'), danger: true }))) return;
    await api('DELETE', '/api/samples/' + card.dataset.id);
    renderLibrary();
  };
}

/* ---------- voice actions ---------- */
function paintVoiceActions(dna, isExample) {
  const el = document.getElementById('voice-actions');
  if (!el) return;
  const ready = appState.settings.configured && appState.sample_count > 0;
  const source = (dna && dna.source_corpus_summary) || {};
  const rate = dna && dna.cadence_and_syntax ? dna.cadence_and_syntax.target_speaking_rate_cpm : '';
  let hint = '';
  if (!appState.sample_count) hint = `${esc(t('learn_need_samples'))} <a href="#/library">${esc(t('go_library'))}</a>`;
  else if (!appState.settings.configured) hint = `${esc(t('no_ai'))} <a href="#/settings">${esc(t('go_settings'))}</a>`;
  const rateBox = isExample ? '' : `<div class="card">
      <label class="field" style="margin-top:0" for="rate">${esc(t('rate_label'))}
        ${source.speaking_rate_source === 'measured' ? `<span class="hint">· ${esc(t('rate_measured'))}</span>`
          : (source.speaking_rate_confirmed ? '' : `<span class="hint">· ${esc(t('rate_unconfirmed'))}</span>`)}</label>
      <div class="row"><input type="number" id="rate" min="100" max="500" style="width:140px" value="${esc(rate)}">
        <button class="btn" id="rate-save">${esc(t('save'))}</button></div>
      <p class="hint">${esc(t('rate_hint'))}</p></div>`;
  el.innerHTML = `${rateBox}
    <div class="row"><button class="btn primary big" id="learn" ${ready ? '' : 'disabled'}>${esc(t(isExample ? 'learn_btn' : 'learn_again'))}</button>
      ${!isExample && source.organic_scripts_analyzed ? `<span class="meta">${esc(tf('learn_source', { n: source.organic_scripts_analyzed }))} · ${esc(source.learned_at || '')}</span>` : ''}</div>
    ${hint ? `<p class="hint">${hint}</p>` : ''}`;
  document.getElementById('learn').onclick = (e) => busy(e.currentTarget, appState.settings.manual ? e.currentTarget.textContent : t('learning'), async () => {
    if (!(await aiRun('voice', {}))) return;
    setupUi.open = 'voice';        // the result is a proposal: review it item by item before it takes effect
    location.hash = '#/start';
  });
  const $save = document.getElementById('rate-save');
  if ($save) $save.onclick = async () => {
    try {
      await api('PUT', '/api/voice/rate', { cpm: Number(document.getElementById('rate').value) });
      toast(t('rate_saved'));
      await router();
    } catch (err) { showError(err); }
  };
}

/* ---------- ideas ---------- */
function ideaHtml(idea, i) {
  const evidence = idea.evidence_titles.length
    ? `<div class="meta">${esc(t('evidence'))}：${idea.evidence_titles.map((x) => `<span class="tag">${esc(x)}</span>`).join('')}</div>` : '';
  const label = (prefix, value) => {
    if (!value) return '';
    const key = prefix + value;
    return `<span class="tag">${esc(t(key) === key ? value : t(key))}</span>`;
  };
  const why = lang === 'zh' ? idea.why_zh : (idea.why_en || idea.why_zh);
  const trendTags = (idea.trend_keywords || []).length
    ? `<div class="meta">${esc(t('idea_trends'))}：${idea.trend_keywords.map((x) => `<span class="tag ok">${esc(x)}</span>`).join('')}</div>` : '';
  return `<div class="card idea">
    <div class="row"><h3>${esc(lang === 'zh' ? idea.title_zh : (idea.title_en || idea.title_zh))}</h3><span class="spacer"></span>
      ${label('kind_', idea.kind)}${label('conf_', idea.confidence)}</div>
    ${why ? `<p>${esc(why)}</p>` : ''}
    ${evidence}${trendTags}
    <div class="row" style="margin-top:12px"><button class="btn primary" data-idea="${i}">${esc(t('use_idea'))}</button>
      <span class="meta">${esc(formatLabel(idea.video_format))} · ${idea.target_duration_sec}${esc(t('sec'))}</span></div>
  </div>`;
}

/* The ideas page itself (renderIdeas) is in discover.js: one request, a web lookup, then these cards. */

/* ---------- target keywords (new-script page and script editor) ---------- */
const MAX_KEYWORDS = 8;          // must match MAX_KEYWORDS_PER_SCRIPT in studio/store.py
const MAX_KEYWORD_CHARS = 40;    // must match MAX_KEYWORD_CHARS in studio/store.py
/* Must match clean_keyword and keyword_key in studio/store.py. */
const kwClean = (s) => String(s || '').replace(/\s+/g, ' ').trim().replace(/^[#\uff03]+/, '').trim();
const kwKey = (s) => String(s || '').replace(/\u2019/g, "'").replace(/\s+/g, '').replace(/^[#\uff03]+/, '').toLowerCase();

let trendSuggestions = [];       // tracked trends that fit her voice, best score first

async function loadTrendSuggestions() {
  trendSuggestions = [];
  if (!appState.trend_count) return;
  try { trendSuggestions = (await api('GET', '/api/trends')).trends.filter((x) => x.usable); }
  catch (e) { /* suggestions are optional; the picker works without them */ }
}

/* Chosen keywords with a remove button, free entry, and suggestions from tracked trends.
   `el.kw.setCoverage(list)` marks where each keyword appears, using keyword_coverage from /api/lint. */
function keywordPicker(el, chosen, onChange) {
  if (!el) return;
  const state = { chosen: (chosen || []).slice(), coverage: null };
  el.innerHTML = `<label class="field" for="kw-input">${esc(t('kw_label'))} <span class="hint">· ${esc(t('kw_hint'))}</span></label>
    <div class="chips" data-kw-chosen></div>
    <div class="row kw-row" style="margin-top:8px">
      <input type="text" id="kw-input" maxlength="${MAX_KEYWORD_CHARS}" placeholder="${esc(t('kw_ph'))}">
      <button type="button" class="btn" data-kw-add>${esc(t('kw_add'))}</button></div>
    <div data-kw-suggest></div>`;
  const $chosen = el.querySelector('[data-kw-chosen]');
  const $suggest = el.querySelector('[data-kw-suggest]');
  const $input = el.querySelector('#kw-input');
  const $add = el.querySelector('[data-kw-add]');
  const draw = () => {
    const found = {};
    (state.coverage || []).forEach((c) => { found[kwKey(c.keyword)] = c; });
    $chosen.innerHTML = state.chosen.map((k, i) => {
      const c = found[kwKey(k)];
      const where = c ? ['spoken', 'title', 'caption', 'hashtags'].filter((f) => c[f]).map((f) => t('kw_in_' + f)).join(' · ') : '';
      const note = c ? `<small>${esc(c.covered ? '✓ ' + where : t('kw_missing'))}</small>` : '';
      return `<span class="kw ${c && !c.covered ? 'miss' : ''}">${esc(k)}${note}<button type="button" data-kw-del="${i}" aria-label="${esc(t('delete'))}">✕</button></span>`;
    }).join('') || `<span class="hint">${esc(t('kw_none'))}</span>`;
    const taken = new Set(state.chosen.map(kwKey));
    const full = state.chosen.length >= MAX_KEYWORDS;
    const offers = full ? [] : trendSuggestions.filter((x) => !taken.has(kwKey(x.keyword))).slice(0, 8);
    $suggest.innerHTML = offers.length ? `<div class="chips" style="margin-top:10px"><span class="hint">${esc(t('kw_suggest'))}</span>${offers.map((x) =>
      `<button type="button" class="chip" data-kw-pick="${esc(x.keyword)}">${esc(x.keyword)} <span class="hint">${esc(x.score)}</span></button>`).join('')}</div>` : '';
    $input.disabled = full; $add.disabled = full;
  };
  const add = (raw) => {
    const k = kwClean(raw);
    if (!k || k.length > MAX_KEYWORD_CHARS || state.chosen.length >= MAX_KEYWORDS) return false;
    if (state.chosen.some((x) => kwKey(x) === kwKey(k))) return false;
    state.chosen.push(k);
    draw(); onChange(state.chosen.slice());
    return true;
  };
  const addTyped = () => { if (add($input.value)) $input.value = ''; if (!$input.disabled) $input.focus(); };
  $add.onclick = addTyped;
  $input.onkeydown = (e) => {
    if (e.key !== 'Enter' || e.isComposing || e.keyCode === 229) return;   // Enter also confirms Chinese input
    e.preventDefault(); addTyped();
  };
  el.onclick = (e) => {
    const del = e.target.closest('[data-kw-del]');
    const pick = e.target.closest('[data-kw-pick]');
    if (del) { state.chosen.splice(Number(del.dataset.kwDel), 1); draw(); onChange(state.chosen.slice()); }
    else if (pick) add(pick.dataset.kwPick);
  };
  el.kw = { setCoverage(list) { state.coverage = Array.isArray(list) ? list : null; draw(); } };
  draw();
}

/* ---------- post copy drafted with a script: titles, cover text, caption, hashtags ---------- */
async function copyPlain(text) {
  const area = document.createElement('textarea');
  area.value = text;
  area.setAttribute('readonly', '');
  area.style.cssText = 'position:fixed;left:0;top:0;opacity:0';
  document.body.appendChild(area);
  const ok = await copyText(text, area);
  area.remove();
  return ok;
}

/* `post` is the model's rednote_post_package, saved as it came, so every field is checked before use. */
function paintPost(el, post) {
  if (!el) return;
  const p = post && typeof post === 'object' ? post : {};
  const str = (v) => (typeof v === 'string' ? v.trim() : '');
  const list = (v) => (Array.isArray(v) ? v : []);
  const titles = list(p.title_options)
    .map((o) => (typeof o === 'string' ? { zh: str(o), en: '' } : { zh: str(o && o.title_zh), en: str(o && o.title_en) }))
    .filter((o) => o.zh);
  const covers = list(p.cover_thumbnail_text_options_zh).map(str).filter(Boolean);
  const caption = str(p.caption_body_zh);
  const hashtags = list(p.hashtags_zh).map(str).filter(Boolean);
  if (!titles.length && !covers.length && !caption && !hashtags.length) { el.innerHTML = ''; return; }
  const copies = [];
  const row = (zh, en) => {
    copies.push(zh);
    return `<div class="post-row"><div class="text">${esc(zh)}${lang === 'en' && en ? `<small>${esc(en)}</small>` : ''}</div>
      <button type="button" class="btn ghost" data-copy="${copies.length - 1}">${esc(t('post_copy'))}</button></div>`;
  };
  const part = (key, html) => (html ? `<h4>${esc(t(key))}</h4>${html}` : '');
  el.innerHTML = `<div class="card post"><h3>${esc(t('post_title'))}</h3><p class="hint">${esc(t('post_hint'))}</p>
    ${part('post_titles', titles.map((o) => row(o.zh, o.en)).join(''))}
    ${part('post_covers', covers.map((c) => row(c, '')).join(''))}
    ${part('post_caption', caption ? row(caption, str(p.caption_body_en)) : '')}
    ${part('post_hashtags', hashtags.length ? row(hashtags.join(' '), '') : '')}</div>`;
  el.onclick = async (e) => {
    const btn = e.target.closest('[data-copy]');
    if (!btn) return;
    toast(t((await copyPlain(copies[Number(btn.dataset.copy)])) ? 'mb_copied' : 'post_copy_failed'));
  };
}

/* ---------- trends ---------- */
const trendForm = { text: '', focus: '' };

function trendHtml(x) {
  const tags = [];
  if (x.source === 'ai') {
    const link = /^https?:\/\//i.test(x.source_url)
      ? ` <a href="${esc(x.source_url)}" target="_blank" rel="noopener noreferrer">${esc(t('tr_src_link'))}</a>` : '';
    tags.push(`<span class="tag warn">${esc(t('tr_src_ai'))}${link}</span>`);
  }
  if (x.stale) tags.push(`<span class="tag warn">${esc(t('tr_stale'))}</span>`);
  if (x.flags.includes('voice')) tags.push(`<span class="tag no">${esc(t('tr_flag_voice'))}</span>`);
  if (x.flags.includes('compliance')) tags.push(`<span class="tag no">${esc(t('tr_flag_compliance'))}</span>`);
  let mine = t('tr_mine_none');
  if (x.lift_raw !== null) mine = tf(countKey('tr_mine_lift', x.her_videos_with_numbers), { n: x.her_videos_with_numbers, x: x.lift_raw });
  else if (x.her_videos) mine = tf(countKey('tr_mine_no_numbers', x.her_videos), { n: x.her_videos });
  const days = Math.floor(x.age_days);
  const age = days < 1 ? t('tr_age_today') : tf(countKey('tr_age_days', days), { n: days });
  const note = lang === 'zh' ? x.note_zh : (x.note_en || x.note_zh);
  const titles = x.evidence_titles.map((s) => `<span class="tag">${esc(s)}</span>`).join('');
  return `<div class="card trend ${x.usable ? '' : 'off'}" data-id="${esc(x.id)}">
    <div class="row">
      <div class="score" title="${esc(t('tr_score'))}">${esc(x.score)}</div>
      <div class="name"><h3>${esc(x.keyword)}</h3>${tags.length ? `<div>${tags.join('')}</div>` : ''}</div>
      ${x.usable ? `<button class="btn" data-use>${esc(t('tr_use'))}</button>` : ''}
      <button class="btn ghost" data-del aria-label="${esc(t('delete'))}">✕</button>
    </div>
    ${note ? `<p class="meta">${esc(note)}</p>` : ''}
    <div class="facts">
      <label>${esc(t('tr_heat'))} <input type="number" min="0" inputmode="numeric" data-heat value="${x.heat === null ? '' : esc(x.heat)}" placeholder="${esc(t('tr_heat_ph'))}"></label>
      <span>${esc(age)}</span><span>${esc(mine)}</span>
    </div>
    ${titles ? `<div class="titles">${titles}</div>` : ''}
  </div>`;
}

/* `data` is a /api/trends payload from a request that just changed something; otherwise it is fetched. */
async function renderTrends(data) {
  const p = data || await api('GET', '/api/trends');
  appState.trend_count = p.trends.length;
  const weights = [['trend', 'tr_w_trend'], ['balanced', 'tr_w_balanced'], ['mine', 'tr_w_mine']];
  const baseline = p.baseline_interactions
    ? tf(countKey('tr_baseline', p.videos_with_numbers), { n: num(Math.round(p.baseline_interactions)), v: p.videos_with_numbers })
    : t('tr_no_baseline');
  const ranking = p.trends.length ? `<div class="row" style="margin:18px 0 12px"><span class="meta">${esc(t('tr_weight'))}</span>
      <div class="chips">${weights.map(([value, key]) =>
        `<button type="button" class="chip ${value === p.weight ? 'on' : ''}" data-weight="${value}">${esc(t(key))}</button>`).join('')}</div></div>` : '';
  $view.innerHTML = `<div class="wrap"><h1>${esc(t('tr_title'))}</h1><p class="sub">${esc(t('tr_sub'))}</p>
    <div class="card">
      <label class="field" style="margin-top:0" for="tr-text">${esc(t('tr_paste'))}</label>
      <textarea id="tr-text" rows="4" placeholder="${esc(t('tr_paste_ph'))}">${esc(trendForm.text)}</textarea>
      <div class="row" style="margin-top:10px"><button class="btn primary" id="tr-add">${esc(t('tr_add'))}</button>${kiControlHtml()}</div>
      <p class="hint">${esc(t('tr_no_fetch'))}</p>
    </div>
    <details class="card"><summary>${esc(t('tr_ai_title'))}</summary>
      <p class="hint">${esc(t('tr_ai_hint'))}</p>
      <label class="field" for="tr-focus">${esc(t('tr_focus'))}</label>
      <input type="text" id="tr-focus" value="${esc(trendForm.focus)}" placeholder="${esc(t('tr_focus_ph'))}">
      <div class="row" style="margin-top:10px"><button class="btn" id="tr-ai">${esc(t('tr_ai_btn'))}</button></div>
    </details>
    ${ranking}
    <div id="trends">${p.trends.map(trendHtml).join('') || `<div class="empty">${esc(t('tr_empty'))}</div>`}</div>
    ${p.trends.length ? `<p class="hint">${esc(tf('tr_how', { d: p.half_life_days }))}</p><p class="hint">${esc(baseline)}</p>${wlNoteHtml(p.wordlist)}` : ''}
  </div>`;

  const $text = document.getElementById('tr-text');
  autoGrow($text);
  kiBind();     /* importing a spreadsheet of keywords, beside the paste box: kwimport.js */
  $text.oninput = () => { trendForm.text = $text.value; autoGrow($text); };
  document.getElementById('tr-add').onclick = (e) => busy(e.currentTarget, e.currentTarget.textContent, async () => {
    const out = await api('POST', '/api/trends', { text: $text.value });
    trendForm.text = '';
    toast(tf('tr_added', { n: out.added }) + (out.skipped ? tf('tr_skipped', { n: out.skipped }) : ''));
    await renderTrends(out);
  });
  const $focus = document.getElementById('tr-focus');
  $focus.oninput = () => { trendForm.focus = $focus.value; };
  /* Looking trends up needs live web search, so this always goes through the copy-and-paste dialog. */
  document.getElementById('tr-ai').onclick = (e) => busy(e.currentTarget, e.currentTarget.textContent, async () => {
    const out = await manualBridge('trends', { focus: $focus.value });
    if (!out) return;
    toast(tf('tr_added', { n: out.added })
      + (out.dropped_no_source ? tf('tr_dropped_source', { n: out.dropped_no_source }) : '')
      + (out.dropped_too_old ? tf('tr_dropped_old', { n: out.dropped_too_old }) : ''));
    await renderTrends(out);
  });
  $view.querySelectorAll('[data-weight]').forEach((b) => {
    b.onclick = async () => {
      try { await renderTrends(await api('PUT', '/api/trends/weight', { weight: b.dataset.weight })); } catch (err) { showError(err); }
    };
  });
  const $list = document.getElementById('trends');
  $list.onchange = async (e) => {
    const card = e.target.closest('.trend');
    if (!card || e.target.dataset.heat === undefined) return;
    try { await renderTrends(await api('PUT', '/api/trends/' + card.dataset.id, { heat: e.target.value })); } catch (err) { showError(err); }
  };
  $list.onclick = async (e) => {
    const card = e.target.closest('.trend');
    if (!card) return;
    const item = p.trends.find((x) => x.id === card.dataset.id);
    if (e.target.closest('[data-use]')) {
      const has = draft.keywords.some((k) => kwKey(k) === kwKey(item.keyword));
      if (!has && draft.keywords.length < MAX_KEYWORDS) draft.keywords.push(item.keyword);
      location.hash = '#/new';
    } else if (e.target.closest('[data-del]')) {
      if (!(await askConfirm(tf('tr_confirm_delete', { k: item.keyword }), { ok: t('delete'), danger: true }))) return;
      try { await renderTrends(await api('DELETE', '/api/trends/' + item.id)); } catch (err) { showError(err); }
    }
  };
}

window.addEventListener('hashchange', router);
window.addEventListener('beforeunload', () => { if (dirty) saveNow(); });
router();
