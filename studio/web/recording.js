'use strict';
/* Voiceover recording booth, audio-aligned subtitles, spoken ad-lib diff review & FCPXML 1.9 timeline export
   (studio/recording.py, studio/exports.py). Loaded before pages.js.

   Mounted inside <div id="rc-box"> on #/s/<id> (#/script/<id>):
   - Summary header always displays the active subtitle timing badge:
     rate estimate, Whisper word/segment alignment, imported .srt/.vtt alignment, or speech-pause alignment.
   - Browser microphone recorder encodes 16 kHz mono 16-bit PCM WAV directly in memory and sends base64 to
     POST /api/scripts/<id>/takes (no raw audio blob is stored in SQLite; temporary uploads are deleted immediately).
   - File attachment accepts .wav, .mp3, .m4a, .mp4, .mov, .webm, .srt and .vtt.
   - Direct FCPXML 1.9 timeline download button plus Export menu entry.
   - Spoken ad-lib diff review with one-click update of the script's spoken line.

   Everything rendered passes through esc(). No inline event attributes, no form elements, no external requests. */

Object.assign(I18N.zh, {
  exp_fcpxml: '时间线（FCPXML 1.9）',
  exp_fcpxml_d: '竖屏 1080×1920 30fps，带字幕轨、花字轨与镜头标记（Final Cut / 达芬奇）',
  rc_title: '录音对齐与时间线',
  rc_mode_rate: '按语速估算',
  rc_mode_whisper: '录音词级对齐',
  rc_mode_subtitle: '字幕轴对齐',
  rc_mode_pause: '录音停顿对齐',
  rc_adlib_badge: '{n} 处口播差异',
  rc_explain_rate: '当前导出的 .srt、花字、剪映素材包和 FCPXML 均按字数与语速估算时间。在下方直接录一段干声，或导入录音 / .srt / .vtt，即可按真实时间轴导出。',
  rc_explain_whisper: '当前使用「{label}」的本机 Whisper 转写时间轴导出字幕、花字、剪映素材包与 FCPXML 1.9（不联网，原始音频不写入数据库）。',
  rc_explain_subtitle: '当前使用「{label}」导入的字幕时间轴导出字幕、花字、剪映素材包与 FCPXML 1.9。',
  rc_explain_pause: '当前使用「{label}」的语音/停顿切分时间轴导出：按录音中 ≥0.25 秒的静音停顿切出语段，并按每口气字数比例分配时间（未经语音识别转写，因此不会检测即兴改词）。',
  rc_privacy: '录音与文件只在这台电脑本机分析，提取出时间轴与声学统计后立即删除临时文件，绝不把音频存进数据库。',
  rc_label_ph: '这一版备注（可不填，例如：第一遍干声）',
  rc_mic_start: '🎙 录一段口播',
  rc_mic_stop: '停止录音',
  rc_mic_asking: '请在浏览器弹窗中允许使用麦克风（再点一次取消等待）…',
  rc_mic_denied: '无法使用麦克风。请检查系统或浏览器的麦克风权限，或直接上传录音文件。',
  rc_mic_no_browser: '当前浏览器不支持直接录音，请用旁边的按钮选择录音或字幕文件。',
  rc_mic_listening: '正在录音 {s} 秒（最长 {max} 秒，念完点「停止录音」）…',
  rc_mic_processing: '正在本机对齐时间轴并测量声学特征…',
  rc_mic_too_short: '录音太短或没有录到声音，请再试一次。',
  rc_file_btn: '导入录音 / 字幕（.wav .mp3 .m4a .mp4 .mov .srt .vtt）',
  rc_file_processing: '正在读取并对齐「{name}」…',
  rc_fcpxml_btn: '导出 FCPXML 1.9 时间线',
  rc_use_rate_btn: '改回按语速估算',
  rc_switched_rate: '已改回按语速估算导出',
  rc_activated: '已切换为当前导出时间轴',
  rc_deleted: '已删除这条录音记录',
  rc_adlibs_h: '检测到的即兴口播差异（对比剧本原句）',
  rc_adlibs_hint: '如果录音时顺口改得更自然，可以一键把剧本台词改成录音原话。',
  rc_adlib_line: '第 {n} 句（吻合度 {pct}%）',
  rc_adlib_written: '原稿：',
  rc_adlib_spoken: '录音：',
  rc_adlib_apply: '改用录音原话',
  rc_adlib_applied: '已把这句更新为录音原话',
  rc_takes_h: '已对齐的录音 / 字幕版本（{n}）',
  rc_takes_empty: '还没有录音或字幕版本。可以先打开提词器念一遍，录好后导进来自动对齐字幕和花字。',
  rc_active_tag: '当前使用',
  rc_use_take: '设为当前时间轴',
  rc_del_ask: '删除',
  rc_del_confirm: '确认删除',
  rc_del_cancel: '取消',
  rc_stat_dur: '{s} 秒',
  rc_stat_cpm: '实测 {cpm} 字/分钟',
  rc_stat_cues: '{n} 条字幕',
  rc_stat_sound: '有声 {pct}%',
  rc_stat_pauses: '停顿 {n} 次',
  rc_stat_pitch: '中位音高 {hz} Hz',
  rc_cues_fold: '查看对齐后的时间轴预览（{n} 条）',
  rc_src_mic: '麦克风录音',
  rc_src_subtitle: '外部字幕',
  rc_src_upload: '导入音频',
  rc_loading: '正在读取对齐记录…',
  rc_load_failed: '对齐记录读取失败，刷新页面可重试。',
});

