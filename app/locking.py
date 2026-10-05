"""Row locks for multi-row writes (ARCHITECTURE.md 8.8): vehicle first, then driver, always in that order."""
from sqlalchemy import select

from .extensions import db


def begin_write():
    """Start the write on a fresh transaction.

    Authentication has already read from the database. Under REPEATABLE READ that read pins a snapshot, which
    would hide a trip another request commits while this one waits for the lock. Ending the transaction first
    means the checks that follow the lock see everything that was committed before it.
    """
    db.session.commit()


def lock_row(model, pk):
    """The row with `FOR UPDATE`, or None. Call begin_write() once before the first lock."""
    column = next(iter(model.__table__.primary_key.columns))
    return db.session.scalar(select(model).where(column == pk).with_for_update())
