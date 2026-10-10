"""Initialize app tables if needed, then print contacts, actions, batches, and results."""

import argparse
from collections import Counter

from sqlalchemy import func, select

from app.database import SessionLocal, init_db
from app.engine import action_statistics
from app.models import BulkAction, BulkActionBatch, BulkActionLog, Contact


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--account-id", default="A001")
    parser.add_argument("--action-id")
    parser.add_argument("--limit", type=int, default=20)
    args = parser.parse_args()

    init_db()
    with SessionLocal() as session:
        contact_counts = session.execute(
            select(Contact.status, func.count())
            .where(Contact.account_id == args.account_id)
            .group_by(Contact.status)
        ).all()
        print(f"Contacts for account {args.account_id}:")
        if contact_counts:
            print("  " + "  ".join(f"{state}={count}" for state, count in contact_counts))
        else:
            print("  (none)")

        contact_rows = session.scalars(
            select(Contact)
            .where(Contact.account_id == args.account_id)
            .order_by(Contact.id.desc())
            .limit(max(1, min(args.limit, 100)))
        ).all()
        print("Recent contact rows: id | status | updated_at")
        for contact in contact_rows:
            print(f"  {contact.id} | {contact.status} | {contact.updated_at}")

        actions = session.scalars(
            select(BulkAction).order_by(BulkAction.created_at.desc()).limit(20)
        ).all()
        print("\nRecent bulk actions: actionId | status | total | selection_complete")
        for action in actions:
            print(f"  {action.id} | {action.status} | {action.total} | {action.selection_complete}")

        action_id = args.action_id or (actions[0].id if actions else None)
        action = session.get(BulkAction, action_id) if action_id else None
        if action is None:
            if args.action_id:
                raise SystemExit(f"Action {args.action_id} was not found")
            print("\nNo bulk action has been created yet.")
            return

        stats = action_statistics(session, action)
        print(f"\nSelected action {action.id} statistics:")
        print(
            "  "
            + "  ".join(
                f"{key}={stats[key]}"
                for key in ("total", "processed", "success", "failed", "skipped", "pending", "progress")
            )
        )

        batches = session.scalars(
            select(BulkActionBatch)
            .where(BulkActionBatch.action_id == action.id)
            .order_by(BulkActionBatch.sequence)
            .limit(max(1, min(args.limit, 500)))
        ).all()
        batch_counts = session.execute(
            select(BulkActionLog.batch_id, BulkActionLog.status, func.count())
            .where(BulkActionLog.batch_id.in_([batch.id for batch in batches]))
            .group_by(BulkActionLog.batch_id, BulkActionLog.status)
        ).all() if batches else []
        counts_by_batch: dict[str, Counter] = {}
        for batch_id, result_status, count in batch_counts:
            counts_by_batch.setdefault(batch_id, Counter())[result_status] = count

        print("Batch rows: batchId | state | items | workerId | success/failed/skipped/pending")
        for batch in batches:
            counts = counts_by_batch.get(batch.id, Counter())
            print(
                f"  {batch.id} | {batch.status} | {batch.item_count} | {batch.worker_id or '-'} | "
                f"{counts['SUCCESS']}/{counts['FAILED']}/{counts['SKIPPED']}/{counts['PENDING']}"
            )

        result_rows = session.scalars(
            select(BulkActionLog)
            .where(BulkActionLog.action_id == action.id)
            .order_by(BulkActionLog.id.desc())
            .limit(max(1, min(args.limit, 100)))
        ).all()
        print("Recent entity result rows: contactId | batchId | state | attempts | error")
        for item in result_rows:
            print(
                f"  {item.entity_id} | {item.batch_id} | {item.status} | "
                f"{item.attempt_count} | {item.error_message or '-'}"
            )


if __name__ == "__main__":
    main()