Object.assign(I18N.en, {
  exp_fcpxml: 'Timeline (FCPXML 1.9)',
  exp_fcpxml_d: 'Vertical 1080×1920 30fps with subtitle lane, on-screen text lane & shot markers',
  rc_title: 'Voiceover Alignment & Timeline',
  rc_mode_rate: 'Rate estimate',
  rc_mode_whisper: 'Whisper aligned',
  rc_mode_subtitle: 'Subtitle aligned',
  rc_mode_pause: 'Speech-pause aligned',
  rc_adlib_badge: '{n} spoken diffs',
  rc_adlib_badge_one: '1 spoken diff',
  rc_explain_rate: 'Exported .srt subtitles, on-screen text, CapCut/Jianying kits and FCPXML currently estimate timing from unit counts and speaking rate. Record a take or attach audio / .srt / .vtt below to align exports to real timing.',
  rc_explain_whisper: 'Exports now use local Whisper transcription timestamps from "{label}" for .srt, on-screen text, CapCut/Jianying kit and FCPXML 1.9 (offline; raw audio is never stored in SQLite).',
  rc_explain_subtitle: 'Exports now use imported subtitle cue timestamps from "{label}" for .srt, on-screen text, CapCut/Jianying kit and FCPXML 1.9.',
  rc_explain_pause: 'Exports now use speech-pause island timing from "{label}": pauses ≥0.25s split the recording into speech spans and clauses are distributed by spoken unit weight (no speech recognition was run, so wording diffs are not detected in this mode).',
  rc_privacy: 'Audio and subtitle files are analyzed locally on this computer; temporary uploads are deleted right after extracting timestamps and acoustic stats.',
  rc_label_ph: 'Optional take note (e.g. Take 1)',
  rc_mic_start: '🎙 Record voiceover',
  rc_mic_stop: 'Stop recording',
  rc_mic_asking: 'Allow microphone access in the browser dialog — click again to cancel waiting…',
  rc_mic_denied: 'Microphone access was denied or unavailable. Check browser/system permissions or attach an audio file instead.',
  rc_mic_no_browser: 'Direct recording is not supported in this browser. Attach an audio or subtitle file instead.',
  rc_mic_listening: 'Recording {s}s (up to {max}s — click "Stop recording" when finished)…',
  rc_mic_processing: 'Aligning timeline and measuring acoustics locally…',
  rc_mic_too_short: 'Recording was empty or too short. Try again.',
  rc_file_btn: 'Attach audio / subtitles (.wav .mp3 .m4a .mp4 .mov .srt .vtt)',
  rc_file_processing: 'Reading and aligning "{name}"…',
  rc_fcpxml_btn: 'Export FCPXML 1.9 timeline',
  rc_use_rate_btn: 'Switch back to rate estimate',
  rc_switched_rate: 'Switched exports back to speaking-rate estimate',
  rc_activated: 'Active timeline updated',
  rc_deleted: 'Take removed',
  rc_adlibs_h: 'Spoken ad-libs vs written script',
  rc_adlibs_hint: 'Where {@name} phrased a line differently while recording, apply the spoken wording to the script in one click.',
  rc_adlib_line: 'Line {n} ({pct}% match)',
  rc_adlib_written: 'Written: ',
  rc_adlib_spoken: 'Spoken: ',
  rc_adlib_apply: 'Use spoken wording',
  rc_adlib_applied: 'Updated script line to spoken wording',
  rc_takes_h: 'Aligned takes & subtitle tracks ({n})',
  rc_takes_empty: 'No aligned takes yet. Record directly here or attach a voiceover / .srt / .vtt file to align subtitles and overlays.',
  rc_active_tag: 'Active',
  rc_use_take: 'Use this take',
  rc_del_ask: 'Delete',
  rc_del_confirm: 'Confirm delete',
  rc_del_cancel: 'Cancel',
  rc_stat_dur: '{s}s',
  rc_stat_cpm: '{cpm} units/min',
  rc_stat_cues: '{n} cues',
  rc_stat_cues_one: '1 cue',
  rc_stat_sound: 'voiced {pct}%',
  rc_stat_pauses: '{n} pauses',
  rc_stat_pauses_one: '1 pause',
  rc_stat_pitch: 'median {hz} Hz',
  rc_cues_fold: 'Preview aligned cue timestamps ({n})',
  rc_src_mic: 'Mic take',
  rc_src_subtitle: 'Subtitle file',
  rc_src_upload: 'Audio upload',
  rc_loading: 'Loading takes…',
  rc_load_failed: 'Could not load takes. Reload to try again.',
});

