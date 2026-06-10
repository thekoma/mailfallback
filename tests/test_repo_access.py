"""Repository access control: grants service, admin UI, enforcement."""

from mailfallback.models import Account, BackupPolicy, Repository, UserRole
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


def _mk_account_owned(db_session, default_store, owner, name="acc-e", path="/data/m/acc-e"):
    acc = Account(name=name, imap_host="h", maildir_path=path, store_id=default_store.id)
    db_session.add(acc)
    db_session.flush()
    acc.owners.append(owner)
    db_session.commit()
    return acc


class TestConfigureEnforcement:
    def test_non_admin_rejected_on_non_allowed_repo(self, client, db_session, default_store):
        owner = create_user(db_session, "own1", "pass", UserRole.user, store_id=default_store.id)
        client.post("/api/auth/login", json={"username": "own1", "password": "pass"})
        acc = _mk_account_owned(db_session, default_store, owner, name="a1", path="/data/m/a1")
        repo = _mk_repo(db_session, "r-deny")

        resp = client.post(
            f"/accounts/{acc.id}/backup/configure",
            data={"destination_id": repo.id, "schedule": "0 2 * * *"},
            follow_redirects=False,
        )

        assert resp.status_code == 303
        assert db_session.query(BackupPolicy).count() == 0

    def test_non_admin_allowed_repo_accepted(self, client, db_session, default_store):
        owner = create_user(db_session, "own2", "pass", UserRole.user, store_id=default_store.id)
        client.post("/api/auth/login", json={"username": "own2", "password": "pass"})
        acc = _mk_account_owned(db_session, default_store, owner, name="a2", path="/data/m/a2")
        repo = _mk_repo(db_session, "r-allow")
        set_allowed_repositories(db_session, owner.id, [repo.id])

        resp = client.post(
            f"/accounts/{acc.id}/backup/configure",
            data={"destination_id": repo.id, "schedule": "0 2 * * *"},
            follow_redirects=False,
        )

        assert resp.status_code == 303
        assert db_session.query(BackupPolicy).count() == 1

    def test_admin_bypasses(self, client, db_session, default_store):
        admin = _login_admin(client, db_session, default_store)
        acc = _mk_account_owned(db_session, default_store, admin, name="a3", path="/data/m/a3")
        repo = _mk_repo(db_session, "r-admin")

        resp = client.post(
            f"/accounts/{acc.id}/backup/configure",
            data={"destination_id": repo.id, "schedule": "0 2 * * *"},
            follow_redirects=False,
        )

        assert resp.status_code == 303
        assert db_session.query(BackupPolicy).count() == 1

    def test_grandfathered_current_repo_resubmit_passes(self, client, db_session, default_store):
        owner = create_user(db_session, "own3", "pass", UserRole.user, store_id=default_store.id)
        client.post("/api/auth/login", json={"username": "own3", "password": "pass"})
        acc = _mk_account_owned(db_session, default_store, owner, name="a4", path="/data/m/a4")
        legacy = _mk_repo(db_session, "r-legacy")
        db_session.add(
            BackupPolicy(account_id=acc.id, destination_id=legacy.id, schedule="0 2 * * *")
        )
        db_session.commit()

        resp = client.post(
            f"/accounts/{acc.id}/backup/configure",
            data={"destination_id": legacy.id, "schedule": "0 3 * * *"},
            follow_redirects=False,
        )

        assert resp.status_code == 303
        db_session.expire_all()
        assert db_session.query(BackupPolicy).one().schedule == "0 3 * * *"

    def test_grandfathered_switch_to_other_non_allowed_rejected(
        self, client, db_session, default_store
    ):
        owner = create_user(db_session, "own4", "pass", UserRole.user, store_id=default_store.id)
        client.post("/api/auth/login", json={"username": "own4", "password": "pass"})
        acc = _mk_account_owned(db_session, default_store, owner, name="a5", path="/data/m/a5")
        legacy = _mk_repo(db_session, "r-legacy2")
        other = _mk_repo(db_session, "r-other")
        db_session.add(
            BackupPolicy(account_id=acc.id, destination_id=legacy.id, schedule="0 2 * * *")
        )
        db_session.commit()

        resp = client.post(
            f"/accounts/{acc.id}/backup/configure",
            data={"destination_id": other.id, "schedule": "0 2 * * *"},
            follow_redirects=False,
        )

        assert resp.status_code == 303
        db_session.expire_all()
        assert db_session.query(BackupPolicy).one().destination_id == legacy.id


class TestAccountPageFilter:
    def test_non_admin_sees_only_allowed(self, client, db_session, default_store):
        owner = create_user(db_session, "own5", "pass", UserRole.user, store_id=default_store.id)
        client.post("/api/auth/login", json={"username": "own5", "password": "pass"})
        acc = _mk_account_owned(db_session, default_store, owner, name="a6", path="/data/m/a6")
        allowed = _mk_repo(db_session, "r-visible")
        _mk_repo(db_session, "r-hidden")
        set_allowed_repositories(db_session, owner.id, [allowed.id])

        resp = client.get(f"/accounts/{acc.id}")

        assert resp.status_code == 200
        assert "r-visible" in resp.text
        assert "r-hidden" not in resp.text

    def test_admin_sees_all(self, client, db_session, default_store):
        admin = _login_admin(client, db_session, default_store)
        acc = _mk_account_owned(db_session, default_store, admin, name="a7", path="/data/m/a7")
        _mk_repo(db_session, "r-one")
        _mk_repo(db_session, "r-two")

        resp = client.get(f"/accounts/{acc.id}")

        assert "r-one" in resp.text and "r-two" in resp.text

    def test_grandfathered_current_marked(self, client, db_session, default_store):
        owner = create_user(db_session, "own6", "pass", UserRole.user, store_id=default_store.id)
        client.post("/api/auth/login", json={"username": "own6", "password": "pass"})
        acc = _mk_account_owned(db_session, default_store, owner, name="a8", path="/data/m/a8")
        legacy = _mk_repo(db_session, "r-legacy3")
        db_session.add(
            BackupPolicy(account_id=acc.id, destination_id=legacy.id, schedule="0 2 * * *")
        )
        db_session.commit()

        resp = client.get(f"/accounts/{acc.id}")

        assert "r-legacy3" in resp.text
        assert "not in your allowed set" in resp.text

    def test_non_admin_no_grants_sees_role_aware_empty_state(
        self, client, db_session, default_store
    ):
        owner = create_user(db_session, "own7", "pass", UserRole.user, store_id=default_store.id)
        client.post("/api/auth/login", json={"username": "own7", "password": "pass"})
        acc = _mk_account_owned(db_session, default_store, owner, name="a9", path="/data/m/a9")
        _mk_repo(db_session, "r-existing")

        resp = client.get(f"/accounts/{acc.id}")

        assert resp.status_code == 200
        assert "ask an administrator" in resp.text
        assert "No repositories configured" not in resp.text

    def test_admin_no_repos_sees_admin_empty_state(self, client, db_session, default_store):
        admin = _login_admin(client, db_session, default_store)
        acc = _mk_account_owned(db_session, default_store, admin, name="a10", path="/data/m/a10")

        resp = client.get(f"/accounts/{acc.id}")

        assert resp.status_code == 200
        assert "No repositories configured" in resp.text
        assert "ask an administrator" not in resp.text
