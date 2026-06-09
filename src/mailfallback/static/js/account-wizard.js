// account-wizard.js — step navigation for /accounts/new wizard.
// Backend POST is unchanged; this script just shows/hides panes and
// orchestrates which sub-flow (OAuth vs IMAP) renders in step 2.

(function () {
    var oauthAvailable = window._oauthAvailable || {};

    function $(id) { return document.getElementById(id); }
    function show(el) { if (el) el.classList.remove("hidden"); }
    function hide(el) { if (el) el.classList.add("hidden"); }

    function setStepIndicator(n) {
        document.querySelectorAll(".wizard-step").forEach(function (li) {
            var step = parseInt(li.dataset.step, 10);
            li.classList.toggle("active", step === n);
            li.classList.toggle("done", step < n);
        });
    }

    function setActivePane(n) {
        document.querySelectorAll(".wizard-pane").forEach(function (p) { p.classList.remove("active"); });
        var pane = $("wizard-pane-" + n);
        if (pane) pane.classList.add("active");
        setStepIndicator(n);
        // Re-render lucide icons that became visible.
        if (window.lucide && window.lucide.createIcons) window.lucide.createIcons();
    }

    window.wizardGoTo = function (n) {
        setActivePane(n);
        // Scroll to top of pane for clarity.
        var pane = $("wizard-pane-" + n);
        if (pane) pane.scrollIntoView({ behavior: "smooth", block: "start" });
    };

    window.wizardPickProvider = function (btn) {
        var provider = btn.dataset.provider;
        $("provider").value = provider;
        document.querySelectorAll(".provider-card").forEach(function (c) { c.classList.remove("picked"); });
        btn.classList.add("picked");

        // Configure step 2 based on provider.
        var oauthBlock = $("wizard-oauth-block");
        var imapBlock = $("wizard-imap-block");
        var oauthLabel = $("wizard-oauth-label");
        var step2Title = $("wizard-step2-title");
        var serverDetails = $("server-settings");

        var providerLabel = btn.querySelector("strong") ? btn.querySelector("strong").textContent : provider;
        step2Title.textContent = "Sign in to " + providerLabel;

        if ((provider === "google" && oauthAvailable.google) ||
            (provider === "microsoft" && oauthAvailable.microsoft)) {
            // OAuth path
            $("auth_type").value = "oauth2";
            show(oauthBlock);
            hide(imapBlock);
            oauthLabel.textContent = "Sign in with " + providerLabel;
            // Populate hidden IMAP server fields — the account record needs a host
            // even for OAuth (it's the IMAP endpoint mbsync syncs from).
            var oauthPresets = {
                google: { host: "imap.gmail.com", port: 993, tls: "IMAPS" },
                microsoft: { host: "outlook.office365.com", port: 993, tls: "IMAPS" }
            };
            var op = oauthPresets[provider];
            if (op) {
                $("imap_host").value = op.host;
                $("imap_port").value = op.port;
                $("tls_type").value = op.tls;
            }
        } else {
            // IMAP path — pre-fill server settings if known
            $("auth_type").value = "app_password";
            hide(oauthBlock);
            show(imapBlock);

            // Pre-fill IMAP host/port for known providers
            var presets = {
                yahoo: { host: "imap.mail.yahoo.com", port: 993, tls: "IMAPS" },
                icloud: { host: "imap.mail.me.com", port: 993, tls: "IMAPS" },
                google: { host: "imap.gmail.com", port: 993, tls: "IMAPS" },
                microsoft: { host: "outlook.office365.com", port: 993, tls: "IMAPS" }
            };
            var preset = presets[provider];
            if (preset) {
                $("imap_host").value = preset.host;
                $("imap_port").value = preset.port;
                $("tls_type").value = preset.tls;
                // Keep server-settings collapsed; user can expand if they need to override.
            } else {
                // 'other' — open server settings so the user provides the host.
                if (serverDetails) serverDetails.open = true;
            }
        }

        wizardGoTo(2);
    };

    window.wizardStartOAuth = function () {
        var provider = $("provider").value;
        var email = ($("oauth_email").value || "").trim();
        if (!email) {
            alert("Enter the email address of the mailbox you're authorising.");
            return;
        }
        // Derive a friendly nickname from the local part if none was given.
        var name = ($("oauth_name").value || "").trim();
        if (!name) {
            var local = email.split("@")[0];
            name = local.charAt(0).toUpperCase() + local.slice(1);
        }
        var storeEl = $("store_id");
        var payload = {
            name: name,
            email_address: email,
            imap_host: $("imap_host").value,
            imap_port: parseInt($("imap_port").value, 10),
            tls_type: $("tls_type").value,
            provider: provider,
            auth_type: "oauth2"
        };
        if (storeEl) payload.store_id = storeEl.value;
        // Reuse the shared create-then-redirect path: it POSTs /api/accounts and,
        // when given an oauthProvider, redirects to /auth/{provider}/start?account_id=…
        var btn = $("wizard-oauth-btn");
        var label = $("wizard-oauth-label");
        if (btn) btn.disabled = true;
        if (label) label.textContent = "Connecting…";
        _createAccountAndRedirect(payload, provider).catch(function (err) {
            if (btn) btn.disabled = false;
            if (label) label.textContent = "Sign in";
            alert("Couldn't start sign-in: " + err.message);
        });
    };

    window.wizardSwitchToImap = function () {
        $("auth_type").value = "app_password";
        hide($("wizard-oauth-block"));
        show($("wizard-imap-block"));
    };

    window.wizardStep2Next = function () {
        // For OAuth: the user must click "Sign in with X" — Continue is hidden.
        // For IMAP: validate that email + password are filled.
        if ($("auth_type").value === "oauth2" && !$("wizard-oauth-block").classList.contains("hidden")) {
            // Skip step 2 confirm — go straight to OAuth on user click of the button above.
            // Continue button shouldn't really be reachable in OAuth mode, but if it is,
            // nudge the user toward the OAuth button.
            alert("Click the 'Sign in with...' button above to authorise.");
            return;
        }
        var email = $("email_address").value.trim();
        var pass = $("credentials").value;
        if (!email || !pass) {
            alert("Enter your email and password to continue.");
            return;
        }
        // Auto-suggest nickname from the email's local part if not yet set.
        var nameField = $("name");
        if (!nameField.value) {
            var local = email.split("@")[0];
            // Title-case the local part as a friendly default.
            nameField.value = local.charAt(0).toUpperCase() + local.slice(1);
        }
        // Update step 3 summary
        $("wizard-summary-provider").textContent = $("provider").value || "Custom";
        $("wizard-summary-email").textContent = email;
        wizardGoTo(3);
    };

    // For OAuth providers: hide the Continue button in step 2; the OAuth button is
    // the action.
    document.addEventListener("DOMContentLoaded", function () {
        // Initial state: only step 1 visible (template default has .active on pane-1).
        if (window.lucide && window.lucide.createIcons) window.lucide.createIcons();
    });
})();
