function restoreWorkspace() {
  return {
    // === State ===
    presets: [
      {id: 'single-mail', label: 'A single mail', icon: 'mail'},
      {id: 'folder', label: 'A folder / subset', icon: 'folder'},
      {id: 'full', label: 'The whole mailbox', icon: 'alert-triangle'},
    ],
    preset: 'single-mail',

    // Inputs
    accountId: '',
    destinationId: '',
    rangeStart: null,           // Date object — managed by flatpickr
    rangeEnd: null,             // Date object — managed by flatpickr
    snapshotDates: [],          // Array of YYYY-MM-DD strings
    _fp: null,                  // flatpickr instance

    includeLive: true,
    includeSnapshots: true,
    ttlOverride: null,
    query: '',
    scopeAccountId: '',         // '' = all visible mailboxes
    deepSearch: false,
    partial: false,
    selectedFolder: '',

    // Async/UI state
    searching: false,
    searched: false,
    results: [],
    selected: [],               // selKey(r) strings — unique across accounts
    statusText: '',
    restoring: false,

    // Preview pane state
    preview: null,
    previewOpen: false,
    previewLoading: false,

    // From the template: data island + root data attribute
    accounts: [],
    webmailUrl: '',

    folders: [],
    folderStatus: '',
    fullStatus: '',

    // Cost preview state
    costText: '— snapshots in range',
    costLoading: false,
    _costSeq: 0,
    _costTimer: null,

    // === Computed ===
    get rangeStartIso() {
      if (!this.rangeStart) return null;
      const d = new Date(this.rangeStart);
      d.setHours(0, 0, 0, 0);
      return d.toISOString();
    },
    get rangeEndIso() {
      if (!this.rangeEnd) return null;
      const d = new Date(this.rangeEnd);
      d.setHours(23, 59, 59, 999);
      return d.toISOString();
    },

    // === Lifecycle ===
    init() {
      // Pick first mailbox
      const sel = document.querySelector('[x-model="accountId"]');
      if (sel && sel.options.length > 0) this.accountId = sel.options[0].value;
      const destSel = document.querySelector('[x-model="destinationId"]');
      if (destSel && destSel.options.length > 0) this.destinationId = destSel.options[0].value;

      // Account names for result badges (data island) + webmail link target.
      const island = document.getElementById('ws-accounts-data');
      if (island) {
        try {
          this.accounts = JSON.parse(island.textContent) || [];
        } catch (e) {
          this.accounts = [];
        }
      }
      this.webmailUrl = (this.$el && this.$el.dataset.webmailUrl) || '';

      this._initCalendar();
      this.refreshIcons();

      // Default range: last 7 days. Set state DIRECTLY (don't rely on
      // flatpickr's onChange firing during init — it doesn't always).
      const end = new Date();
      const start = new Date();
      start.setDate(start.getDate() - 7);
      this.rangeStart = start;
      this.rangeEnd = end;
      if (this._fp) this._fp.setDate([start, end], false);

      this.fetchSnapshotDates().then(() => this.updateRangeCost());
      this.updateRangeCost();
    },

    _initCalendar() {
      const self = this;
      const input = document.getElementById('ws-calendar-input');
      if (!input || !window.flatpickr) return;
      this._fp = window.flatpickr(input, {
        mode: 'range',
        inline: true,
        dateFormat: 'Y-m-d',
        maxDate: 'today',
        onChange(selectedDates) {
          if (selectedDates.length === 2) {
            self.rangeStart = selectedDates[0];
            self.rangeEnd = selectedDates[1];
            self.updateRangeCost();
          }
          // selectedDates.length === 1 — mid-range selection, wait for second click
        },
        onDayCreate(dObj, dStr, fp, dayElem) {
          const d = dayElem.dateObj;
          if (!d) return;
          const iso = d.getFullYear() + '-' +
                      String(d.getMonth() + 1).padStart(2, '0') + '-' +
                      String(d.getDate()).padStart(2, '0');
          if (self.snapshotDates.includes(iso)) {
            dayElem.classList.add('has-snapshot');
            const dot = document.createElement('span');
            dot.className = 'snapshot-dot';
            dayElem.appendChild(dot);
          }
        },
      });
    },

    async fetchSnapshotDates() {
      if (!this.accountId) return;
      try {
        const resp = await fetch('/api/restore/workspace/snapshot-dates', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({account_id: this.accountId}),
        });
        if (!resp.ok) {
          this.snapshotDates = [];
          return;
        }
        const body = await resp.json();
        this.snapshotDates = body.dates || [];
        // Re-render flatpickr to re-run onDayCreate with the fresh data set.
        if (this._fp) this._fp.redraw();
      } catch (e) {
        this.snapshotDates = [];
      }
    },

    refreshIcons() {
      // Re-init Lucide after Alpine renders dynamic content
      this.$nextTick(() => {
        if (window.lucide) window.lucide.createIcons();
      });
    },

    // === Result/preview helpers ===
    accountName(id) {
      const a = this.accounts.find(x => x.id === id);
      return a ? a.name : '?';
    },
    fmtSize(n) {
      if (n == null) return '?';
      if (n < 1024) return n + ' B';
      if (n < 1048576) return (n / 1024).toFixed(0) + ' KB';
      return (n / 1048576).toFixed(1) + ' MB';
    },
    resultMeta(r) {
      const parts = [r.from_addr || '?', r.folder_path || ''];
      if (r.date_sent) parts.push(r.date_sent.slice(0, 10));
      return parts.filter(Boolean).join(' · ');
    },
    selKey(r) { return r.account_id + ':' + r.message_id; },
    async openPreview(r) {
      this.previewOpen = true;
      this.previewLoading = true;
      try {
        const resp = await fetch(`/api/restore/preview/${r.account_id}/${r.message_id_hash}`);
        this.preview = resp.ok ? await resp.json() : null;
      } catch (e) {
        this.preview = null;
      } finally {
        this.previewLoading = false;
        this.refreshIcons();
      }
    },

    // === Actions ===
    applyPreset(id) {
      this.preset = id;
      const days = {'single-mail': 7, 'folder': 30, 'full': 90}[id] || 7;
      const end = new Date();
      const start = new Date();
      start.setDate(start.getDate() - days);
      this.rangeStart = start;
      this.rangeEnd = end;
      if (this._fp) this._fp.setDate([start, end], false);
      this.results = [];
      this.selected = [];
      this.searched = false;
      this.statusText = '';
      this.preview = null;
      this.previewOpen = false;
      this.refreshIcons();
      if (id === 'folder') this.loadFolders();
      this.updateRangeCost();
    },

    onAccountChange() {
      this.results = [];
      this.selected = [];
      this.searched = false;
      this.statusText = '';
      this.fetchSnapshotDates().then(() => this.updateRangeCost());
      if (this.preset === 'folder') this.loadFolders();
    },

    updateRangeCost() {
      this.costText = '⟳ calculating…';
      this.costLoading = true;
      clearTimeout(this._costTimer);
      this._costTimer = setTimeout(() => this._fetchCost(), 250);
    },

    async _fetchCost() {
      if (!this.accountId || !this.rangeStart || !this.rangeEnd) {
        this.costText = '— pick a range';
        this.costLoading = false;
        return;
      }
      const seq = ++this._costSeq;
      try {
        const resp = await fetch('/api/restore/workspace/snapshot-count', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({
            account_id: this.accountId,
            range_start: this.rangeStartIso,
            range_end: this.rangeEndIso,
          }),
        });
        if (seq !== this._costSeq) return;
        if (!resp.ok) {
          this.costText = '— snapshots in range (error)';
          this.costLoading = false;
          return;
        }
        const body = await resp.json();
        if (seq !== this._costSeq) return;
        const sizeMB = (body.size_bytes / 1_000_000).toFixed(0);
        this.costText = `${body.count} snapshot${body.count === 1 ? '' : 's'} in range · ~${sizeMB} MB`;
        this.costLoading = false;
      } catch (e) {
        if (seq !== this._costSeq) return;
        this.costText = '— snapshots in range';
        this.costLoading = false;
      }
    },

    async runSearch() {
      if (!this.query.trim()) return;
      this.searching = true;
      this.searched = true;
      this.statusText = 'Searching…';
      this.results = [];
      this.partial = false;
      this.selected = [];
      this.preview = null;
      this.previewOpen = false;
      this.refreshIcons();
      try {
        const resp = await fetch('/api/restore/search', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({
            query: this.query,
            account_ids: this.scopeAccountId ? [this.scopeAccountId] : null,
            range_start: this.rangeStartIso,
            range_end: this.rangeEndIso,
            include_deleted: this.includeSnapshots,
            deep: this.deepSearch,
            page: 1,
            page_size: 100,
          }),
        });
        if (!resp.ok) {
          this.statusText = `Search failed: ${resp.status}`;
          return;
        }
        const body = await resp.json();
        let results = body.results || [];
        // Source filters: the index API has no include_live param, so when
        // "Live" is unchecked we drop rows whose ONLY source is live mail
        // (i.e. keep rows that also exist in at least one snapshot). The
        // "Snapshots" toggle maps to include_deleted=false server-side, which
        // drops snapshot-only rows (messages no longer in live mail).
        if (!this.includeLive) {
          results = results.filter(r => (r.snapshots || []).length > 0);
        }
        this.results = results;
        this.partial = !!body.partial;
        this.statusText = `${body.total} result${body.total === 1 ? '' : 's'}`;
      } catch (e) {
        this.statusText = `Search error: ${e.message}`;
      } finally {
        this.searching = false;
        this.refreshIcons();
      }
    },

    toggleSelectAll(checked) {
      this.selected = checked ? this.results.map(r => this.selKey(r)) : [];
    },

    async restoreSelected() {
      // Restore-to-origin: group selected messages per account, resolve their
      // Message-Ids to live IMAP UIDs, then submit one selection-mode restore
      // job per account with source == target.
      if (this.selected.length === 0) return;
      this.restoring = true;
      this.refreshIcons();
      try {
        const byKey = Object.fromEntries(this.results.map(r => [this.selKey(r), r]));
        const byAccount = {};
        for (const key of this.selected) {
          const r = byKey[key];
          if (r) (byAccount[r.account_id] ||= []).push(r.message_id);
        }
        const jobs = [];
        let skippedTotal = 0;
        for (const [accountId, messageIds] of Object.entries(byAccount)) {
          const res = await fetch('/api/restore/resolve-uids', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({account_id: accountId, message_ids: messageIds}),
          });
          if (!res.ok) { this.statusText = `Resolve failed: ${res.status}`; return; }
          const {resolved, missing} = await res.json();
          skippedTotal += missing.length;
          if (Object.keys(resolved).length === 0) continue;
          // SEAM CONTRACT: `resolved` keys are namespace-prefixed IMAP paths
          // produced by /api/restore/resolve-uids — pass the mapping to
          // /api/restore COMPLETELY UNTOUCHED (no prefixing/stripping).
          const r = await fetch('/api/restore', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
              source_account_id: accountId,
              target_account_id: accountId,
              restore_mode: 'selection',
              selected_uids: resolved,
            }),
          });
          if (r.ok) jobs.push((await r.json()).job_id);
          else { this.statusText = `Restore failed for ${this.accountName(accountId)}: ${r.status}`; return; }
        }
        const bits = [];
        if (jobs.length) bits.push(`Started ${jobs.length} restore job${jobs.length === 1 ? '' : 's'} (to origin)`);
        if (skippedTotal) bits.push(`${skippedTotal} message${skippedTotal === 1 ? '' : 's'} not in live mail — skipped (snapshot-only restore arrives with the staging area)`);
        this.statusText = bits.join(' · ') || 'Nothing to restore.';
        this.selected = [];
      } finally {
        this.restoring = false;
        this.refreshIcons();
      }
    },

    async loadFolders() {
      if (!this.accountId) return;
      this.folders = [];
      try {
        const resp = await fetch(`/api/accounts/${this.accountId}/mailboxes`);
        if (!resp.ok) return;
        this.folders = await resp.json();
      } catch (e) {
        // ignore
      }
    },

    async restoreFolder() {
      if (!this.selectedFolder) return;
      this.restoring = true;
      this.refreshIcons();
      try {
        const resp = await fetch('/api/restore', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({
            source_account_id: this.accountId,
            target_account_id: this.destinationId,
            restore_mode: 'folder',
            selected_folders: [this.selectedFolder],
          }),
        });
        if (resp.ok) {
          this.folderStatus = `Folder restore started — job ${(await resp.json()).job_id}`;
        } else {
          this.folderStatus = `Failed: ${resp.status}`;
        }
      } finally {
        this.restoring = false;
        this.refreshIcons();
      }
    },

    async restoreFull() {
      if (!confirm('Full restore copies the entire mailbox. Continue?')) return;
      this.restoring = true;
      this.refreshIcons();
      try {
        const resp = await fetch('/api/restore', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({
            source_account_id: this.accountId,
            target_account_id: this.destinationId,
            restore_mode: 'full',
          }),
        });
        if (resp.ok) {
          this.fullStatus = `Full restore started — job ${(await resp.json()).job_id}`;
        } else {
          this.fullStatus = `Failed: ${resp.status}`;
        }
      } finally {
        this.restoring = false;
        this.refreshIcons();
      }
    },
  };
}
