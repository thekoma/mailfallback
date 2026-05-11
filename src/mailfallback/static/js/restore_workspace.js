(function () {
  const RW = window.RestoreWorkspace = {};

  function setDefaultRange() {
    // Defaults are encoded in the HTML range inputs (start=7, end=0).
    // Sync the hidden ISO fields and labels.
    syncRangeFromSlider();
  }

  function syncRangeFromSlider() {
    const startEl = document.getElementById('ws-range-start-days');
    const endEl = document.getElementById('ws-range-end-days');
    if (!startEl || !endEl) return;
    let startDays = parseInt(startEl.value, 10);
    let endDays = parseInt(endEl.value, 10);
    // Enforce: start >= end (start is further back in time)
    if (startDays < endDays) {
      [startDays, endDays] = [endDays, startDays];
    }
    const now = new Date();
    const startDate = new Date(now);
    startDate.setDate(now.getDate() - startDays);
    const endDate = new Date(now);
    endDate.setDate(now.getDate() - endDays);

    // Update hidden ISO fields (yyyy-mm-dd format expected by existing code paths)
    document.getElementById('ws-range-start').value = startDate.toISOString().slice(0, 10);
    document.getElementById('ws-range-end').value = endDate.toISOString().slice(0, 10);

    const fmt = (d) => d.toLocaleDateString(undefined, {month: 'short', day: 'numeric'});
    // Update visible labels
    document.getElementById('ws-range-start-label').textContent =
      startDays === 0 ? 'today' : `${startDays}d ago`;
    document.getElementById('ws-range-end-label').textContent =
      endDays === 0 ? 'today' : `${endDays}d ago`;

    // Update the gradient fill to span the chosen range visually.
    // Slider is "days ago": value 0 = right edge (today), value 365 = left edge.
    const max = parseInt(startEl.max, 10);
    const fill = document.getElementById('ws-range-fill');
    if (fill) {
      const leftDays = Math.max(startDays, endDays);
      const rightDays = Math.min(startDays, endDays);
      const leftPct = ((max - leftDays) / max) * 100;
      const rightPct = ((max - rightDays) / max) * 100;
      fill.style.left = leftPct + '%';
      fill.style.width = (rightPct - leftPct) + '%';
    }

    // Position tooltips above thumbs and write dates
    const startTooltip = document.getElementById('ws-range-start-tooltip');
    const endTooltip = document.getElementById('ws-range-end-tooltip');
    if (startTooltip) {
      const startPct = ((max - startDays) / max) * 100;
      startTooltip.style.left = startPct + '%';
      startTooltip.textContent = fmt(startDate);
    }
    if (endTooltip) {
      const endPct = ((max - endDays) / max) * 100;
      endTooltip.style.left = endPct + '%';
      endTooltip.textContent = fmt(endDate);
    }
  }

  // Hoisted so applyPreset can call them — actual fetches happen at click time.
  async function populateFolderPicker() {
    const accountId = document.getElementById('ws-account').value;
    const select = document.getElementById('ws-folder-select');
    if (!accountId) {
      select.innerHTML = '<option value="">(select a mailbox first)</option>';
      return;
    }
    select.innerHTML = '<option value="">loading…</option>';
    try {
      const resp = await fetch(`/api/accounts/${accountId}/mailboxes`);
      if (!resp.ok) {
        select.innerHTML = '<option value="">— failed to load —</option>';
        return;
      }
      const folders = await resp.json();
      if (!folders.length) {
        select.innerHTML = '<option value="">— no folders found —</option>';
        return;
      }
      select.innerHTML = folders.map(f =>
        `<option value="${escapeHtml(f.full_name || f.name)}">${escapeHtml(f.name)}</option>`
      ).join('');
    } catch (e) {
      select.innerHTML = '<option value="">— error —</option>';
    }
  }

  async function populateSnapshotPicker() {
    const accountId = document.getElementById('ws-account').value;
    const rangeStart = document.getElementById('ws-range-start').value;
    const rangeEnd = document.getElementById('ws-range-end').value;
    const select = document.getElementById('ws-snapshot-select');
    if (!accountId || !rangeStart || !rangeEnd) {
      select.innerHTML = '<option value="">(select range first)</option>';
      return;
    }
    // v1: no per-snapshot picker yet — engine picks the latest snapshot in range.
    // A full per-snapshot picker is a follow-up.
    select.innerHTML = '<option value="latest-in-range">latest snapshot in range</option>';
  }

  RW.applyPreset = function (preset) {
    document.querySelectorAll('.preset-chip').forEach(c => c.classList.toggle('is-active', c.dataset.preset === preset));

    const searchRow = document.getElementById('ws-search-row');
    const folderPicker = document.getElementById('ws-folder-picker');
    const snapshotPicker = document.getElementById('ws-snapshot-picker');
    const actionBar = document.getElementById('ws-action-bar');

    // Reset visibility
    searchRow.classList.add('hidden');
    folderPicker.classList.add('hidden');
    snapshotPicker.classList.add('hidden');
    actionBar.classList.add('hidden');

    // Default range for each preset — set sliders, then propagate
    const days = {'single-mail': 7, 'folder': 30, 'full': 90}[preset] || 7;
    document.getElementById('ws-range-start-days').value = String(days);
    document.getElementById('ws-range-end-days').value = '0';
    syncRangeFromSlider();

    if (preset === 'single-mail') {
      searchRow.classList.remove('hidden');
      document.getElementById('ws-include-live').checked = true;
      document.getElementById('ws-include-snapshots').checked = true;
    } else if (preset === 'folder') {
      folderPicker.classList.remove('hidden');
      document.getElementById('ws-include-live').checked = true;
      document.getElementById('ws-include-snapshots').checked = false;
      populateFolderPicker();
    } else if (preset === 'full') {
      snapshotPicker.classList.remove('hidden');
      document.getElementById('ws-include-live').checked = false;
      document.getElementById('ws-include-snapshots').checked = true;
      populateSnapshotPicker();
    }

    if (typeof RW._updateRangeCost === 'function') RW._updateRangeCost();
  };

  RW.runSearch = async function () {
    const accountId = document.getElementById('ws-account').value;
    const query = document.getElementById('ws-query').value.trim();
    const rangeStart = document.getElementById('ws-range-start').value;
    const rangeEnd = document.getElementById('ws-range-end').value;
    const includeLive = document.getElementById('ws-include-live').checked;
    const includeSnapshots = document.getElementById('ws-include-snapshots').checked;
    // Multi-field filter panel — defaults to Subject ON when nothing else
    // touched. The legacy "Search in body" switch in the sidebar Advanced
    // section is OR'd with the new Body filter so neither path regresses.
    const sfSubject = document.getElementById('ws-sf-subject').checked;
    const sfFrom = document.getElementById('ws-sf-from').checked;
    const sfTo = document.getElementById('ws-sf-to').checked;
    const sfBody = document.getElementById('ws-sf-body').checked;
    const sidebarSearchBody = document.getElementById('ws-search-body').checked;
    const typeFilter = document.getElementById('ws-sf-type').value;
    const ttlOverrideRaw = document.getElementById('ws-ttl-override').value.trim();
    const ttlOverride = ttlOverrideRaw ? parseInt(ttlOverrideRaw, 10) : null;
    if (!query) return;

    const resultsEl = document.getElementById('ws-results');
    const progressEl = document.getElementById('ws-mount-progress');
    progressEl.textContent = 'Mounting snapshots & searching…';
    resultsEl.innerHTML = '';

    try {
      const resp = await fetch('/api/restore/workspace/search', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
          account_id: accountId,
          query: query,
          range_start: new Date(rangeStart).toISOString(),
          range_end: new Date(rangeEnd + 'T23:59:59').toISOString(),
          include_live: includeLive,
          include_snapshots: includeSnapshots,
          search_subject: sfSubject,
          search_from: sfFrom,
          search_to: sfTo,
          search_body: sfBody || sidebarSearchBody,
          type_filter: typeFilter,
          ttl_minutes: ttlOverride,
        }),
      });
      if (!resp.ok) {
        progressEl.textContent = `Search failed: ${resp.status}`;
        return;
      }
      const body = await resp.json();
      progressEl.textContent = `${body.results.length} result(s) · ${body.mounted_snapshots.length} snapshot(s) mounted`;
      RW.renderResults(body.results);
    } catch (e) {
      progressEl.textContent = `Search error: ${e.message}`;
    }
  };

  RW.renderResults = function (results) {
    RW.lastResults = results;
    const el = document.getElementById('ws-results');
    const bar = document.getElementById('ws-action-bar');
    if (!results.length) {
      el.innerHTML = '<p class="text-muted text-small">Nothing matched. Try expanding the time range.</p>';
      bar.classList.add('hidden');
      return;
    }
    el.innerHTML = results.map(r => `
      <div class="ws-result" data-msgid="${r.message_id || ''}">
        <label class="ws-result-row">
          <input type="checkbox" class="ws-result-cb" value="${r.message_id || ''}">
          <div class="ws-result-meta">
            <strong>${escapeHtml(r.subject || '(no subject)')}</strong>
            <div class="text-muted text-xsmall">${escapeHtml(r.folder || '')} · from ${escapeHtml(r.from || '?')}</div>
            <div class="ws-badges">
              ${r.sources.map(s => `<span class="ws-badge ws-badge-${s === 'live' ? 'live' : 'snap'}">${escapeHtml(s)}</span>`).join('')}
            </div>
          </div>
        </label>
      </div>
    `).join('');
    bar.classList.remove('hidden');
  };

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  }

  document.addEventListener('DOMContentLoaded', () => {
    setDefaultRange();
    document.querySelectorAll('.preset-chip').forEach(chip => {
      chip.addEventListener('click', () => RW.applyPreset(chip.dataset.preset));
    });
    document.getElementById('ws-select-all').addEventListener('change', e => {
      document.querySelectorAll('.ws-result-cb').forEach(cb => { cb.checked = e.target.checked; });
    });

    document.getElementById('ws-restore-selected').addEventListener('click', async () => {
      const selectedRows = Array.from(document.querySelectorAll('.ws-result-cb:checked'))
        .map(cb => cb.closest('.ws-result'));
      if (!selectedRows.length) {
        alert('Select at least one result');
        return;
      }

      // RW.lastResults is set by renderResults — used to look up locations.
      const byMsgid = Object.fromEntries((RW.lastResults || []).map(r => [r.message_id, r]));

      // Group locations by source label; pick "best" location per result
      // (priority: live > snapshot listed earliest in sources).
      const grouped = {};  // sourceLabel -> {folder: [uid, ...]}
      for (const row of selectedRows) {
        const msgid = row.dataset.msgid;
        const result = byMsgid[msgid];
        if (!result) continue;
        const best = result.locations.find(l => l.source === 'live') || result.locations[0];
        if (!best) continue;
        if (!grouped[best.source]) grouped[best.source] = {};
        const folderKey = (best.namespace || '') + best.folder;
        if (!grouped[best.source][folderKey]) grouped[best.source][folderKey] = [];
        grouped[best.source][folderKey].push(String(best.uid));
      }

      const sourceAcct = document.getElementById('ws-account').value;
      const destAcct = document.getElementById('ws-destination').value;
      const jobs = [];
      for (const [sourceLabel, selected_uids] of Object.entries(grouped)) {
        const resp = await fetch('/api/restore', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({
            source_account_id: sourceAcct,
            target_account_id: destAcct,
            restore_mode: 'selection',
            selected_uids: selected_uids,
          }),
        });
        if (resp.ok) {
          jobs.push((await resp.json()).job_id);
        } else {
          alert(`Failed for source ${sourceLabel}: ${resp.status}`);
          return;
        }
      }
      alert(`Started ${jobs.length} restore job(s): ${jobs.join(', ')}`);
    });

    document.getElementById('ws-restore-folder-btn').addEventListener('click', async () => {
      const folder = document.getElementById('ws-folder-select').value;
      if (!folder) {
        alert('Pick a folder first');
        return;
      }
      const sourceAcct = document.getElementById('ws-account').value;
      const destAcct = document.getElementById('ws-destination').value;
      const progressEl = document.getElementById('ws-folder-progress');
      progressEl.textContent = 'Submitting…';
      try {
        const resp = await fetch('/api/restore', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({
            source_account_id: sourceAcct,
            target_account_id: destAcct,
            restore_mode: 'folder',
            selected_folders: [folder],
          }),
        });
        if (resp.ok) {
          const job = await resp.json();
          progressEl.textContent = `Folder restore started — job ${job.job_id}`;
          alert(`Folder restore started — job ${job.job_id}`);
        } else {
          progressEl.textContent = `Failed: ${resp.status}`;
          alert(`Failed: ${resp.status}`);
        }
      } catch (e) {
        progressEl.textContent = `Error: ${e.message}`;
      }
    });

    document.getElementById('ws-restore-full-btn').addEventListener('click', async () => {
      const sourceAcct = document.getElementById('ws-account').value;
      const destAcct = document.getElementById('ws-destination').value;
      if (!confirm('Full restore copies the entire mailbox. Continue?')) return;
      const progressEl = document.getElementById('ws-full-progress');
      progressEl.textContent = 'Submitting…';
      try {
        const resp = await fetch('/api/restore', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({
            source_account_id: sourceAcct,
            target_account_id: destAcct,
            restore_mode: 'full',
          }),
        });
        if (resp.ok) {
          const job = await resp.json();
          progressEl.textContent = `Full restore started — job ${job.job_id}`;
          alert(`Full restore started — job ${job.job_id}`);
        } else {
          progressEl.textContent = `Failed: ${resp.status}`;
          alert(`Failed: ${resp.status}`);
        }
      } catch (e) {
        progressEl.textContent = `Error: ${e.message}`;
      }
    });

    let _costRequestSeq = 0;  // to ignore stale responses
    let _costDebounceTimer = null;

    function updateRangeCost() {
      // Show "calculating..." immediately for snappy feedback
      const el = document.getElementById('ws-range-cost');
      if (!el) return;
      el.textContent = '⟳ calculating…';
      el.classList.add('ws-cost-loading');

      // Debounce the actual fetch (250ms) to avoid spamming during drag
      clearTimeout(_costDebounceTimer);
      _costDebounceTimer = setTimeout(() => {
        _doUpdateRangeCost();
      }, 250);
    }

    async function _doUpdateRangeCost() {
      const accountId = document.getElementById('ws-account').value;
      const rangeStart = document.getElementById('ws-range-start').value;
      const rangeEnd = document.getElementById('ws-range-end').value;
      const el = document.getElementById('ws-range-cost');
      if (!accountId || !rangeStart || !rangeEnd) {
        el.textContent = '— snapshots in range';
        el.classList.remove('ws-cost-loading');
        return;
      }
      const seq = ++_costRequestSeq;
      try {
        const resp = await fetch('/api/restore/workspace/snapshot-count', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({
            account_id: accountId,
            range_start: new Date(rangeStart).toISOString(),
            range_end: new Date(rangeEnd + 'T23:59:59').toISOString(),
          }),
        });
        if (seq !== _costRequestSeq) return;  // a newer request superseded
        if (!resp.ok) {
          el.textContent = '— snapshots in range (error)';
          el.classList.remove('ws-cost-loading');
          return;
        }
        const body = await resp.json();
        if (seq !== _costRequestSeq) return;
        const sizeMB = (body.size_bytes / 1_000_000).toFixed(0);
        el.textContent = `${body.count} snapshot${body.count === 1 ? '' : 's'} in range · ~${sizeMB} MB`;
        el.classList.remove('ws-cost-loading');
      } catch (e) {
        if (seq !== _costRequestSeq) return;
        el.textContent = '— snapshots in range';
        el.classList.remove('ws-cost-loading');
      }
    }

    const startEl = document.getElementById('ws-range-start-days');
    const endEl = document.getElementById('ws-range-end-days');
    if (startEl) startEl.addEventListener('input', () => { syncRangeFromSlider(); updateRangeCost(); });
    if (endEl) endEl.addEventListener('input', () => { syncRangeFromSlider(); updateRangeCost(); });
    document.getElementById('ws-account').addEventListener('change', updateRangeCost);
    // Expose for applyPreset (which lives outside this scope) so it can
    // refresh the cost preview after changing the default range.
    RW._updateRangeCost = updateRangeCost;
    // Trigger once on load (after setDefaultRange has populated the inputs)
    updateRangeCost();
  });
})();
