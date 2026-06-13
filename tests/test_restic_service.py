"""Tests for restic_service — subprocess mocked, decrypt_credentials mocked."""

import json
import subprocess
from unittest.mock import MagicMock, patch

import pytest

from mailfallback.services import restic_service


@pytest.fixture
def s3_destination():
    dest = MagicMock()
    dest.backend_type.value = "s3"
    dest.s3_endpoint = "enc-endpoint"
    dest.s3_bucket = "enc-bucket"
    dest.s3_access_key = "enc-access"
    dest.s3_secret_key = "enc-secret"
    dest.local_path = None
    dest.restic_password = "enc-password"
    return dest


@pytest.fixture
def local_destination():
    dest = MagicMock()
    dest.backend_type.value = "local"
    dest.s3_endpoint = None
    dest.s3_bucket = None
    dest.s3_access_key = None
    dest.s3_secret_key = None
    dest.local_path = "enc-local-path"
    dest.restic_password = "enc-password"
    return dest


@pytest.fixture(autouse=True)
def mock_decrypt():
    """Mock decrypt_credentials to return the value without 'enc-' prefix."""

    def fake_decrypt(value, _key):
        if value and value.startswith("enc-"):
            return value[4:]  # strip "enc-" prefix
        return value

    with patch.object(restic_service, "decrypt_credentials", side_effect=fake_decrypt):
        yield


class TestTestDestination:
    @patch("mailfallback.services.s3_probe.probe")
    def test_delegates_to_probe(self, mock_probe, s3_destination):
        mock_probe.return_value = {"ok": True, "error": None}
        result = restic_service.test_destination(s3_destination)
        assert result == {"ok": True, "error": None}
        mock_probe.assert_called_once_with(s3_destination)

    @patch("mailfallback.services.s3_probe.probe")
    def test_propagates_failure(self, mock_probe, s3_destination):
        mock_probe.return_value = {"ok": False, "error": "AccessDenied"}
        result = restic_service.test_destination(s3_destination)
        assert result["ok"] is False
        assert result["error"] == "AccessDenied"


class TestBuildRepoUrl:
    def test_s3_url(self, s3_destination):
        url = restic_service.build_repo_url(s3_destination, "acc-123")
        assert url == "s3:endpoint/bucket/acc-123"

    def test_local_url(self, local_destination):
        url = restic_service.build_repo_url(local_destination, "acc-456")
        assert url == "local-path/acc-456"


class TestBuildEnv:
    def test_s3_env(self, s3_destination):
        env = restic_service.build_env(s3_destination, "acc-123")
        assert env["RESTIC_REPOSITORY"] == "s3:endpoint/bucket/acc-123"
        assert env["RESTIC_PASSWORD"] == "password"
        assert env["AWS_ACCESS_KEY_ID"] == "access"
        assert env["AWS_SECRET_ACCESS_KEY"] == "secret"

    def test_local_env(self, local_destination):
        env = restic_service.build_env(local_destination, "acc-456")
        assert env["RESTIC_REPOSITORY"] == "local-path/acc-456"
        assert env["RESTIC_PASSWORD"] == "password"
        assert "AWS_ACCESS_KEY_ID" not in env
        assert "AWS_SECRET_ACCESS_KEY" not in env


class TestRetentionArgs:
    def test_light_preset(self):
        args = restic_service.get_retention_args("light")
        assert args == ["--keep-daily", "7", "--keep-weekly", "4"]

    def test_standard_preset(self):
        args = restic_service.get_retention_args("standard")
        assert args == [
            "--keep-daily",
            "30",
            "--keep-weekly",
            "12",
            "--keep-monthly",
            "6",
        ]

    def test_full_preset(self):
        args = restic_service.get_retention_args("full")
        assert args == [
            "--keep-daily",
            "90",
            "--keep-weekly",
            "52",
            "--keep-monthly",
            "24",
        ]

    def test_custom_preset(self):
        args = restic_service.get_retention_args(
            "custom", keep_daily=14, keep_weekly=8, keep_monthly=3
        )
        assert args == [
            "--keep-daily",
            "14",
            "--keep-weekly",
            "8",
            "--keep-monthly",
            "3",
        ]

    def test_custom_preset_partial(self):
        args = restic_service.get_retention_args("custom", keep_daily=5)
        assert args == ["--keep-daily", "5"]

    def test_custom_preset_all_none(self):
        args = restic_service.get_retention_args("custom")
        assert args == []


