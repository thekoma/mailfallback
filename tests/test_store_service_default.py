# tests/test_store_service_default.py
"""ensure_default_store: adopting the store that already sits at the bootstrap path.

Issue #238. The function only ever asked "is there a store flagged default?",
never "is there already a store at the path I am about to claim?" — so with a
non-default store occupying bootstrap_store_path it ran the insert anyway and
the unique constraint on MailStore.path turned a startup into an IntegrityError
inside the lifespan.
"""

from mailfallback.config import settings
from mailfallback.models import MailStore
from mailfallback.services.store_service import ensure_default_store


class TestEnsureDefaultStore:
    def test_creates_a_default_store_on_first_boot(self, db_session):
        store = ensure_default_store(db_session)

        assert store.is_default is True
        assert store.path == settings.bootstrap_store_path.rstrip("/")
        assert db_session.query(MailStore).count() == 1

    def test_returns_the_existing_default_untouched(self, db_session):
        existing = MailStore(name="primary", path="/srv/mail", is_default=True)
        db_session.add(existing)
        db_session.commit()

        store = ensure_default_store(db_session)

        assert store.id == existing.id
        assert db_session.query(MailStore).count() == 1

    def test_adopts_a_non_default_store_already_at_the_bootstrap_path(self, db_session):
        # The pre-state from the issue: nothing is flagged default, but the
        # bootstrap path is taken. Inserting would violate the unique
        # constraint on MailStore.path and take the whole app down.
        squatter = MailStore(
            name="legacy",
            path=settings.bootstrap_store_path.rstrip("/"),
            is_default=False,
        )
        db_session.add(squatter)
        db_session.commit()

        store = ensure_default_store(db_session)

        assert store.id == squatter.id
        assert store.is_default is True
        assert db_session.query(MailStore).count() == 1

    def test_adopts_even_when_the_stored_path_carries_a_trailing_slash(self, db_session):
        squatter = MailStore(
            name="legacy",
            path=settings.bootstrap_store_path.rstrip("/") + "/",
            is_default=False,
        )
        db_session.add(squatter)
        db_session.commit()

        store = ensure_default_store(db_session)

        assert store.id == squatter.id
        assert store.is_default is True
        assert db_session.query(MailStore).count() == 1
