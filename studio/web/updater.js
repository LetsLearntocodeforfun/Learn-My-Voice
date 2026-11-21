'use strict';
/* Look for a newer release on GitHub, stream and verify a download against its published .sha256, and bring an
   earlier portable .zip folder's data into an empty new folder (studio/updater.py).

   Loaded after backup.js and before pages.js. Nothing leaves this computer while st.checks.on is false, or on
   startup when st.auto_check is false. Every value from release metadata or disk goes through esc(), and links
   to GitHub open through openOutside(url). */

Object.assign(I18N.zh, {
  up_sec_title: '版本与更新',
  up_persist_desktop: '桌面版把脚本、素材库、个人资料、声音档案和设置保存在系统的应用数据文件夹中（在安装包之外），安装新版本时会自动保留。',
  up_persist_zip: '便携压缩包（.zip）每个版本解压在各自的 learn-my-voice-<版本> 文件夹内，数据在其中的 data/ 目录。在下方解压新压缩包会把当前 data/ 复制过去；新解压的空文件夹也可以从旁边的旧版本文件夹复制数据。本机转写环境（local_tools/whisper）不会复制，换到新文件夹后如需使用请重新安装。',
  up_data_path: '当前数据文件夹：{path}',
  up_privacy_note: '检查更新会向 GitHub 的发布接口（api.github.com）发送一次 GET 请求，只读取本项目的版本列表，不发送你的脚本、素材、声音档案或设置；GitHub 会看到这台电脑的 IP 地址和请求时间。',
  up_sha_note: '下载时会边写入临时文件边核对与该版本一同发布的 .sha256 校验值。校验一致说明文件下载完整、与 GitHub 上发布的一致；但安装包未做代码签名，且文件与校验值都来自同一个 GitHub 仓库，它不能证明发布者身份。',
  up_auto_label: '启动时检查是否有新版本（每 12 小时最多一次）',
  up_pre_label: '包含测试版（Beta / RC）',
  up_check_btn: '检查更新',
  up_checking: '正在检查…',
  up_up_to_date: '当前已是可用最新版本（{v}）。',
  up_found_banner: '有新版本 v{v}（当前：{cur}）。',
  up_download_btn: '下载 v{v}',
  up_downloading: '正在下载并核对 SHA-256…',
  up_no_verified_pkg: 'v{v} 没有发布适用于当前系统且带 .sha256 校验文件的安装包，因此不在应用内直接下载。',
  up_open_release: '打开 GitHub 发布页',
  up_notes_btn: '新版本说明',
  up_notes_empty: '该版本没有填写文字说明，可打开 GitHub 发布页查看详情。',
  up_skip_btn: '跳过此版本',
  up_later_btn: '稍后',
  up_downloaded_desktop: '已核对 SHA-256 并保存安装包：{name}。请先退出本应用再运行安装程序，系统应用数据目录中的内容会保持原样。',
  up_downloaded_else: '已核对 SHA-256 并保存到：{path}。',
  up_downloaded_zip: '已核对 SHA-256 并保存压缩包：{name}。可点击下方按钮解压到旁边的新版本文件夹并复制数据。',
  up_whisper_note: '注意：本机转写环境（local_tools/whisper）不会复制到新压缩包文件夹，如需本机转写请在新文件夹中重新设置。',
  up_apply_zip_btn: '解压到新文件夹并复制数据',
  up_applying_zip: '正在解压并复制数据…',
  up_applied_zip_done: '已解压到 {folder} 并复制数据与设置。本机转写未复制，如需使用请在新文件夹中重新安装。现在可关闭当前窗口并从新文件夹启动。',
  up_show_file: '在文件夹中显示',
  up_prev_banner: '检测到旁边的旧版本文件夹 {folder}（{sc} 篇脚本、{sa} 条素材）。要把其中的数据与设置复制到当前空文件夹吗？',
  up_prev_title: '旁边的旧版本文件夹',
  up_scan_btn: '查找旁边的旧版本文件夹',
  up_scanning: '正在查找…',
  up_prev_none: '旁边没有找到其他带数据的 learn-my-voice-<版本> 文件夹。',
  up_prev_nonempty: '当前文件夹里已经有内容了，为避免覆盖，这里不提供直接迁入。如需用旧版本替换当前内容，请先在旧版本中导出备份，再在上方「备份和恢复」中恢复。',
  up_prev_counts: '{sc} 篇脚本 · {sa} 条素材',
  up_migrate_btn: '复制数据与设置到这里',
  up_migrating: '正在复制…',
  up_migrated_ok: '已从旧版本复制 {sc} 篇脚本、{sa} 条素材与设置。本机转写环境未复制，如需使用请重新安装。',
  up_off: '这次运行没有开启检查更新：应用是用 LEARN_MY_VOICE_NO_UPDATE_CHECK=1 启动的，或者是由试运行的脚本启动的。所以这里不会向 GitHub 询问，也不提供下载。',
});

