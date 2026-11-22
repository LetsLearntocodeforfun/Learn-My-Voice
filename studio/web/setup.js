'use strict';
/* Guided setup: who the creator is, their material, the evidence-checked voice profile, and what keeps learning.
   Loaded after app.js and before pages.js, which boots the app. Helpers from pages.js are only used at call time.
   English text names the creator through the {@name} tokens that t() fills (see app.js); Chinese speaks to them as 你. */

Object.assign(I18N.zh, {
  nav_start: '开始', st_title: '开始使用', st_sub: '五步，把工作台变成你自己的。每一步做了什么、依据是什么，都摆在你面前。',
  st_promise_1: '能数出来的就数出来：语速、每口气多长、常用语气词，都是程序量的，不是 AI 猜的。',
  st_promise_2: 'AI 说你有什么说话习惯，必须拿出你的原话；程序会回到你的素材里核对，找不到就告诉你。',
  st_promise_3: '学出来的声音档案只是一份提议，你点了采用才会生效，旧的档案会留着。',
  st_promise_4: '每一步都写明什么会离开这台电脑、什么不会。',
  st_step_creator: '关于你', st_why_creator: '名字、内容方向，还有你坚决不说的话。',
  st_step_material: '你的素材', st_why_material: '以前亲手写的脚本、字幕文件，或者直接放视频。',
  st_step_voice: '你的声音', st_why_voice: '先看量出来的，再看 AI 的提议和它的依据，逐条决定。',
  st_step_script: '第一篇脚本', st_why_script: '写一篇试试，并看到它参照了你的哪些素材。',
  st_step_grow: '越用越像你', st_why_grow: '你改稿的地方会变成可以确认的经验；素材多了可以重新学习。',
  st_continue: '继续', st_saved: '已保存', st_home_next: '设置还没做完，下一步：', st_home_go: '继续设置',
  st_ap_title: '可选：开启自动学习（Autopilot）',
  st_ap_desc: '默认关闭。开启后可在应用打开期间为近期计划匹配热词建议、刷新候选选题或监控草稿文件夹，所有运行均可一键撤销。',
  st_ap_enable_suggest: '开启（只给建议）',
  st_ap_enable_auto: '开启（安全边界内自动应用）',
  st_ap_open: '打开自动学习设置',
  st_ap_enabled_badge: '已开启：{m}',
  cr_storage: '这些保存在这台电脑上。之后用 AI 写稿、学声音、想选题时，它们会随提示词一起发给你选的 AI；查热词时只发内容方向。用本地模型的话，不会离开这台电脑。',
  cr_name: '怎么称呼你', cr_name_ph: '名字或昵称', cr_pronoun: '英文界面里用哪个称呼', cr_she: '她 she', cr_he: '他 he', cr_they: 'TA they',
  cr_channel: '账号名（可不填）', cr_niches: '你主要拍什么', cr_niches_ph: '例如：护肤、日常 Vlog、好物分享',
  cr_about: '用你自己的话介绍一下（可不填）', cr_about_ph: '你是谁、在哪里生活、视频大概是什么感觉',
  cr_partner: '视频里常出现的另一个人', cr_partner_none: '没有', cr_partner_yes: '有',
  cr_partner_called: '你在视频里怎么叫 TA', cr_partner_called_ph: '例如：我老公、男朋友、我妈、室友',
  cr_partner_lang: 'TA 说什么语言（可不填）', cr_partner_lang_ph: '例如：英语',
  cr_never: '你坚决不说的话', cr_never_ph: '一行一个，例如：\n宝子们\n绝绝子',
  cr_never_hint: '这些话只要出现在脚本的口播台词里就会标红，不分大小写。这一条靠的是规则，不是 AI，你随时可以自己试。现场原声、花字、标题和正文目前不检查。',
  cr_save: '保存并继续', err_name_required: '请先填一下怎么称呼你。',
  mt_scripts: '上传脚本文件', mt_scripts_d: '.txt、.md、.docx', mt_subs: '上传字幕文件', mt_subs_d: '.srt、.vtt（剪映识别字幕后导出）',
  mt_video: '导入小红书视频 / 录音', mt_video_d: '.mp4、.mov、.m4a、.mp3 · 预览画面，提取口播、语速与花字',
  mt_paste: '或者粘贴一篇脚本', mt_paste_ph: '把脚本内容粘贴到这里', mt_paste_add: '加入',
  mt_none: '还没有素材。', mt_kind_script: '脚本', mt_kind_subtitle: '字幕', mt_kind_video: '视频',
  mt_units: '{n} 字', mt_timed: '实录 {d} · {r} 字/分钟', mt_sponsored: '商单', mt_metrics_later: '播放、点赞这些数据可以稍后到「素材库」里填。',
  mt_meter_count: '非商单素材 {n} 篇，量到 {c} 口气。',
  mt_meter_rate: '语速：从 {d} 的实录里量出来是 {r} 字/分钟。',
  mt_meter_norate: '语速：带时间的素材（字幕或视频）还不够一分钟，先用默认值，之后也可以自己填。',
  mt_unreviewed: '有 {n} 条转写还没确认，暂时只用来量语速。',
  mt_thin: '素材太少，学出来的声音不可靠。至少放 3 篇，5 篇以上更好。',
  mt_ok: '可以开始了。再多放几篇会更准，5 篇以上比较稳。', mt_good: '素材够用了。',
  mt_cadence_short: '要量准每口气的长度，至少需要 30 口气的素材，现在还不够，会先用 AI 的估计。',
  mt_uploading: '正在读取…', mt_done: '完成', mt_failed: '失败：',
  mt_bad_subs: '「{name}」不是能识别的字幕文件。', mt_bad_media: '「{name}」不是支持的视频或录音格式。', mt_added_n: '已加入 {n} 篇',
  mt_confirm_delete: '确定删除这篇素材吗？', mt_video_removed: '转写完成后，上传的视频副本会从工作台里删除，只留下文字和时间。',
  tr_local_title: '在这台电脑上转写视频',
  tr_local_need: '需要先下载一个语音识别模型和运行它的组件，一共约 2 GB，放在这台电脑的这个文件夹里：{where}。装好以后转写不联网，视频不会离开这台电脑。',
  tr_local_install: '下载并安装', tr_local_installing: '正在安装：', tr_local_ready: '已经装好，可以直接放视频。转写时不联网。',
  tr_local_caution: '机器转写会有错字，尤其是句尾的语气词（啦/了、嘛/吗）。所以转写出来的文字要你看过、确认以后，才会用来学你的说话习惯；确认之前只用它量语速。',
  tr_local_unsupported: '这台电脑暂时不能本地转写：', tr_local_use_subs: '可以改用字幕文件：在剪映里识别字幕，再导出 .srt。',
  tr_local_failed: '安装没有成功：', tr_local_remove: '卸载', tr_local_confirm_remove: '卸载本地转写组件和模型吗？已经转写好的素材不受影响。',
  rn_title: '这是机器转写的，还没确认。', rn_body: '请对照视频看一遍，把错字改掉，重点看句尾的语气词。确认之前，它只用来量语速，不会用来学说话习惯。',
  rn_unclear: '有 {n} 段没听清，没有记下来。', rn_confirm: '我看过了，文字没问题', rn_confirmed: '已确认',
  lib_more_kinds: '字幕文件和视频在「开始」的第二步添加。', lib_more_kinds_go: '去添加', rate_measured: '从你的实录里量出来的',
  vo_measured: '先看量出来的', vo_measured_d: '这部分不经过 AI，是程序直接从你的素材里数的。',
  vo_m_clause: '每口气的长度', vo_m_clause_v: '大多在 {a}–{b} 字之间，中位数 {m}（共 {c} 口气）',
  vo_m_particles: '句尾语气词', vo_m_english: '夹在中文里的英文', vo_m_rate: '语速', vo_m_rate_measured: '{r} 字/分钟（从 {d} 实录量出）',
  vo_m_rate_default: '还没量过（带时间的素材不够一分钟）', vo_m_none: '还没有可以量的素材。',
  vo_need_material: '先在上一步放几篇素材。', vo_need_ai: '这一步需要 AI 来读你的素材。',
  vo_use_manual: '用 ChatGPT 订阅（复制粘贴，不需要密钥）', vo_other_ai: '用别的方式（API 或本地模型）',
  vo_sends: '这一步会把你的素材文字和第一步填的资料发给你选的 AI。如果选的是本地模型，内容不会离开这台电脑。',
  vo_propose: '✨ 让 AI 读我的素材，提出一份声音档案', vo_again: '重新学习', vo_proposing: '正在读，可能要一两分钟…',
  vo_active: '正在使用的声音档案：根据 {n} 篇素材，{d} 采用。', vo_new_since: '之后又加了 {n} 篇素材，可以重新学习。',
  vo_open: '查看完整档案',
  rv_title: 'AI 的提议，等你确认', rv_intro: '下面每一条都回到你的素材里核对过。勾着的会保留，没勾的会去掉。你确认之前，这份档案不会生效。',
  rv_summary: 'AI 提了 {n} 条可以核对的说法。它说你会说的：{f} 条在你的素材里找到了，{m} 条没找到。它说你不说的：{a} 条确实没出现过，{c} 条其实出现过。',
  rv_found: '找到 {n} 处：{titles}', rv_found_sponsored: '只在商单里出现过：{titles}', rv_not_found: '在你的素材里没找到',
  rv_not_found_quote: '在你的素材里没找到这句原话，不会保留',
  rv_absent: '你的素材里确实没出现过', rv_conflict: 'AI 说你不说，但出现过 {n} 次：{titles}。不会加入禁用。', rv_not_checkable: '这是一句描述，没法逐字核对',
  rv_summary_title: 'AI 的概括', rv_summary_d: '这部分是它读完后的判断，没法逐条核对。勾着的会保留，以后每次写稿都会交给 AI；没勾的会去掉。',
  rvf_about: '说的是：{text}', rvf_goes_with: '它说的那一条没有保留，这句说明会跟着一起去掉。',
  rvf_essence: '一句话概括', rvf_essence_en: '一句话概括（英文）', rvf_couple_tone: '说到身边人时的语气', rvf_accent: '口音和地方说法',
  rvf_sentences: '句子习惯', rvf_pauses: '停顿和标点', rvf_opener_patterns: '开头的句式', rvf_opener_when: '这些开头什么时候用',
  rvf_particle_rules: '语气词怎么用', rvf_mix_density: '中英混说的多少', rvf_mix_natural: '自然会说英文的地方', rvf_mix_never: '不会夹英文的地方',
  rvf_mix_forced: '要避免的生硬中英混说', rvf_vlog_hooks: '日常视频怎么开场', rvf_product_share: '分享好物的结构', rvf_sponsor_pivot: '怎么转到商单',
  rvf_closings: '怎么结尾', rvf_flower_text: '花字风格', rvf_titles: '标题句式', rvf_captions: '文案和表情习惯', rvf_from_edits: '从修改里学到的、不这样说的话',
  rv_approve: '采用这份档案', rv_discard: '放弃这份提议', rv_approved: '已采用', rv_discarded: '已放弃',
  err_stale_proposal: '这一页显示的不是现在保存的提议，可能已被新提议替换。已为你刷新。',
  err_no_pending: '现在没有等你确认的声音档案。',
  evg_openers: '常用开头', evg_partner: '提到身边人的说法', evg_codeswitch: '中英混说的例子',
  evg_address: '怎么称呼观众', evg_nicknames: '怎么称呼身边的人', evg_colloquial: '口头习惯', evg_connectors: '连接词', evg_particles: '语气词',
  evg_praise: '夸东西的说法', evg_never_address: '不这样称呼观众', evg_never_connectors: '不用的书面连接词', evg_never_particles: '不用的语气词',
  evg_never_phrases: '不说的话',
  sc_intro: '写第一篇试试。写出来以后，脚本下面会列出它参照了你的哪几篇素材，你可以自己对照。', sc_go: '写一篇脚本',
  sc_done: '已经让 AI 写过 {n} 篇初稿。', sc_need_voice: '还没有采用你自己的声音档案，现在写出来用的是示例档案，不会像你。',
  gr_edits_title: '你改稿的地方', gr_edits_d: 'AI 的初稿你改过以后，打开那篇脚本下面的「我改了什么」，挑出想让它记住的改法。记住的改法会放进以后每一次写稿的提示词里。',
  gr_lessons_n: '已经记住 {n} 条改法。', gr_lessons_none: '还没有记住任何改法。',
  gr_suggest: '你在不止一篇脚本里删掉过这些说法，要不要加入「你坚决不说的话」？', gr_suggest_add: '加入', gr_suggest_n: '{n} 篇里删过',
  gr_fresh_title: '素材变多以后', gr_fresh_d: '新视频发出去以后，把脚本或字幕放进素材库，再回到第三步重新学习。新档案同样要你确认才生效。',
  gr_forget: '忘掉', gr_ai_wrote: 'AI 写的', gr_you_wrote: '你改成',
  bs_title: '这篇初稿参照了什么', bs_exemplars: '参照的你的素材：', bs_none: '没有可参照的素材，只用了声音档案。',
  bs_profile_example: '声音档案：示例档案（不是你的）', bs_profile_yours: '声音档案：你确认过的那份（{d}）',
  bs_lessons: '用上了 {n} 条你记住的改法', bs_keywords: '目标关键词：', bs_openings: '开场参照了这几条的开头：',
  ed_title: '我改了什么', ed_intro: '左边是 AI 的初稿，右边是你现在的版本。只列出真正改了字的地方。',
  ed_none: '你还没有改动 AI 的初稿。', ed_remember: '记住这个改法', ed_remembered: '已记住', ed_deleted: '（整句删掉了）', ed_added: '（你新加的）',
  ed_ai_text: '你采用的 AI 建议', ed_ai_note: '这句是 AI 写的，不算你的改法',
  job_queued: '排队中', job_starting: '开始', job_environment: '准备运行环境', job_packages: '下载组件', job_model: '下载语音识别模型',
  job_check: '试转一段，确认能用', job_audio: '取出声音', job_transcribe: '正在这台电脑上转写',
  job_ai_upload: '正在读取视频与准备分析', job_ai_analyze: 'AI 正在识别口播、语气与屏幕花字', job_done: '完成',
  vi_drop_hint: '也可以直接把小红书视频（.mp4 / .mov）、剪映字幕（.srt）或脚本文件拖到这里',
  vi_caption_ph: '小红书标题 / 正文话题备注（可不填，有助于识别专有名词和花字）',
  vi_btn_local: '在本机离线转写口播与语速',
  vi_btn_gemini: '✨ 用 Gemini 提取口播、语气与屏幕花字',
  vi_btn_openai: '✨ 用 OpenAI Whisper 识别口播与语速',
  vi_btn_manual: '✨ 用 ChatGPT / Gemini 网页版提取口播与花字（复制粘贴）',
  vi_btn_install_local: '下载本机离线转写组件（约 2 GB）',
  vi_privacy_note: '隐私说明：上方画面预览与关键帧只在本窗口生成，不上传任何内容；离线转写全程不联网；Gemini / OpenAI 按钮会把这条音视频发给你在「设置」连接的接口，完成后立即删除本机暂存副本；复制粘贴模式由你自己在网页里附上视频。',
  vi_probing: '正在读取视频时长与画面预览…',
  vi_keyframes: '关键帧预览（点击可跳转画面）',
  vi_tone_tag: '语气与状态',
  vi_flower_label: '提取到的屏幕花字：',
  vi_mb_title: '用 Gemini / ChatGPT 提取视频口播、语气与花字',
  vi_mb_intro: '不需要 API 密钥，也不用下载 2 GB 本地组件。在 Gemini（推荐，可直接看完整视频画面与花字）或 ChatGPT 里附上「{name}」，粘贴下面的提示词，再把返回的 JSON 粘贴回来即可。',
  vi_mb_step1: '第一步：复制提示词，在 Gemini 或 ChatGPT 里附上视频「{name}」一起发送',
  mb_open_gemini: '打开 Gemini ↗',
  lib_voice_title: '声音与语气档案',
  lib_voice_counts: '已确认非商单素材 {n} 篇（脚本 {s} · 视频/字幕 {v}），共 {c} 口气。',
  lib_voice_unreviewed: '还有 {n} 条视频转写待确认：在下方对照视频核对文字（重点看句尾语气词 啦/嘛/哈）并点「我看过了，文字没问题」后，就会纳入你的声音档案学习。',
  lib_voice_create: '✨ 从这些素材生成我的声音档案',
  lib_voice_update: '✨ 用最新素材更新声音档案',
  lib_voice_review: '查看并确认待采用的声音档案提议 →',
  lib_voice_open: '查看当前声音档案',
});
Object.assign(I18N.en, {
  nav_start: 'Start', st_title: 'Getting started', st_sub: 'Five steps to make the studio {@names} own. What each step did, and what it was based on, stays in view.',
  st_promise_1: 'What can be counted is counted: speaking rate, breath length and particles are measured in code, not guessed by AI.',
  st_promise_2: 'When the AI says {@name} has a speech habit, it has to quote {@them}. Code then looks for that quote in {@their} material and says so when it is not there.',
  st_promise_3: 'A learned voice profile is only a proposal. It takes effect when it is accepted here, and earlier profiles are kept.',
  st_promise_4: 'Every step states what leaves this computer and what does not.',
  st_step_creator: 'About {@name}', st_why_creator: 'Name, niches, and phrases never to be used.',
  st_step_material: '{@Names} material', st_why_material: 'Hand-written scripts, subtitle files, or the videos themselves.',
  st_step_voice: '{@Names} voice', st_why_voice: 'Measurements first, then the AI\'s proposal with its evidence, decided item by item.',
  st_step_script: 'First script', st_why_script: 'Draft one and see which of {@names} own material it was based on.',
  st_step_grow: 'Keeps learning', st_why_grow: 'Edits to drafts become lessons to confirm; more material means learning again.',
  st_continue: 'Continue', st_saved: 'Saved', st_home_next: 'Setup is not finished. Next step: ', st_home_go: 'Continue setup',
  st_ap_title: 'Optional: Autopilot background helpers',
  st_ap_desc: 'Off by default. While the app is open, Autopilot can suggest trend keywords for {@names} plans, refresh candidate topic ideas or watch a drafts folder — every run can be undone.',
  st_ap_enable_suggest: 'Enable (Suggest only)',
  st_ap_enable_auto: 'Enable (Auto within bounds)',
  st_ap_open: 'Open Autopilot',
  st_ap_enabled_badge: 'Enabled: {m}',
  cr_storage: 'This is stored on this computer. Later, when AI drafts a script, learns the voice or suggests topics, it is sent to the chosen AI with the prompt; a trend lookup sends only the niches. With a local model it does not leave this computer.',
  cr_name: 'Name', cr_name_ph: 'Name or nickname', cr_pronoun: 'Pronoun used in the English interface', cr_she: 'she', cr_he: 'he', cr_they: 'they',
  cr_channel: 'Account name (optional)', cr_niches: 'Main kinds of content', cr_niches_ph: 'e.g. skincare, daily vlogs, product picks',
  cr_about: 'Self-description, in the creator\'s own words (optional)', cr_about_ph: 'Who they are, where they live, what the videos feel like',
  cr_partner: 'Someone who often appears on camera too', cr_partner_none: 'Nobody', cr_partner_yes: 'Yes',
  cr_partner_called: 'What that person is called on camera', cr_partner_called_ph: 'e.g. 我老公, 男朋友, 我妈, 室友',
  cr_partner_lang: 'Language that person speaks (optional)', cr_partner_lang_ph: 'e.g. English',
  cr_never: 'Phrases never to be used', cr_never_ph: 'One per line, e.g.\n宝子们\n绝绝子',
  cr_never_hint: 'A script is flagged in red whenever one of these appears in a spoken line, whatever the letter case. This is a rule, not AI, so it can be tested at any time. Live clips, on-screen text, titles and captions are not checked yet.',
  cr_save: 'Save and continue', err_name_required: 'Enter a name first.',
  mt_scripts: 'Upload script files', mt_scripts_d: '.txt, .md, .docx', mt_subs: 'Upload subtitle files', mt_subs_d: '.srt, .vtt (exported after auto-captions in 剪映 / CapCut)',
  mt_video: 'Ingest RedNote video or audio', mt_video_d: '.mp4, .mov, .m4a, .mp3 · Preview & extract speech, rate, tone & 花字',
  mt_paste: 'Or paste a script', mt_paste_ph: 'Paste the script here', mt_paste_add: 'Add',
  mt_none: 'No material yet.', mt_kind_script: 'Script', mt_kind_subtitle: 'Subtitles', mt_kind_video: 'Video',
  mt_units: '{n} units', mt_timed: '{d} recorded · {r} units/min', mt_sponsored: 'Sponsored', mt_metrics_later: 'Views and likes can be added later in the Library.',
  mt_meter_count: 'Non-sponsored items: {n}. Breaths measured: {c}.',
  mt_meter_rate: 'Speaking rate: {r} units/min, measured from {d} of recording.',
  mt_meter_norate: 'Speaking rate: there is under a minute of timed material (subtitles or video), so a default is used for now. It can also be entered by hand later.',
  mt_unreviewed: '{n} transcripts are not confirmed yet and count only toward speaking rate for now.',
  mt_unreviewed_one: '1 transcript is not confirmed yet and counts only toward speaking rate for now.',
  mt_thin: 'Too little material for a reliable voice. Add at least 3 items; 5 or more is better.',
  mt_ok: 'Enough to start. A few more will make it more accurate; 5 or more is steadier.', mt_good: 'Enough material.',
  mt_cadence_short: 'Measuring breath length needs at least 30 breaths. There are not enough yet, so the AI\'s estimate will be used for now.',
  mt_uploading: 'Reading…', mt_done: 'Done', mt_failed: 'Failed: ',
  mt_bad_subs: '"{name}" is not a subtitle file that could be read.', mt_bad_media: '"{name}" is not a supported video or audio format.', mt_added_n: 'Added {n}',
  mt_confirm_delete: 'Delete this item?', mt_video_removed: 'After transcription the uploaded copy of the video is deleted from the studio. Only the text and timing are kept.',
  tr_local_title: 'Transcribe videos on this computer',
  tr_local_need: 'This needs a speech-recognition model and the components that run it, about 2 GB in all, kept in this folder on this computer: {where}. After that, transcription works offline and videos never leave this computer.',
  tr_local_install: 'Download and install', tr_local_installing: 'Installing: ', tr_local_ready: 'Installed. Add videos directly. Transcription makes no network requests.',
  tr_local_caution: 'Machine transcripts contain mistakes, especially in sentence-final particles (啦/了, 嘛/吗). So a transcript is used to learn {@names} way of speaking only after it has been read and confirmed; before that it counts only toward speaking rate.',
  tr_local_unsupported: 'This computer cannot transcribe locally yet: ', tr_local_use_subs: 'Use subtitle files instead: run auto-captions in 剪映 / CapCut and export .srt.',
  tr_local_failed: 'The install did not succeed: ', tr_local_remove: 'Uninstall', tr_local_confirm_remove: 'Remove the local transcription components and model? Material already transcribed is not affected.',
  rn_title: 'Machine transcript, not confirmed yet.', rn_body: 'Read it against the video and fix the mistakes, especially sentence-final particles. Until it is confirmed it counts only toward speaking rate, not toward the way of speaking.',
  rn_unclear: '{n} stretches were unclear and were left out.', rn_unclear_one: '1 stretch was unclear and was left out.',
  rn_confirm: 'The text has been read and is right', rn_confirmed: 'Confirmed',
  lib_more_kinds: 'Subtitle files and videos are added in step 2 of Start.', lib_more_kinds_go: 'Add them', rate_measured: 'measured from recordings',
  vo_measured: 'Measured first', vo_measured_d: 'No AI is involved here. Code counted these directly from the material.',
  vo_m_clause: 'Length of each breath', vo_m_clause_v: 'mostly {a}–{b} units, median {m} ({c} breaths in total)',
  vo_m_particles: 'Sentence-final particles', vo_m_english: 'English used inside Chinese', vo_m_rate: 'Speaking rate', vo_m_rate_measured: '{r} units/min (measured from {d} of recording)',
  vo_m_rate_default: 'not measured yet (under a minute of timed material)', vo_m_none: 'Nothing to measure yet.',
  vo_need_material: 'Add some material in the previous step first.', vo_need_ai: 'This step needs an AI to read the material.',
  vo_use_manual: 'Use a ChatGPT subscription (copy & paste, no key)', vo_other_ai: 'Use something else (API or local model)',
  vo_sends: 'This step sends the text of the material and the details from step 1 to the chosen AI. With a local model nothing leaves this computer.',
  vo_propose: '✨ Have the AI read the material and propose a voice profile', vo_again: 'Learn again', vo_proposing: 'Reading, this can take a minute or two…',
  vo_active: 'Voice profile in use: based on {n} items, accepted {d}.', vo_new_since: '{n} items have been added since. Learning again is possible.',
  vo_open: 'Open the full profile',
  rv_title: 'The AI\'s proposal, waiting for a decision', rv_intro: 'Each item below was checked against {@names} material. Ticked items are kept, unticked ones are removed. Nothing takes effect until the profile is accepted.',
  rv_summary: 'The AI made {n} checkable claims. Of the things it says {@name} says, {f} were found in the material and {m} were not. Of the things it says {@name} never says, {a} are indeed absent and {c} do appear.',
  rv_found: 'Found {n} times: {titles}', rv_found_sponsored: 'Only found in sponsored material: {titles}', rv_not_found: 'Not found in the material',
  rv_not_found_quote: 'This line was not found in the material, so it is not kept',
  rv_absent: 'Does not appear in the material', rv_conflict: 'The AI said this is never used, but it appears {n} times: {titles}. It will not be banned.', rv_not_checkable: 'A description, which cannot be checked word for word',
  rv_summary_title: 'The AI\'s summary', rv_summary_d: 'This is its judgment after reading and cannot be checked item by item. Ticked items are kept and are given to the AI with every draft; unticked ones are removed.',
  rvf_about: 'About: {text}', rvf_goes_with: 'The entry it describes is not kept, so this note goes with it.',
  rvf_essence: 'In one line', rvf_essence_en: 'In one line (English)', rvf_couple_tone: 'Tone when talking about people close to {@them}', rvf_accent: 'Accent and regional speech',
  rvf_sentences: 'Sentence habits', rvf_pauses: 'Pauses and punctuation', rvf_opener_patterns: 'Opener patterns', rvf_opener_when: 'When each opener is used',
  rvf_particle_rules: 'How the particles are used', rvf_mix_density: 'How much English is mixed in', rvf_mix_natural: 'Where English comes naturally', rvf_mix_never: 'Where English is never mixed in',
  rvf_mix_forced: 'Forced mixing to avoid', rvf_vlog_hooks: 'Ways to open a vlog', rvf_product_share: 'Structure of a product share', rvf_sponsor_pivot: 'Ways to turn to a sponsor',
  rvf_closings: 'Ways to close', rvf_flower_text: 'On-screen text style', rvf_titles: 'Title patterns', rvf_captions: 'Caption and emoji habits', rvf_from_edits: 'Learned from edits: wording to avoid',
  rv_approve: 'Accept this profile', rv_discard: 'Discard the proposal', rv_approved: 'Accepted', rv_discarded: 'Discarded',
  err_stale_proposal: 'This page was showing an older proposal that has since been replaced. Reloaded with the current one.',
  err_no_pending: 'There is no proposed voice profile waiting to be accepted.',
  evg_openers: 'Typical openers', evg_partner: 'Mentions of people close to {@them}', evg_codeswitch: 'Examples of mixing English in',
  evg_address: 'Ways of addressing viewers', evg_nicknames: 'Names for people close to {@them}', evg_colloquial: 'Spoken habits', evg_connectors: 'Connectors', evg_particles: 'Particles',
  evg_praise: 'Ways of praising things', evg_never_address: 'Ways of addressing viewers that are not used', evg_never_connectors: 'Written-style connectors that are avoided', evg_never_particles: 'Particles that are not used',
  evg_never_phrases: 'Phrases that are not said',
  sc_intro: 'Draft a first script. Under it, the studio lists which of {@names} items it was based on, so the two can be compared.', sc_go: 'Write a script',
  sc_done: 'The AI has drafted {n} scripts so far.', sc_need_voice: '{@Names} own voice profile has not been accepted yet, so a draft now uses the example profile and will not sound like {@them}.',
  gr_edits_title: 'Edits to drafts', gr_edits_d: 'After an AI draft has been changed, open "What changed" under that script and pick the changes worth remembering. Remembered changes go into the prompt for every later draft.',
  gr_lessons_n: '{n} changes remembered.', gr_lessons_none: 'No changes remembered yet.',
  gr_suggest: 'These phrases were removed in more than one script. Add them to the phrases never to be used?', gr_suggest_add: 'Add', gr_suggest_n: 'removed in {n} scripts',
  gr_fresh_title: 'As the material grows', gr_fresh_d: 'After new videos go out, add their scripts or subtitles to the library, then come back to step 3 and learn again. The new profile also waits for approval.',
  gr_forget: 'Forget', gr_ai_wrote: 'AI wrote', gr_you_wrote: 'Changed to',
  bs_title: 'What this draft was based on', bs_exemplars: 'Material used as examples: ', bs_none: 'There was no material to use as examples, only the voice profile.',
  bs_profile_example: 'Voice profile: the bundled example (not {@names} own)', bs_profile_yours: 'Voice profile: the accepted one ({d})',
  bs_lessons: '{n} remembered changes were used', bs_keywords: 'Target keywords: ', bs_openings: 'Openings taken as examples, from: ',
  ed_title: 'What changed', ed_intro: 'The AI\'s draft is on the left and the current version on the right. Only lines whose wording changed are listed.',
  ed_none: 'The AI\'s draft has not been changed yet.', ed_remember: 'Remember this change', ed_remembered: 'Remembered', ed_deleted: '(line deleted)', ed_added: '(a line added by hand)',
  ed_ai_text: 'An AI proposal that was taken', ed_ai_note: 'Written by an AI, so not remembered as {@names} wording',
  job_queued: 'waiting', job_starting: 'starting', job_environment: 'preparing the environment', job_packages: 'downloading components', job_model: 'downloading the speech model',
  job_check: 'running a test transcription', job_audio: 'extracting the audio', job_transcribe: 'transcribing on this computer',
  job_ai_upload: 'reading media for analysis', job_ai_analyze: 'analyzing speech, tone & on-screen text', job_done: 'done',
  vi_drop_hint: 'Or drag & drop RedNote videos (.mp4 / .mov), subtitles (.srt), or script files anywhere here',
  vi_caption_ph: 'Optional RedNote caption / hashtags note (helps recognize brand names & 花字)',
  vi_btn_local: 'Transcribe offline on this Mac',
  vi_btn_gemini: '✨ Analyze speech, tone & 花字 with Gemini',
  vi_btn_openai: '✨ Transcribe speech & rate with OpenAI Whisper',
  vi_btn_manual: '✨ Extract speech, tone & 花字 via ChatGPT / Gemini (copy & paste)',
  vi_btn_install_local: 'Install offline transcriber (~2 GB)',
  vi_privacy_note: 'Privacy: video preview & keyframes are rendered locally in this window without uploading; offline transcription never uses the network; Gemini / OpenAI sends this file to your connected key and deletes the local temp copy right after; copy & paste lets you attach the video yourself.',
  vi_probing: 'Reading duration & video keyframes…',
  vi_keyframes: 'Keyframes (click to seek)',
  vi_tone_tag: 'Vocal tone',
  vi_flower_label: 'Extracted on-screen 花字:',
  vi_mb_title: 'Extract speech, tone & 花字 with Gemini or ChatGPT',
  vi_mb_intro: 'Works with zero API key and zero 2 GB install. Attach "{name}" in Gemini (recommended for full video + 花字) or ChatGPT, send the prompt below, and paste the JSON reply back.',
  vi_mb_step1: 'Step 1: copy the prompt, attach "{name}" in Gemini or ChatGPT, and send',
  mb_open_gemini: 'Open Gemini ↗',
  lib_voice_title: 'Tone & Voice Profile',
  lib_voice_counts: 'Confirmed non-sponsored material: {n} items ({s} scripts · {v} videos/subtitles), {c} breaths.',
  lib_voice_unreviewed: '{n} video transcripts are waiting for confirmation below: check particles against the video and click confirm so they count toward {@names} voice profile.',
  lib_voice_unreviewed_one: '1 video transcript is waiting for confirmation below: check particles against the video and click confirm so it counts toward {@names} voice profile.',
  lib_voice_create: '✨ Create Voice Profile from Material',
  lib_voice_update: '✨ Update Voice Profile from Material',
  lib_voice_review: 'Review & accept pending voice profile proposal →',
  lib_voice_open: 'Open active voice profile',
});

