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

  RW.applyPreset = function (preset) {
    document.querySelectorAll('.preset-chip').forEach(c => c.classList.toggle('is-active', c.dataset.preset === preset));
    if (preset === 'full') {
      document.getElementById('ws-include-live').checked = false;
      document.getElementById('ws-include-snapshots').checked = true;
    } else if (preset === 'folder') {
      document.getElementById('ws-include-live').checked = true;
      document.getElementById('ws-include-snapshots').checked = true;
    } else {
      document.getElementById('ws-include-live').checked = true;
      document.getElementById('ws-include-snapshots').checked = true;
    }
  };

  RW.runSearch = async function () {
    const accountId = document.getElementById('ws-account').value;
    const query = document.getElementById('ws-query').value.trim();
    const rangeStart = document.getElementById('ws-range-start').value;
    const rangeEnd = document.getElementById('ws-range-end').value;
    const includeLive = document.getElementById('ws-include-live').checked;
    const includeSnapshots = document.getElementById('ws-include-snapshots').checked;
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
  });
})();
