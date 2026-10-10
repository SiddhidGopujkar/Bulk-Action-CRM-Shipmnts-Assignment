"""Claim one batch, shorten its lease, and exit before processing to demo recovery."""

import argparse
import os
from datetime import timedelta

from sqlalchemy import select

from app.database import SessionLocal, init_db
from app.engine import claim_action, claim_batch, stage_next_page, utcnow
from app.models import BulkActionBatch


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lease-seconds", type=int, default=10)
    args = parser.parse_args()
    if not 1 <= args.lease_seconds <= 60:
        parser.error("--lease-seconds must be between 1 and 60 for a short recovery demo")

    init_db()
    action_claim = claim_action()
    if action_claim:
        action_id, owner = action_claim
        stage_next_page(action_id, owner)

    batch_claim = claim_batch()
    if batch_claim is None:
        raise SystemExit("No queued batch was available; submit an action first and keep healthy workers stopped")

    batch_id, owner = batch_claim
    with SessionLocal() as session, session.begin():
        batch = session.scalar(
            select(BulkActionBatch).where(BulkActionBatch.id == batch_id).with_for_update()
        )
        if batch is None or batch.lease_owner != owner:
            raise SystemExit("Claimed batch lease changed before the failure simulation")
        batch.lease_until = utcnow() + timedelta(seconds=args.lease_seconds)

    print(
        f"Simulating worker crash after claim: batch={batch_id}, "
        f"recovery_after_seconds={args.lease_seconds}; process exits with code 91",
        flush=True,
    )
    os._exit(91)


if __name__ == "__main__":
    main()
