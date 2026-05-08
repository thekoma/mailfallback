/* MFB — Account detail scripts (account_detail.html) */

function testConnectionFromHero(host, port, tls) {
    var result = document.createElement('p');
    result.className = 'text-small text-muted mt-025';
    result.textContent = 'Testing connection…';
    var btn = event.target.closest('.icon-btn');
    btn.parentNode.appendChild(result);
    btn.disabled = true;

    fetch('/api/sync/test-connection', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({imap_host: host, imap_port: port, tls_type: tls})
    }).then(function(r) { return r.json(); }).then(function(data) {
        if (data.ok) {
            result.className = 'text-small alert-success mt-025';
            result.textContent = 'Connection OK — server is reachable';
        } else {
            result.className = 'text-small alert-error mt-025';
            result.textContent = data.message || 'Connection failed';
        }
        btn.disabled = false;
    }).catch(function(e) {
        result.className = 'text-small alert-error mt-025';
        result.textContent = 'Error: ' + e;
        btn.disabled = false;
    });
}

function initStatsToggle(statsDetails) {
    statsDetails.addEventListener('toggle', function() {
        document.getElementById('stats-inline').style.display = this.open ? 'none' : '';
    });
}

document.addEventListener('DOMContentLoaded', function() {
    var page = document.getElementById('account-page');
    if (!page) return;
    var accountId = page.dataset.accountId;

    /* Diagnostic mode: auto-open History when last sync failed */
    var heroPanel = document.getElementById('hero-panel');
    if (heroPanel && (heroPanel.classList.contains('hero-error') || heroPanel.classList.contains('hero-sign-in-needed'))) {
        var historySection = document.getElementById('history-section');
        if (historySection) {
            var historyDetails = historySection.closest('details');
            if (historyDetails) historyDetails.open = true;
        }
    }

    /* Restore section open/close state from localStorage */
    var storageKey = 'mfb-sections-' + accountId;
    var savedState = {};
    try { savedState = JSON.parse(localStorage.getItem(storageKey) || '{}'); } catch(e) {}

    document.querySelectorAll('#account-page details[id]').forEach(function(det) {
        if (savedState[det.id] !== undefined) {
            det.open = savedState[det.id];
        }
        det.addEventListener('toggle', function() {
            try {
                var state = JSON.parse(localStorage.getItem(storageKey) || '{}');
                state[det.id] = det.open;
                localStorage.setItem(storageKey, JSON.stringify(state));
            } catch(e) {}
        });
    });

    /* sync-finished event: refresh history */
    document.body.addEventListener('sync-finished', function() {
        htmx.ajax('GET', '/accounts/' + accountId + '/partials/history', '#history-section');
    });

    /* Stats toggle */
    var statsDetails = document.getElementById('stats-details');
    if (statsDetails) {
        initStatsToggle(statsDetails);
    }
});