const SETUP_STEPS = ['creator', 'material', 'voice', 'script', 'grow'];
const setupUi = { open: null, poll: 0 };
const fmtSpan = (sec) => {
  const s = Math.max(0, Math.round(sec || 0));
  const m = Math.floor(s / 60);
  return lang === 'zh' ? (m ? `${m}分${s % 60}秒` : `${s}秒`) : (m ? `${m} min ${s % 60} s` : `${s} s`);
};
const titleList = (titles) => (titles || []).map((x) => (lang === 'zh' ? `《${x}》` : `"${x}"`)).join(lang === 'zh' ? '' : ', ');
/* A failure message in the interface language when the server has one. */
const failText = (err) => ((err && ((lang === 'zh' && err.message_zh) || err.message)) || '');
const stageText = (stage) => (I18N.zh['job_' + stage] !== undefined ? t('job_' + stage) : (stage || ''));

function stopSetupPolling() { clearInterval(setupUi.poll); setupUi.poll = 0; }

/* Calls `fn` on a timer until it returns true, the page is left, or the view is repainted. */
function pollSetup(fn, ms, allowedHashes) {
  stopSetupPolling();
  const hashes = allowedHashes || ['#/start'];
  const timer = setInterval(async () => {
    if (!hashes.includes(location.hash) || setupUi.poll !== timer) return clearInterval(timer);
    try { if (await fn()) { clearInterval(timer); if (setupUi.poll === timer) setupUi.poll = 0; } } catch (e) { /* try again on the next tick */ }
  }, ms || 1500);
  setupUi.poll = timer;
}

