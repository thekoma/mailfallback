/* MFB — Restore page scripts (restore.html) */

function syncRestoreSelects(sourceSelect, targetSelect) {
    var srcVal = sourceSelect ? sourceSelect.value : '';
    var tgtVal = targetSelect ? targetSelect.value : '';
    if (targetSelect) {
        Array.from(targetSelect.options).forEach(function(opt) {
            opt.disabled = opt.value !== '' && opt.value === srcVal;
        });
        if (tgtVal && tgtVal === srcVal) targetSelect.value = '';
    }
    if (sourceSelect) {
        Array.from(sourceSelect.options).forEach(function(opt) {
            opt.disabled = opt.value !== '' && opt.value === tgtVal;
        });
    }
}

document.addEventListener('DOMContentLoaded', function() {
    if (!document.getElementById('restore-form')) return;

    var radios = document.querySelectorAll('[name="restore_mode"]');
    radios.forEach(function(r) {
        r.addEventListener('change', updateRestoreMode);
    });

    var sourceSelect = document.getElementById('source-account');
    var targetSelect = document.getElementById('target-account');
    if (sourceSelect) {
        sourceSelect.addEventListener('change', function() {
            updateSearchFolders();
            updateRestoreMode();
            syncRestoreSelects(sourceSelect, targetSelect);
        });
    }
    if (targetSelect) {
        targetSelect.addEventListener('change', function() {
            syncRestoreSelects(sourceSelect, targetSelect);
        });
    }
    updateRestoreMode();
});

function updateRestoreMode() {
    var mode = (document.querySelector('[name="restore_mode"]:checked') || {}).value || 'full';
    var folderPanel = document.getElementById('folder-panel');
    var searchPanel = document.getElementById('search-panel');

    if (mode === 'folder') {
        folderPanel.classList.remove('hidden');
        searchPanel.classList.add('hidden');
    } else if (mode === 'selection') {
        folderPanel.classList.add('hidden');
        searchPanel.classList.remove('hidden');
        updateSearchFolders();
    } else {
        folderPanel.classList.add('hidden');
        searchPanel.classList.add('hidden');
    }
}

function updateSearchFolders() {
    var sourceId = document.getElementById('source-account').value;
    var select = document.getElementById('search-folder');
    if (!sourceId || !select) return;

    fetch('/api/accounts/' + sourceId + '/mailboxes')
        .then(function(r) { return r.json(); })
        .then(function(folders) {
            select.innerHTML = '';
            folders.forEach(function(f) {
                var opt = document.createElement('option');
                opt.value = f.name;
                opt.textContent = f.name + ' (' + f.messages + ')';
                select.appendChild(opt);
            });
        })
        .catch(function() {
            select.innerHTML = '<option value="">Failed to load folders</option>';
        });
}

function updateSearchScope() {
    var scope = document.getElementById('search-scope').value;
    var folderSelect = document.getElementById('search-folder');
    folderSelect.disabled = scope === 'all';
}

function toggleSubFields(id) {
    document.getElementById(id).classList.toggle('hidden');
}

function toggleEntireMessage(cb) {
    var others = ['sf-subject', 'sf-sender', 'sf-recipient', 'sf-body'];
    others.forEach(function(id) {
        var el = document.getElementById(id);
        if (el) {
            el.disabled = cb.checked;
            if (cb.checked) el.checked = false;
        }
    });
    ['sf-from', 'sf-reply-to', 'sf-followup-to', 'sf-to', 'sf-cc', 'sf-bcc'].forEach(function(id) {
        var el = document.getElementById(id);
        if (el) {
            el.disabled = cb.checked;
            if (cb.checked) el.checked = false;
        }
    });
}

