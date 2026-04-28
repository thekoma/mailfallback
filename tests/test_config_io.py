# tests/test_config_io.py
from mailfallback.models import UserRole
from mailfallback.services.account_service import create_account
from mailfallback.services.user_service import create_user


def _login_admin(client, db_session):
    create_user(db_session, "admin", "pass", UserRole.admin)
    client.post("/api/auth/login", json={"username": "admin", "password": "pass"})


def test_export_config(client, db_session):
    _login_admin(client, db_session)
    create_account(db_session, "Gmail", "imap.gmail.com", 993, "app_password", "/data/gmail")

    resp = client.get("/api/config/export")
    assert resp.status_code == 200
    data = resp.json()
    assert "accounts" in data
    assert len(data["accounts"]) == 1
    assert data["accounts"][0]["name"] == "Gmail"
    assert "credentials" not in data["accounts"][0]


def test_import_config(client, db_session):
    _login_admin(client, db_session)
    payload = {
        "accounts": [
            {
                "name": "Imported",
                "imap_host": "imap.imported.com",
                "imap_port": 993,
                "auth_type": "app_password",
                "maildir_path": "/data/imported",
                "sync_schedule": "0 */6 * * *",
            }
        ]
    }
    resp = client.post("/api/config/import", json=payload)
    assert resp.status_code == 200
    assert resp.json()["imported"] == 1

    resp = client.get("/api/config/export")
    assert len(resp.json()["accounts"]) == 1


def test_export_requires_admin(client, db_session):
    create_user(db_session, "user1", "pass", UserRole.user)
    client.post("/api/auth/login", json={"username": "user1", "password": "pass"})
    resp = client.get("/api/config/export")
    assert resp.status_code == 403