async function renderSetup(openStep) {
  stopSetupPolling();
  const [s, ap] = await Promise.all([api('GET', '/api/setup'), api('GET', '/api/autopilot').catch(() => null)]);
  if (typeof openStep === 'string') setupUi.open = openStep;
  if (!SETUP_STEPS.includes(setupUi.open)) setupUi.open = s.next === 'done' ? 'grow' : s.next;
  const done = { creator: s.creator.done, material: s.material.done, voice: s.voice.state === 'active', script: s.first_script.done, grow: s.lessons.accepted > 0 };
  const sep = lang === 'zh' ? '、' : ', ';
  const summary = {
    creator: s.creator.done ? [s.creator.name, (s.creator.niches || []).join(sep)].filter(Boolean).join(' · ') : '',
    material: s.material.total ? tf('mt_meter_count', { n: s.material.organic, c: s.material.clauses }) : '',
    voice: s.voice.has_pending || s.voice.state === 'pending' ? t('rv_title')
      : (s.voice.state === 'active' ? tf('vo_active', { n: s.voice.based_on, d: s.voice.learned_at || '' }) : ''),
    script: s.first_script.done ? tf('sc_done', { n: s.first_script.count }) : '',
    grow: s.lessons.accepted ? tf('gr_lessons_n', { n: s.lessons.accepted }) : '',
  };
  const step = (key, i) => {
    const open = setupUi.open === key;
    return `<section class="step ${done[key] ? 'done' : ''} ${open ? 'open' : ''}" data-step="${key}">
      <button type="button" class="step-head" data-open="${key}" aria-expanded="${open}">
        <span class="step-no">${done[key] ? '✓' : i + 1}</span>
        <span class="step-title">${esc(t('st_step_' + key))}<small>${esc(summary[key] || t('st_why_' + key))}</small></span></button>
      ${open ? '<div class="step-body" id="step-body"></div>' : ''}</section>`;
  };
  const apModeLabel = ap && ap.level === 'auto' ? t('ap_level_auto') : t('ap_level_suggest');
  const apCard = `<div class="card soft" id="st-autopilot-card" style="margin-top:18px">
    <div class="row" style="flex-wrap:wrap;gap:10px;align-items:center">
      <div class="spacer">
        <h3 style="margin:0">${esc(t('st_ap_title'))}</h3>
        <p class="hint" style="margin:4px 0 0">${esc(t('st_ap_desc'))}</p>
      </div>
      ${ap && ap.enabled ? `<span class="tag ok" id="st-ap-badge">${esc(tf('st_ap_enabled_badge', { m: apModeLabel }))}</span>` : ''}
      ${ap && ap.on && !ap.enabled ? `<button type="button" class="btn" data-st-ap-enable="suggest">${esc(t('st_ap_enable_suggest'))}</button>
        <button type="button" class="btn" data-st-ap-enable="auto">${esc(t('st_ap_enable_auto'))}</button>` : ''}
      <a class="btn" href="#/autopilot" id="st-ap-open">${esc(t('st_ap_open'))}</a>
    </div>
  </div>`;
  $view.innerHTML = `<div class="wrap"><h1>${esc(t('st_title'))}</h1><p class="sub">${esc(t('st_sub'))}</p>
    <ul class="promise">${[1, 2, 3, 4].map((n) => `<li>${esc(t('st_promise_' + n))}</li>`).join('')}</ul>
    ${SETUP_STEPS.map(step).join('')}${apCard}</div>`;
  $view.querySelectorAll('[data-open]').forEach((b) => { b.onclick = () => renderSetup(b.dataset.open); });
  $view.querySelectorAll('[data-st-ap-enable]').forEach((b) => {
    b.onclick = async () => {
      try {
        await api('POST', '/api/autopilot/settings', { enabled: true, level: b.dataset.stApEnable });
        toast(t('ap_saved_toast'));
        await renderSetup(setupUi.open);
      } catch (err) { showError(err); }
    };
  });
  const body = document.getElementById('step-body');
  if (!body) return;
  const painters = { creator: paintStepCreator, material: paintStepMaterial, voice: paintStepVoice, script: paintStepScript, grow: paintStepGrow };
  try { await painters[setupUi.open](body, s); } catch (e) { showError(e); }
}