Object.assign(I18N.en, {
  up_sec_title: 'Updates',
  up_persist_desktop: 'In the desktop app, {@names} scripts, library, profiles and settings live in the system\'s application-data folder outside the app, so installing a newer version leaves them in place.',
  up_persist_zip: 'Each portable .zip release unpacks into its own learn-my-voice-<version> folder with its own data/ folder inside. Unpacking a new .zip below copies {@names} current data/ into the new folder, and an empty new folder can copy data/ from an earlier sibling folder. On-device transcription (local_tools/whisper) is never copied and must be set up again in the new folder.',
  up_data_path: 'Data folder in use: {path}',
  up_privacy_note: 'Checking for updates sends one GET request to GitHub\'s releases API (api.github.com) for this project\'s release list. Nothing of {@names} scripts, library, voice profile or settings is sent; GitHub sees this computer\'s IP address and when it asked.',
  up_sha_note: 'A download is checked as it arrives against the .sha256 file published with that release. A match shows the download arrived complete as published on GitHub; it does not prove release authenticity, because both files come from the same GitHub repository and installers are not signed.',
  up_auto_label: 'Check for a newer version on startup (at most once every 12 hours)',
  up_pre_label: 'Include pre-releases (Beta / RC)',
  up_check_btn: 'Check for updates',
  up_checking: 'Checking…',
  up_up_to_date: 'No newer release was found (this copy is {v}).',
  up_found_banner: 'A newer version is out: v{v} (this copy is {cur}).',
  up_download_btn: 'Download v{v}',
  up_downloading: 'Downloading and checking SHA-256…',
  up_no_verified_pkg: 'Release v{v} did not publish a package with a .sha256 checksum for this system, so it is not downloaded inside the app.',
  up_open_release: 'Open GitHub release page',
  up_notes_btn: 'What\'s new',
  up_notes_empty: 'No release notes text was included; open the GitHub release page for details.',
  up_skip_btn: 'Skip this version',
  up_later_btn: 'Later',
  up_downloaded_desktop: 'Checked SHA-256 and saved installer: {name}. Quit Learn My Voice and run the installer; data in the application-data folder stays in place.',
  up_downloaded_else: 'Checked SHA-256 and saved package to: {path}.',
  up_downloaded_zip: 'Checked SHA-256 and saved archive: {name}. Use the button below to unpack it into a new folder beside this one and copy data over.',
  up_whisper_note: 'Note: on-device transcription (local_tools/whisper) is not copied to a new .zip folder and must be set up again there if you use it.',
  up_apply_zip_btn: 'Unpack to new folder and copy data',
  up_applying_zip: 'Unpacking and copying data…',
  up_applied_zip_done: 'Unpacked to {folder} and copied data and settings. On-device transcription is not copied and must be set up again there if needed. Close this copy and start from the new folder.',
  up_show_file: 'Show in folder',
  up_prev_banner: 'Found an earlier version folder beside this one: {folder} ({sc} scripts, {sa} library items). Copy its data and settings into this empty folder?',
  up_prev_title: 'Earlier version folders beside this one',
  up_scan_btn: 'Look for earlier version folders',
  up_scanning: 'Looking…',
  up_prev_none: 'No neighbouring learn-my-voice-<version> folder with data was found.',
  up_prev_nonempty: 'This folder already holds work, so direct migration over it is turned off. To replace what is here with data from an earlier folder, make a backup there and restore it under Backup and restore above.',
  up_prev_counts: '{sc} scripts · {sa} library items',
  up_migrate_btn: 'Copy data and settings here',
  up_migrating: 'Copying…',
  up_migrated_ok: 'Copied {sc} scripts, {sa} library items and settings. On-device transcription is not copied and must be set up again if needed.',
  up_off: 'Looking for updates is switched off for this run: the app was started with LEARN_MY_VOICE_NO_UPDATE_CHECK=1, or by a script that tries it. So nothing is asked of GitHub here, and no download is offered.',
});