const RC_MAX_RECORD_SEC = 180;
const rcUi = {
  scriptId: null,
  page: null,
  open: false,
  loading: false,
  failed: false,
  seq: 0,
  takes: [],
  activeTake: null,
  caps: null,
  label: '',
  busy: false,
  micState: 'idle',   // 'idle' | 'asking' | 'listening' | 'processing'
  asked: null,
  recorder: null,
  stream: null,
  tick: null,
  note: '',
  confirmDeleteId: null,
  cuesOpen: {},
};

const rcSayErr = (e) => (e && ((lang === 'zh' && e.message_zh) || e.detail || e.message)) || t('err_default');

function rcModeInfo(take) {
  if (!take) return { badgeKey: 'rc_mode_rate', explainKey: 'rc_explain_rate', cls: '' };
  const m = take.alignment_mode;
  if (m === 'whisper_aligned') return { badgeKey: 'rc_mode_whisper', explainKey: 'rc_explain_whisper', cls: 'ok' };
  if (m === 'subtitle_aligned') return { badgeKey: 'rc_mode_subtitle', explainKey: 'rc_explain_subtitle', cls: 'ok' };
  return { badgeKey: 'rc_mode_pause', explainKey: 'rc_explain_pause', cls: 'ok' };
}

function rcFmtTime(sec) {
  const s = Math.max(0, Number(sec) || 0);
  const mins = Math.floor(s / 60);
  const rem = (s - mins * 60).toFixed(2).padStart(5, '0');
  return String(mins).padStart(2, '0') + ':' + rem;
}

function rcToBase64(buffer) {
  const bytes = new Uint8Array(buffer);
  const chunk = 0x8000;
  const parts = [];
  for (let i = 0; i < bytes.length; i += chunk) {
    parts.push(String.fromCharCode.apply(null, bytes.subarray(i, i + chunk)));
  }
  return btoa(parts.join(''));
}

/* Downsample any browser AudioBuffer to a compact 16 kHz mono 16-bit PCM WAV ArrayBuffer. */
function rcEncodeWav16k(audio) {
  const srcRate = audio.sampleRate || 16000;
  const srcLen = audio.length || 0;
  const chCount = Math.max(1, audio.numberOfChannels || 1);
  const mono = new Float32Array(srcLen);
  for (let c = 0; c < chCount; c++) {
    const ch = audio.getChannelData(c);
    for (let i = 0; i < srcLen; i++) mono[i] += ch[i] / chCount;
  }
  const dstRate = 16000;
  let samples;
  if (srcRate === dstRate || srcLen === 0) {
    samples = mono;
  } else {
    const dstLen = Math.max(1, Math.round(srcLen * dstRate / srcRate));
    samples = new Float32Array(dstLen);
    const ratio = srcRate / dstRate;
    for (let i = 0; i < dstLen; i++) {
      const start = i * ratio;
      const end = Math.min(srcLen, (i + 1) * ratio);
      const i0 = Math.floor(start);
      const i1 = Math.max(i0 + 1, Math.ceil(end));
      let acc = 0;
      let count = 0;
      for (let j = i0; j < i1 && j < srcLen; j++) {
        acc += mono[j];
        count++;
      }
      samples[i] = count ? acc / count : 0;
    }
  }
  const n = samples.length;
  const buf = new ArrayBuffer(44 + n * 2);
  const view = new DataView(buf);
  const ascii = (at, s) => { for (let i = 0; i < s.length; i++) view.setUint8(at + i, s.charCodeAt(i)); };
  ascii(0, 'RIFF');
  view.setUint32(4, 36 + n * 2, true);
  ascii(8, 'WAVE');
  ascii(12, 'fmt ');
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, dstRate, true);
  view.setUint32(28, dstRate * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  ascii(36, 'data');
  view.setUint32(40, n * 2, true);
  for (let i = 0; i < n; i++) {
    const v = Math.max(-1, Math.min(1, samples[i]));
    view.setInt16(44 + i * 2, v < 0 ? v * 0x8000 : v * 0x7fff, true);
  }
  return buf;
}

