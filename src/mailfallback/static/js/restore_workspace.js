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
    filters: {subject: true, from: false, to: false, body: false, type: 'all'},
    filtersOpen: false,
    selectedFolder: '',

    // Async/UI state
    searching: false,
    searched: false,
    results: [],
    selected: [],
    statusText: '',
    restoring: false,

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
      this.statusText = 'Mounting snapshots & searching…';
      this.results = [];
      this.selected = [];
      this.refreshIcons();
      try {
        const resp = await fetch('/api/restore/workspace/search', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({
            account_id: this.accountId,
            query: this.query,
            range_start: this.rangeStartIso,
            range_end: this.rangeEndIso,
            include_live: this.includeLive,
            include_snapshots: this.includeSnapshots,
            search_subject: this.filters.subject,
            search_from: this.filters.from,
            search_to: this.filters.to,
            search_body: this.filters.body,
            type_filter: this.filters.type,
            ttl_minutes: this.ttlOverride,
          }),
        });
        if (!resp.ok) {
          this.statusText = `Search failed: ${resp.status}`;
          return;
        }
        const body = await resp.json();
        this.results = body.results || [];
        this.statusText = `${this.results.length} result${this.results.length === 1 ? '' : 's'} · ${(body.mounted_snapshots || []).length} snapshot${(body.mounted_snapshots || []).length === 1 ? '' : 's'} mounted`;
      } catch (e) {
        this.statusText = `Search error: ${e.message}`;
      } finally {
        this.searching = false;
        this.refreshIcons();
      }
    },

    toggleSelectAll(checked) {
      this.selected = checked ? this.results.map(r => r.message_id).filter(Boolean) : [];
    },

    async restoreSelected() {
      if (this.selected.length === 0) return;
      this.restoring = true;
      this.refreshIcons();
      try {
        const byMsgid = Object.fromEntries(this.results.map(r => [r.message_id, r]));
        const grouped = {};
        for (const msgid of this.selected) {
          const r = byMsgid[msgid];
          if (!r) continue;
          const best = r.locations.find(l => l.source === 'live') || r.locations[0];
          if (!best) continue;
          if (!grouped[best.source]) grouped[best.source] = {};
          const folderKey = (best.namespace || '') + best.folder;
          if (!grouped[best.source][folderKey]) grouped[best.source][folderKey] = [];
          grouped[best.source][folderKey].push(String(best.uid));
        }
        const jobs = [];
        for (const [src, selected_uids] of Object.entries(grouped)) {
          const r = await fetch('/api/restore', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
              source_account_id: this.accountId,
              target_account_id: this.destinationId,
              restore_mode: 'selection',
              selected_uids: selected_uids,
            }),
          });
          if (r.ok) jobs.push((await r.json()).job_id);
          else {
            this.statusText = `Failed for source ${src}: ${r.status}`;
            return;
          }
        }
        this.statusText = `Started ${jobs.length} restore job${jobs.length === 1 ? '' : 's'}: ${jobs.join(', ')}`;
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