const upState = {
  status: null,
  lastDownload: null,
  appliedFolder: '',
  notesOpen: false,
  manualScanned: false,
  startupDismissed: false,
  prevDismissed: false,
};

function upHasVerifiedPackage(latest) {
  return !!(latest && latest.asset && latest.asset.url && latest.checksum_asset && latest.checksum_asset.url);
}

function upReleaseUrl(st) {
  return (st && st.latest && st.latest.html_url) || (st && st.releases_url) || '';
}

function upNotesHtml(st) {
  if (!upState.notesOpen || !st || !st.latest) return '';
  const body = st.latest.notes ? esc(st.latest.notes) : esc(t('up_notes_empty'));
  return `<div id="up-notes-box" style="margin-top:10px;padding-top:10px;border-top:1px solid var(--line)">
    <div style="white-space:pre-line;font-size:13.5px">${body}</div>
    <div class="row" style="margin-top:8px">
      <button type="button" class="btn" data-up-open-url="${esc(upReleaseUrl(st))}" id="up-release-link">${esc(t('up_open_release'))}</button>
    </div>
  </div>`;
}

function upDownloadHtml(dl) {
  if (!dl || !dl.saved) return '';
  const savedLine = dl.saved.in_downloads
    ? tf(dl.desktop ? 'up_downloaded_desktop' : 'up_downloaded_zip', { name: dl.saved.name })
    : tf('up_downloaded_else', { path: dl.saved.path });
  const whisperLine = dl.desktop ? '' : `<div style="margin-top:6px;font-size:13px">${esc(t('up_whisper_note'))}</div>`;
  const appliedLine = upState.appliedFolder
    ? `<div id="up-applied-msg" style="margin-top:6px;font-size:13.5px"><strong>${esc(tf('up_applied_zip_done', { folder: upState.appliedFolder }))}</strong></div>`
    : '';
  return `<div id="up-dl-result" style="margin-top:10px;padding-top:10px;border-top:1px solid var(--line)">
    <div>${esc(savedLine)}</div>
    <div id="up-dl-sha-note" style="margin-top:4px;font-size:13px">${esc(t('up_sha_note'))}</div>
    ${whisperLine}
    ${appliedLine}
    <div class="row" style="margin-top:8px;gap:8px;flex-wrap:wrap">
      <button type="button" class="btn" data-up-show="${esc(dl.saved.path)}">${esc(t('up_show_file'))}</button>
      ${dl.desktop || upState.appliedFolder ? '' : `<button type="button" class="btn primary" data-up-apply-zip="${esc(dl.saved.path)}">${esc(t('up_apply_zip_btn'))}</button>`}
    </div>
  </div>`;
}