class TestInitRepo:
    @patch("mailfallback.services.restic_service._run_restic")
    def test_init_success(self, mock_run, s3_destination):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )
        assert restic_service.init_repo(s3_destination, "acc-123") is True
        mock_run.assert_called_once()

    @patch("mailfallback.services.restic_service._run_restic")
    def test_init_already_exists(self, mock_run, s3_destination):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="repo already initialized"
        )
        assert restic_service.init_repo(s3_destination, "acc-123") is True

    @patch("mailfallback.services.restic_service._run_restic")
    def test_init_failure(self, mock_run, s3_destination):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="connection refused"
        )
        assert restic_service.init_repo(s3_destination, "acc-123") is False


class TestRunBackup:
    @patch("mailfallback.services.restic_service._run_restic")
    def test_backup_success(self, mock_run, s3_destination):
        summary = {"message_type": "summary", "files_new": 10, "data_added": 1024}
        stdout_lines = [
            json.dumps({"message_type": "status", "percent_done": 0.5}),
            json.dumps(summary),
        ]
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="\n".join(stdout_lines), stderr=""
        )
        result = restic_service.run_backup(s3_destination, "acc-123", "/data/mail")
        assert result["message_type"] == "summary"
        assert result["files_new"] == 10

    @patch("mailfallback.services.restic_service._run_restic")
    def test_backup_failure(self, mock_run, s3_destination):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="backup error"
        )
        with pytest.raises(RuntimeError, match="Restic backup failed"):
            restic_service.run_backup(s3_destination, "acc-123", "/data/mail")


class TestListSnapshots:
    @patch("mailfallback.services.restic_service._run_restic")
    def test_list_success(self, mock_run, s3_destination):
        snapshots = [
            {"short_id": "abc123", "time": "2026-05-10T00:00:00Z"},
            {"short_id": "def456", "time": "2026-05-09T00:00:00Z"},
        ]
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=json.dumps(snapshots), stderr=""
        )
        result = restic_service.list_snapshots(s3_destination, "acc-123")
        assert len(result) == 2
        assert result[0]["short_id"] == "abc123"

    @patch("mailfallback.services.restic_service._run_restic")
    def test_list_failure(self, mock_run, s3_destination):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="snapshots error"
        )
        with pytest.raises(RuntimeError, match="Restic snapshots failed"):
            restic_service.list_snapshots(s3_destination, "acc-123")


@patch("mailfallback.services.restic_service._run_restic")
def test_list_files_parses_restic_ls_json(mock_run, db_session, default_store):
    from mailfallback.models import Repository
    from mailfallback.services import restic_service

    repo = Repository(name="r", backend_type="local", local_path="/tmp/r", restic_password="x")
    db_session.add(repo)
    db_session.commit()

    # restic ls --json emits one JSON object per line, type="node" for files
    fake_stdout = "\n".join(
        [
            json.dumps({"struct_type": "snapshot", "id": "abc"}),
            json.dumps(
                {
                    "type": "node",
                    "name": "INBOX",
                    "path": "/INBOX",
                    "struct_type": "node",
                    "node_type": "dir",
                }
            ),
            json.dumps(
                {
                    "type": "node",
                    "name": "1234.host:2,S",
                    "path": "/INBOX/cur/1234.host:2,S",
                    "struct_type": "node",
                    "node_type": "file",
                }
            ),
            json.dumps(
                {
                    "type": "node",
                    "name": "1235.host:2,",
                    "path": "/INBOX/cur/1235.host:2,",
                    "struct_type": "node",
                    "node_type": "file",
                }
            ),
        ]
    )
    mock_run.return_value = MagicMock(returncode=0, stdout=fake_stdout, stderr="")

    files = list(restic_service.list_files(repo, "abc12345", "abc"))
    assert "/INBOX/cur/1234.host:2,S" in files
    assert "/INBOX/cur/1235.host:2," in files
    # Directory entries excluded
    assert "/INBOX" not in files