async function rcDecodeBufferToWav16k(arrayBuffer) {
  const Ctx = window.AudioContext || window.webkitAudioContext;
  if (!Ctx) throw new Error(t('rc_mic_no_browser'));
  const ctx = new Ctx();
  try {
    const audio = await new Promise((resolve, reject) => {
      const p = ctx.decodeAudioData(arrayBuffer.slice(0), resolve, reject);
      if (p && p.catch) p.catch(() => {});
    });
    return rcEncodeWav16k(audio);
  } finally {
    try { ctx.close(); } catch (e) { /* ignore close error */ }
  }
}

function rcStopMicTracks() {
  if (rcUi.tick) { clearInterval(rcUi.tick); rcUi.tick = null; }
  if (rcUi.stream) {
    rcUi.stream.getTracks().forEach((tr) => tr.stop());
    rcUi.stream = null;
  }
  rcUi.recorder = null;
}

function rcApplyServerState(res) {
  if (Array.isArray(res.takes)) rcUi.takes = res.takes;
  if ('active_take' in res) rcUi.activeTake = res.active_take || null;
  if (res.capabilities) rcUi.caps = res.capabilities;
}

async function rcLoad(scriptId) {
  const seq = ++rcUi.seq;
  rcUi.loading = true;
  rcUi.failed = false;
  rcPaint();
  try {
    const res = await api('GET', '/api/scripts/' + scriptId + '/takes');
    if (seq !== rcUi.seq || !cur || cur.script.id !== scriptId) return;
    rcApplyServerState(res);
    rcUi.loading = false;
    rcPaint();
  } catch (e) {
    if (seq !== rcUi.seq || !cur || cur.script.id !== scriptId) return;
    rcUi.loading = false;
    rcUi.failed = true;
    rcPaint();
  }
}

async function rcSubmitTake(filename, buffer, sourceKind) {
  if (!cur || !cur.script) return;
  const scriptId = cur.script.id;
  await flushSave();
  const label = rcUi.label.trim();
  const payload = {
    filename,
    data_base64: rcToBase64(buffer),
    source_kind: sourceKind,
    label,
  };
  const res = await api('POST', '/api/scripts/' + scriptId + '/takes', payload);
  if (!cur || cur.script.id !== scriptId) return;
  rcUi.label = '';
  rcUi.open = true;
  rcApplyServerState(res);
}

async function rcHandleFile(file) {
  if (!file || !cur || rcUi.busy) return;
  const name = file.name || 'take.wav';
  const ext = (name.includes('.') ? '.' + name.split('.').pop() : '').toLowerCase();
  const isSub = ext === '.srt' || ext === '.vtt';
  rcUi.busy = true;
  rcUi.note = tf('rc_file_processing', { name });
  rcPaint();
  try {
    const raw = await file.arrayBuffer();
    if (!raw.byteLength) throw new Error(t('err_empty_text'));
    const needBrowserWav = !isSub && ext !== '.wav'
      && rcUi.caps && !rcUi.caps.afconvert && !rcUi.caps.whisper
      && Boolean(window.AudioContext || window.webkitAudioContext);
    if (needBrowserWav) {
      const wavBuf = await rcDecodeBufferToWav16k(raw);
      const stem = name.replace(/\.[^.]+$/, '') || 'take';
      await rcSubmitTake(stem + '.wav', wavBuf, 'audio');
    } else {
      try {
        await rcSubmitTake(name, raw, isSub ? ext.slice(1) : 'audio');
      } catch (err) {
        if (err && err.code === 'recording_needs_wav_or_srt' && !isSub && (window.AudioContext || window.webkitAudioContext)) {
          const wavBuf = await rcDecodeBufferToWav16k(raw);
          const stem = name.replace(/\.[^.]+$/, '') || 'take';
          await rcSubmitTake(stem + '.wav', wavBuf, 'audio');
        } else {
          throw err;
        }
      }
    }
    rcUi.note = '';
  } catch (e) {
    rcUi.note = rcSayErr(e);
  } finally {
    rcUi.busy = false;
    rcPaint();
  }
}