/* ---------- step 1: about the creator ---------- */
async function paintStepCreator(el) {
  const c = await api('GET', '/api/creator');
  const f = {
    pronoun: ['she', 'he', 'they'].includes(c.pronoun) ? c.pronoun : 'they',
    hasPartner: !!(c.partner && c.partner.called),
  };
  const field = (id, key, value, ph) => `<label class="field" for="${id}">${esc(t(key))}</label>
    <input type="text" id="${id}" value="${esc(value || '')}" placeholder="${esc(ph ? t(ph) : '')}">`;
  const draw = () => {
    const keep = {};
    el.querySelectorAll('input[type=text], textarea').forEach((x) => { keep[x.id] = x.value; });
    const v = (id, fallback) => (keep[id] !== undefined ? keep[id] : (fallback || ''));
    el.innerHTML = `<p class="hint" style="margin-top:0">${esc(t('cr_storage'))}</p>
      ${field('cr-name', 'cr_name', v('cr-name', c.name), 'cr_name_ph')}
      <label class="field">${esc(t('cr_pronoun'))}</label>
      <div class="chips">${['she', 'he', 'they'].map((p) =>
        `<button type="button" class="chip ${p === f.pronoun ? 'on' : ''}" data-pronoun="${p}">${esc(t('cr_' + p))}</button>`).join('')}</div>
      ${field('cr-niches', 'cr_niches', v('cr-niches', (c.niches || []).join('、')), 'cr_niches_ph')}
      ${field('cr-channel', 'cr_channel', v('cr-channel', c.channel))}
      <label class="field">${esc(t('cr_partner'))}</label>
      <div class="chips"><button type="button" class="chip ${f.hasPartner ? '' : 'on'}" data-partner="0">${esc(t('cr_partner_none'))}</button>
        <button type="button" class="chip ${f.hasPartner ? 'on' : ''}" data-partner="1">${esc(t('cr_partner_yes'))}</button></div>
      ${f.hasPartner ? field('cr-called', 'cr_partner_called', v('cr-called', c.partner && c.partner.called), 'cr_partner_called_ph')
        + field('cr-lang', 'cr_partner_lang', v('cr-lang', c.partner && c.partner.language), 'cr_partner_lang_ph') : ''}
      <label class="field" for="cr-about">${esc(t('cr_about'))}</label>
      <textarea id="cr-about" rows="2" placeholder="${esc(t('cr_about_ph'))}">${esc(v('cr-about', c.about))}</textarea>
      <label class="field" for="cr-never">${esc(t('cr_never'))}</label>
      <textarea id="cr-never" rows="3" placeholder="${esc(t('cr_never_ph'))}">${esc(v('cr-never', (c.never || []).join('\n')))}</textarea>
      <p class="hint">${esc(t('cr_never_hint'))}</p>
      <div class="row" style="margin-top:18px"><button class="btn primary big" id="cr-save">${esc(t('cr_save'))}</button></div>`;
    el.querySelectorAll('textarea').forEach((x) => { autoGrow(x); x.oninput = () => autoGrow(x); });
    el.querySelectorAll('[data-pronoun]').forEach((b) => { b.onclick = () => { f.pronoun = b.dataset.pronoun; draw(); }; });
    el.querySelectorAll('[data-partner]').forEach((b) => { b.onclick = () => { f.hasPartner = b.dataset.partner === '1'; draw(); }; });
    document.getElementById('cr-save').onclick = (e) => busy(e.currentTarget, e.currentTarget.textContent, async () => {
      const val = (id) => { const x = document.getElementById(id); return x ? x.value.trim() : ''; };
      await api('PUT', '/api/creator', {
        name: val('cr-name'), pronoun: f.pronoun, channel: val('cr-channel'), niches: val('cr-niches'), about: val('cr-about'),
        partner: f.hasPartner && val('cr-called') ? { called: val('cr-called'), language: val('cr-lang') } : null,
        never: val('cr-never').split('\n').map((x) => x.trim()).filter(Boolean),
      });
      toast(t('st_saved'));
      setupUi.open = 'material';
      await router();
    });
  };
  draw();
}

/* ---------- step 2 & library: visual RedNote video ingestion & material ---------- */
const stagedVideos = [];          // staged RedNote video/audio cards with local blob preview & keyframes
const videoPreviews = new Map();  // sampleId -> { url, frames, duration, isVideo, name } for side-by-side review
let stagedSeq = 0;

const DEFAULT_MEDIA_EXTS = ['mp4', 'mov', 'm4v', 'm4a', 'mp3', 'wav', 'aac', 'aif', 'aiff', 'caf', 'flac', '3gp'];
const VIDEO_FILE_RE = /\.(mp4|mov|m4v|3gp)$/i;

function fmtClock(sec) {
  const s = Math.max(0, Math.round(Number(sec) || 0));
  const m = Math.floor(s / 60);
  const r = s % 60;
  return `${String(m).padStart(2, '0')}:${String(r).padStart(2, '0')}`;
}

function fmtBytes(bytes) {
  const n = Number(bytes) || 0;
  if (n >= 1024 * 1024) return (n / (1024 * 1024)).toFixed(1) + ' MB';
  return Math.max(1, Math.round(n / 1024)) + ' KB';
}

function isPreviewUrlInUse(url) {
  if (!url) return false;
  if (stagedVideos.some((x) => x.preview && x.preview.url === url)) return true;
  for (const p of videoPreviews.values()) if (p && p.url === url) return true;
  return false;
}

function rememberVideoPreview(sampleId, preview) {
  if (!sampleId || !preview || !preview.url) return;
  videoPreviews.set(sampleId, preview);
  while (videoPreviews.size > 20) {
    const oldest = videoPreviews.keys().next().value;
    const oldPrev = videoPreviews.get(oldest);
    videoPreviews.delete(oldest);
    if (oldPrev && oldPrev.url && !isPreviewUrlInUse(oldPrev.url)) {
      try { URL.revokeObjectURL(oldPrev.url); } catch (e) { /* ignore */ }
    }
  }
}

function forgetVideoPreview(sampleId) {
  const prev = videoPreviews.get(sampleId);
  videoPreviews.delete(sampleId);
  if (prev && prev.url && !isPreviewUrlInUse(prev.url)) {
    try { URL.revokeObjectURL(prev.url); } catch (e) { /* ignore */ }
  }
}

function dismissStagedVideo(uid) {
  const idx = stagedVideos.findIndex((x) => x.uid === uid);
  if (idx === -1) return;
  const [removed] = stagedVideos.splice(idx, 1);
  if (removed && removed.preview && removed.preview.url && !isPreviewUrlInUse(removed.preview.url)) {
    try { URL.revokeObjectURL(removed.preview.url); } catch (e) { /* ignore */ }
  }
}

/* Probes duration and 4 visual keyframes locally in-browser without uploading a single byte. */
function probeMediaPreview(item) {
  const preview = item.preview;
  if (!preview || !preview.url) return;
  const el = document.createElement(preview.isVideo ? 'video' : 'audio');
  el.preload = 'metadata';
  el.muted = true;
  el.playsInline = true;
  let settled = false;
  const finish = () => {
    if (settled) return;
    settled = true;
    preview.probing = false;
    try { el.removeAttribute('src'); el.load(); } catch (e) { /* ignore */ }
    refreshStagedCardDom(item);
  };
  const guard = setTimeout(finish, 4000);
  el.onerror = () => { clearTimeout(guard); finish(); };
  el.onloadedmetadata = async () => {
    if (Number.isFinite(el.duration) && el.duration > 0) preview.duration = el.duration;
    if (!preview.isVideo || !el.videoWidth || !el.videoHeight || !(preview.duration > 0)) {
      clearTimeout(guard);
      finish();
      return;
    }
    const w = Math.min(160, el.videoWidth);
    const h = Math.min(240, Math.max(90, Math.round((w * el.videoHeight) / el.videoWidth)));
    const canvas = document.createElement('canvas');
    canvas.width = w;
    canvas.height = h;
    const ctx = canvas.getContext('2d');
    const marks = [0.12, 0.37, 0.62, 0.86];
    for (const ratio of marks) {
      if (settled) break;
      const target = Math.min(Math.max(preview.duration * ratio, 0.05), Math.max(preview.duration - 0.05, 0.05));
      const ok = await new Promise((resolve) => {
        let timer = setTimeout(() => resolve(false), 850);
        el.onseeked = () => { clearTimeout(timer); resolve(true); };
        try { el.currentTime = target; } catch (e) { clearTimeout(timer); resolve(false); }
      });
      if (ok && ctx) {
        try {
          ctx.drawImage(el, 0, 0, w, h);
          preview.frames.push({ time: Math.round(target), dataUrl: canvas.toDataURL('image/jpeg', 0.7) });
        } catch (e) { break; }
      }
    }
    clearTimeout(guard);
    finish();
  };
  el.src = preview.url;
}