function _getSearchFields() {
    if (document.getElementById('sf-entire').checked) return 'text';

    var fields = [];
    if (document.getElementById('sf-subject').checked) fields.push('subject');
    if (document.getElementById('sf-sender').checked) {
        if (document.getElementById('sf-from').checked) fields.push('from');
        if (document.getElementById('sf-reply-to').checked) fields.push('reply_to');
        if (document.getElementById('sf-followup-to').checked) fields.push('followup_to');
        if (!document.getElementById('sf-from').checked &&
            !document.getElementById('sf-reply-to').checked &&
            !document.getElementById('sf-followup-to').checked) {
            fields.push('from');
        }
    }
    if (document.getElementById('sf-recipient').checked) {
        if (document.getElementById('sf-to').checked) fields.push('to');
        if (document.getElementById('sf-cc').checked) fields.push('cc');
        if (document.getElementById('sf-bcc').checked) fields.push('bcc');
        if (!document.getElementById('sf-to').checked &&
            !document.getElementById('sf-cc').checked &&
            !document.getElementById('sf-bcc').checked) {
            fields.push('to');
        }
    }
    if (document.getElementById('sf-body').checked) fields.push('body');
    return fields.length ? fields.join(',') : 'text';
}

function executeSearch() {
    var btn = document.getElementById('search-btn');
    var icon = document.getElementById('search-icon');
    var spinner = document.getElementById('search-spinner');
    var btnText = document.getElementById('search-btn-text');
    var query = document.getElementById('search-query').value;
    if (!query) return;

    btn.disabled = true;
    icon.classList.add('hidden');
    spinner.classList.remove('hidden');
    btnText.textContent = 'Searching…';

    var scope = document.getElementById('search-scope').value;
    var folder = scope === 'all' ? '*' : document.getElementById('search-folder').value;

    var params = new URLSearchParams({
        source_account_id: document.getElementById('source-account').value,
        search_folder: folder,
        search_query: query,
        search_in: _getSearchFields(),
        type_filter: document.getElementById('search-type').value,
        date_since: document.getElementById('search-since').value,
        date_before: document.getElementById('search-before').value,
    });

    htmx.ajax('GET', '/restore/partials/messages?' + params.toString(), {
        target: '#message-panel',
        swap: 'outerHTML'
    }).then(function() {
        btn.disabled = false;
        icon.classList.remove('hidden');
        spinner.classList.add('hidden');
        btnText.textContent = 'Search';
        lucide.createIcons();
        initResizableColumns();
    });
}

/* === Results table: sort, select all, column toggle === */

function sortByCol(header) {
    var table = document.getElementById('results-table');
    if (!table) return;
    var headerRow = header.parentNode;
    var colIdx = Array.from(headerRow.children).indexOf(header);
    var tbody = table.querySelector('tbody');
    var rows = Array.from(tbody.querySelectorAll('tr'));
    var asc = header.dataset.sortDir !== 'asc';
    header.dataset.sortDir = asc ? 'asc' : 'desc';

    table.querySelectorAll('.sort-arrow').forEach(function(el) { el.textContent = ''; });
    header.querySelector('.sort-arrow').textContent = asc ? ' ▲' : ' ▼';

    rows.sort(function(a, b) {
        var cellA = a.cells[colIdx];
        var cellB = b.cells[colIdx];
        if (!cellA || !cellB) return 0;
        var valA = (cellA.dataset.sort || cellA.textContent).trim().toLowerCase();
        var valB = (cellB.dataset.sort || cellB.textContent).trim().toLowerCase();
        if (valA < valB) return asc ? -1 : 1;
        if (valA > valB) return asc ? 1 : -1;
        return 0;
    });
    rows.forEach(function(row) { tbody.appendChild(row); });
}

function toggleSelectAll(masterCb) {
    var checkboxes = document.querySelectorAll('#results-table [name="selected_uids"]');
    checkboxes.forEach(function(cb) { cb.checked = masterCb.checked; });
}

function toggleColMenu() {
    var menu = document.getElementById('col-menu');
    if (menu) menu.classList.toggle('hidden');
}