async function rcMicToggle() {
  if (rcUi.micState === 'listening' && rcUi.recorder) {
    rcUi.recorder.stop();
    return;
  }
  if (rcUi.micState === 'asking') {
    rcUi.asked = null;
    rcUi.micState = 'idle';
    rcUi.note = '';
    rcPaint();
    return;
  }
  if (rcUi.busy || rcUi.micState !== 'idle' || !cur) return;
  if (!navigator.mediaDevices || !window.MediaRecorder || !(window.AudioContext || window.webkitAudioContext)) {
    rcUi.note = t('rc_mic_no_browser');
    rcPaint();
    return;
  }
  const asked = {};
  rcUi.asked = asked;
  rcUi.micState = 'asking';
  rcUi.note = t('rc_mic_asking');
  rcPaint();
  let stream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  } catch (e) {
    if (rcUi.asked === asked) {
      rcUi.micState = 'idle';
      rcUi.note = t('rc_mic_denied');
      rcPaint();
    }
    return;
  }
  if (rcUi.asked !== asked || !cur) {
    stream.getTracks().forEach((tr) => tr.stop());
    return;
  }
  rcUi.stream = stream;
  const recorder = new MediaRecorder(stream);
  rcUi.recorder = recorder;
  const chunks = [];
  const started = Date.now();
  const scriptId = cur.script.id;
  recorder.ondataavailable = (e) => { if (e.data && e.data.size) chunks.push(e.data); };
  rcUi.tick = setInterval(() => {
    const secs = Math.floor((Date.now() - started) / 1000);
    const gone = !cur || cur.script.id !== scriptId || !document.getElementById('rc-panel');
    if (gone && recorder.state === 'recording') {
      recorder.stop();
      return;
    }
    if (secs >= RC_MAX_RECORD_SEC && recorder.state === 'recording') {
      recorder.stop();
      return;
    }
    rcUi.note = tf('rc_mic_listening', { s: secs, max: RC_MAX_RECORD_SEC });
    const $n = document.getElementById('rc-note');
    if ($n) $n.textContent = rcUi.note;
  }, 250);
  recorder.onstop = async () => {
    rcStopMicTracks();
    if (!cur || cur.script.id !== scriptId) {
      rcUi.micState = 'idle';
      return;
    }
    rcUi.micState = 'processing';
    rcUi.busy = true;
    rcUi.note = t('rc_mic_processing');
    rcPaint();
    try {
      const raw = await new Blob(chunks).arrayBuffer();
      if (!raw.byteLength) throw new Error(t('rc_mic_too_short'));
      const wavBuf = await rcDecodeBufferToWav16k(raw);
      await rcSubmitTake('mic-take.wav', wavBuf, 'mic');
      rcUi.note = '';
    } catch (e) {
      rcUi.note = rcSayErr(e);
    } finally {
      rcUi.micState = 'idle';
      rcUi.busy = false;
      rcPaint();
    }
  };
  recorder.start();
  rcUi.micState = 'listening';
  rcUi.note = tf('rc_mic_listening', { s: 0, max: RC_MAX_RECORD_SEC });
  rcPaint();
}

function rcStatsTagsHtml(take) {
  const out = [];
  const mode = rcModeInfo(take);
  out.push(`<span class="tag ${mode.cls}">${esc(t(mode.badgeKey))}</span>`);
  const isSub = take.source_kind === 'srt' || take.source_kind === 'vtt' || take.source_kind === 'subtitle';
  const srcKey = take.source_kind === 'mic' ? 'rc_src_mic' : (isSub ? 'rc_src_subtitle' : 'rc_src_upload');
  out.push(`<span class="tag">${esc(t(srcKey))}</span>`);
  if (take.duration_sec > 0) out.push(`<span class="tag">${esc(tf('rc_stat_dur', { s: Number(take.duration_sec).toFixed(1) }))}</span>`);
  if (take.measured_cpm > 0) out.push(`<span class="tag">${esc(tf('rc_stat_cpm', { cpm: Math.round(take.measured_cpm) }))}</span>`);
  const cueCount = Array.isArray(take.cues) ? take.cues.length : 0;
  if (cueCount > 0) out.push(`<span class="tag">${esc(tf(countKey('rc_stat_cues', cueCount), { n: cueCount }))}</span>`);
  const ss = take.sound || {};
  if (ss.measured) {
    if (typeof ss.sound_pct === 'number') out.push(`<span class="tag">${esc(tf('rc_stat_sound', { pct: Math.round(ss.sound_pct) }))}</span>`);
    if (ss.pauses && typeof ss.pauses.count === 'number') {
      out.push(`<span class="tag">${esc(tf(countKey('rc_stat_pauses', ss.pauses.count), { n: ss.pauses.count }))}</span>`);
    }
    if (ss.pitch && typeof ss.pitch.median_hz === 'number' && ss.pitch.median_hz > 0) {
      out.push(`<span class="tag">${esc(tf('rc_stat_pitch', { hz: Math.round(ss.pitch.median_hz) }))}</span>`);
    }
  }
  return out.join('');
}