function upLatestBannerHtml(st, includeLater) {
  const latest = st.latest;
  const curVer = (st.current && st.current.version) || '';
  const canDl = upHasVerifiedPackage(latest);
  const primaryAction = canDl
    ? `<button type="button" class="btn primary" id="up-dl-now">${esc(tf('up_download_btn', { v: latest.version }))}</button>`
    : `<button type="button" class="btn" data-up-open-url="${esc(upReleaseUrl(st))}" id="up-release-btn">${esc(t('up_open_release'))}</button>`;
  const noPkgLine = canDl ? '' : `<div style="margin-top:6px;font-size:13.5px">${esc(tf('up_no_verified_pkg', { v: latest.version }))}</div>`;
  const laterBtn = includeLater ? `<button type="button" class="btn" id="up-later-now">${esc(t('up_later_btn'))}</button>` : '';
  return `<div class="row" style="flex-wrap:wrap;gap:10px;align-items:center">
      <div class="spacer"><strong>${esc(tf('up_found_banner', { v: latest.version, cur: curVer }))}</strong></div>
      ${primaryAction}
      <button type="button" class="btn" id="up-notes-now" aria-expanded="${upState.notesOpen ? 'true' : 'false'}">${esc(t('up_notes_btn'))}</button>
      ${laterBtn}
      <button type="button" class="btn" id="up-skip-now">${esc(t('up_skip_btn'))}</button>
    </div>
    ${noPkgLine}
    ${upNotesHtml(st)}
    ${upDownloadHtml(upState.lastDownload)}`;
}

function upMountStartupBanner() {
  if (appState.window_check) return;
  const wrap = $view && $view.querySelector('.wrap');
  if (!wrap) return;
  const old = document.getElementById('up-startup-host');
  if (old) old.remove();

  const st = upState.status;
  if (!st) return;

  const cards = [];
  if (st.prompt_on_startup && st.latest && !upState.startupDismissed) {
    cards.push(`<div class="banner" id="up-startup-banner" style="margin-bottom:16px">
      ${upLatestBannerHtml(st, true)}
    </div>`);
  }

  const prevList = Array.isArray(st.previous_versions) ? st.previous_versions : [];
  if (!st.current.desktop && st.data_empty && prevList.length > 0 && !upState.prevDismissed) {
    const topPrev = prevList[0];
    cards.push(`<div class="banner" id="up-prev-banner" style="margin-bottom:16px">
      <div class="row" style="flex-wrap:wrap;gap:10px;align-items:center">
        <div class="spacer">${esc(tf('up_prev_banner', { folder: topPrev.folder_name, sc: topPrev.scripts, sa: topPrev.samples }))}</div>
        <button type="button" class="btn primary" data-up-migrate="${esc(topPrev.path)}">${esc(t('up_migrate_btn'))}</button>
        <button type="button" class="btn" id="up-prev-later">${esc(t('up_later_btn'))}</button>
      </div>
      <div style="margin-top:6px;font-size:13px">${esc(t('up_whisper_note'))}</div>
    </div>`);
  }

  if (!cards.length) return;
  const host = document.createElement('div');
  host.id = 'up-startup-host';
  host.innerHTML = cards.join('');
  wrap.insertBefore(host, wrap.firstChild);
  upBindActions(host, () => upMountStartupBanner());
}

