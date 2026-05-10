// repository-wizard.js — step navigation for the Add Repository wizard
// on /admin/backup. Backend POST /admin/backup/new is unchanged; this
// just shows/hides panes and wires up backend-type selection.

(function () {
    function $(id) { return document.getElementById(id); }
    function show(el) { if (el) el.classList.remove("hidden"); }
    function hide(el) { if (el) el.classList.add("hidden"); }

    function setStep(n) {
        document.querySelectorAll("#repo-wizard-form ~ * .wizard-step, #repo-wizard-details .wizard-step").forEach(function (li) {
            var step = parseInt(li.dataset.step, 10);
            li.classList.toggle("active", step === n);
            li.classList.toggle("done", step < n);
        });
        document.querySelectorAll("#repo-wizard-details .wizard-pane").forEach(function (p) { p.classList.remove("active"); });
        var pane = $("repo-pane-" + n);
        if (pane) pane.classList.add("active");
        if (window.lucide && window.lucide.createIcons) window.lucide.createIcons();
    }

    window.repoWizardGoTo = function (n) {
        setStep(n);
        var pane = $("repo-pane-" + n);
        if (pane) pane.scrollIntoView({ behavior: "smooth", block: "start" });
    };

    window.repoWizardPickBackend = function (btn) {
        var backend = btn.dataset.backend;
        $("backend_type").value = backend;
        document.querySelectorAll("#repo-wizard-form .provider-card").forEach(function (c) { c.classList.remove("picked"); });
        btn.classList.add("picked");

        var s3Block = $("repo-s3-block");
        var localBlock = $("repo-local-block");
        var step2Title = $("repo-step2-title");
        if (backend === "s3") {
            show(s3Block);
            hide(localBlock);
            step2Title.textContent = "S3 connection";
        } else {
            hide(s3Block);
            show(localBlock);
            step2Title.textContent = "Local path";
        }
        repoWizardGoTo(2);
    };

    window.repoWizardStep2Next = function () {
        var backend = $("backend_type").value;
        if (backend === "s3") {
            var endpoint = $("s3_endpoint").value.trim();
            var bucket = $("s3_bucket").value.trim();
            var ak = $("s3_access_key").value;
            var sk = $("s3_secret_key").value;
            if (!endpoint || !bucket || !ak || !sk) {
                alert("Fill all S3 fields to continue.");
                return;
            }
            $("repo-summary-backend").textContent = "S3";
            $("repo-summary-target").textContent = endpoint + " — " + bucket;
        } else {
            var path = $("local_path").value.trim();
            if (!path) {
                alert("Enter a local path to continue.");
                return;
            }
            $("repo-summary-backend").textContent = "Local";
            $("repo-summary-target").textContent = path;
        }
        repoWizardGoTo(3);
    };

    document.addEventListener("DOMContentLoaded", function () {
        if (window.lucide && window.lucide.createIcons) window.lucide.createIcons();
    });
})();