function rcAdlibsHtml(take) {
  if (!take || !Array.isArray(take.adlibs) || !take.adlibs.length) return '';
  const rows = take.adlibs.map((ad, idx) => {
    const written = ad.scripted_zh !== undefined ? ad.scripted_zh : (ad.written || '');
    const spoken = ad.spoken_zh !== undefined ? ad.spoken_zh : (ad.spoken || '');
    const sim = ad.similarity !== undefined ? ad.similarity : (ad.ratio || 0);
    return `<div class="card soft" data-rc-adlib-row="${idx}" style="margin-top:8px;padding:12px 14px">
      <div class="row">
        <span class="meta"><b>${esc(tf('rc_adlib_line', { n: ad.line_no, pct: Math.round((Number(sim) || 0) * 100) }))}</b></span>
        <span class="spacer"></span>
        <button type="button" class="btn primary" data-rc-apply-adlib="${idx}" ${rcUi.busy ? 'disabled' : ''}>${esc(t('rc_adlib_apply'))}</button>
      </div>
      <div class="meta" style="margin-top:6px">${esc(t('rc_adlib_written'))}${esc(written)}</div>
      <div style="margin-top:4px">${esc(t('rc_adlib_spoken'))}<b>${esc(spoken)}</b></div>
    </div>`;
  }).join('');
  return `<div id="rc-adlibs" style="margin-top:14px">
    <div class="meta"><b>${esc(t('rc_adlibs_h'))}</b> — ${esc(t('rc_adlibs_hint'))}</div>
    ${rows}
  </div>`;
}

function rcTakeRowHtml(take) {
  const cues = Array.isArray(take.cues) ? take.cues : [];
  const isConfirming = rcUi.confirmDeleteId === take.id;
  const cuesFold = cues.length ? `<details data-rc-cues="${take.id}" ${rcUi.cuesOpen[take.id] ? 'open' : ''} style="margin-top:8px">
    <summary class="meta">${esc(tf('rc_cues_fold', { n: cues.length }))}</summary>
    <div style="margin-top:6px">${cues.map((c) => `<div class="meta" style="font-variant-numeric:tabular-nums;margin-top:3px">${esc(rcFmtTime(c.start_sec))} → ${esc(rcFmtTime(c.end_sec))} · ${esc(c.text)}</div>`).join('')}</div>
  </details>` : '';
  return `<div class="card soft" data-rc-take="${take.id}" style="margin-top:8px;padding:12px 14px">
    <div class="row">
      <b>${esc(take.label || ('Take ' + take.id))}</b>
      ${take.active ? `<span class="tag ok">${esc(t('rc_active_tag'))}</span>` : ''}
      <span class="spacer"></span>
      ${take.active ? '' : `<button type="button" class="btn" data-rc-activate="${take.id}" ${rcUi.busy ? 'disabled' : ''}>${esc(t('rc_use_take'))}</button>`}
      ${isConfirming
        ? `<button type="button" class="btn primary danger" data-rc-del-yes="${take.id}" ${rcUi.busy ? 'disabled' : ''}>${esc(t('rc_del_confirm'))}</button>
           <button type="button" class="btn ghost" data-rc-del-no="${take.id}" ${rcUi.busy ? 'disabled' : ''}>${esc(t('rc_del_cancel'))}</button>`
        : `<button type="button" class="btn ghost" data-rc-del-ask="${take.id}" ${rcUi.busy ? 'disabled' : ''}>${esc(t('rc_del_ask'))}</button>`}
    </div>
    <div style="margin-top:8px">${rcStatsTagsHtml(take)}</div>
    ${cuesFold}
  </div>`;
}