function upBindActions(root, rerender) {
  if (!root) return;
  const $dl = root.querySelector('#up-dl-now');
  if ($dl) {
    $dl.onclick = async () => {
      const orig = $dl.textContent;
      $dl.disabled = true;
      $dl.textContent = t('up_downloading');
      try {
        upState.appliedFolder = '';
        upState.lastDownload = await api('POST', '/api/update/download', {});
        rerender();
      } catch (e) {
        $dl.disabled = false;
        $dl.textContent = orig;
        showError(e);
      }
    };
  }
  const $notes = root.querySelector('#up-notes-now');
  if ($notes) {
    $notes.onclick = () => {
      upState.notesOpen = !upState.notesOpen;
      rerender();
    };
  }
  root.querySelectorAll('[data-up-open-url]').forEach((b) => {
    b.onclick = () => {
      const url = b.dataset.upOpenUrl;
      if (url) openOutside(url);
    };
  });
  const $later = root.querySelector('#up-later-now');
  if ($later) {
    $later.onclick = () => { upState.startupDismissed = true; rerender(); };
  }
  const $skip = root.querySelector('#up-skip-now');
  if ($skip && upState.status && upState.status.latest) {
    $skip.onclick = async () => {
      try {
        upState.status = await api('POST', '/api/update/settings', { dismissed_version: upState.status.latest.version });
        upState.startupDismissed = true;
        rerender();
      } catch (e) { showError(e); }
    };
  }
  const $prevLater = root.querySelector('#up-prev-later');
  if ($prevLater) {
    $prevLater.onclick = () => { upState.prevDismissed = true; rerender(); };
  }
  root.querySelectorAll('[data-up-show]').forEach((b) => {
    b.onclick = () => api('POST', '/api/outside/show', { path: b.dataset.upShow }).catch(showError);
  });
  root.querySelectorAll('[data-up-apply-zip]').forEach((b) => {
    b.onclick = async () => {
      const orig = b.textContent;
      b.disabled = true;
      b.textContent = t('up_applying_zip');
      try {
        const res = await api('POST', '/api/update/apply-zip', { zip_path: b.dataset.upApplyZip });
        upState.appliedFolder = res.folder_name || '';
        toast(tf('up_applied_zip_done', { folder: res.folder_name }));
        rerender();
      } catch (e) {
        b.disabled = false;
        b.textContent = orig;
        showError(e);
      }
    };
  });
  root.querySelectorAll('[data-up-migrate]').forEach((b) => {
    b.onclick = async () => {
      const orig = b.textContent;
      b.disabled = true;
      b.textContent = t('up_migrating');
      if (typeof bkData === 'object' && bkData) bkData.replacing = true;
      try {
        const res = await api('POST', '/api/update/migrate', { source_path: b.dataset.upMigrate });
        if (typeof bkData === 'object' && bkData) {
          bkData.epoch = null;
          bkData.replacing = false;
        }
        upState.prevDismissed = true;
        upState.status = null;
        toast(tf('up_migrated_ok', { sc: res.scripts, sa: res.samples }));
        await router();
      } catch (e) {
        if (typeof bkData === 'object' && bkData) bkData.replacing = false;
        b.disabled = false;
        b.textContent = orig;
        showError(e);
      }
    };
  });
}

async function upCheckOnStartup() {
  if (appState.window_check) return;
  if (upState.status) {
    upMountStartupBanner();
    return;
  }
  try {
    const first = !sessionStorage.getItem('up-checked');
    sessionStorage.setItem('up-checked', '1');
    upState.status = await api('GET', first ? '/api/update?check=1' : '/api/update');
    upMountStartupBanner();
  } catch (e) {
    /* Non-blocking: offline startup never disturbs the creator. */
  }
}

