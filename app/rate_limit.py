"""PostgreSQL-coordinated per-account event rate limits."""

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.config import ACCOUNT_EVENTS_PER_MINUTE
from app.models import AccountRateWindow

EVENTS_PER_ACCOUNT_PER_MINUTE = ACCOUNT_EVENTS_PER_MINUTE


def current_window(now: datetime | None = None) -> datetime:
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return now.replace(second=0, microsecond=0)


def reserve_account_events(
    session: Session,
    account_id: str,
    event_count: int,
    now: datetime | None = None,
) -> datetime | None:
    """Reserve events in the current UTC minute; return next window if capped.

    Call inside a transaction. The row lock makes the cap account-wide across workers.
    """
    if event_count < 1:
        return None
    if event_count > EVENTS_PER_ACCOUNT_PER_MINUTE:
        raise ValueError("A single batch cannot exceed the account event limit")

    window_start = current_window(now)
    session.execute(
        insert(AccountRateWindow)
        .values(account_id=account_id, window_start=window_start, events_reserved=0)
        .on_conflict_do_nothing(index_elements=[AccountRateWindow.account_id])
    )
    row = session.scalar(
        select(AccountRateWindow)
        .where(AccountRateWindow.account_id == account_id)
        .with_for_update()
    )
    if row is None:
        raise RuntimeError(f"Could not lock rate-limit window for account {account_id}")

    if row.window_start != window_start:
        row.window_start = window_start
        row.events_reserved = 0

    if row.events_reserved + event_count > EVENTS_PER_ACCOUNT_PER_MINUTE:
        return window_start + timedelta(minutes=1)

    row.events_reserved += event_count
    return None
