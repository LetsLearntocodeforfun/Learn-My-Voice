/* Auto-updater, startup version prompt, SHA-256 download, and cross-version settings migration (studio/updater.py). */

Object.assign(I18N.zh, {
  up_sec_title: '版本与自动更新',
  up_sec_sub: '你的设置、加密保存的 API 密钥、声音档案和脚本在升级版本时都会保留。',
  up_data_path: '数据与设置保存目录：{path}',
  up_auto_label: '启动时自动检查并提示升级新版本',
  up_pre_label: '包含测试版更新（Beta / RC）',
  up_check_btn: '检查更新',
  up_checking: '正在检查…',
  up_up_to_date: '当前已是最新版本（{v}）。',
  up_found_banner: '发现新版本 v{v}（当前：{cur}）· 升级后你的设置、密钥、声音档案与脚本全部保留。',
  up_download_btn: '下载并升级 v{v}',
  up_downloading: '正在下载并校验 SHA-256…',
  up_notes_btn: '查看更新说明',
  up_skip_btn: '跳过此版本',
  up_later_btn: '稍后提醒',
  up_downloaded_desktop: '安装包已通过 SHA-256 校验并保存：{name}。请先退出本应用，再运行新安装包覆盖安装（数据与设置自动保留）。',
  up_downloaded_zip: '新版压缩包已通过 SHA-256 校验并保存：{name}。可点击「一键解压并迁移全部设置与数据」。',
  up_apply_zip_btn: '一键解压新版并迁移设置与数据',
  up_applying_zip: '正在解压并迁移数据…',
  up_applied_zip_done: '已解压到 {folder}，并完整迁移了设置、API 密钥、声音档案与脚本！现在可关闭旧版并从新版目录启动。',
  up_show_file: '在文件夹中显示',
  up_prev_banner: '检测到旧版本文件夹（{folder}，含 {sc} 篇脚本、{sa} 条素材）。是否一键迁移你的设置、API 密钥、声音档案与数据？',
  up_prev_title: '从旧版本文件夹迁移设置与数据',
  up_migrate_btn: '一键迁移此旧版数据与设置',
  up_migrating: '正在迁移…',
  up_migrated_ok: '已成功迁移 {sc} 篇脚本、{sa} 条素材及全部设置！',
  up_off: '这次运行没有开启检查更新：应用是用 LEARN_MY_VOICE_NO_UPDATE_CHECK=1 启动的，或者是由试运行的脚本启动的。所以这里不会向 GitHub 询问，也不提供下载。',
});

Object.assign(I18N.en, {
  up_sec_title: 'Version & Auto-Update',
  up_sec_sub: 'Your settings, sealed API keys, Voice DNA, and scripts are preserved across app upgrades.',
  up_data_path: 'Settings & data location: {path}',
  up_auto_label: 'Check for updates and prompt to upgrade on startup',
  up_pre_label: 'Include Beta / Release Candidate updates',
  up_check_btn: 'Check for updates',
  up_checking: 'Checking…',
  up_up_to_date: 'You are on the latest version ({v}).',
  up_found_banner: 'Update available: v{v} (current: {cur}) · Your settings, API keys, Voice DNA, and scripts stay intact.',
  up_download_btn: 'Download & Upgrade to v{v}',
  up_downloading: 'Downloading & verifying SHA-256…',
  up_notes_btn: 'Release notes',
  up_skip_btn: 'Skip this version',
  up_later_btn: 'Remind me later',
  up_downloaded_desktop: 'Verified SHA-256 & saved installer: {name}. Quit Learn My Voice and run the installer — all settings and data stay intact.',
  up_downloaded_zip: 'Verified SHA-256 & saved package: {name}. Click below to unpack & carry over all settings, keys, and data automatically.',
  up_apply_zip_btn: 'Unpack New Version & Migrate All Settings',
  up_applying_zip: 'Unpacking & migrating…',
  up_applied_zip_done: 'Unpacked to {folder} with all settings, API keys, Voice DNA, and scripts migrated! Launch from the new folder.',
  up_show_file: 'Show in folder',
  up_prev_banner: 'Detected earlier version ({folder}: {sc} scripts, {sa} library items). Carry over all settings, API keys, Voice DNA, and data?',
  up_prev_title: 'Migrate Settings & Data from Earlier Version',
  up_migrate_btn: 'Carry over settings & data',
  up_migrating: 'Migrating…',
  up_migrated_ok: 'Migrated {sc} scripts, {sa} library items, and all settings!',
  up_off: 'Looking for updates is switched off for this run: the app was started with LEARN_MY_VOICE_NO_UPDATE_CHECK=1, or by a script that tries it. So nothing is asked of GitHub here, and no download is offered.',
});

