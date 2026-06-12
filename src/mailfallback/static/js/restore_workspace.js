// Attachment type-filter groups — chip id → extensions (lowercase, no dot,
// the index's documented `ext` contract). No "Other" chip: the search API
// only supports ext IN-lists, and a chip that can't filter would lie
// (copy-must-match-behavior).
const ATT_EXT_GROUPS = {
  pdf: ['pdf'],
  doc: ['doc', 'docx', 'odt', 'rtf', 'txt'],
  sheet: ['xls', 'xlsx', 'ods', 'csv'],
  image: ['jpg', 'jpeg', 'png', 'gif', 'webp', 'heic', 'svg'],
  archive: ['zip', 'rar', '7z', 'tar', 'gz'],
};
const ATT_MIN_SIZE_BYTES = 1048576;  // the "> 1 MB" size chip

// Shared confirm() reassurance — every restore entry point leads with an
// op-specific line and closes with this EXACT copy (frozen contract, see
// test_workspace_js_restore_confirms_share_reassurance). The staging Empty
// confirm intentionally does NOT use it: that one deletes staged copies.
const RESTORE_REASSURANCE = 'Restores never delete anything: existing messages are kept and duplicates are skipped.';

function restoreWorkspace() {
  return {
    // === State ===
    presets: [
      {id: 'single-mail', label: 'A single mail', icon: 'mail'},
      {id: 'attachment', label: 'An attachment', icon: 'paperclip'},
      {id: 'folder', label: 'A folder / subset', icon: 'folder'},
      {id: 'full', label: 'The whole mailbox', icon: 'alert-triangle'},
    ],
    preset: 'single-mail',

    // Inputs
    accountId: '',
    // Time chips — exactly one active; All time is the DEFAULT (no hidden
    // 7-day trap). 'custom' is only ever set by Apply in the popover.
    timePreset: 'all',          // '7d'|'30d'|'90d'|'1y'|'all'|'custom'
    timeChips: [
      {id: '7d', label: '7d'},
      {id: '30d', label: '30d'},
      {id: '90d', label: '90d'},
      {id: '1y', label: '1y'},
      {id: 'all', label: 'All time'},
    ],
    customLabel: '',            // compact chip label ("4–12 Jun") once applied
    customPopoverOpen: false,
    rangeStart: null,           // Date object — flatpickr's PENDING custom pick
    rangeEnd: null,             // Date object — pending too (see customStart)
    customStart: null,          // COMMITTED pair — Apply copies the pending
    customEnd: null,            // pick here; searches/label only ever read these
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
    selectedFolders: [],        // folder-preset multi-select (full IMAP names)

    // Unified destination panel — folder + full presets (one shared state
    // set: only one panel is visible at a time, and both answer "where do
    // restored messages go", like the sidebar select they replaced).
    restDestMode: 'back',       // 'back' (into the source mailbox) | 'other'
    restDestOtherId: '',
    restFolderMode: 'original', // 'original' | 'restored' | 'custom'
    restCustomFolder: '',
    restPickedFolder: '',       // the picker's own selection — KEPT visible after a pick
    restFolderPulse: false,     // input highlight while a pick lands
    _restPickerLastId: '',      // picker account tracking — a dest change resets the pick
    _destFolderCache: {},       // account id -> mailboxes list (lazy, per panel pickers)
    _pulseTimers: {},           // per-panel pulse timeouts (restart-safe)

    // Async/UI state
    searching: false,
    searched: false,
    _msgSeq: 0,                 // monotonic seq — stale search responses are dropped
    results: [],
    selected: [],               // selKey(r) strings — unique across accounts
    statusText: '',
    restoring: false,

    // Attachment preset state
    attGroupDefs: [
      {id: 'pdf', label: 'PDF'},
      {id: 'doc', label: 'Documents'},
      {id: 'sheet', label: 'Sheets'},
      {id: 'image', label: 'Images'},
      {id: 'archive', label: 'Archives'},
    ],
    attGroups: [],              // selected type-group ids (multi-select)
    attMinSize: null,           // null | ATT_MIN_SIZE_BYTES
    attIncludeContent: false,   // default ON when Tika is on (init reads the data attr)
    attResults: [],
    attTotal: 0,
    attSearching: false,
    attSearched: false,
    _attSeq: 0,                 // monotonic seq — stale search responses are dropped

    // Preview pane state
    preview: null,
    previewRef: null,           // the result row behind the open preview — staging adds need its ids
    previewOpen: false,
    previewLoading: false,
    _previewSeq: 0,             // monotonic seq — stale preview responses are dropped

    // Staging bar / push panel state
    staging: {exists: false, count: 0, bytes_used: 0, expires_at: null, max_bytes: 0},
    stagingStatus: '',          // staging feedback — rendered in the bar (statusText
                                // only renders inside the two search presets)
    pushPanelOpen: false,
    pushDestination: 'origin',  // 'origin' | 'override' (the API gets an account id)
    pushOverrideId: '',
    pushFolderMode: 'original', // 'original' | 'restored' | 'custom'
    pushCustomFolder: '',
    pushPickedFolder: '',       // picker selection (kept after pick, see rest*)
    pushFolderPulse: false,
    _pushPickerLastId: '',
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
    // The shared search row's submit button spinner — each search preset
    // owns its busy flag.
    get anySearching() {
      return this.preset === 'attachment' ? this.attSearching : this.searching;
    },
    // The open preview came from an attachment hit (vs a message row) —
    // drives the pane's selected-attachment line, the Download action and
    // the webmail link swap.
    get previewIsAttachment() {
      return !!(this.previewRef && this.previewRef.part_index !== undefined);
    },
    // Destination pickers: the single account whose existing folders the
    // custom-folder picker lists. A push to origin targets MANY accounts,
    // so its picker only renders in override mode (template x-show).
    get restPickerAccountId() {
      return this.restDestMode === 'other' ? this.restDestOtherId : this.accountId;
    },
    get pushPickerAccountId() {
      return this.pushDestination === 'override' ? this.pushOverrideId : '';
    },
    get restPickerFolders() {
      return this._destFolderCache[this.restPickerAccountId] || [];
    },
    get pushPickerFolders() {
      return this._destFolderCache[this.pushPickerAccountId] || [];
    },
    // === Time range ===
    // One source of truth for what BOTH searches send: the active chip.
    // 'all' → null/null (no date filter); fixed chips → now-N days with an
    // open end; 'custom' → the applied flatpickr pair as whole days.
    currentRange() {
      if (this.timePreset === 'custom' && this.customStart) {
        // COMMITTED pair only — a pick left un-applied in the popover must
        // never silently change what a re-search sends (the chip label and
        // the query stay in lockstep).
        const s = new Date(this.customStart);
        s.setHours(0, 0, 0, 0);
        const e = new Date(this.customEnd || this.customStart);
        e.setHours(23, 59, 59, 999);
        return {start: s.toISOString(), end: e.toISOString()};
      }
      const days = {'7d': 7, '30d': 30, '90d': 90, '1y': 365}[this.timePreset];
      if (!days) return {start: null, end: null};  // 'all' (or empty custom)
      const s = new Date();
      s.setDate(s.getDate() - days);
      s.setHours(0, 0, 0, 0);
      return {start: s.toISOString(), end: null};
    },
    setTimePreset(id) {
      this.customPopoverOpen = false;
      if (this.timePreset === id) return;
      if (this.timePreset === 'custom') {
        // Leaving custom: drop the applied pick so the chip reverts to
        // "Custom…" and a later re-open starts clean.
        this._resetCustom();
      }
      this.timePreset = id;
      this._requeryActive();
    },
    applyCustomRange() {
      if (!this.rangeStart) return;  // nothing picked yet — Apply is a no-op
      this.customStart = this.rangeStart;
      this.customEnd = this.rangeEnd || this.rangeStart;
      this.customLabel = this._fmtRangeLabel(this.customStart, this.customEnd);
      this.timePreset = 'custom';
      this.customPopoverOpen = false;
      this._requeryActive();
    },
    clearCustomRange() {
      const wasCustom = this.timePreset === 'custom';
      this._resetCustom();
      this.customPopoverOpen = false;
      if (wasCustom) {
        this.timePreset = 'all';  // Clear reverts to All time
        this._requeryActive();
      }
    },
    _resetCustom() {
      this.customLabel = '';
      this.customStart = null;
      this.customEnd = null;
      this.rangeStart = null;
      this.rangeEnd = null;
      if (this._fp) this._fp.clear(false);
    },
    _fmtRangeLabel(start, end) {
      // Compact chip label: "12 Jun" / "4–12 Jun" / "28 May – 3 Jun";
      // cross-year ranges spell the years out.
      const M = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
      const s = new Date(start);
      const e = new Date(end);
      const sm = M[s.getMonth()];
      const em = M[e.getMonth()];
      if (s.getFullYear() !== e.getFullYear()) {
        return `${s.getDate()} ${sm} ${s.getFullYear()} – ${e.getDate()} ${em} ${e.getFullYear()}`;
      }
      if (s.getMonth() !== e.getMonth()) return `${s.getDate()} ${sm} – ${e.getDate()} ${em}`;
      if (s.getDate() === e.getDate()) return `${s.getDate()} ${sm}`;
      return `${s.getDate()}–${e.getDate()} ${sm}`;
    },
    _requeryActive() {
      // Chip and source-toggle switches must never leave visible results
      // stale relative to the controls — same re-query pattern as the
      // attachment filter chips (the _msgSeq/_attSeq guards drop racing
      // responses); a no-op until a search ran.
      if (this.preset === 'attachment') {
        if (this.attSearched) this.runAttachmentSearch();
      } else if (this.searched) {
        this.runSearch();
      }
    },

    // === Lifecycle ===
    init() {
      // Pick first mailbox. The "Another mailbox" select lives inside x-if
      // templates (not in the DOM yet), but it renders the SAME Jinja option
      // list as the sidebar Mailbox select — seed it from there so the
      // select never displays an option its model doesn't hold.
      const sel = document.querySelector('[x-model="accountId"]');
      if (sel && sel.options.length > 0) {
        this.accountId = sel.options[0].value;
        this.restDestOtherId = sel.options[0].value;
      }

      // Account names for result badges + scope options (data islands) and
      // webmail link target. The all-accounts island only exists for admins.
      this.accountsAccessible = this._parseIsland('ws-accounts-data');
      this.accountsAll = this._parseIsland('ws-accounts-all-data');
      this.accounts = this.accountsAccessible;
      this.webmailUrl = (this.$el && this.$el.dataset.webmailUrl) || '';
      // Content search defaults ON exactly when its toggle exists (the
      // template gates both on the same tika_enabled flag).
      this.attIncludeContent = !!(this.$el && this.$el.dataset.tikaEnabled);
      // Pre-pick a push-override destination so the select isn't empty when
      // the user switches the destination radio to "override".
      if (this.accounts.length) this.pushOverrideId = this.accounts[0].id;

      this._initCalendar();
      this._initStagingHeightVar();
      this.refreshIcons();

      // No default range: timePreset starts at 'all' — searches run unfiltered
      // until the user narrows them (the old silent 7-day default hid hits).
      this.fetchSnapshotDates();
      this.refreshStaging();
    },

    _initStagingHeightVar() {
      // ONE source of truth for every bottom offset that must clear the
      // docked staging bar (sticky action bar, mobile preview sheet, push
      // panel, content padding): the bar WRAPS at narrow widths
      // (flex-wrap ≤768px) so any fixed 4.5rem-style constant lies exactly
      // when the bar grows past one row — it covered the selection bar.
      const bar = document.querySelector('.ws-staging-bar');
      const root = document.documentElement;
      if (!bar) {
        root.style.setProperty('--ws-staging-h', '0px');
        return;
      }
      const update = () => {
        // offsetHeight is 0 while x-show keeps the bar display:none.
        root.style.setProperty('--ws-staging-h', Math.ceil(bar.offsetHeight) + 'px');
      };
      // Observe once at init — the element is always in the DOM (x-show
      // only toggles display) and the observer tracks wrap/resize growth.
      if (window.ResizeObserver) {
        new ResizeObserver(update).observe(bar);
      }
      // Not every engine fires RO across display:none flips (and the bar's
      // x-transition delays display:none past $nextTick on leave) — mirror
      // the exists flag explicitly: 0px the moment the bar starts leaving,
      // re-measure once it is shown again.
      this.$watch('staging.exists', exists => {
        if (!exists) {
          root.style.setProperty('--ws-staging-h', '0px');
        } else {
          this.$nextTick(update);
        }
      });
      update();
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
          // PENDING custom selection only — Apply commits it. One date counts
          // as a single-day range until the second click widens it.
          if (selectedDates.length >= 1) {
            self.rangeStart = selectedDates[0];
            self.rangeEnd = selectedDates.length === 2 ? selectedDates[1] : selectedDates[0];
          }
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
    // Selection is keyed at MESSAGE level for BOTH search presets — message
    // rows and attachment rows each carry account_id + message_id_hash, so
    // one shared `selected` array (and one selection bar) serves both.
    // Sibling attachments of one message share the key on purpose: staging
    // and restore operate on whole messages, never half of one.
    selKey(r) { return r.account_id + ':' + r.message_id_hash; },
    _activeRows() {
      return this.preset === 'attachment' ? this.attResults : this.results;
    },
    _selectionByKey() {
      // key → message ref of the active preset's rows. Object.fromEntries
      // dedupes sibling attachment rows for free (same message-level key,
      // same underlying message).
      return Object.fromEntries(this._activeRows().map(r => [this.selKey(r), r]));
    },
    get selectableCount() {
      // What "select all" would select — MESSAGES, not rows.
      return new Set(this._activeRows().map(r => this.selKey(r))).size;
    },
    isPreviewing(r) {
      // The row behind the open preview pane (message-level marker).
      return this.previewOpen && !!this.previewRef
        && this.selKey(this.previewRef) === this.selKey(r);
    },
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
    closePreview() {
      // Bump the seq so an in-flight openPreview fetch can't reopen the pane
      // (its guarded writes and finally all no-op on a stale seq).
      this._previewSeq++;
      this.previewOpen = false;
      this.preview = null;
      this.previewRef = null;
      this.previewLoading = false;
    },

    // === Actions ===
    applyPreset(id) {
      // The time chips are SHARED state between the two search presets —
      // switching preset keeps the active range, it only closes the popover.
      this.preset = id;
      this.customPopoverOpen = false;
      this._clearMsgState();
      this._clearAttState();
      this.statusText = '';
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
      // "Back into <source>" destination follows the source: refresh the
      // custom-folder picker if it is open on the source account.
      this.ensureRestFolders();
    },

    // === Unified destination panel (folder + full presets, push panel) ===
    todayStamp() {
      // UTC — matches the server-side Restored/<date> stamp on staging pushes.
      return new Date().toISOString().slice(0, 10);
    },
    async ensureDestFolders(accountId) {
      // Lazy per-account cache for the "pick from existing folders" selects.
      if (!accountId || this._destFolderCache[accountId]) return;
      try {
        const resp = await fetch(`/api/accounts/${accountId}/mailboxes`);
        if (resp.ok) this._destFolderCache[accountId] = await resp.json();
      } catch (e) { /* picker stays empty — the text input still works */ }
    },
    ensureRestFolders() {
      if (this.restFolderMode !== 'custom') return;
      // A destination change swaps the folder list — a kept pick from the
      // old account would leave the select displaying nothing (or a folder
      // the new destination doesn't have). The typed input stays untouched.
      if (this._restPickerLastId !== this.restPickerAccountId) {
        this._restPickerLastId = this.restPickerAccountId;
        this.restPickedFolder = '';
      }
      this.ensureDestFolders(this.restPickerAccountId);
    },
    ensurePushFolders() {
      if (this.pushFolderMode !== 'custom') return;
      if (this._pushPickerLastId !== this.pushPickerAccountId) {
        this._pushPickerLastId = this.pushPickerAccountId;
        this.pushPickedFolder = '';
      }
      this.ensureDestFolders(this.pushPickerAccountId);
    },
    pickRestFolder() {
      // The picker KEEPS its selection (x-model) — picking copies into the
      // text input, which stays the source of truth (freely editable); the
      // pulse is the visible confirmation that the copy landed.
      if (!this.restPickedFolder) return;
      this.restCustomFolder = this.restPickedFolder;
      this._pulseInput('rest');
    },
    pickPushFolder() {
      if (!this.pushPickedFolder) return;
      this.pushCustomFolder = this.pushPickedFolder;
      this._pulseInput('push');
    },
    _pulseInput(which) {
      // Restart-safe: drop the class, re-add next tick (a re-pick re-fires
      // the CSS highlight), auto-remove after ~600ms.
      const flag = which + 'FolderPulse';
      this[flag] = false;
      clearTimeout(this._pulseTimers[which]);
      this.$nextTick(() => {
        this[flag] = true;
        this._pulseTimers[which] = setTimeout(() => { this[flag] = false; }, 600);
      });
    },
    selectAllFolders(checked) {
      this.selectedFolders = checked ? this.folders.map(f => f.full_name || f.name) : [];
    },
    _restTargetId() {
      return this.restDestMode === 'other' ? this.restDestOtherId : this.accountId;
    },
    _restFolderMapping() {
      // Anything but "original" is a destination root the worker nests
      // everything under — including our dated Restored/ and typed paths.
      if (this.restFolderMode === 'restored') return 'Restored/' + this.todayStamp();
      if (this.restFolderMode === 'custom') return this.restCustomFolder.trim();
      return 'original';
    },
    _restDestPhrase(target) {
      return target === this.accountId
        ? 'back into the same mailbox'
        : `into "${this.accountName(target)}"`;
    },

    onScopeChange() {
      // New scope invalidates the current result set and the calendar dots.
      this._clearMsgState();
      this._clearAttState();
      this.statusText = '';
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

    submitSearch() {
      // The shared search row serves two presets — route by the active one.
      if (this.preset === 'attachment') return this.runAttachmentSearch();
      return this.runSearch();
    },

    _clearMsgState() {
      this._msgSeq++;  // drop in-flight responses
      this.results = [];
      this.selected = [];
      this.searching = false;
      this.searched = false;
      this.partial = false;
    },

    async runSearch() {
      if (!this.query.trim()) {
        // Nothing to ask for — also clears stale results when a chip or
        // source toggle re-queries after the query box was emptied (the
        // attachment guard's mirror).
        this._clearMsgState();
        this.statusText = '';
        return;
      }
      // Seq guard: a slow (deep) search left in flight must not bleed its
      // late response into the shared statusText or repopulate results that
      // a preset/scope switch already cleared — mirror the _attSeq pattern.
      const seq = ++this._msgSeq;
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
        const range = this.currentRange();
        const resp = await fetch('/api/restore/search', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({
            query: this.query,
            account_ids: this.scopeAccountId ? [this.scopeAccountId] : null,
            range_start: range.start,
            range_end: range.end,
            include_deleted: this.includeSnapshots,
            deep: this.deepSearch,
            include_all: this.includeAll,
            page: 1,
            page_size: 100,
          }),
        });
        if (!resp.ok) {
          if (seq === this._msgSeq) this.statusText = `Search failed: ${resp.status}`;
          return;
        }
        const body = await resp.json();
        if (seq !== this._msgSeq) return;
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
        if (seq === this._msgSeq) this.statusText = `Search error: ${e.message}`;
      } finally {
        if (seq === this._msgSeq) {
          this.searching = false;
          this.refreshIcons();
        }
      }
    },

    // === Attachment search ===
    _clearAttState() {
      this._attSeq++;  // drop in-flight responses
      this.attResults = [];
      this.attTotal = 0;
      this.attSearching = false;
      this.attSearched = false;
      // Shared selection: keys could reference rows no longer visible.
      this.selected = [];
    },
    toggleAttGroup(id) {
      this.attGroups = this.attGroups.includes(id)
        ? this.attGroups.filter(g => g !== id)
        : [...this.attGroups, id];
      this.onAttFilterChange();
    },
    toggleAttMinSize() {
      this.attMinSize = this.attMinSize === null ? ATT_MIN_SIZE_BYTES : null;
      this.onAttFilterChange();
    },
    onAttFilterChange() {
      // Live filters: visible results must never go stale relative to the
      // chips — re-run once a search happened (the seq guard handles races).
      if (this.attSearched) this.runAttachmentSearch();
    },
    attKey(a) {
      return a.account_id + ':' + a.message_id_hash + ':' + a.part_index;
    },
    attIsPreviewing(a) {
      // The exact attachment row behind the open preview pane (part-level:
      // the user clicked THIS row, not its same-message sibling).
      return this.previewIsAttachment && this.attKey(this.previewRef) === this.attKey(a);
    },
    attachmentDownloadUrl(accountId, hashHex, partIndex) {
      // Shared by the attachment table rows AND the preview pane's chips
      // (there the ids come from previewRef, part_index from the payload).
      return `/api/restore/attachments/${accountId}/${hashHex}/${partIndex}/download`
        + (this.includeAll ? '?include_all=true' : '');
    },
    attDownloadUrl(a) {
      return this.attachmentDownloadUrl(a.account_id, a.message_id_hash, a.part_index);
    },
    attIcon(ext) {
      const e = (ext || '').toLowerCase();
      if (ATT_EXT_GROUPS.pdf.includes(e) || ATT_EXT_GROUPS.doc.includes(e)) return 'file-text';
      if (ATT_EXT_GROUPS.sheet.includes(e)) return 'file-spreadsheet';
      if (ATT_EXT_GROUPS.image.includes(e)) return 'image';
      if (ATT_EXT_GROUPS.archive.includes(e)) return 'archive';
      return 'file';  // unknown/other types — the generic document glyph
    },
    _attNameSplit(a) {
      // "report.pdf" → ["report", ".pdf"] — the extension renders accented.
      const name = a.filename || '(unnamed)';
      const ext = (a.ext || '').toLowerCase();
      if (ext && name.toLowerCase().endsWith('.' + ext)) {
        const cut = name.length - ext.length - 1;
        return [name.slice(0, cut), name.slice(cut)];
      }
      return [name, ''];
    },
    attNameBase(a) { return this._attNameSplit(a)[0]; },
    attNameExt(a) { return this._attNameSplit(a)[1]; },
    attMeta(a) {
      return [a.from_addr || '?', a.folder_path || ''].filter(Boolean).join(' · ');
    },
    attSnippetParts(snippet) {
      // XSS contract: ts_headline output is HOSTILE text extracted from mail
      // attachments, carrying [[[/]]] match markers. Split it into segments
      // the template renders as TEXT nodes (x-text) — never x-html.
      if (!snippet) return [];
      const segs = [];
      let rest = snippet;
      while (rest) {
        const open = rest.indexOf('[[[');
        if (open === -1) {
          segs.push({mark: false, text: rest});
          break;
        }
        if (open > 0) segs.push({mark: false, text: rest.slice(0, open)});
        rest = rest.slice(open + 3);
        const close = rest.indexOf(']]]');
        if (close === -1) {
          // Unbalanced marker — render the remainder unhighlighted.
          segs.push({mark: false, text: rest});
          break;
        }
        segs.push({mark: true, text: rest.slice(0, close)});
        rest = rest.slice(close + 3);
      }
      return segs.filter(s => s.text);
    },
    async runAttachmentSearch() {
      const hasFilters = this.attGroups.length > 0 || this.attMinSize !== null;
      if (!this.query.trim() && !hasFilters) {
        // Nothing to ask for — also clears stale results when the last
        // filter is toggled off with an empty query.
        this._clearAttState();
        this.statusText = '';
        return;
      }
      const seq = ++this._attSeq;
      this.attSearching = true;
      this.attSearched = true;
      this.statusText = 'Searching…';
      this.attResults = [];
      this.attTotal = 0;
      this.selected = [];  // new result set — same clearing as runSearch
      this.preview = null;
      this.previewRef = null;
      this.previewOpen = false;
      this.refreshIcons();
      try {
        const exts = this.attGroups.length
          ? this.attGroups.flatMap(g => ATT_EXT_GROUPS[g] || [])
          : null;
        const range = this.currentRange();
        const resp = await fetch('/api/restore/attachments/search', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({
            query: this.query,
            account_ids: this.scopeAccountId ? [this.scopeAccountId] : null,
            include_all: this.includeAll,
            exts,
            min_size: this.attMinSize,
            include_content: this.attIncludeContent,
            range_start: range.start,
            range_end: range.end,
            page: 1,
            page_size: 100,
          }),
        });
        if (!resp.ok) {
          if (seq === this._attSeq) this.statusText = `Search failed: ${resp.status}`;
          return;
        }
        const body = await resp.json();
        if (seq !== this._attSeq) return;
        this.attResults = body.results || [];
        this.attTotal = body.total || 0;
        // Honest counts: what's visible vs what matched (page_size cap).
        const shown = this.attResults.length;
        this.statusText = `${shown} attachment${shown === 1 ? '' : 's'}`
          + (this.attTotal > shown ? ` of ${this.attTotal}` : '');
      } catch (e) {
        if (seq === this._attSeq) this.statusText = `Search error: ${e.message}`;
      } finally {
        if (seq === this._attSeq) {
          this.attSearching = false;
          this.refreshIcons();
        }
      }
    },

    toggleSelectAll(checked) {
      this.selected = checked ? Object.keys(this._selectionByKey()) : [];
    },

    async restoreSelected() {
      // Restore-to-origin: group selected messages per account, resolve their
      // Message-Ids to live IMAP UIDs, then submit one selection-mode restore
      // job per account with source == target.
      if (this.selected.length === 0) return;
      const n = this.selected.length;
      const where = n === 1 ? 'its origin mailbox' : 'their origin mailboxes';
      if (!confirm(`Restore ${n} selected message${n === 1 ? '' : 's'} to ${where}?\n\n${RESTORE_REASSURANCE}`)) return;
      this.restoring = true;
      this.refreshIcons();
      try {
        // Preset-agnostic: rows from the active preset are message refs
        // (attachment hits carry message_id too — resolve-uids needs it).
        const byKey = this._selectionByKey();
        const byAccount = {};
        for (const key of this.selected) {
          const r = byKey[key];
          if (r) (byAccount[r.account_id] ||= []).push(r);
        }
        const jobs = [];
        let skippedTotal = 0;
        let failure = '';
        const startedAccounts = new Set();  // accounts whose restore job started
        const missingKeys = new Set();      // keys not in live mail — unrestorable here
        for (const [accountId, rows] of Object.entries(byAccount)) {
          const res = await fetch('/api/restore/resolve-uids', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
              account_id: accountId,
              message_ids: rows.map(r => r.message_id),
              include_all: this.includeAll,
            }),
          });
          if (!res.ok) {
            failure = `resolve failed for ${this.accountName(accountId)}: ${res.status}`;
            break;
          }
          const {resolved, missing} = await res.json();
          skippedTotal += missing.length;
          // Map missing Message-Ids back into selKey space (keys are
          // hash-based; the API reports raw Message-Ids).
          const missingSet = new Set(missing);
          for (const r of rows) {
            if (missingSet.has(r.message_id)) missingKeys.add(this.selKey(r));
          }
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
      // the message — fall back to statusText, which IS rendered in both
      // search presets (single-mail + attachment), everywhere an
      // add-to-staging entry point lives.
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
      // Preset-agnostic: the active rows map to message refs (account_id +
      // message_id_hash), which is all addToStaging sends.
      const byKey = this._selectionByKey();
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
      const dest = this.pushDestination === 'origin' ? 'origin' : this.pushOverrideId;
      if (!dest) {
        this.stagingStatus = 'Pick a destination mailbox';
        return;
      }
      const customFolder = this.pushFolderMode === 'custom' ? this.pushCustomFolder.trim() : null;
      if (this.pushFolderMode === 'custom' && !customFolder) {
        this.stagingStatus = 'Enter a destination folder';  // button disabled too — belt and braces
        return;
      }
      const n = this.staging.count;
      const where = dest === 'origin'
        ? (n === 1 ? 'its origin mailbox' : 'their origin mailboxes')
        : `"${this.accountName(dest)}"`;
      if (!confirm(`Push ${n} staged message${n === 1 ? '' : 's'} to ${where}?\n\n${RESTORE_REASSURANCE}`)) return;
      this.pushing = true;
      try {
        const resp = await fetch('/api/restore/staging/push', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({
            destination: dest,
            folder_mode: this.pushFolderMode,
            custom_folder: customFolder,
          }),
        });
        if (!resp.ok) {
          this.stagingStatus = `Push failed: ${await this._errDetail(resp)}`;
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
    async _errDetail(resp) {
      // Hygiene 400s carry an actionable detail ("folder_mapping has empty
      // or relative path segments") — surface it instead of a bare code.
      try {
        const body = await resp.json();
        if (body && body.detail) {
          return typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail);
        }
      } catch (e) { /* not JSON — fall through to the status code */ }
      return `${resp.status}`;
    },

    async loadFolders() {
      if (!this.accountId) return;
      this.folders = [];
      this.selectedFolders = [];  // stale picks must not survive a reload
      try {
        const resp = await fetch(`/api/accounts/${this.accountId}/mailboxes`);
        if (!resp.ok) return;
        this.folders = await resp.json();
        // The source list doubles as the destination picker cache for
        // restores back into the same mailbox.
        this._destFolderCache[this.accountId] = this.folders;
      } catch (e) {
        // ignore
      }
    },

    async restoreFolder() {
      if (!this.selectedFolders.length) return;
      const target = this._restTargetId();
      const mapping = this._restFolderMapping();
      if (!target || !mapping) return;  // custom mode with empty path — button disabled too
      const n = this.selectedFolders.length;
      const first = `Restore ${n} folder${n === 1 ? '' : 's'} from "${this.accountName(this.accountId)}" ${this._restDestPhrase(target)}?`;
      if (!confirm(`${first}\n\n${RESTORE_REASSURANCE}`)) return;
      this.restoring = true;
      this.refreshIcons();
      try {
        const resp = await fetch('/api/restore', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({
            source_account_id: this.accountId,
            target_account_id: target,
            restore_mode: 'folder',
            selected_folders: this.selectedFolders,
            folder_mapping: mapping,
          }),
        });
        if (resp.ok) {
          this.folderStatus = `Folder restore started — job ${(await resp.json()).job_id}`;
        } else {
          this.folderStatus = `Failed: ${await this._errDetail(resp)}`;
        }
      } finally {
        this.restoring = false;
        this.refreshIcons();
      }
    },

    async restoreFull() {
      const target = this._restTargetId();
      const mapping = this._restFolderMapping();
      if (!target || !mapping) return;  // custom mode with empty path — button disabled too
      const first = `Restore the whole mailbox "${this.accountName(this.accountId)}" ${this._restDestPhrase(target)}?`;
      if (!confirm(`${first}\n\n${RESTORE_REASSURANCE}`)) return;
      this.restoring = true;
      this.refreshIcons();
      try {
        const resp = await fetch('/api/restore', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({
            source_account_id: this.accountId,
            target_account_id: target,
            restore_mode: 'full',
            folder_mapping: mapping,
          }),
        });
        if (resp.ok) {
          this.fullStatus = `Full restore started — job ${(await resp.json()).job_id}`;
        } else {
          this.fullStatus = `Failed: ${await this._errDetail(resp)}`;
        }
      } finally {
        this.restoring = false;
        this.refreshIcons();
      }
    },
  };
}
