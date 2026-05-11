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
    startDays: 7,
    endDays: 0,
    includeLive: true,
    includeSnapshots: true,
    ttlOverride: null,
    query: '',
    filters: {subject: true, from: false, to: false, body: false, type: 'all'},
    filtersOpen: false,
    selectedFolder: '',

    // Slider drag state
    dragging: false,

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
    get startLabel() {
      return this.startDays === 0 ? 'today' : `${this.startDays}d ago`;
    },
    get endLabel() {
      return this.endDays === 0 ? 'today' : `${this.endDays}d ago`;
    },
    get startDateLabel() {
      const d = new Date();
      d.setDate(d.getDate() - this.startDays);
      return d.toLocaleDateString(undefined, {month: 'short', day: 'numeric'});
    },
    get endDateLabel() {
      const d = new Date();
      d.setDate(d.getDate() - this.endDays);
      return d.toLocaleDateString(undefined, {month: 'short', day: 'numeric'});
    },
    get startThumbPct() {
      return ((365 - this.startDays) / 365) * 100;
    },
    get endThumbPct() {
      return ((365 - this.endDays) / 365) * 100;
    },
    get fillLeft() {
      const max = Math.max(this.startDays, this.endDays);
      return ((365 - max) / 365) * 100;
    },
    get fillWidth() {
      const max = Math.max(this.startDays, this.endDays);
      const min = Math.min(this.startDays, this.endDays);
      return (((365 - min) / 365) * 100) - (((365 - max) / 365) * 100);
    },
    get rangeStartIso() {
      const d = new Date();
      d.setDate(d.getDate() - Math.max(this.startDays, this.endDays));
      d.setHours(0, 0, 0, 0);
      return d.toISOString();
    },
    get rangeEndIso() {
      const d = new Date();
      d.setDate(d.getDate() - Math.min(this.startDays, this.endDays));
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
      this.refreshIcons();
      this.updateRangeCost();
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
      this.startDays = days;
      this.endDays = 0;
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
      this.updateRangeCost();
      if (this.preset === 'folder') this.loadFolders();
    },

    onRangeChange() {
      // Swap if start crossed end
      if (this.startDays < this.endDays) {
        const tmp = this.startDays;
        this.startDays = this.endDays;
        this.endDays = tmp;
      }
      this.updateRangeCost();
    },

    updateRangeCost() {
      this.costText = '⟳ calculating…';
      this.costLoading = true;
      clearTimeout(this._costTimer);
      this._costTimer = setTimeout(() => this._fetchCost(), 250);
    },

    async _fetchCost() {
      if (!this.accountId) {
        this.costText = '— snapshots in range';
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