function toggleColumn(cb) {
    var colClass = cb.dataset.col;
    var cells = document.querySelectorAll('#results-table .' + colClass);
    cells.forEach(function(cell) {
        cell.style.display = cb.checked ? '' : 'none';
    });
}

/* === Resizable columns === */

function initResizableColumns() {
    var table = document.getElementById('results-table');
    if (!table || table.dataset.resizable) return;
    table.dataset.resizable = '1';
    var headers = table.querySelectorAll('thead th');
    headers.forEach(function(th) {
        if (th.classList.contains('col-check')) return;
        if (th.style.display === 'none') return;
        var handle = document.createElement('div');
        handle.className = 'resize-handle';
        th.appendChild(handle);
        th.style.position = 'relative';

        var startX, startWidth;
        handle.addEventListener('mousedown', function(e) {
            e.preventDefault();
            e.stopPropagation();
            startX = e.pageX;
            startWidth = th.offsetWidth;
            document.addEventListener('mousemove', onDrag);
            document.addEventListener('mouseup', onStop);
            document.body.style.cursor = 'col-resize';
            document.body.style.userSelect = 'none';
        });

        function onDrag(e) {
            var w = startWidth + (e.pageX - startX);
            if (w > 40) th.style.width = w + 'px';
        }
        function onStop() {
            document.removeEventListener('mousemove', onDrag);
            document.removeEventListener('mouseup', onStop);
            document.body.style.cursor = '';
            document.body.style.userSelect = '';
        }
    });
}

function executeRestore() {
    var payload = buildRestorePayload();
    if (!payload.source_account_id || !payload.target_account_id) {
        alert('Please select both source and destination accounts.');
        return;
    }
    var btn = document.getElementById('start-restore-btn');
    btn.disabled = true;
    btn.textContent = 'Starting…';

    fetch('/api/restore', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(payload)
    }).then(function(r) { return r.json(); }).then(function(data) {
        btn.disabled = false;
        btn.innerHTML = '<i data-lucide="play" class="icon-sm icon-inline"></i> Start Restore';
        lucide.createIcons();
        if (data.job_id) {
            htmx.ajax('GET', '/restore/partials/progress?job_id=' + data.job_id, {
                target: '#restore-progress',
                swap: 'outerHTML'
            });
        } else {
            alert(data.detail || 'Failed to start restore');
        }
    }).catch(function(e) {
        btn.disabled = false;
        btn.innerHTML = '<i data-lucide="play" class="icon-sm icon-inline"></i> Start Restore';
        lucide.createIcons();
        alert('Error: ' + e.message);
    });
}

function toggleCustomPrefix() {
    var sel = document.getElementById('folder-mapping-select');
    var row = document.getElementById('custom-prefix-row');
    if (sel.value === 'custom') {
        row.classList.remove('hidden');
    } else {
        row.classList.add('hidden');
    }
}

function buildRestorePayload() {
    var mode = document.querySelector('[name="restore_mode"]:checked').value;
    var mappingSel = document.getElementById('folder-mapping-select');
    var mapping = mappingSel.value;
    if (mapping === 'custom') {
        mapping = document.getElementById('custom-prefix').value || 'Restored';
    }

    var payload = {
        source_account_id: document.getElementById('source-account').value,
        target_account_id: document.getElementById('target-account').value,
        restore_mode: mode,
        folder_mapping: mapping,
        skip_duplicates: document.querySelector('[name="skip_duplicates"]').checked
    };

    if (mode === 'folder') {
        var checked = document.querySelectorAll('[name="selected_folders"]:checked');
        payload.selected_folders = Array.from(checked).map(function(cb) { return cb.value; });
    }

    if (mode === 'selection') {
        var checked = document.querySelectorAll('[name="selected_uids"]:checked');
        var folder = document.getElementById('search-folder').value;
        if (checked.length && folder) {
            var uids = {};
            uids[folder] = Array.from(checked).map(function(cb) { return parseInt(cb.value); });
            payload.selected_uids = uids;
        }
    }

    return payload;
}