const upState = {
  status: null,
  lastDownload: null,
  startupDismissed: false,
  prevDismissed: false,
};

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
    const dl = upState.lastDownload;
    const dlControls = dl ? `
      <div class="row" style="margin-top:10px;flex-wrap:wrap;gap:8px">
        <span class="meta">${esc(tf(dl.desktop ? 'up_downloaded_desktop' : 'up_downloaded_zip', { name: dl.saved.name }))}</span>
        <button type="button" class="btn" data-up-show="${esc(dl.saved.path)}">${esc(t('up_show_file'))}</button>
        ${dl.desktop ? '' : `<button type="button" class="btn primary" data-up-apply-zip="${esc(dl.saved.path)}">${esc(t('up_apply_zip_btn'))}</button>`}
      </div>` : '';
    cards.push(`<div class="banner" id="up-startup-banner" style="margin-bottom:16px">
      <div class="row" style="flex-wrap:wrap;gap:10px;align-items:center">
        <div class="spacer"><strong>${esc(tf('up_found_banner', { v: st.latest.version, cur: (st.current && st.current.version) || '' }))}</strong></div>
        <button type="button" class="btn primary" id="up-dl-now">${esc(tf('up_download_btn', { v: st.latest.version }))}</button>
        <button type="button" class="btn" id="up-notes-now">${esc(t('up_notes_btn'))}</button>
        <button type="button" class="btn" id="up-later-now">${esc(t('up_later_btn'))}</button>
        <button type="button" class="btn" id="up-skip-now">${esc(t('up_skip_btn'))}</button>
      </div>
      ${dlControls}
    </div>`);
  }

  const prevList = Array.isArray(st.previous_versions) ? st.previous_versions : [];
  if (!st.current.desktop && appState.sample_count === 0 && prevList.length > 0 && !upState.prevDismissed) {
    const topPrev = prevList[0];
    cards.push(`<div class="banner" id="up-prev-banner" style="margin-bottom:16px">
      <div class="row" style="flex-wrap:wrap;gap:10px;align-items:center">
        <div class="spacer">${esc(tf('up_prev_banner', { folder: topPrev.folder_name, sc: topPrev.scripts, sa: topPrev.samples }))}</div>
        <button type="button" class="btn primary" data-up-migrate="${esc(topPrev.path)}">${esc(t('up_migrate_btn'))}</button>
        <button type="button" class="btn" id="up-prev-later">${esc(t('up_later_btn'))}</button>
      </div>
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
  if ($notes && upState.status && upState.status.latest) {
    $notes.onclick = () => openOutside(upState.status.latest.html_url || upState.status.releases_url);
  }
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
        toast(tf('up_applied_zip_done', { folder: res.folder_name }));
        b.textContent = orig;
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
      try {
        const res = await api('POST', '/api/update/migrate', { source_path: b.dataset.upMigrate });
        toast(tf('up_migrated_ok', { sc: res.scripts, sa: res.samples }));
        upState.prevDismissed = true;
        await router();
      } catch (e) {
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
    /* Non-blocking: offline startup must never disturb the creator. */
  }
}

async function upPaintSettings(box) {
  if (!box) return;
  try {
    if (!upState.status) upState.status = await api('GET', '/api/update');
  } catch (e) { return; }

  const draw = () => {
    const st = upState.status;
    if (!st) return;
    const latest = st.latest;
    const dl = upState.lastDownload;
    const prevList = Array.isArray(st.previous_versions) ? st.previous_versions : [];
    const on = !!(st.checks && st.checks.on);
    const latestHtml = !on ? '' : st.update_available && latest ? `
      <div class="banner" style="margin-top:14px">
        <div class="row" style="flex-wrap:wrap;gap:10px;align-items:center">
          <div class="spacer"><strong>${esc(tf('up_found_banner', { v: latest.version, cur: (st.current && st.current.version) || '' }))}</strong></div>
          <button type="button" class="btn primary" id="up-dl-now">${esc(tf('up_download_btn', { v: latest.version }))}</button>
          <button type="button" class="btn" id="up-notes-now">${esc(t('up_notes_btn'))}</button>
        </div>
        ${dl ? `<div class="row" style="margin-top:10px;flex-wrap:wrap;gap:8px">
          <span class="meta">${esc(tf(dl.desktop ? 'up_downloaded_desktop' : 'up_downloaded_zip', { name: dl.saved.name }))}</span>
          <button type="button" class="btn" data-up-show="${esc(dl.saved.path)}">${esc(t('up_show_file'))}</button>
          ${dl.desktop ? '' : `<button type="button" class="btn primary" data-up-apply-zip="${esc(dl.saved.path)}">${esc(t('up_apply_zip_btn'))}</button>`}
        </div>` : ''}
      </div>` : (st.last_checked_at ? `<p class="meta" style="margin-top:10px">${esc(tf('up_up_to_date', { v: (st.current && st.current.version) || '' }))}</p>` : '');

    const prevHtml = (!st.current.desktop && prevList.length) ? `
      <div style="margin-top:16px">
        <div class="meta" style="margin-bottom:8px"><strong>${esc(t('up_prev_title'))}</strong></div>
        ${prevList.map((p) => `<div class="row" style="margin-top:6px;flex-wrap:wrap;gap:8px">
          <span class="meta">${esc(p.folder_name)} · ${p.scripts} / ${p.samples}</span>
          <span class="spacer"></span>
          <button type="button" class="btn" data-up-migrate="${esc(p.path)}">${esc(t('up_migrate_btn'))}</button>
        </div>`).join('')}
      </div>` : '';

    box.innerHTML = `<h2 class="sec-h">${esc(t('up_sec_title'))}</h2>
      <div class="card" id="up-settings-card">
        <p class="hint" style="margin-top:0">${esc(t('up_sec_sub'))}</p>
        <p class="meta mono">${esc(tf('up_data_path', { path: st.data_dir }))}</p>
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
        } catch (e) { showError(e); }
      };
    }
    const $pre = box.querySelector('#up-pre-chk');
    if ($pre) {
      $pre.onchange = async () => {
        try {
          upState.status = await api('POST', '/api/update/settings', { include_prereleases: $pre.checked });
          toast(t('saved'));
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
        } catch (e) {
          $force.disabled = false;
          $force.textContent = t('up_check_btn');
          showError(e);
        }
      };
    }
  };
  draw();
}