class TestDumpFile:
    @patch("mailfallback.services.restic_service.subprocess.run")
    def test_dump_file_returns_bytes(self, mock_run, s3_destination):
        s3_destination.insecure_tls = False
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=b"RAW BYTES", stderr=b""
        )

        out = restic_service.dump_file(s3_destination, "acct-id", "ab12", "/data/m/acct/cur/x:2,S")

        assert out == b"RAW BYTES"
        cmd = mock_run.call_args.args[0]
        assert cmd == ["restic", "dump", "ab12", "/data/m/acct/cur/x:2,S"]
        # binary mode: text=True would corrupt raw message bytes
        assert "text" not in mock_run.call_args.kwargs
        env = mock_run.call_args.kwargs["env"]
        assert env["RESTIC_REPOSITORY"] == "s3:endpoint/bucket/acct-id"

    @patch("mailfallback.services.restic_service.subprocess.run")
    def test_dump_file_failure_returns_none(self, mock_run, s3_destination):
        s3_destination.insecure_tls = False
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=1, stdout=b"", stderr=b"dump error"
        )

        out = restic_service.dump_file(s3_destination, "acct-id", "ab12", "/x")

        assert out is None

    @patch("mailfallback.services.restic_service.subprocess.run")
    def test_dump_file_truncates_at_max_bytes(self, mock_run, s3_destination):
        s3_destination.insecure_tls = False
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=b"A" * 100, stderr=b""
        )

        out = restic_service.dump_file(s3_destination, "acct-id", "ab12", "/x", max_bytes=10)

        assert out == b"A" * 10

    @patch("mailfallback.services.restic_service.subprocess.run")
    def test_dump_file_insecure_tls_flag(self, mock_run, s3_destination):
        s3_destination.insecure_tls = True
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=b"x", stderr=b""
        )

        restic_service.dump_file(s3_destination, "acct-id", "ab12", "/x")

        cmd = mock_run.call_args.args[0]
        assert "--insecure-tls" in cmd
        assert cmd.index("--insecure-tls") < cmd.index("dump")


class TestPasswordOverride:
    def test_build_env_uses_override_when_given(self, s3_destination):
        # mock_decrypt (autouse) strips the "enc-" prefix, mirroring decryption
        env = restic_service.build_env(
            s3_destination, "some-prefix", restic_password_enc="enc-other-password"
        )
        assert env["RESTIC_PASSWORD"] == "other-password"  # pragma: allowlist secret

    def test_build_env_defaults_to_destination_password(self, s3_destination):
        env = restic_service.build_env(s3_destination, "some-prefix")
        assert env["RESTIC_PASSWORD"]  # non-empty, from destination

    @patch("mailfallback.services.restic_service._run_restic")
    def test_list_snapshots_threads_override(self, mock_run, s3_destination):
        mock_run.return_value = MagicMock(returncode=0, stdout="[]", stderr="")

        restic_service.list_snapshots(s3_destination, "ghost", restic_password_enc="enc-attpass")

        env = mock_run.call_args.args[1]
        assert env["RESTIC_PASSWORD"] == "attpass"  # pragma: allowlist secret

    @patch("mailfallback.services.restic_service._run_restic")
    def test_restore_snapshot_threads_override(self, mock_run, s3_destination):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        restic_service.restore_snapshot(
            s3_destination, "ghost", "ab12", "/tmp/x", restic_password_enc="enc-attpass"
        )

        env = mock_run.call_args.args[1]
        assert env["RESTIC_PASSWORD"] == "attpass"  # pragma: allowlist secret


class TestBackupTags:
    @patch("mailfallback.services.restic_service._run_restic")
    def test_run_backup_passes_tag_flags(self, mock_run, s3_destination):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        restic_service.run_backup(
            s3_destination,
            "acc-id",
            "/data/m/x",
            tags=["mfb:email=a@b.c", "mfb:name=Work"],
        )

        args = mock_run.call_args.args[0]
        assert "--tag=mfb:email=a@b.c" in args
        assert "--tag=mfb:name=Work" in args

    @patch("mailfallback.services.restic_service._run_restic")
    def test_run_backup_without_tags_unchanged(self, mock_run, s3_destination):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        restic_service.run_backup(s3_destination, "acc-id", "/data/m/x")

        args = mock_run.call_args.args[0]
        assert not any(a.startswith("--tag") for a in args)


class TestAddTags:
    @patch("mailfallback.services.restic_service._run_restic")
    def test_add_tags_builds_command(self, mock_run, s3_destination):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        ok = restic_service.add_tags(
            s3_destination,
            "acc-id",
            ["ab12", "cd34"],
            ["mfb:email=a@b.c", "mfb:name=Work"],
        )

        assert ok is True
        args = mock_run.call_args.args[0]
        assert args == ["tag", "--add", "mfb:email=a@b.c", "--add", "mfb:name=Work", "ab12", "cd34"]

    @patch("mailfallback.services.restic_service._run_restic")
    def test_add_tags_failure_returns_false(self, mock_run, s3_destination):
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="boom")

        assert restic_service.add_tags(s3_destination, "acc-id", ["ab12"], ["t"]) is False

    @patch("mailfallback.services.restic_service._run_restic")
    def test_add_tags_empty_ids_is_noop(self, mock_run, s3_destination):
        from mailfallback.services.restic_service import add_tags

        assert add_tags(s3_destination, "acc-id", [], ["t"]) is True
        mock_run.assert_not_called()
