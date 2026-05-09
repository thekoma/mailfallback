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
