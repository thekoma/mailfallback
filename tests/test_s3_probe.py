"""Tests for s3_probe — boto3 mocked, no network."""

import os
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

from mailfallback.config import settings
from mailfallback.models import BackendType, Repository
from mailfallback.security import encrypt_credentials
from mailfallback.services import s3_probe


def _enc(value: str) -> str:
    return encrypt_credentials(value, settings.secret_key)


@pytest.fixture
def s3_destination():
    return Repository(
        name="probe-s3",
        backend_type=BackendType.s3,
        s3_endpoint=_enc("https://s3.example.com"),
        s3_bucket=_enc("mfb-bucket"),
        s3_access_key=_enc("AKIA123"),
        s3_secret_key=_enc("sekrit"),
        restic_password=_enc("resticpass"),
        insecure_tls=False,
    )


@pytest.fixture
def local_destination(tmp_path):
    return Repository(
        name="probe-local",
        backend_type=BackendType.local,
        local_path=_enc(str(tmp_path / "repo")),
        restic_password=_enc("resticpass"),
    )


class TestProbeS3:
    @patch("mailfallback.services.s3_probe.boto3")
    def test_success_puts_and_deletes_probe_object(self, mock_boto3, s3_destination):
        client = MagicMock()
        client.list_objects_v2.return_value = {"Contents": []}
        mock_boto3.client.return_value = client

        result = s3_probe.probe(s3_destination)

        assert result["ok"] is True
        assert client.put_object.call_count == 1
        put_kwargs = client.put_object.call_args.kwargs
        assert put_kwargs["Bucket"] == "mfb-bucket"
        assert put_kwargs["Key"].startswith(".mfb-probe-")
        delete_kwargs = client.delete_object.call_args.kwargs
        assert delete_kwargs["Key"] == put_kwargs["Key"]

    @patch("mailfallback.services.s3_probe.boto3")
    def test_failure_returns_error(self, mock_boto3, s3_destination):
        client = MagicMock()
        client.put_object.side_effect = ClientError(
            {"Error": {"Code": "AccessDenied", "Message": "denied"}}, "PutObject"
        )
        mock_boto3.client.return_value = client

        result = s3_probe.probe(s3_destination)

        assert result["ok"] is False
        assert "AccessDenied" in result["error"]

    @patch("mailfallback.services.s3_probe.boto3")
    def test_success_cleans_legacy_junk(self, mock_boto3, s3_destination):
        client = MagicMock()
        client.list_objects_v2.return_value = {
            "Contents": [{"Key": "__mfb_connection_test__/config"}]
        }
        mock_boto3.client.return_value = client

        result = s3_probe.probe(s3_destination)

        assert result["ok"] is True
        client.delete_objects.assert_called_once()
        deleted = client.delete_objects.call_args.kwargs["Delete"]["Objects"]
        assert deleted == [{"Key": "__mfb_connection_test__/config"}]

    @patch("mailfallback.services.s3_probe.boto3")
    def test_insecure_tls_disables_verify(self, mock_boto3, s3_destination):
        s3_destination.insecure_tls = True
        client = MagicMock()
        client.list_objects_v2.return_value = {"Contents": []}
        mock_boto3.client.return_value = client

        s3_probe.probe(s3_destination)

        assert mock_boto3.client.call_args.kwargs["verify"] is False


class TestProbeLocal:
    def test_success_creates_and_removes_probe_file(self, local_destination, tmp_path):
        result = s3_probe.probe(local_destination)

        assert result["ok"] is True
        repo_dir = tmp_path / "repo"
        assert repo_dir.is_dir()
        assert not any(f.name.startswith(".mfb-probe-") for f in repo_dir.iterdir())

    def test_unwritable_path_returns_error(self, tmp_path):
        target = tmp_path / "ro"
        target.mkdir()
        os.chmod(target, 0o500)
        dest = Repository(
            name="probe-ro",
            backend_type=BackendType.local,
            local_path=_enc(str(target)),
            restic_password=_enc("x"),
        )
        try:
            result = s3_probe.probe(dest)
        finally:
            os.chmod(target, 0o700)

        assert result["ok"] is False
        assert result["error"]