function keyframesStripHtml(frames) {
  if (!frames || !frames.length) return '';
  return `<div class="sv-frames" aria-label="${esc(t('vi_keyframes'))}">${frames.map((f) =>
    `<button type="button" class="sv-frame" data-seek="${esc(f.time)}" title="${esc(fmtClock(f.time))}">` +
    `<img src="${esc(f.dataUrl)}" alt=""><span>${esc(fmtClock(f.time))}</span></button>`).join('')}</div>`;
}

function refreshStagedCardDom(item) {
  const card = document.querySelector(`.sv-card[data-uid="${item.uid}"]`);
  if (!card) return;
  const metaEl = card.querySelector('[data-sv-meta]');
  if (metaEl) {
    const dur = item.preview.duration > 0 ? `${fmtClock(item.preview.duration)} · ` : '';
    metaEl.textContent = `${dur}${fmtBytes(item.size)}`;
  }
  const slot = card.querySelector('[data-sv-frames]');
  if (slot) slot.innerHTML = keyframesStripHtml(item.preview.frames);
}

function stageMediaFiles(files, extensions, repaint) {
  const allowed = (extensions && extensions.length ? extensions : DEFAULT_MEDIA_EXTS).map((x) => x.toLowerCase());
  let added = 0;
  for (const file of files) {
    const ext = (file.name.match(/\.([^.]+)$/) || ['', ''])[1].toLowerCase();
    if (!allowed.includes(ext)) {
      toast(tf('mt_bad_media', { name: file.name }));
      continue;
    }
    let url = '';
    try { url = URL.createObjectURL(file); } catch (e) { url = ''; }
    const item = {
      uid: 'sv-' + (++stagedSeq),
      file,
      name: file.name,
      size: file.size || 0,
      title: file.name.replace(/\.[^.]+$/, ''),
      caption: '',
      isSponsored: false,
      preview: { url, frames: [], duration: 0, isVideo: VIDEO_FILE_RE.test(file.name), name: file.name, probing: !!url },
      state: 'ready',
      stage: '',
      detail: '',
      jobId: null,
    };
    stagedVideos.unshift(item);
    added += 1;
    if (url) probeMediaPreview(item);
  }
  if (added && repaint) repaint();
}

function stagedVideoCardHtml(item, tr) {
  const p = item.preview || {};
  const mediaTag = p.url
    ? (p.isVideo
      ? `<video class="sv-player" controls preload="metadata" muted playsinline src="${esc(p.url)}"></video>`
      : `<audio class="sv-audio" controls preload="metadata" src="${esc(p.url)}"></audio>`)
    : '';
  const dur = p.duration > 0 ? `${fmtClock(p.duration)} · ` : (p.probing ? `${t('vi_probing')} · ` : '');
  const localReady = !!(tr && tr.supported && tr.installed && tr.model_ready);
  const aiCapable = !!(tr && tr.ai_video_capable);
  const aiProvider = (tr && tr.ai_video_provider) || '';
  let primary = 'manual';
  if (aiCapable && aiProvider === 'gemini') primary = 'ai';
  else if (localReady) primary = 'local';
  else if (aiCapable) primary = 'ai';

  const working = item.state === 'uploading' || item.state === 'working';
  const errBanner = item.state === 'failed'
    ? `<div class="banner" style="margin:8px 0">${esc(t('mt_failed') + (item.detail || ''))}</div>` : '';

  let controlsHtml;
  if (working) {
    const statusLabel = item.state === 'uploading' ? t('mt_uploading') : stageText(item.stage);
    controlsHtml = `<div class="dv-progress" style="margin-top:10px"><span class="dv-spin"></span><span>${esc(statusLabel)}</span></div>`;
  } else {
    const installBtn = tr && tr.supported && !localReady && !(tr.job && tr.job.status === 'running')
      ? `<button type="button" class="btn" data-sv-act="install-local">${esc(t('vi_btn_install_local'))}</button>` : '';
    controlsHtml = `${errBanner}<div class="sv-actions">
        ${aiCapable ? `<button type="button" class="btn ${primary === 'ai' ? 'primary' : ''}" data-sv-act="ai">${esc(t(aiProvider === 'gemini' ? 'vi_btn_gemini' : 'vi_btn_openai'))}</button>` : ''}
        ${localReady ? `<button type="button" class="btn ${primary === 'local' ? 'primary' : ''}" data-sv-act="local">${esc(t('vi_btn_local'))}</button>` : ''}
        <button type="button" class="btn ${primary === 'manual' ? 'primary' : ''}" data-sv-act="manual">${esc(t('vi_btn_manual'))}</button>
        ${installBtn}
      </div>
      <p class="hint sv-privacy">${esc(t('vi_privacy_note'))}</p>`;
  }

  return `<div class="card sv-card" data-uid="${esc(item.uid)}">
    <div class="sv-grid">
      <div class="sv-media">${mediaTag}<div data-sv-frames>${keyframesStripHtml(p.frames)}</div></div>
      <div class="sv-body">
        <div class="row">
          <span class="tag ok">${esc(t('mt_kind_video'))}</span>
          <span class="meta" data-sv-meta>${esc(dur + fmtBytes(item.size))}</span>
          <span class="spacer"></span>
          <label class="check"><input type="checkbox" data-sv-sponsored ${item.isSponsored ? 'checked' : ''} ${working ? 'disabled' : ''}> ${esc(t('mt_sponsored'))}</label>
          ${working ? '' : `<button type="button" class="btn ghost" data-sv-dismiss aria-label="${esc(t('close'))}">✕</button>`}
        </div>
        <input type="text" class="sv-input" data-sv-title value="${esc(item.title)}" placeholder="${esc(t('title_ph'))}" ${working ? 'disabled' : ''}>
        <input type="text" class="sv-input" data-sv-caption value="${esc(item.caption)}" placeholder="${esc(t('vi_caption_ph'))}" ${working ? 'disabled' : ''}>
        ${controlsHtml}
      </div>
    </div>
  </div>`;
}

async function runStagedVideoJob(item, useAi, repaint) {
  item.state = 'uploading';
  item.stage = '';
  item.detail = '';
  if (repaint) repaint();
  try {
    const res = await fetch('/api/uploads?name=' + encodeURIComponent(item.name), {
      method: 'PUT',
      headers: studioHeaders({ 'Content-Type': 'application/octet-stream' }),
      body: item.file,
    });
    const up = await res.json().catch(() => ({}));
    if (!res.ok) throw Object.assign(new Error(up.message || up.error || 'upload'), { message_zh: up.message_zh });
    const endpoint = useAi ? '/api/videos/ai' : '/api/videos';
    const body = {
      upload_id: up.upload_id,
      title: (item.title || '').trim() || item.name.replace(/\.[^.]+$/, ''),
      is_sponsored: !!item.isSponsored,
    };
    if (useAi) {
      if ((item.caption || '').trim()) body.caption = item.caption.trim();
      if (item.preview && item.preview.duration > 0) body.duration_sec = item.preview.duration;
    }
    const { job } = await api('POST', endpoint, body);
    item.jobId = job.id;
    item.state = 'working';
    item.stage = job.stage;
  } catch (e) {
    item.state = 'failed';
    item.detail = failText(e) || e.detail || e.message || '';
  }
  if (repaint) repaint();
}

async function runManualStagedVideo(item, repaint) {
  const payload = {
    title: (item.title || '').trim() || item.name.replace(/\.[^.]+$/, ''),
    caption: (item.caption || '').trim(),
    is_sponsored: !!item.isSponsored,
    source_name: item.name,
  };
  if (item.preview && item.preview.duration > 0) payload.duration_sec = Math.round(item.preview.duration * 10) / 10;
  try {
    const out = await manualBridge('video_ingest', payload, {
      title: t('vi_mb_title'),
      intro: tf('vi_mb_intro', { name: item.name }),
      step1: tf('vi_mb_step1', { name: item.name }),
    });
    if (!out || !out.sample) return;
    rememberVideoPreview(out.sample.id, item.preview);
    const idx = stagedVideos.indexOf(item);
    if (idx !== -1) stagedVideos.splice(idx, 1);
    toast(tf('mt_added_n', { n: 1 }));
    if (repaint) repaint();
  } catch (e) {
    showError(e);
  }
}

function visualIngestHtml(x) {
  const vis = x && x.timing && x.timing.visual_ingest;
  if (!vis || typeof vis !== 'object') return '';
  const energy = typeof vis.vocal_energy === 'string' && vis.vocal_energy.trim()
    ? `<div class="vi-line"><span class="tag ok">${esc(t('vi_tone_tag'))}</span><span>${esc(vis.vocal_energy)}</span></div>` : '';
  const flowers = Array.isArray(vis.onscreen_flower_texts) && vis.onscreen_flower_texts.length
    ? `<div class="vi-line"><span class="meta">${esc(t('vi_flower_label'))}</span>${vis.onscreen_flower_texts.slice(0, 14).map((f) => `<span class="tag">${esc(f)}</span>`).join('')}</div>` : '';
  if (!energy && !flowers) return '';
  return `<div class="vi-notes">${energy}${flowers}</div>`;
}

function sessionVideoReviewHtml(sampleId) {
  const p = videoPreviews.get(sampleId);
  if (!p || !p.url) return '';
  const player = p.isVideo
    ? `<video class="sv-player" controls preload="metadata" muted playsinline src="${esc(p.url)}"></video>`
    : `<audio class="sv-audio" controls preload="metadata" src="${esc(p.url)}"></audio>`;
  return `<div class="vi-review-media">${player}${keyframesStripHtml(p.frames)}</div>`;
}

function materialAddersHtml(tr, prefix) {
  const extensions = ((tr && tr.upload && tr.upload.extensions) || DEFAULT_MEDIA_EXTS)
    .map((x) => String(x).replace(/^\./, '').toLowerCase());
  const stagedHtml = stagedVideos.map((item) => stagedVideoCardHtml(item, tr)).join('');
  return `<div class="adders-drop" data-drop-zone="${esc(prefix)}">
      <div class="adders">
        <label class="adder adder-video" for="${esc(prefix)}-video"><b>${esc(t('mt_video'))}</b><small>${esc(t('mt_video_d'))}</small></label>
        <input type="file" id="${esc(prefix)}-video" multiple accept="${esc(extensions.map((x) => '.' + x).join(','))}" hidden>
        <label class="adder" for="${esc(prefix)}-subs"><b>${esc(t('mt_subs'))}</b><small>${esc(t('mt_subs_d'))}</small></label>
        <input type="file" id="${esc(prefix)}-subs" multiple accept=".srt,.vtt" hidden>
        <label class="adder" for="${esc(prefix)}-scripts"><b>${esc(t('mt_scripts'))}</b><small>${esc(t('mt_scripts_d'))}</small></label>
        <input type="file" id="${esc(prefix)}-scripts" multiple accept=".txt,.md,.docx,text/plain" hidden>
      </div>
      <p class="hint adders-drop-hint">${esc(t('vi_drop_hint'))}</p>
    </div>
    ${stagedHtml ? `<div class="sv-list" data-sv-list="${esc(prefix)}">${stagedHtml}</div>` : ''}`;
}

async function importSubtitleFiles(files) {
  let added = 0;
  for (const file of files) {
    try {
      await api('POST', '/api/samples', { title: file.name.replace(/\.[^.]+$/, ''), subtitle_text: await readTextFile(file), source_name: file.name });
      added += 1;
    } catch (err) { toast(tf('mt_bad_subs', { name: file.name })); }
  }
  return added;
}

