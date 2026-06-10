"""Tests for repo_inventory — boto3 and restic mocked."""

from unittest.mock import MagicMock, patch

import pytest

from mailfallback.config import settings
from mailfallback.models import Account, BackendType, MailStore, Repository, RepositoryAttachment
from mailfallback.security import encrypt_credentials
from mailfallback.services import repo_inventory


def _enc(value: str) -> str:
    return encrypt_credentials(value, settings.secret_key)


@pytest.fixture
def store(db_session):
    s = MailStore(name="default", path="/data/mailboxes")
    db_session.add(s)
    db_session.commit()
    return s


@pytest.fixture
def account(db_session, store):
    a = Account(name="acc", imap_host="h", maildir_path="/data/mailboxes/u1", store_id=store.id)
    db_session.add(a)
    db_session.commit()
    return a


@pytest.fixture
def s3_repo(db_session):
    r = Repository(
        name="s3repo",
        backend_type=BackendType.s3,
        s3_endpoint=_enc("https://s3.example.com"),
        s3_bucket=_enc("bucket"),
        s3_access_key=_enc("ak"),
        s3_secret_key=_enc("sk"),  # pragma: allowlist secret
        restic_password=_enc("rp"),  # pragma: allowlist secret
    )
    db_session.add(r)
    db_session.commit()
    return r


class TestListPrefixes:
    @patch("mailfallback.services.s3_probe.s3_client")
    def test_s3_lists_common_prefixes_paginated(self, mock_client_fn, s3_repo):
        client = MagicMock()
        client.list_objects_v2.side_effect = [
            {
                "CommonPrefixes": [{"Prefix": "aaa/"}, {"Prefix": "bbb/"}],
                "IsTruncated": True,
                "NextContinuationToken": "tok",
            },
            {"CommonPrefixes": [{"Prefix": "ccc/"}], "IsTruncated": False},
        ]
        mock_client_fn.return_value = client

        prefixes = repo_inventory.list_prefixes(s3_repo)

        assert prefixes == ["aaa", "bbb", "ccc"]
        # second call must carry the continuation token
        assert client.list_objects_v2.call_args_list[1].kwargs["ContinuationToken"] == "tok"

    def test_local_lists_subdirectories(self, db_session, tmp_path):
        (tmp_path / "one").mkdir()
        (tmp_path / "two").mkdir()
        (tmp_path / "stray-file").write_text("x")
        repo = Repository(
            name="loc",
            backend_type=BackendType.local,
            local_path=_enc(str(tmp_path)),
            restic_password=_enc("rp"),  # pragma: allowlist secret
        )
        db_session.add(repo)
        db_session.commit()

        assert repo_inventory.list_prefixes(repo) == ["one", "two"]

    def test_local_missing_dir_returns_empty(self, db_session, tmp_path):
        repo = Repository(
            name="loc2",
            backend_type=BackendType.local,
            local_path=_enc(str(tmp_path / "nope")),
            restic_password=_enc("rp"),  # pragma: allowlist secret
        )
        db_session.add(repo)
        db_session.commit()

        assert repo_inventory.list_prefixes(repo) == []


class TestClassify:
    def test_classification_kinds(self, db_session, s3_repo, account):
        db_session.add(
            RepositoryAttachment(
                repository_id=s3_repo.id, account_id=account.id, prefix="old-prefix"
            )
        )
        db_session.commit()

        prefixes = [account.id, "__mfb_config__", "old-prefix", "stranger"]
        entries = repo_inventory.classify(db_session, s3_repo, prefixes)

        kinds = {e["prefix"]: e["kind"] for e in entries}
        assert kinds[account.id] == "account"
        assert kinds["__mfb_config__"] == "config"
        assert kinds["old-prefix"] == "attached"
        assert kinds["stranger"] == "orphan"
        acc_entry = next(e for e in entries if e["prefix"] == account.id)
        assert acc_entry["account"].id == account.id
        att_entry = next(e for e in entries if e["prefix"] == "old-prefix")
        assert att_entry["attachment"] is not None
        assert att_entry["account"].id == account.id


class TestPrefixDetail:
    @patch("mailfallback.services.repo_inventory.restic_service")
    def test_detail_returns_snapshot_count_and_latest(self, mock_restic, s3_repo):
        mock_restic.list_snapshots.return_value = [
            {"short_id": "ab12", "time": "2026-06-09T03:00:00Z"},
            {"short_id": "cd34", "time": "2026-06-08T03:00:00Z"},
        ]

        detail = repo_inventory.prefix_detail(s3_repo, "stranger")

        assert detail["ok"] is True
        assert detail["snapshot_count"] == 2
        assert detail["latest"] == "2026-06-09T03:00:00Z"
        mock_restic.list_snapshots.assert_called_once_with(
            s3_repo, "stranger", restic_password_enc=None
        )

    @patch("mailfallback.services.repo_inventory.restic_service")
    def test_detail_reports_error(self, mock_restic, s3_repo):
        mock_restic.list_snapshots.side_effect = RuntimeError("wrong password")

        detail = repo_inventory.prefix_detail(s3_repo, "stranger")

        assert detail["ok"] is False
        assert "wrong password" in detail["error"]


class TestPrefixDetailOverride:
    @patch("mailfallback.services.repo_inventory.restic_service")
    def test_detail_threads_password_override(self, mock_restic, s3_repo):
        mock_restic.list_snapshots.return_value = []

        repo_inventory.prefix_detail(s3_repo, "ghost", restic_password_enc="enc-override")

        kwargs = mock_restic.list_snapshots.call_args.kwargs
        assert kwargs["restic_password_enc"] == "enc-override"
