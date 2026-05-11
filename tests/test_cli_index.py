"""Tests for `mfb index` CLI subcommands."""

import sys
from unittest.mock import MagicMock, patch


def test_cli_index_status_runs(capsys, monkeypatch):
    """`mfb index status` doesn't crash on empty DB."""
    from mailfallback.cli import app

    fake_session = MagicMock()
    fake_session.query.return_value.all.return_value = []  # no rebuild_status rows
    fake_factory = MagicMock(return_value=fake_session)
    monkeypatch.setattr("mailfallback.cli.index.SessionLocal", fake_factory)

    with patch.object(sys, "argv", ["mfb", "index", "status"]):
        rc = app()
    assert rc == 0
    captured = capsys.readouterr()
    assert "rebuild_status" in captured.out or "No rebuild_status" in captured.out
