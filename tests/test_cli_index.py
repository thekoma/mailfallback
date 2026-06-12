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


def test_cli_backfill_attachments_content_only_dispatches(capsys, monkeypatch):
    """--content-only routes to backfill_attachment_content, not the metadata backfill."""
    from mailfallback.cli import app
    from mailfallback.services import index_service

    monkeypatch.setattr("mailfallback.cli.index.SessionLocal", MagicMock(return_value=MagicMock()))
    seen = {}

    def fake_content(db, account_id):
        seen["account_id"] = account_id
        return 7

    def fail_metadata(db, account_id):
        raise AssertionError("metadata backfill must not run with --content-only")

    monkeypatch.setattr(index_service, "backfill_attachment_content", fake_content)
    monkeypatch.setattr(index_service, "backfill_attachments", fail_metadata)

    argv = ["mfb", "index", "backfill-attachments", "acc-1", "--content-only"]
    with patch.object(sys, "argv", argv):
        rc = app()
    assert rc == 0
    assert seen["account_id"] == "acc-1"
    assert "7 attachment row(s)" in capsys.readouterr().out


def test_cli_backfill_attachments_without_flag_keeps_metadata_path(capsys, monkeypatch):
    """Without --content-only the existing metadata backfill runs unchanged."""
    from mailfallback.cli import app
    from mailfallback.services import index_service

    monkeypatch.setattr("mailfallback.cli.index.SessionLocal", MagicMock(return_value=MagicMock()))

    def fake_metadata(db, account_id):
        return 3

    def fail_content(db, account_id):
        raise AssertionError("content backfill must not run without --content-only")

    monkeypatch.setattr(index_service, "backfill_attachments", fake_metadata)
    monkeypatch.setattr(index_service, "backfill_attachment_content", fail_content)

    with patch.object(sys, "argv", ["mfb", "index", "backfill-attachments", "acc-1"]):
        rc = app()
    assert rc == 0
    assert "Backfilled attachments for 3 message(s)" in capsys.readouterr().out


def test_cli_backfill_content_only_tika_disabled_errors(capsys, monkeypatch):
    """The service's tika-disabled refusal surfaces as a clear message + rc 1."""
    from mailfallback.cli import app
    from mailfallback.services import index_service

    monkeypatch.setattr("mailfallback.cli.index.SessionLocal", MagicMock(return_value=MagicMock()))

    def refuse(db, account_id):
        raise ValueError("Tika is disabled")

    monkeypatch.setattr(index_service, "backfill_attachment_content", refuse)

    argv = ["mfb", "index", "backfill-attachments", "acc-1", "--content-only"]
    with patch.object(sys, "argv", argv):
        rc = app()
    assert rc == 1
    assert "Tika is disabled" in capsys.readouterr().out
