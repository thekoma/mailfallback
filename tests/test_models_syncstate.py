from mailfallback.models import SyncState


def test_needs_reauth_member():
    assert SyncState.needs_reauth.value == "needs_reauth"
    assert SyncState("needs_reauth") is SyncState.needs_reauth
