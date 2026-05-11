(function () {
  const RW = window.RestoreWorkspace = {};

  // Default range: last 7 days.
  function setDefaultRange() {
    const end = new Date();
    const start = new Date();
    start.setDate(start.getDate() - 7);
    document.getElementById('ws-range-start').valueAsDate = start;
    document.getElementById('ws-range-end').valueAsDate = end;
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

    // Default range for each preset
    const days = {'single-mail': 7, 'folder': 30, 'full': 90}[preset] || 7;
    const end = new Date();
    const start = new Date();
    start.setDate(start.getDate() - days);
    document.getElementById('ws-range-start').valueAsDate = start;
    document.getElementById('ws-range-end').valueAsDate = end;

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
    const searchBody = document.getElementById('ws-search-body').checked;
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
          search_body: searchBody,
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

    async function updateRangeCost() {
      const accountId = document.getElementById('ws-account').value;
      const rangeStart = document.getElementById('ws-range-start').value;
      const rangeEnd = document.getElementById('ws-range-end').value;
      const el = document.getElementById('ws-range-cost');
      if (!accountId || !rangeStart || !rangeEnd) {
        el.textContent = '— snapshots in range';
        return;
      }
      el.textContent = 'counting…';
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
        if (!resp.ok) {
          el.textContent = '— snapshots in range (error)';
          return;
        }
        const body = await resp.json();
        const sizeMB = (body.size_bytes / 1_000_000).toFixed(0);
        el.textContent = `${body.count} snapshot${body.count === 1 ? '' : 's'} in range · ~${sizeMB} MB`;
      } catch (e) {
        el.textContent = '— snapshots in range';
      }
    }

    document.getElementById('ws-range-start').addEventListener('change', updateRangeCost);
    document.getElementById('ws-range-end').addEventListener('change', updateRangeCost);
    document.getElementById('ws-account').addEventListener('change', updateRangeCost);
    // Expose for applyPreset (which lives outside this scope) so it can
    // refresh the cost preview after changing the default range.
    RW._updateRangeCost = updateRangeCost;
    // Trigger once on load (after setDefaultRange has populated the inputs)
    updateRangeCost();
  });
})();