function bindSeekClicks(container) {
  if (!container) return;
  container.addEventListener('click', (e) => {
    const seekBtn = e.target.closest('[data-seek]');
    if (!seekBtn) return;
    const holder = seekBtn.closest('.sv-media, .vi-review-media');
    const video = holder && holder.querySelector('video');
    if (video) {
      try { video.currentTime = Number(seekBtn.dataset.seek) || 0; } catch (err) { /* ignore */ }
    }
  });
}

function bindMaterialAdders(root, tr, prefix, repaint, allowedHashes) {
  const extensions = ((tr && tr.upload && tr.upload.extensions) || DEFAULT_MEDIA_EXTS)
    .map((x) => String(x).replace(/^\./, '').toLowerCase());
  const $scripts = document.getElementById(prefix + '-scripts');
  const $subs = document.getElementById(prefix + '-subs');
  const $video = document.getElementById(prefix + '-video');

  if ($scripts) {
    $scripts.onchange = async (e) => {
      const added = await importFiles(Array.from(e.target.files));
      if (added) toast(tf('mt_added_n', { n: added }));
      repaint();
    };
  }
  if ($subs) {
    $subs.onchange = async (e) => {
      const added = await importSubtitleFiles(Array.from(e.target.files));
      if (added) toast(tf('mt_added_n', { n: added }));
      repaint();
    };
  }
  if ($video) {
    $video.onchange = (e) => {
      stageMediaFiles(Array.from(e.target.files), extensions, repaint);
      e.target.value = '';
    };
  }

  const dropZone = root.querySelector(`[data-drop-zone="${prefix}"]`);
  if (dropZone) {
    dropZone.ondragover = (e) => { e.preventDefault(); dropZone.classList.add('drag-on'); };
    dropZone.ondragleave = (e) => { if (!dropZone.contains(e.relatedTarget)) dropZone.classList.remove('drag-on'); };
    dropZone.ondrop = async (e) => {
      e.preventDefault();
      dropZone.classList.remove('drag-on');
      const dropped = Array.from((e.dataTransfer && e.dataTransfer.files) || []);
      if (!dropped.length) return;
      const media = [];
      const subs = [];
      const scripts = [];
      for (const f of dropped) {
        const ext = (f.name.match(/\.([^.]+)$/) || ['', ''])[1].toLowerCase();
        if (extensions.includes(ext)) media.push(f);
        else if (ext === 'srt' || ext === 'vtt') subs.push(f);
        else if (['txt', 'md', 'docx'].includes(ext)) scripts.push(f);
        else toast(tf('mt_bad_media', { name: f.name }));
      }
      let count = 0;
      if (scripts.length) count += await importFiles(scripts);
      if (subs.length) count += await importSubtitleFiles(subs);
      if (count) toast(tf('mt_added_n', { n: count }));
      if (media.length) stageMediaFiles(media, extensions, repaint);
      else if (count) repaint();
    };
  }

  const svList = root.querySelector(`[data-sv-list="${prefix}"]`);
  if (svList) {
    bindSeekClicks(svList);
    svList.oninput = (e) => {
      const card = e.target.closest('.sv-card');
      if (!card) return;
      const item = stagedVideos.find((x) => x.uid === card.dataset.uid);
      if (!item) return;
      if (e.target.dataset.svTitle !== undefined) item.title = e.target.value;
      else if (e.target.dataset.svCaption !== undefined) item.caption = e.target.value;
    };
    svList.onchange = (e) => {
      const card = e.target.closest('.sv-card');
      if (!card || e.target.dataset.svSponsored === undefined) return;
      const item = stagedVideos.find((x) => x.uid === card.dataset.uid);
      if (item) item.isSponsored = e.target.checked;
    };
    svList.onclick = async (e) => {
      const card = e.target.closest('.sv-card');
      if (!card) return;
      const item = stagedVideos.find((x) => x.uid === card.dataset.uid);
      if (!item) return;
      if (e.target.closest('[data-sv-dismiss]')) {
        dismissStagedVideo(item.uid);
        repaint();
        return;
      }
      const actBtn = e.target.closest('[data-sv-act]');
      if (!actBtn) return;
      const act = actBtn.dataset.svAct;
      if (act === 'local') await runStagedVideoJob(item, false, repaint);
      else if (act === 'ai') await runStagedVideoJob(item, true, repaint);
      else if (act === 'manual') await runManualStagedVideo(item, repaint);
      else if (act === 'install-local') {
        try { await api('POST', '/api/transcriber/install', {}); repaint(); } catch (err) { showError(err); }
      }
    };
  }

  /* Poll running install or video ingestion jobs on whichever material view is open. */
  const installing = tr && tr.job && tr.job.status === 'running';
  const working = stagedVideos.some((j) => j.state === 'working');
  if (installing) {
    pollSetup(async () => {
      const now = await api('GET', '/api/transcriber');
      repaint();
      return !(now.job && now.job.status === 'running');
    }, 2000, allowedHashes);
  } else if (working) {
    const checkWorking = async () => {
      let changed = false;
      for (const j of stagedVideos.filter((x) => x.jobId && x.state === 'working')) {
        const { job } = await api('GET', '/api/jobs/' + j.jobId);
        const state = job.status === 'done' ? 'done' : (job.status === 'failed' ? 'failed' : 'working');
        if (state !== j.state || job.stage !== j.stage) {
          j.state = state;
          j.stage = job.stage;
          j.detail = failText(job.error);
          if (state === 'done' && job.result && job.result.sample_id) {
            rememberVideoPreview(job.result.sample_id, j.preview);
          }
          changed = true;
        }
      }
      if (changed) {
        let finished = 0;
        for (let i = stagedVideos.length - 1; i >= 0; i--) {
          if (stagedVideos[i].state === 'done') {
            stagedVideos.splice(i, 1);
            finished += 1;
          }
        }
        if (finished) toast(tf('mt_added_n', { n: finished }));
        repaint();
      }
      return !stagedVideos.some((x) => x.state === 'working');
    };
    setTimeout(() => { checkWorking().catch(() => {}); }, 150);
    pollSetup(checkWorking, 900, allowedHashes);
  }
}

function sufficiencyNote(m) {
  if (m.organic < 3) return `<div class="banner">${esc(t('mt_thin'))}</div>`;
  const cadence = m.enough_for_cadence ? '' : ` ${t('mt_cadence_short')}`;
  return `<p class="hint">${esc(t(m.organic < 5 ? 'mt_ok' : 'mt_good') + cadence)}</p>`;
}

function rateLine(measured) {
  return measured ? tf('mt_meter_rate', { r: measured.speaking_rate_cpm, d: fmtSpan(measured.spoken_sec) }) : t('mt_meter_norate');
}

function transcriberHtml(tr) {
  let body;
  if (!tr.supported) {
    const reason = (lang === 'zh' ? tr.reason_zh : tr.reason_en) || tr.reason || '';
    body = `<p class="hint">${esc(t('tr_local_unsupported') + reason)}</p><p class="hint">${esc(t('tr_local_use_subs'))}</p>`;
  } else if (tr.job && tr.job.status === 'running') {
    const pct = tr.job.fraction === null || tr.job.fraction === undefined ? null : Math.round(tr.job.fraction * 100);
    body = `<p class="hint">${esc(t('tr_local_installing') + stageText(tr.job.stage))}${pct === null ? '' : ` ${pct}%`}</p>
      <div class="meter"><i style="width:${pct === null ? 8 : pct}%"></i></div>`;
  } else if (tr.installed && tr.model_ready) {
    body = `<p class="hint">${esc(t('tr_local_ready'))} <a href="#" id="tr-remove">${esc(t('tr_local_remove'))}</a></p>
      <p class="hint">${esc(t('tr_local_caution'))}</p>`;
  } else {
    const failed = tr.job && tr.job.status === 'failed' ? `<div class="banner">${esc(t('tr_local_failed') + failText(tr.job.error))}</div>` : '';
    body = `${failed}<p class="hint">${esc(tf('tr_local_need', { where: tr.location || '' }))}</p><p class="hint">${esc(t('tr_local_caution'))}</p>
      <div class="row" style="margin-top:10px"><button class="btn" id="tr-install">${esc(t('tr_local_install'))}</button></div>`;
  }
  return `<div class="card soft"><h3>${esc(t('tr_local_title'))}</h3>${body}</div>`;
}

/* The notice on a machine transcript the creator has not confirmed. Also used on the Library page. */
function reviewNoteHtml(x, withButton) {
  if (poWaits(x)) return poReviewNoteHtml(x, withButton);     // a posted script, not a transcript: posted.js says why it waits
  const n = x.timing && x.timing.unclear_dropped;
  const unclear = n ? ' ' + tf(countKey('rn_unclear', n), { n }) : '';
  const button = withButton === false ? '' : `<div class="row" style="margin-top:8px"><button type="button" class="btn primary" data-confirm>${esc(t('rn_confirm'))}</button></div>`;
  return `<div class="banner review-note"><b>${esc(t('rn_title'))}</b> ${esc(t('rn_body') + unclear)}${button}</div>`;
}

function materialRowHtml(x) {
  const kind = ['script', 'subtitle', 'video'].includes(x.kind) ? x.kind : 'script';
  const timed = x.timing && x.timing.speaking_rate_cpm
    ? ` · ${tf('mt_timed', { d: fmtSpan(x.timing.spoken_sec), r: x.timing.speaking_rate_cpm })}` : '';
  const mediaSide = x.needs_review && !poWaits(x) ? sessionVideoReviewHtml(x.id) : '';
  const review = x.needs_review ? `<div class="mat-review">${reviewNoteHtml(x, false)}
      <div class="vi-review-split">${mediaSide}<div class="vi-review-text"><textarea data-review-text rows="5">${esc(x.text)}</textarea>
      <div class="row" style="margin-top:8px"><button type="button" class="btn primary" data-confirm>${esc(poConfirmLabel(x))}</button></div></div></div></div>` : '';
  return `<div class="mat-wrap" data-id="${esc(x.id)}"><div class="mat">
    <span class="tag ${kind === 'script' ? '' : 'ok'}">${esc(t('mt_kind_' + kind))}</span>${poRowTag(x)}
    <span class="mat-title">${esc(x.title)}<small>${esc(tf('mt_units', { n: spokenUnits(x.text) }) + timed)}</small></span>
    <label class="check"><input type="checkbox" data-sponsored ${x.is_sponsored ? 'checked' : ''}> ${esc(t('mt_sponsored'))}</label>
    <button class="btn ghost" data-del aria-label="${esc(t('delete'))}">✕</button></div>${visualIngestHtml(x)}${review}</div>`;
}

