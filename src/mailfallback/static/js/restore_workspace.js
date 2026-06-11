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
    _datesSeq: 0,               // monotonic seq — stale snapshot-dates responses are dropped
    _fp: null,                  // flatpickr instance

    includeLive: true,
    includeSnapshots: true,
    query: '',
    scopeAccountId: '',         // '' = all visible mailboxes
    includeAll: false,          // admin-only: audited cross-user scope escalation
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
    previewRef: null,           // the result row behind the open preview — staging adds need its ids
    previewOpen: false,
    previewLoading: false,
    _previewSeq: 0,             // monotonic seq — stale preview responses are dropped

    // Staging bar / push panel state
    staging: {exists: false, count: 0, bytes_used: 0, expires_at: null, max_bytes: 0},
    stagingStatus: '',          // staging feedback — rendered in the bar (statusText
                                // only renders inside the single-mail preset)
    pushPanelOpen: false,
    pushDestination: 'origin',  // 'origin' | 'override' (the API gets an account id)
    pushOverrideId: '',
    pushFolderMode: 'original', // 'original' | 'restored'
    pushing: false,             // guards against overlapping pushes
    _stagingTimers: [],         // post-push delayed refreshes — cleared on re-push

    // From the template: data islands + root data attribute
    accounts: [],               // active scope dataset (accessible or all)
    accountsAccessible: [],     // ownership OR groups — the privacy default
    accountsAll: [],            // admins only — empty island for everyone else
    webmailUrl: '',

    folders: [],
    folderStatus: '',
    fullStatus: '',

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

      // Account names for result badges + scope options (data islands) and
      // webmail link target. The all-accounts island only exists for admins.
      this.accountsAccessible = this._parseIsland('ws-accounts-data');
      this.accountsAll = this._parseIsland('ws-accounts-all-data');
      this.accounts = this.accountsAccessible;
      this.webmailUrl = (this.$el && this.$el.dataset.webmailUrl) || '';
      // Pre-pick a push-override destination so the select isn't empty when
      // the user switches the destination radio to "override".
      if (this.accounts.length) this.pushOverrideId = this.accounts[0].id;

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

      this.fetchSnapshotDates();
      this.refreshStaging();
    },

    _parseIsland(id) {
      const el = document.getElementById(id);
      if (!el) return [];
      try {
        return JSON.parse(el.textContent) || [];
      } catch (e) {
        return [];
      }
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
      // Seq guard: a slow snapshot-dates fetch for mailbox A must not repaint
      // A's dots after the scope already moved on (e.g. to "All mailboxes").
      const seq = ++this._datesSeq;
      // Calendar dots follow the search scope: a single mailbox shows its
      // snapshot days; "All mailboxes" shows none (a merged dot-strip across
      // accounts would be misleading).
      if (!this.scopeAccountId) {
        this.snapshotDates = [];
      } else {
        let dates = [];
        try {
          const resp = await fetch('/api/restore/workspace/snapshot-dates', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
              account_id: this.scopeAccountId,
              include_all: this.includeAll,
            }),
          });
          const body = resp.ok ? await resp.json() : {};
          dates = body.dates || [];
        } catch (e) {
          dates = [];
        }
        if (seq !== this._datesSeq) return;
        this.snapshotDates = dates;
      }
      // Re-render flatpickr to re-run onDayCreate with the fresh data set
      // (also clears stale dots when the scope changes or the fetch fails) —
      // winning call only.
      if (seq !== this._datesSeq) return;
      if (this._fp) this._fp.redraw();
    },

    refreshIcons() {
      // Re-init Lucide after Alpine renders dynamic content
      this.$nextTick(() => {
        if (window.lucide) window.lucide.createIcons();
      });
    },

    // === Result/preview helpers ===
    accountName(id) {
      // Both datasets: a result badge must resolve even after the admin
      // toggle swapped the active scope list.
      const a = this.accountsAccessible.find(x => x.id === id)
        || this.accountsAll.find(x => x.id === id);
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
      // Seq guard: rapid row clicks race their fetches — only the latest
      // request may write state, stale responses are dropped.
      const seq = ++this._previewSeq;
      this.previewRef = r;
      this.previewOpen = true;
      this.previewLoading = true;
      try {
        const url = `/api/restore/preview/${r.account_id}/${r.message_id_hash}`
          + (this.includeAll ? '?include_all=true' : '');
        const resp = await fetch(url);
        const data = resp.ok ? await resp.json() : null;
        if (seq !== this._previewSeq) return;
        this.preview = data;
      } catch (e) {
        if (seq !== this._previewSeq) return;
        this.preview = null;
      } finally {
        if (seq === this._previewSeq) {
          this.previewLoading = false;
          this.refreshIcons();
        }
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
      this.partial = false;
      this.preview = null;
      this.previewRef = null;
      this.previewOpen = false;
      this.refreshIcons();
      if (id === 'folder') this.loadFolders();
    },

    onAccountChange() {
      // Source mailbox for the folder/full presets — search results live in
      // the single-mail preset and follow scopeAccountId instead.
      if (this.preset === 'folder') this.loadFolders();
    },

    onScopeChange() {
      // New scope invalidates the current result set and the calendar dots.
      this.results = [];
      this.selected = [];
      this.searched = false;
      this.statusText = '';
      this.partial = false;
      this.preview = null;
      this.previewRef = null;
      this.previewOpen = false;
      this.fetchSnapshotDates();
    },

    onIncludeAllChange() {
      // Swapping the dataset re-renders the scope options; everything tied
      // to the old dataset (scope, results, selection, preview, dots) resets.
      this.accounts = this.includeAll ? this.accountsAll : this.accountsAccessible;
      this.scopeAccountId = '';
      // The push-override select renders from the active dataset too — keep
      // its pick valid (toggling OFF can drop a foreign account; the API
      // would 404 on it anyway).
      if (!this.accounts.some(a => a.id === this.pushOverrideId)) {
        this.pushOverrideId = this.accounts.length ? this.accounts[0].id : '';
      }
      this.onScopeChange();
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
      this.previewRef = null;
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
            include_all: this.includeAll,
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
        // drops snapshot-only rows (messages no longer in live mail). With
        // BOTH unchecked the two filters compose to the intersection: rows
        // that are in live mail AND in at least one snapshot.
        if (!this.includeLive) {
          results = results.filter(r => (r.snapshots || []).length > 0);
        }
        this.results = results;
        this.partial = !!body.partial;
        // Honest counts: show what's visible; body.total can be larger after
        // the includeLive client filter and/or page_size truncation.
        const shown = results.length;
        this.statusText = `${shown} result${shown === 1 ? '' : 's'}`
          + (body.total > shown ? ` of ${body.total}` : '');
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
        let failure = '';
        const startedAccounts = new Set();  // accounts whose restore job started
        const missingKeys = new Set();      // keys not in live mail — unrestorable here
        for (const [accountId, messageIds] of Object.entries(byAccount)) {
          const res = await fetch('/api/restore/resolve-uids', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
              account_id: accountId,
              message_ids: messageIds,
              include_all: this.includeAll,
            }),
          });
          if (!res.ok) {
            failure = `resolve failed for ${this.accountName(accountId)}: ${res.status}`;
            break;
          }
          const {resolved, missing} = await res.json();
          skippedTotal += missing.length;
          for (const mid of missing) missingKeys.add(accountId + ':' + mid);
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
          if (r.ok) {
            jobs.push((await r.json()).job_id);
            startedAccounts.add(accountId);
          } else {
            failure = `failed for ${this.accountName(accountId)}: ${r.status}`;
            break;
          }
        }
        // A mid-loop failure must not hide jobs that DID start before it.
        const bits = [];
        if (jobs.length) bits.push(`Started ${jobs.length} restore job${jobs.length === 1 ? '' : 's'} (to origin)`);
        if (skippedTotal) bits.push(`${skippedTotal} message${skippedTotal === 1 ? '' : 's'} not in live mail — skipped (snapshot-only restore arrives with the staging area)`);
        if (failure) bits.push(failure);
        this.statusText = bits.join(' · ') || 'Nothing to restore.';
        if (!failure) {
          this.selected = [];
        } else {
          // Safe retry: keep only keys a retry could still restore — drop
          // accounts whose job already started (retrying would duplicate the
          // restore) and messages not in live mail (this path can't restore
          // them; they're reported in the status above).
          this.selected = this.selected.filter(key => {
            if (missingKeys.has(key)) return false;
            const row = byKey[key];
            return !(row && startedAccounts.has(row.account_id));
          });
        }
      } catch (e) {
        this.statusText = `Restore error: ${e.message}`;
      } finally {
        this.restoring = false;
        this.refreshIcons();
      }
    },

    // === Staging area ===
    async refreshStaging() {
      // No seq guard: refreshes are idempotent reads — last write wins.
      try {
        const resp = await fetch('/api/restore/staging');
        if (resp.ok) this.staging = await resp.json();
      } catch (e) { /* keep last known state */ }
      finally {
        // The status slot lives in the bar: when the area disappears the bar
        // hides, and a stale message must not resurface with the next one.
        if (!this.staging.exists) this.stagingStatus = '';
        this.refreshIcons();
      }
    },
    fmtExpiry(iso) {
      if (!iso) return '—';
      const ms = new Date(iso) - new Date();
      if (ms <= 0) return 'expired';
      const days = Math.floor(ms / 86400000);
      if (days >= 1) return 'in ' + days + 'd';
      const hours = Math.floor(ms / 3600000);
      if (hours >= 1) return 'in ' + hours + 'h';
      return 'in ' + Math.max(1, Math.floor(ms / 60000)) + 'm';
    },
    _stagingFeedback(msg) {
      // Staging messages go to the bar — single slot, no duplication. But a
      // rejected FIRST add creates no area server-side (verified: the service
      // quota-checks before creating anything), so there is no bar to host
      // the message — fall back to statusText, which IS rendered in the
      // single-mail preset where add-to-staging lives.
      if (this.staging.exists) this.stagingStatus = msg;
      else this.statusText = msg;
    },
    async addToStaging(results) {
      const items = results
        .filter(Boolean)
        .map(r => ({account_id: r.account_id, message_id_hash: r.message_id_hash}));
      if (!items.length) return;
      try {
        const resp = await fetch('/api/restore/staging/items', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({items, include_all: this.includeAll}),
        });
        if (resp.status === 413) {
          // Quota refusal — the detail is the user-facing message.
          this._stagingFeedback((await resp.json()).detail);
          return;
        }
        if (!resp.ok) {
          this._stagingFeedback(`Add to staging failed: ${resp.status}`);
          return;
        }
        const r = await resp.json();
        const bits = [`${r.staged} staged`];
        if (r.skipped) bits.push(`${r.skipped} already there`);
        if (r.failed) bits.push(`${r.failed} failed`);
        // Refresh FIRST: a successful first add just created the area, so the
        // bar is up before the message lands in its status slot.
        await this.refreshStaging();
        this.stagingStatus = bits.join(' · ');
      } catch (e) {
        this._stagingFeedback(`Add to staging error: ${e.message}`);
      } finally {
        this.refreshIcons();
      }
    },
    addSelectedToStaging() {
      const byKey = Object.fromEntries(this.results.map(r => [this.selKey(r), r]));
      return this.addToStaging(this.selected.map(k => byKey[k]));
    },
    async emptyStaging() {
      if (!confirm('Empty the staging area? Staged copies are removed (originals are untouched).')) return;
      try {
        const resp = await fetch('/api/restore/staging', {method: 'DELETE'});
        if (resp.ok) {
          // The refresh below flips exists=false and clears this again —
          // the disappearing bar IS the success feedback.
          this.stagingStatus = 'Staging emptied';
          this.pushPanelOpen = false;
        } else {
          this.stagingStatus = `Empty failed: ${resp.status}`;
        }
        await this.refreshStaging();
      } catch (e) {
        this.stagingStatus = `Empty failed: ${e.message}`;
      } finally {
        this.refreshIcons();
      }
    },
    async pushStaging() {
      if (this.pushing) return;  // overlapping pushes would double-submit
      this.pushing = true;
      try {
        const dest = this.pushDestination === 'origin' ? 'origin' : this.pushOverrideId;
        if (!dest) {
          this.stagingStatus = 'Pick a destination mailbox';
          return;
        }
        const resp = await fetch('/api/restore/staging/push', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({destination: dest, folder_mode: this.pushFolderMode}),
        });
        if (!resp.ok) {
          this.stagingStatus = `Push failed: ${resp.status}`;
          return;
        }
        const r = await resp.json();
        const bits = [];
        if (r.job_ids.length) {
          bits.push(`Started ${r.job_ids.length} push job${r.job_ids.length === 1 ? '' : 's'}`);
        }
        if (r.skipped_targets.length) {
          bits.push(`${r.skipped_targets.length} target${r.skipped_targets.length === 1 ? '' : 's'} busy — those messages stay staged`);
        }
        this.pushPanelOpen = false;
        await this.refreshStaging();
        this.stagingStatus = bits.join(' · ') || 'Nothing to push';
        if (r.job_ids.length) this._schedulePostPushRefresh();
      } catch (e) {
        this.stagingStatus = `Push error: ${e.message}`;
      } finally {
        this.pushing = false;
        this.refreshIcons();
      }
    },
    _schedulePostPushRefresh() {
      // The bar must reflect job completion (pushed rows leave staging) but
      // continuous polling is overkill — a few delayed refreshes instead.
      for (const t of this._stagingTimers) clearTimeout(t);
      this._stagingTimers = [5000, 15000, 30000].map(
        ms => setTimeout(() => this.refreshStaging(), ms),
      );
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
