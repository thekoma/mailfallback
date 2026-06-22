from sqlalchemy import BigInteger

from mailfallback.models import Account


def test_maildir_size_bytes_is_bigint():
    """maildir_size_bytes stores raw byte totals; a mailbox can exceed the
    PostgreSQL INTEGER max (~2.1 GB), so the column must be BigInteger.
    Regression: a ~15 GB Gmail account overflowed INTEGER, failing the
    collect_account_stats UPDATE with NumericValueOutOfRange — no stats ever
    persisted (2026-06-22)."""
    col = Account.__table__.c.maildir_size_bytes
    assert isinstance(col.type, BigInteger), f"expected BigInteger, got {col.type!r}"