async function paintStepMaterial(el, s) {
  const [lib, tr] = await Promise.all([api('GET', '/api/samples'), api('GET', '/api/transcriber')]);
  const ready = tr.supported && tr.installed && tr.model_ready;
  const unreviewed = s.material.unreviewed || 0;
  el.innerHTML = `${materialAddersHtml(tr, 'mt')}
    ${transcriberHtml(tr)}
    <details class="card soft"><summary>${esc(t('mt_paste'))}</summary>
      <textarea id="mt-paste" rows="4" placeholder="${esc(t('mt_paste_ph'))}"></textarea>
      <div class="row" style="margin-top:10px"><button class="btn" id="mt-paste-add">${esc(t('mt_paste_add'))}</button></div></details>
    <p class="meta">${esc(tf('mt_meter_count', { n: s.material.organic, c: s.material.clauses }))} ${esc(rateLine(lib.measured))}
      ${unreviewed ? esc(tf(countKey('mt_unreviewed', unreviewed), { n: unreviewed })) : ''}</p>
    ${sufficiencyNote(s.material)}
    <div id="mt-list">${lib.samples.map(materialRowHtml).join('') || (stagedVideos.length ? '' : `<div class="empty">${esc(t('mt_none'))}</div>`)}</div>
    <p class="hint">${esc(t('mt_metrics_later'))} ${ready || tr.ai_video_capable ? esc(t('mt_video_removed')) : ''}</p>
    <div class="row" style="margin-top:18px"><button class="btn primary big" id="mt-next" ${s.material.organic ? '' : 'disabled'}>${esc(t('st_continue'))}</button></div>`;
  el.querySelectorAll('[data-review-text]').forEach((x) => { autoGrow(x); x.oninput = () => autoGrow(x); });

  const repaint = () => renderSetup('material');
  bindMaterialAdders(el, tr, 'mt', repaint, ['#/start']);
  document.getElementById('mt-paste-add').onclick = async () => {
    try { await api('POST', '/api/samples', { text: document.getElementById('mt-paste').value }); toast(tf('mt_added_n', { n: 1 })); repaint(); }
    catch (err) { showError(err); }
  };
  document.getElementById('mt-next').onclick = () => renderSetup('voice');
  const $install = document.getElementById('tr-install');
  if ($install) $install.onclick = async () => { try { await api('POST', '/api/transcriber/install', {}); repaint(); } catch (err) { showError(err); } };
  const $remove = document.getElementById('tr-remove');
  if ($remove) $remove.onclick = async (e) => {
    e.preventDefault();
    if (!(await askConfirm(t('tr_local_confirm_remove'), { ok: t('delete'), danger: true }))) return;
    try { await api('DELETE', '/api/transcriber'); repaint(); } catch (err) { showError(err); }
  };
  const $list = document.getElementById('mt-list');
  bindSeekClicks($list);
  $list.onchange = async (e) => {
    const row = e.target.closest('[data-id]');
    if (!row || e.target.dataset.sponsored === undefined) return;
    try { await api('PUT', '/api/samples/' + row.dataset.id, { is_sponsored: e.target.checked }); repaint(); } catch (err) { showError(err); }
  };
  $list.onclick = async (e) => {
    const row = e.target.closest('[data-id]');
    if (!row) return;
    try {
      if (e.target.closest('[data-confirm]')) {
        /* save what is in the box first, so what gets confirmed is what the creator is looking at */
        await api('PUT', '/api/samples/' + row.dataset.id, { text: row.querySelector('[data-review-text]').value, needs_review: false });
        toast(t('rn_confirmed'));
        repaint();
      } else if (e.target.closest('[data-del]')) {
        if (!(await askConfirm(poDeleteSampleText(row.dataset.id, 'mt_confirm_delete'), { ok: t('delete'), danger: true }))) return;
        forgetVideoPreview(row.dataset.id);
        await api('DELETE', '/api/samples/' + row.dataset.id);
        repaint();
      }
    } catch (err) { showError(err); }
  };
}

/* ---------- step 3: voice ---------- */
function measuredHtml(lib) {
  const st = lib.stats || {};
  if (!st.clauses) return `<p class="hint">${esc(t('vo_m_none'))}</p>`;
  const len = st.clause_length_units || {};
  const particles = (st.clause_final_particles || []).slice(0, 6)
    .map((p) => `<span class="tag">${esc(p.particle)} <small>${esc(p.count)}</small></span>`).join('');
  const english = (st.top_english_tokens || []).slice(0, 10).map((w) => `<span class="tag">${esc(w)}</span>`).join('');
  const rate = lib.measured ? tf('vo_m_rate_measured', { r: lib.measured.speaking_rate_cpm, d: fmtSpan(lib.measured.spoken_sec) }) : t('vo_m_rate_default');
  const row = (key, html) => (html ? `<dt>${esc(t(key))}</dt><dd>${html}</dd>` : '');
  return `<dl class="kv">
    ${row('vo_m_clause', esc(tf('vo_m_clause_v', { a: len.p5, b: len.p95, m: len.median, c: st.clauses })))}
    ${row('vo_m_particles', particles)}${row('vo_m_english', english)}${row('vo_m_rate', esc(rate))}</dl>`;
}

function evidenceStatusText(item) {
  const titles = titleList(item.titles);
  if (item.status === 'found') return tf(item.sponsored_only ? 'rv_found_sponsored' : 'rv_found', { n: item.count, titles });
  if (item.status === 'conflict') return tf('rv_conflict', { n: item.count, titles });
  if (item.status === 'not_found') return t(item.kind === 'quote' ? 'rv_not_found_quote' : 'rv_not_found');
  return t(item.status === 'absent' ? 'rv_absent' : 'rv_not_checkable');
}

/* Report groups are profile paths (see GROUPS in studio/evidence.py). */
const EVIDENCE_GROUP_KEYS = {
  'persona_summary.audience_address_terms.uses': 'evg_address',
  'persona_summary.audience_address_terms.never_uses': 'evg_never_address',
  'persona_summary.husband_and_family_references.spoken_nicknames_zh_en': 'evg_nicknames',
  'persona_summary.husband_and_family_references.verbatim_examples': 'evg_partner',
  'persona_summary.regional_and_phonetic_nuances.preserve_colloquialisms': 'evg_colloquial',
  'lexical_fingerprint.signature_openers': 'evg_openers',
  'lexical_fingerprint.discourse_connectors.high_frequency': 'evg_connectors',
  'lexical_fingerprint.discourse_connectors.forbidden_bookish_connectors': 'evg_never_connectors',
  'lexical_fingerprint.modal_particles.primary': 'evg_particles',
  'lexical_fingerprint.modal_particles.rare_or_zero': 'evg_never_particles',
  'lexical_fingerprint.genuine_praise_lexicon': 'evg_praise',
  'code_switching_grammar.good_vs_bad_examples': 'evg_codeswitch',
  'anti_voice_blacklist.banned_phrases_zh': 'evg_never_phrases',
};

/* Free text is listed under the field it sits in (``field`` of unverified_fields in studio/evidence.py). */
const FREE_TEXT_KEYS = {
  'persona_summary.one_line_essence_zh': 'rvf_essence',
  'persona_summary.one_line_essence_en': 'rvf_essence_en',
  'persona_summary.husband_and_family_references.couple_dynamic_tone': 'rvf_couple_tone',
  'persona_summary.regional_and_phonetic_nuances.accent_region_notes': 'rvf_accent',
  'cadence_and_syntax.sentence_structure_habits': 'rvf_sentences',
  'cadence_and_syntax.punctuation_and_pause_style': 'rvf_pauses',
  'lexical_fingerprint.signature_openers.pattern_zh': 'rvf_opener_patterns',
  'lexical_fingerprint.signature_openers.when_used_en': 'rvf_opener_when',
  'lexical_fingerprint.modal_particles.primary.placement_rule': 'rvf_particle_rules',
  'code_switching_grammar.overall_density': 'rvf_mix_density',
  'code_switching_grammar.natural_english_categories': 'rvf_mix_natural',
  'code_switching_grammar.never_code_switch_rules': 'rvf_mix_never',
  'code_switching_grammar.good_vs_bad_examples.fake_chinglish_to_avoid': 'rvf_mix_forced',
  'beat_playbooks.vlog_hook_formulas': 'rvf_vlog_hooks',
  'beat_playbooks.product_share_structure': 'rvf_product_share',
  'beat_playbooks.sponsor_soft_pivot_formulas': 'rvf_sponsor_pivot',
  'beat_playbooks.closing_formulas': 'rvf_closings',
  'onscreen_flower_text_and_caption_dna.flower_text_style': 'rvf_flower_text',
  'onscreen_flower_text_and_caption_dna.title_formulas': 'rvf_titles',
  'onscreen_flower_text_and_caption_dna.caption_and_emoji_habits': 'rvf_captions',
  'anti_voice_blacklist.learned_from_her_edits': 'rvf_from_edits',
};

function reviewHtml(p) {
  const items = (p.report && p.report.items) || [];
  const freeItems = ((p.report && p.report.unverified) || []).filter((f) => f && typeof f.path === 'string' && f.text);
  const sum = (p.report && p.report.summary) || {};
  const groups = [];
  items.forEach((it) => {
    let g = groups.find((x) => x.name === it.group);
    if (!g) { g = { name: it.group, items: [] }; groups.push(g); }
    g.items.push(it);
  });
  const label = (name) => (EVIDENCE_GROUP_KEYS[name] ? t(EVIDENCE_GROUP_KEYS[name]) : name);
  /* Two outcomes are not up for a vote: a ban the material contradicts is always dropped, and a line presented
     as a quote that cannot be found is never kept as one. */
  const isFixed = (it) => it.status === 'conflict' || (it.kind === 'quote' && it.status === 'not_found');
  const ticked = (it) => !isFixed(it) && (it.status === 'found' || it.status === 'absent' || it.status === 'not_checkable');
  const rows = (g) => g.items.map((it) => {
    const fixed = isFixed(it);
    /* What the material did not bear out stays only when the server is told so (`keep`): a tick alone is not enough. */
    const needsKeep = !fixed && (it.status === 'not_found' || it.status === 'not_checkable');
    return `<label class="ev ${esc(it.status)}"><input type="checkbox" data-path="${esc(it.path)}"${needsKeep ? ' data-needs-keep="1"' : ''} ${ticked(it) ? 'checked' : ''} ${fixed ? 'disabled' : ''}>
      <span class="ev-text">${esc(it.text)}</span><span class="ev-status">${esc(evidenceStatusText(it))}</span></label>`;
  }).join('');
  /* A list of "never says" entries that all checked out is long and uneventful: fold it, but keep it one click away. */
  const group = (g) => (g.items.every((it) => it.status === 'absent')
    ? `<details class="ev-fold"><summary>${esc(label(g.name))} · ${g.items.length} · ${esc(t('rv_absent'))}</summary>${rows(g)}</details>`
    : `<h4>${esc(label(g.name))}</h4>${rows(g)}`);
  /* Free text: what the AI wrote that nothing can be looked up for. Every piece is shown, under the name of the
     field it sits in, because every piece that stays is given to the AI with each draft, and the server keeps
     only what this page sends back as ticked. A note that describes an entry above (the pattern of an opener,
     where a particle goes) leaves with that entry: it says which entry that is, and its box is unticked and
     locked while the entry's box is not ticked (paintStepVoice keeps the two together). */
  const byPath = {};
  items.forEach((it) => { byPath[it.path] = it; });
  const fields = [];
  freeItems.forEach((f) => {
    let g = fields.find((x) => x.name === f.field);
    if (!g) { g = { name: f.field, items: [] }; fields.push(g); }
    g.items.push(f);
  });
  const freeRow = (f) => {
    const owner = f.belongs_to ? byPath[f.belongs_to] : null;
    const about = owner ? `<span class="ev-status">${esc(tf('rvf_about', { text: owner.text }))}</span><span class="ev-off">${esc(t('rvf_goes_with'))}</span>` : '';
    return `<label class="ev free-text"><input type="checkbox" data-free-path="${esc(f.path)}"${owner ? ` data-owner="${esc(owner.path)}"` : ''} ${!owner || ticked(owner) ? 'checked' : 'disabled'}>
      <span class="ev-text">${esc(f.text)}</span>${about}</label>`;
  };
  const freeHtml = fields.map((g) => `<h4>${esc(FREE_TEXT_KEYS[g.name] ? t(FREE_TEXT_KEYS[g.name]) : g.name)}</h4>${g.items.map(freeRow).join('')}`).join('');
  const judgment = freeItems.length ? `<div class="card soft review"><h3>${esc(t('rv_summary_title'))}</h3><p class="hint">${esc(t('rv_summary_d'))}</p>
      ${freeHtml}</div>` : '';
  return `<div class="card review"><h3>${esc(t('rv_title'))}</h3><p class="hint">${esc(t('rv_intro'))}</p>
      <p>${esc(tf('rv_summary', { n: sum.checked || 0, f: sum.found || 0, m: sum.not_found || 0, a: sum.absent || 0, c: sum.conflict || 0 }))}</p>
      ${groups.map(group).join('')}</div>
    ${judgment}
    <div class="row" style="margin-top:14px"><button class="btn primary big" id="rv-approve">${esc(t('rv_approve'))}</button>
      <button class="btn" id="rv-discard">${esc(t('rv_discard'))}</button></div>`;
}

