"""Repository access control: grants service, admin UI, enforcement."""

from mailfallback.models import Repository, UserRole
from mailfallback.services.user_service import create_user, set_allowed_repositories


def _login_admin(client, db_session, default_store):
    user = create_user(db_session, "admin", "pass", UserRole.admin, store_id=default_store.id)
    client.post("/api/auth/login", json={"username": "admin", "password": "pass"})
    return user


def _mk_repo(db_session, name="repo-a"):
    r = Repository(name=name, backend_type="s3", restic_password="enc")  # pragma: allowlist secret
    db_session.add(r)
    db_session.commit()
    return r


class TestSetAllowedRepositories:
    def test_sets_and_replaces(self, db_session, default_store):
        user = create_user(db_session, "u1", "pass", UserRole.user, store_id=default_store.id)
        r1, r2 = _mk_repo(db_session, "r1"), _mk_repo(db_session, "r2")

        set_allowed_repositories(db_session, user.id, [r1.id])
        assert [r.id for r in user.allowed_repositories] == [r1.id]

        set_allowed_repositories(db_session, user.id, [r2.id])
        db_session.refresh(user)
        assert [r.id for r in user.allowed_repositories] == [r2.id]

    def test_unknown_ids_ignored(self, db_session, default_store):
        user = create_user(db_session, "u2", "pass", UserRole.user, store_id=default_store.id)
        r1 = _mk_repo(db_session, "r3")

        set_allowed_repositories(db_session, user.id, [r1.id, "nonexistent"])

        assert [r.id for r in user.allowed_repositories] == [r1.id]

    def test_unknown_user_returns_error(self, db_session):
        assert set_allowed_repositories(db_session, "ghost", []) is not None


class TestAllowedRepositoriesRoute:
    def test_admin_sets_grants(self, client, db_session, default_store):
        _login_admin(client, db_session, default_store)
        target = create_user(db_session, "u4", "pass", UserRole.user, store_id=default_store.id)
        r1 = _mk_repo(db_session, "r4")

        resp = client.post(
            f"/admin/users/{target.id}/allowed-repositories",
            data={"repository_ids": [r1.id]},
            follow_redirects=False,
        )

        assert resp.status_code == 303
        db_session.refresh(target)
        assert [r.id for r in target.allowed_repositories] == [r1.id]

    def test_non_admin_rejected(self, client, db_session, default_store):
        create_user(db_session, "u5", "pass", UserRole.user, store_id=default_store.id)
        client.post("/api/auth/login", json={"username": "u5", "password": "pass"})
        target = create_user(db_session, "u6", "pass", UserRole.user, store_id=default_store.id)

        resp = client.post(
            f"/admin/users/{target.id}/allowed-repositories",
            data={"repository_ids": []},
            follow_redirects=False,
        )

        assert resp.status_code == 303
        assert target.allowed_repositories == []

    def test_users_page_renders_repo_checkboxes(self, client, db_session, default_store):
        _login_admin(client, db_session, default_store)
        _mk_repo(db_session, "repo-visible")

        resp = client.get("/admin/users")

        assert resp.status_code == 200
        assert "Allowed repositories" in resp.text
        assert "repo-visible" in resp.text
