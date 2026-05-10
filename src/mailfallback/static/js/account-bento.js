// account-bento.js — bento page interactivity
// 1) Treemap → timeline linked-view: hover a folder, the timeline strip
//    re-renders to show that folder's `added_done` per sync run.
// 2) Bottom admin row: clicking a button reveals one inline section,
//    persisted per-account in localStorage so reloads don't lose context.

(function () {
    "use strict";

    function readBentoData() {
        const tag = document.getElementById("bento-data");
        if (!tag) return { folder_timeline: {}, timeline_global: [] };
        try {
            return JSON.parse(tag.textContent);
        } catch (e) {
            return { folder_timeline: {}, timeline_global: [] };
        }
    }

    function setupTreemapLinkedView() {
        const tm = document.getElementById("treemap");
        const strip = document.getElementById("timeline-strip");
        if (!tm || !strip) return;

        const data = readBentoData();
        const perFolder = data.folder_timeline || {};
        const bars = Array.from(strip.querySelectorAll(".timeline-bar"));
        const modeLabel = document.getElementById("tl-mode-label");

        function restore() {
            tm.classList.remove("has-active");
            tm.querySelectorAll(".tm-active").forEach((el) => el.classList.remove("tm-active"));
            bars.forEach((bar) => {
                bar.classList.remove("tl-dim");
                bar.style.height = "60%";
            });
            strip.dataset.mode = "status";
            if (modeLabel) modeLabel.textContent = "";
        }

        tm.querySelectorAll(".treemap-cell").forEach((cell) => {
            cell.addEventListener("mouseenter", () => {
                const folder = cell.dataset.folder;
                const points = perFolder[folder] || [];
                if (!points.length) return;

                tm.classList.add("has-active");
                cell.classList.add("tm-active");

                // Build a ts→added lookup for fast matching.
                const lookup = Object.create(null);
                let max = 0;
                for (const p of points) {
                    lookup[p.ts] = p.added;
                    if (p.added > max) max = p.added;
                }

                bars.forEach((bar) => {
                    const ts = bar.dataset.ts;
                    if (ts in lookup) {
                        const v = lookup[ts];
                        const h = max > 0 ? Math.max(8, Math.round((v / max) * 100)) : 4;
                        bar.style.height = h + "%";
                        bar.classList.toggle("tl-dim", v === 0);
                    } else {
                        bar.style.height = "8%";
                        bar.classList.add("tl-dim");
                    }
                });

                strip.dataset.mode = "folder";
                if (modeLabel) {
                    modeLabel.textContent =
                        max > 0
                            ? "showing: " + folder + " · peak " + max + " new msgs/sync"
                            : "showing: " + folder + " · no new mail in last " + bars.length + " syncs";
                }
            });

            cell.addEventListener("mouseleave", restore);
        });
    }

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

        // Restore last-open section, but never auto-restore "delete" (too dangerous).
        const last = localStorage.getItem(storageKey);
        if (last && last !== "delete") {
            open(last);
        }
    }

    function init() {
        setupTreemapLinkedView();
        setupAdminRow();
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", init);
    } else {
        init();
    }
})();
