"""Tests for restic_service._stream_restic — the streamed backup path.

A fake binary stands in for restic (via _restic_cmd), so these exercise the
streaming, the stderr drain and the summary extraction without needing a real
repository. Real restic is covered by live verification on docker compose.
"""

import subprocess
import sys
import threading

import pytest

from mailfallback.services import restic_service


def _python(code: str) -> list[str]:
    return [sys.executable, "-c", code]


def test_stream_extracts_summary_and_emits_events(monkeypatch):
    """Every JSON line reaches on_event; only the summary is returned."""
    script = (
        "import json\n"
        "for i in range(3):\n"
        "    print(json.dumps({'message_type':'status','percent_done':i/3}))\n"
        "print(json.dumps({'message_type':'summary','snapshot_id':'abc123',"
        "'total_bytes_processed':14000000000,'data_added':500}))\n"
    )
    monkeypatch.setattr(restic_service, "_restic_cmd", lambda args, insecure: _python(script))

    events = []
    rc, summary, _ = restic_service._stream_restic(
        ["backup"], {}, False, on_event=events.append, register=None
    )

    assert rc == 0
    assert summary["snapshot_id"] == "abc123"
    assert summary["total_bytes_processed"] == 14000000000
    assert [e["message_type"] for e in events] == ["status", "status", "status", "summary"]


def test_stream_ignores_non_json_lines(monkeypatch):
    script = (
        "import json\n"
        "print('not json at all')\n"
        "print(json.dumps({'message_type':'summary','snapshot_id':'ok'}))\n"
    )
    monkeypatch.setattr(restic_service, "_restic_cmd", lambda args, insecure: _python(script))
    rc, summary, _ = restic_service._stream_restic(["backup"], {}, False, None, None)
    assert rc == 0
    assert summary["snapshot_id"] == "ok"


def test_stream_does_not_deadlock_on_large_stderr(monkeypatch):
    """restic writing more than a pipe buffer to stderr must not wedge us.

    Without a concurrent stderr drain this deadlocks: we block reading stdout
    while the child blocks writing stderr. Run it on a thread with a join
    deadline so a regression FAILS the suite instead of hanging it forever
    (pytest-timeout is not a dependency of this project).
    """
    script = (
        "import json,sys\n"
        "sys.stderr.write('x'*500000)\n"
        "sys.stderr.flush()\n"
        "print(json.dumps({'message_type':'summary','snapshot_id':'done'}))\n"
    )
    monkeypatch.setattr(restic_service, "_restic_cmd", lambda args, insecure: _python(script))

    result = {}

    def _run():
        result["value"] = restic_service._stream_restic(["backup"], {}, False, None, None)

    worker = threading.Thread(target=_run, daemon=True)
    worker.start()
    worker.join(timeout=30)

    assert not worker.is_alive(), "deadlocked — stderr is not being drained concurrently"
    rc, summary, stderr_tail = result["value"]
    assert rc == 0
    assert summary["snapshot_id"] == "done"
    assert len(stderr_tail) > 0


def test_stream_bounds_the_stderr_tail(monkeypatch):
    """Only the tail is kept — we never hold the whole stream in memory."""
    script = "import sys\nfor i in range(5000):\n    sys.stderr.write(f'line {i}\\n')\n"
    monkeypatch.setattr(restic_service, "_restic_cmd", lambda args, insecure: _python(script))
    _, _, stderr_tail = restic_service._stream_restic(["backup"], {}, False, None, None)
    assert stderr_tail.count("\n") <= restic_service._STDERR_TAIL_LINES
    assert "line 4999" in stderr_tail
    assert "line 0\n" not in stderr_tail


def test_stream_registers_the_live_process(monkeypatch):
    """The watchdog can only kill a process it has a handle on."""
    script = "import json\nprint(json.dumps({'message_type':'summary'}))\n"
    monkeypatch.setattr(restic_service, "_restic_cmd", lambda args, insecure: _python(script))
    seen = []
    restic_service._stream_restic(["backup"], {}, False, None, register=seen.append)
    assert len(seen) == 1
    assert isinstance(seen[0], subprocess.Popen)


def test_stream_reports_a_nonzero_returncode(monkeypatch):
    script = "import sys\nsys.stderr.write('repository is locked\\n')\nsys.exit(1)\n"
    monkeypatch.setattr(restic_service, "_restic_cmd", lambda args, insecure: _python(script))
    rc, summary, stderr_tail = restic_service._stream_restic(["backup"], {}, False, None, None)
    assert rc == 1
    assert summary == {}
    assert "repository is locked" in stderr_tail


def test_run_backup_raises_with_the_stderr_tail_on_failure(monkeypatch):
    script = "import sys\nsys.stderr.write('repository is locked\\n')\nsys.exit(1)\n"
    monkeypatch.setattr(restic_service, "_restic_cmd", lambda args, insecure: _python(script))
    monkeypatch.setattr(restic_service, "build_env", lambda *a, **k: {})
    monkeypatch.setattr(restic_service, "_is_insecure", lambda d: False)

    with pytest.raises(RuntimeError, match="repository is locked"):
        restic_service.run_backup(object(), "acct", "/data/mailboxes/acct")


def test_run_backup_passes_tags_and_returns_the_summary(monkeypatch):
    """Tags must still reach restic, and the return shape is unchanged."""
    captured = {}

    def fake_cmd(args, insecure):
        captured["args"] = args
        return _python(
            "import json\nprint(json.dumps({'message_type':'summary','snapshot_id':'s1'}))\n"
        )

    monkeypatch.setattr(restic_service, "_restic_cmd", fake_cmd)
    monkeypatch.setattr(restic_service, "build_env", lambda *a, **k: {})
    monkeypatch.setattr(restic_service, "_is_insecure", lambda d: False)

    summary = restic_service.run_backup(
        object(), "acct", "/data/mailboxes/acct", tags=["account:acct", "mfb"]
    )

    assert summary == {"message_type": "summary", "snapshot_id": "s1"}
    assert "--tag=account:acct" in captured["args"]
    assert "--tag=mfb" in captured["args"]
    assert captured["args"][-1] == "/data/mailboxes/acct"