async function paintStepVoice(el, s) {
  const [lib, pend] = await Promise.all([api('GET', '/api/samples'), api('GET', '/api/voice/pending')]);
  const p = pend.pending;
  const measured = `<div class="card soft"><h3>${esc(t('vo_measured'))}</h3><p class="hint">${esc(t('vo_measured_d'))}</p>${measuredHtml(lib)}</div>`;
  let action;
  if (p) action = reviewHtml(p);
  else if (!s.material.organic) action = `<p class="hint">${esc(t('vo_need_material'))}</p>`;
  else if (!s.ai.configured) {
    action = `<p class="hint">${esc(t('vo_need_ai'))}</p><div class="row"><button class="btn primary" id="vo-manual">${esc(t('vo_use_manual'))}</button>
      <button class="btn" id="vo-import-json">${esc(t('vo_import_json'))}</button>
      <a class="btn" href="#/settings">${esc(t('vo_other_ai'))}</a></div>`;
  } else {
    const active = s.voice.state === 'active';
    const status = active ? `<p>${esc(tf('vo_active', { n: s.voice.based_on, d: s.voice.learned_at || '' }))}
        ${s.voice.new_since ? esc(tf('vo_new_since', { n: s.voice.new_since })) : ''} <a href="#/voice">${esc(t('vo_open'))}</a></p>` : '';
    action = `${status}<p class="hint">${esc(t('vo_sends'))}</p>
      <div class="row"><button class="btn ${active ? '' : 'primary big'}" id="vo-propose">${esc(t(active ? 'vo_again' : 'vo_propose'))}</button>
        <button class="btn ${active ? '' : 'big'}" id="vo-import-json">${esc(t('vo_import_json'))}</button>
        ${active ? `<button class="btn primary big" id="vo-next">${esc(t('st_continue'))}</button>` : ''}</div>`;
  }
  el.innerHTML = measured + action;

  /* A note leaves with the entry it describes (reviewHtml): while that entry's box is not ticked, the note's is
     unticked and locked; ticked again, the note comes back ticked. */
  el.querySelectorAll('.ev input[data-path]').forEach((box) => {
    box.onchange = () => el.querySelectorAll('.ev input[data-owner]').forEach((note) => {
      if (note.dataset.owner !== box.dataset.path) return;
      note.disabled = !box.checked;
      note.checked = box.checked;
    });
  });

  const on = (id, fn) => { const x = document.getElementById(id); if (x) x.onclick = fn; };
  on('vo-manual', async () => {
    try { await api('POST', '/api/settings', { provider: 'manual' }); await router(); } catch (err) { showError(err); }
  });
  on('vo-propose', (e) => busy(e.currentTarget, appState.settings.manual ? e.currentTarget.textContent : t('vo_proposing'), async () => {
    if (!(await aiRun('voice', {}))) return;
    await renderSetup('voice');
  }));
  on('vo-import-json', async () => {
    try {
      if (!(await manualBridge('voice', {}, { replyOnly: true, title: t('mb_import_voice_title'), intro: t('mb_import_voice_intro') }))) return;
      await renderSetup('voice');
    } catch (err) { showError(err); }
  });
  on('vo-next', () => renderSetup('script'));
  on('rv-approve', (e) => busy(e.currentTarget, e.currentTarget.textContent, async () => {
    /* What the server needs (POST /api/voice/approve): `created_at`, which names the proposal on this screen, so
       that one that replaced it meanwhile is not accepted unseen; `rejected`, what was unticked; and `keep`, what
       is ticked among the things the material does not bear out, free text included. A locked box is not sent:
       what it stands for is decided already. */
    const evBoxes = Array.from(el.querySelectorAll('.ev input[data-path]')).filter((x) => !x.disabled);
    const freeBoxes = Array.from(el.querySelectorAll('.ev input[data-free-path]')).filter((x) => !x.disabled);
    const rejected = evBoxes.filter((x) => !x.checked).map((x) => x.dataset.path)
      .concat(freeBoxes.filter((x) => !x.checked).map((x) => x.dataset.freePath));
    const keep = evBoxes.filter((x) => x.checked && x.dataset.needsKeep === '1').map((x) => x.dataset.path)
      .concat(freeBoxes.filter((x) => x.checked).map((x) => x.dataset.freePath));
    try {
      await api('POST', '/api/voice/approve', { created_at: p.created_at, keep, rejected });
      toast(t('rv_approved'));
      setupUi.open = 'script';
      await router();
    } catch (err) {
      if (err && (err.code === 'stale_proposal' || err.code === 'no_pending')) {
        showError(err);
        await renderSetup('voice');
        return;
      }
      throw err;
    }
  }));
  on('rv-discard', async () => {
    try { await api('DELETE', '/api/voice/pending'); toast(t('rv_discarded')); await renderSetup('voice'); } catch (err) { showError(err); }
  });
}

/* ---------- step 4: first script ---------- */
function paintStepScript(el, s) {
  el.innerHTML = `${s.voice.state === 'active' ? '' : `<div class="banner">${esc(t('sc_need_voice'))}</div>`}
    <p>${esc(t('sc_intro'))}</p>${s.first_script.done ? `<p class="hint">${esc(tf('sc_done', { n: s.first_script.count }))}</p>` : ''}
    <div class="row" style="margin-top:14px"><a class="btn primary big" href="#/new">${esc(t('sc_go'))}</a>
      <button type="button" class="btn big" id="sc-import-json">${esc(t('import_script_json'))}</button></div>`;
  const $imp = document.getElementById('sc-import-json');
  if ($imp) $imp.onclick = async () => {
    try {
      const script = await manualBridge('draft', { lang, notes: '', target_sec: 60, video_format: 'VLOG_VOICEOVER', keywords: [] },
        { replyOnly: true, title: t('mb_import_script_title'), intro: t('mb_import_script_intro') });
      if (script && script.id) location.hash = '#/s/' + script.id;
    } catch (err) { showError(err); }
  };
}

/* ---------- step 5: keeps learning ---------- */
function lessonHtml(x) {
  return `<div class="lesson" data-id="${esc(x.id)}"><div><small>${esc(t('gr_ai_wrote'))}</small><del>${esc(x.ai || t('ed_added'))}</del></div>
    <div><small>${esc(t('gr_you_wrote'))}</small><ins>${esc(x.final || t('ed_deleted'))}</ins></div>
    <button class="btn ghost" data-forget>${esc(t('gr_forget'))}</button></div>`;
}

async function paintStepGrow(el, s) {
  const data = await api('GET', '/api/lessons');
  const suggestions = (data.suggestions || []).map((x) => `<span class="kw">${esc(x.phrase)}<small>${esc(tf('gr_suggest_n', { n: x.scripts }))}</small>
      <button type="button" data-never="${esc(x.phrase)}">${esc(t('gr_suggest_add'))}</button></span>`).join('');
  el.innerHTML = `<h4>${esc(t('gr_edits_title'))}</h4><p>${esc(t('gr_edits_d'))}</p>
    <p class="meta">${esc(data.lessons.length ? tf('gr_lessons_n', { n: data.lessons.length }) : t('gr_lessons_none'))}</p>
    <div id="gr-lessons">${data.lessons.map(lessonHtml).join('')}</div>
    ${suggestions ? `<p class="hint">${esc(t('gr_suggest'))}</p><div class="chips" id="gr-suggest">${suggestions}</div>` : ''}
    <h4>${esc(t('gr_fresh_title'))}</h4><p>${esc(t('gr_fresh_d'))}</p>
    ${s.voice.state === 'active' && s.voice.new_since ? `<p class="hint">${esc(tf('vo_new_since', { n: s.voice.new_since }))}</p>` : ''}`;
  el.onclick = async (e) => {
    const forget = e.target.closest('[data-forget]');
    const never = e.target.closest('[data-never]');
    try {
      if (forget) { await api('DELETE', '/api/lessons/' + forget.closest('.lesson').dataset.id); await renderSetup('grow'); }
      else if (never) {
        const c = await api('GET', '/api/creator');
        await api('PUT', '/api/creator', { never: (c.never || []).concat([never.dataset.never]) });
        toast(t('st_saved'));
        await renderSetup('grow');
      }
    } catch (err) { showError(err); }
  };
}

/* ---------- script editor: what a draft was based on, and what was changed since ---------- */
function paintBasis(el, script) {
  if (!el) return;
  const b = script.basis;
  if (!b) { el.innerHTML = ''; return; }
  const titles = (b.exemplar_titles || []).map((x) => `<span class="tag">${esc(x)}</span>`).join('');
  const openings = (Array.isArray(b.opening_titles) ? b.opening_titles : []).map((x) => `<span class="tag">${esc(x)}</span>`).join('');
  const keywords = (script.keywords || []).map((x) => `<span class="tag ok">${esc(x)}</span>`).join('');
  el.innerHTML = `<details class="card soft"><summary>${esc(t('bs_title'))}</summary>
    <p>${titles ? esc(t('bs_exemplars')) + titles : esc(t('bs_none'))}</p>
    ${openings ? `<p id="bs-openings">${esc(t('bs_openings'))}${openings}</p>` : ''}
    <p class="hint">${esc(b.profile === 'learned' ? tf('bs_profile_yours', { d: b.learned_at || '' }) : t('bs_profile_example'))}
      ${b.lessons_used ? ' · ' + esc(tf('bs_lessons', { n: b.lessons_used })) : ''}</p>
    ${keywords ? `<p class="hint">${esc(t('bs_keywords'))}${keywords}</p>` : ''}</details>`;
}

function paintEdits(el, script) {
  if (!el) return;
  if (!script.has_ai_draft) { el.innerHTML = ''; return; }
  el.innerHTML = `<details class="card soft" id="ed-box"><summary>${esc(t('ed_title'))}</summary><div id="ed-body"></div></details>`;
  const $box = document.getElementById('ed-box');
  const load = async () => {
    const $body = document.getElementById('ed-body');
    await flushSave();
    const { changes } = await api('GET', '/api/scripts/' + script.id + '/changes');
    $body.innerHTML = `<p class="hint">${esc(t(changes.length ? 'ed_intro' : 'ed_none'))}</p>` + changes.map((c, i) =>
      `<div class="lesson" data-i="${i}"><div><small>${esc(t('gr_ai_wrote'))}</small><del>${esc(c.ai || t('ed_added'))}</del></div>
        <div><small>${esc(t(c.by_ai ? 'ed_ai_text' : 'gr_you_wrote'))}</small><ins>${esc(c.final || t('ed_deleted'))}</ins></div>
        ${c.by_ai ? `<span class="meta" data-ai-text>${esc(t('ed_ai_note'))}</span>`
    : (c.kind === 'added' ? '<span></span>' : `<button class="btn ${c.lesson_id ? 'ghost' : ''}" data-remember ${c.lesson_id ? 'disabled' : ''}>${esc(t(c.lesson_id ? 'ed_remembered' : 'ed_remember'))}</button>`)}</div>`).join('');
    $body.onclick = async (e) => {
      const btn = e.target.closest('[data-remember]');
      if (!btn) return;
      const c = changes[Number(btn.closest('.lesson').dataset.i)];
      try { await api('POST', '/api/lessons', { script_id: script.id, ai: c.ai, final: c.final }); await load(); } catch (err) { showError(err); }
    };
  };
  $box.ontoggle = () => { if ($box.open) load().catch(showError); };
}