async function upPaintSettings(box) {
  if (!box) return;
  try {
    if (!upState.status) upState.status = await api('GET', '/api/update');
  } catch (e) { return; }

  const draw = () => {
    const st = upState.status;
    if (!st || !box.isConnected) return;
    const latest = st.latest;
    const prevList = Array.isArray(st.previous_versions) ? st.previous_versions : [];
    const on = !!(st.checks && st.checks.on);
    const isDesktop = !!(st.current && st.current.desktop);

    const latestHtml = !on ? '' : (st.update_available && latest ? `
      <div class="banner" id="up-settings-banner" style="margin-top:14px;margin-bottom:0">
        ${upLatestBannerHtml(st, false)}
      </div>` : (st.last_checked_at ? `<p class="meta" id="up-up-to-date" style="margin-top:10px;margin-bottom:0">${esc(tf('up_up_to_date', { v: (st.current && st.current.version) || '' }))}</p>` : ''));

    let prevHtml = '';
    if (!isDesktop) {
      let prevRows = '';
      if (prevList.length > 0) {
        prevRows = prevList.map((p) => `<div class="row" style="margin-top:8px;flex-wrap:wrap;gap:8px">
          <span class="mono" style="font-size:13.5px">${esc(p.folder_name)}</span>
          <span class="meta">${esc(tf('up_prev_counts', { sc: p.scripts, sa: p.samples }))}</span>
          <span class="spacer"></span>
          ${st.data_empty ? `<button type="button" class="btn" data-up-migrate="${esc(p.path)}">${esc(t('up_migrate_btn'))}</button>` : ''}
        </div>`).join('') + (st.data_empty
          ? `<p class="hint" style="margin:8px 0 0">${esc(t('up_whisper_note'))}</p>`
          : `<p class="hint" id="up-prev-nonempty" style="margin:8px 0 0">${esc(t('up_prev_nonempty'))}</p>`);
      } else if (upState.manualScanned) {
        prevRows = `<p class="meta" id="up-prev-none" style="margin:8px 0 0">${esc(t('up_prev_none'))}</p>`;
      }
      prevHtml = `<div id="up-prev-section" style="margin-top:16px;padding-top:14px;border-top:1px solid var(--line)">
        <div class="row" style="gap:10px;flex-wrap:wrap">
          <strong>${esc(t('up_prev_title'))}</strong>
          <span class="spacer"></span>
          <button type="button" class="btn" id="up-scan-btn">${esc(t('up_scan_btn'))}</button>
        </div>
        ${prevRows}
      </div>`;
    }

    box.innerHTML = `<h2 class="sec-h">${esc(t('up_sec_title'))}</h2>
      <div class="card" id="up-settings-card">
        <p class="hint" id="up-persistence-note" style="margin-top:0">${esc(t(isDesktop ? 'up_persist_desktop' : 'up_persist_zip'))}</p>
        <p class="meta mono" id="up-data-path">${esc(tf('up_data_path', { path: st.data_dir }))}</p>
        <p class="hint" id="up-privacy-note">${esc(t('up_privacy_note'))}</p>
        <p class="hint" id="up-sha-note">${esc(t('up_sha_note'))}</p>
        ${on ? `<div class="row" style="margin-top:12px;flex-wrap:wrap;gap:14px">
          <label class="row" style="gap:6px;cursor:pointer">
            <input type="checkbox" id="up-auto-chk" ${st.auto_check ? 'checked' : ''}>
            <span>${esc(t('up_auto_label'))}</span>
          </label>
          <label class="row" style="gap:6px;cursor:pointer">
            <input type="checkbox" id="up-pre-chk" ${st.include_prereleases ? 'checked' : ''}>
            <span>${esc(t('up_pre_label'))}</span>
          </label>
          <span class="spacer"></span>
          <button type="button" class="btn" id="up-force-btn">${esc(t('up_check_btn'))}</button>
        </div>` : `<p class="hint" id="up-off">${esc(t('up_off'))}</p>`}
        ${latestHtml}
        ${prevHtml}
      </div>`;

    upBindActions(box, draw);
    const $auto = box.querySelector('#up-auto-chk');
    if ($auto) {
      $auto.onchange = async () => {
        try {
          upState.status = await api('POST', '/api/update/settings', { auto_check: $auto.checked });
          toast(t('saved'));
          upMountStartupBanner();
        } catch (e) { showError(e); }
      };
    }
    const $pre = box.querySelector('#up-pre-chk');
    if ($pre) {
      $pre.onchange = async () => {
        try {
          upState.status = await api('POST', '/api/update/settings', { include_prereleases: $pre.checked });
          toast(t('saved'));
          draw();
        } catch (e) { showError(e); }
      };
    }
    const $force = box.querySelector('#up-force-btn');
    if ($force) {
      $force.onclick = async () => {
        $force.disabled = true;
        $force.textContent = t('up_checking');
        try {
          upState.status = await api('GET', '/api/update?check=1&force=1');
          draw();
          upMountStartupBanner();
        } catch (e) {
          $force.disabled = false;
          $force.textContent = t('up_check_btn');
          showError(e);
        }
      };
    }
    const $scan = box.querySelector('#up-scan-btn');
    if ($scan) {
      $scan.onclick = async () => {
        $scan.disabled = true;
        $scan.textContent = t('up_scanning');
        try {
          upState.manualScanned = true;
          upState.status = await api('GET', '/api/update?scan=1');
          draw();
        } catch (e) {
          $scan.disabled = false;
          $scan.textContent = t('up_scan_btn');
          showError(e);
        }
      };
    }
  };
  draw();
}
