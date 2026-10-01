// account-bento.js — mailbox detail tools row: clicking a button reveals one
// inline section, persisted per-account in localStorage so reloads don't lose
// context. [data-admin-open="<target>"] elsewhere on the page opens a section.

(function () {
    "use strict";

    function setupAdminRow() {
        const row = document.getElementById("admin-row");
        if (!row) return;
        const acctId = document.getElementById("account-page")?.dataset.accountId || "default";
        const storageKey = "mfb-admin-section-" + acctId;

        const buttons = Array.from(row.querySelectorAll("[data-admin-target]"));
        const sections = Array.from(document.querySelectorAll(".admin-section"));

        function open(target) {
            sections.forEach((s) => s.classList.toggle("is-open", s.id === "admin-" + target));
            buttons.forEach((b) => b.classList.toggle("is-active", b.dataset.adminTarget === target));
        }

        function close() {
            sections.forEach((s) => s.classList.remove("is-open"));
            buttons.forEach((b) => b.classList.remove("is-active"));
        }

        buttons.forEach((btn) => {
            btn.addEventListener("click", () => {
                const target = btn.dataset.adminTarget;
                if (btn.classList.contains("is-active")) {
                    close();
                    localStorage.removeItem(storageKey);
                } else {
                    open(target);
                    localStorage.setItem(storageKey, target);
                    document.getElementById("admin-" + target)?.scrollIntoView({
                        behavior: "smooth",
                        block: "nearest",
                    });
                }
            });
        });

        // URL hash takes priority over localStorage so deep-links from /recover
        // and similar wizards land on the right section open. Hash format:
        // #admin-edit, #admin-offsite, #admin-ownership, etc. Never auto-open delete.
        // Returns true when the hash named a section and it was opened.
        function openFromHash() {
            const hashTarget = (location.hash || "").replace(/^#admin-/, "");
            if (!hashTarget || hashTarget === "delete" || !document.getElementById("admin-" + hashTarget)) {
                return false;
            }
            open(hashTarget);
            localStorage.setItem(storageKey, hashTarget);
            // Defer scroll to next tick so the layout has settled.
            setTimeout(() => {
                document.getElementById("admin-" + hashTarget)?.scrollIntoView({
                    behavior: "smooth",
                    block: "start",
                });
            }, 50);
            // Drop the hash once it has done its job: a second click on the
            // same "#admin-edit" link must change the hash again, or no
            // hashchange fires and the link silently stops working.
            history.replaceState(null, "", location.pathname + location.search);
            return true;
        }

        // Shortcuts elsewhere on the page (the Health box's "Configure").
        document.addEventListener("click", (e) => {
            const opener = e.target.closest("[data-admin-open]");
            if (!opener) return;
            const target = opener.dataset.adminOpen;
            open(target);
            localStorage.setItem(storageKey, target);
            document.getElementById("admin-" + target)?.scrollIntoView({ behavior: "smooth", block: "start" });
        });

        // In-page links (the hero's "Update password" → #admin-edit) change
        // only the hash, so the load-time check alone would never see them.
        window.addEventListener("hashchange", openFromHash);

        if (openFromHash()) {
            return;
        }

        // Restore last-open section, but never auto-restore "delete" (too dangerous).
        const last = localStorage.getItem(storageKey);
        if (last && last !== "delete") {
            open(last);
        }
    }

    function init() {
        setupAdminRow();
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", init);
    } else {
        init();
    }
})();