function rcPaint() {
  const box = document.getElementById('rc-box');
  if (!box || !cur || !cur.script) return;
  const active = rcUi.activeTake;
  const mode = rcModeInfo(active);
  const adCount = active && Array.isArray(active.adlibs) ? active.adlibs.length : 0;
  const adBadge = adCount ? ` <span class="tag warn" id="rc-adlib-summary-badge">${esc(tf(countKey('rc_adlib_badge', adCount), { n: adCount }))}</span>` : '';
  const explainText = active
    ? tf(mode.explainKey, { label: active.label || ('Take ' + active.id) })
    : t('rc_explain_rate');
  const micListening = rcUi.micState === 'listening';
  const takes = Array.isArray(rcUi.takes) ? rcUi.takes : [];

  box.innerHTML = `<details class="card" id="rc-panel" ${rcUi.open ? 'open' : ''}>
    <summary class="row" style="list-style:none">
      <span><b>${esc(t('rc_title'))}</b></span>
      <span class="tag ${mode.cls}" id="rc-mode-badge">${esc(t(mode.badgeKey))}</span>${adBadge}
    </summary>
    <div style="margin-top:12px">
      <p class="hint" id="rc-explain" style="margin:0 0 8px">${esc(explainText)}</p>
      <p class="hint" style="margin:0 0 12px">${esc(t('rc_privacy'))}</p>
      <div class="row" style="margin-bottom:10px">
        <input type="text" id="rc-label-input" value="${esc(rcUi.label)}" maxlength="120" placeholder="${esc(t('rc_label_ph'))}" ${rcUi.busy ? 'disabled' : ''}>
      </div>
      <div class="row" style="gap:8px;flex-wrap:wrap">
        <button type="button" class="btn dv-mic ${micListening ? 'on' : ''}" id="rc-mic-btn" ${rcUi.busy && !micListening ? 'disabled' : ''}>${esc(t(micListening ? 'rc_mic_stop' : 'rc_mic_start'))}</button>
        <button type="button" class="btn" id="rc-file-btn" ${rcUi.busy ? 'disabled' : ''}>${esc(t('rc_file_btn'))}</button>
        <input type="file" id="rc-file-input" accept=".wav,.mp3,.m4a,.mp4,.mov,.webm,.srt,.vtt" hidden>
        <button type="button" class="btn" id="rc-fcpxml-btn" ${rcUi.busy ? 'disabled' : ''}>${esc(t('rc_fcpxml_btn'))}</button>
        ${active ? `<button type="button" class="btn ghost" id="rc-clear-active-btn" ${rcUi.busy ? 'disabled' : ''}>${esc(t('rc_use_rate_btn'))}</button>` : ''}
      </div>
      <p class="hint" id="rc-note" role="status" aria-live="polite" style="margin:8px 0 0">${esc(rcUi.note || (rcUi.loading ? t('rc_loading') : (rcUi.failed ? t('rc_load_failed') : '')))}</p>
      ${rcAdlibsHtml(active)}
      <div id="rc-takes-list" style="margin-top:14px">
        <div class="meta"><b>${esc(tf('rc_takes_h', { n: takes.length }))}</b></div>
        ${takes.length ? takes.map(rcTakeRowHtml).join('') : `<p class="hint" style="margin:6px 0 0">${esc(t('rc_takes_empty'))}</p>`}
      </div>
    </div>
  </details>`;

  const $panel = document.getElementById('rc-panel');
  if ($panel) $panel.ontoggle = () => { rcUi.open = $panel.open; };

  const $label = document.getElementById('rc-label-input');
  if ($label) $label.oninput = () => { rcUi.label = $label.value; };

  const $mic = document.getElementById('rc-mic-btn');
  if ($mic) $mic.onclick = rcMicToggle;

  const $fileBtn = document.getElementById('rc-file-btn');
  const $fileIn = document.getElementById('rc-file-input');
  if ($fileBtn && $fileIn) {
    $fileBtn.onclick = () => { if (!rcUi.busy) $fileIn.click(); };
    $fileIn.onchange = () => {
      const f = $fileIn.files && $fileIn.files[0];
      $fileIn.value = '';
      if (f) rcHandleFile(f);
    };
  }

  const $fcpxml = document.getElementById('rc-fcpxml-btn');
  if ($fcpxml) {
    $fcpxml.onclick = async () => {
      if (!cur || !cur.script) return;
      await flushSave();
      exportScript(cur.script.id, 'fcpxml');
    };
  }

  const $clear = document.getElementById('rc-clear-active-btn');
  if ($clear) {
    $clear.onclick = async () => {
      if (!cur || rcUi.busy) return;
      rcUi.busy = true;
      rcPaint();
      try {
        const res = await api('POST', '/api/scripts/' + cur.script.id + '/takes/clear-active', {});
        rcApplyServerState(res);
        toast(t('rc_switched_rate'));
      } catch (e) {
        rcUi.note = rcSayErr(e);
      } finally {
        rcUi.busy = false;
        rcPaint();
      }
    };
  }

  box.querySelectorAll('details[data-rc-cues]').forEach((d) => {
    d.ontoggle = () => { rcUi.cuesOpen[d.dataset.rcCues] = d.open; };
  });

  box.onclick = async (e) => {
    if (!cur || rcUi.busy) return;
    const scriptId = cur.script.id;

    const applyBtn = e.target.closest('[data-rc-apply-adlib]');
    if (applyBtn && rcUi.activeTake) {
      const idx = Number(applyBtn.dataset.rcApplyAdlib);
      const ad = (rcUi.activeTake.adlibs || [])[idx];
      if (!ad) return;
      rcUi.busy = true;
      rcPaint();
      try {
        await flushSave();
        const res = await api('POST', '/api/scripts/' + scriptId + '/takes/' + rcUi.activeTake.id + '/apply-adlib', {
          line_no: ad.line_no,
        });
        if (!cur || cur.script.id !== scriptId) return;
        cur.script = res.script;
        rcUi.page = cur;
        rcApplyServerState(res);
        const lint = await api('POST', '/api/lint', lintBody(cur.script));
        if (cur && cur.script.id === scriptId) {
          cur.findings = lint.findings;
          cur.leftOut = lint.findings_left_out || 0;
          cur.stats = lint.stats;
          cur.coverage = lint.keyword_coverage;
          cur.voice = lint.voice_match;
          cur.wordlist = lint.wordlist;
        }
        rcUi.busy = false;
        paintScript();
        toast(t('rc_adlib_applied'));
        return;
      } catch (err) {
        rcUi.busy = false;
        rcUi.note = rcSayErr(err);
        rcPaint();
        return;
      }
    }

    const actBtn = e.target.closest('[data-rc-activate]');
    if (actBtn) {
      const takeId = Number(actBtn.dataset.rcActivate);
      rcUi.busy = true;
      rcPaint();
      try {
        const res = await api('POST', '/api/scripts/' + scriptId + '/takes/' + takeId + '/activate', {});
        rcApplyServerState(res);
        toast(t('rc_activated'));
      } catch (err) {
        rcUi.note = rcSayErr(err);
      } finally {
        rcUi.busy = false;
        rcPaint();
      }
      return;
    }

    const askDel = e.target.closest('[data-rc-del-ask]');
    if (askDel) {
      rcUi.confirmDeleteId = Number(askDel.dataset.rcDelAsk);
      rcPaint();
      return;
    }

    const noDel = e.target.closest('[data-rc-del-no]');
    if (noDel) {
      rcUi.confirmDeleteId = null;
      rcPaint();
      return;
    }

    const yesDel = e.target.closest('[data-rc-del-yes]');
    if (yesDel) {
      const takeId = Number(yesDel.dataset.rcDelYes);
      rcUi.busy = true;
      rcUi.confirmDeleteId = null;
      rcPaint();
      try {
        const res = await api('DELETE', '/api/scripts/' + scriptId + '/takes/' + takeId);
        rcApplyServerState(res);
        toast(t('rc_deleted'));
      } catch (err) {
        rcUi.note = rcSayErr(err);
      } finally {
        rcUi.busy = false;
        rcPaint();
      }
    }
  };
}

function rcAttach() {
  if (!cur || !cur.script) return;
  if (rcUi.scriptId !== cur.script.id || rcUi.page !== cur) {
    rcStopMicTracks();
    const sameScript = rcUi.scriptId === cur.script.id;
    rcUi.scriptId = cur.script.id;
    rcUi.page = cur;
    if (!sameScript) {
      rcUi.open = false;
      rcUi.takes = [];
      rcUi.activeTake = null;
      rcUi.label = '';
      rcUi.cuesOpen = {};
    }
    rcUi.busy = false;
    rcUi.micState = 'idle';
    rcUi.note = '';
    rcUi.confirmDeleteId = null;
    rcLoad(cur.script.id);
    return;
  }
  rcPaint();
}

function rcAfterSave(s) {
  if (!cur || !s || cur.script.id !== s.id) return;
  rcPaint();
}
