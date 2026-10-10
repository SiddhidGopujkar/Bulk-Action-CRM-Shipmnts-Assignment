"""Verify persisted batch sizes, entity outcomes, and applied contact updates."""

import argparse
from collections import Counter

from sqlalchemy import case, func, or_, select

from app.config import settings
from app.database import SessionLocal, init_db
from app.engine import action_statistics
from app.models import BulkAction, BulkActionBatch, BulkActionLog, Contact


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action_id", help="Bulk action ID to verify")
    args = parser.parse_args()
    problems: list[str] = []

    init_db()
    with SessionLocal() as session:
        action = session.get(BulkAction, args.action_id)
        if action is None:
            raise SystemExit(f"Action {args.action_id} was not found in the configured database")
        stats = action_statistics(session, action)
        log_total = session.scalar(
            select(func.count()).select_from(BulkActionLog).where(BulkActionLog.action_id == action.id)
        ) or 0
        if action.selection_complete and log_total != action.total:
            problems.append(f"action total={action.total}, but entity logs={log_total}")

        state_totals = Counter(dict(session.execute(
            select(BulkActionLog.status, func.count())
            .where(BulkActionLog.action_id == action.id)
            .group_by(BulkActionLog.status)
        ).all()))
        if sum(state_totals.values()) != log_total:
            problems.append("entity result totals do not add up to the log row count")
        if action.status == "COMPLETED" and state_totals["PENDING"]:
            problems.append(f"completed action still has {state_totals['PENDING']} pending entities")

        batch_rows = session.execute(
            select(
                BulkActionBatch.id,
                BulkActionBatch.sequence,
                BulkActionBatch.item_count,
                BulkActionBatch.status,
                BulkActionBatch.worker_id,
                func.count(BulkActionLog.id).label("actual_count"),
                func.sum(case((BulkActionLog.status == "SUCCESS", 1), else_=0)).label("success"),
                func.sum(case((BulkActionLog.status == "FAILED", 1), else_=0)).label("failed"),
                func.sum(case((BulkActionLog.status == "SKIPPED", 1), else_=0)).label("skipped"),
                func.sum(case((BulkActionLog.status == "PENDING", 1), else_=0)).label("pending"),
            )
            .outerjoin(BulkActionLog, BulkActionLog.batch_id == BulkActionBatch.id)
            .where(BulkActionBatch.action_id == action.id)
            .group_by(BulkActionBatch.id)
            .order_by(BulkActionBatch.sequence)
        ).all()
        workers: Counter[str] = Counter()
        actual_total = 0
        for row in batch_rows:
            actual_total += row.actual_count
            if row.item_count != row.actual_count:
                problems.append(f"{row.id} item_count={row.item_count}, logs={row.actual_count}")
            if row.item_count > settings.batch_size:
                problems.append(f"{row.id} has {row.item_count} items, above configured batch size {settings.batch_size}")
            if row.status == "COMPLETED" and row.pending:
                problems.append(f"{row.id} is completed but still has {row.pending} pending logs")
            if row.worker_id:
                workers[row.worker_id] += 1
        if actual_total != log_total:
            problems.append(f"batch item counts={actual_total}, but entity logs={log_total}")
        if action.status == "COMPLETED" and any(row.status != "COMPLETED" for row in batch_rows):
            problems.append("completed action contains a batch that is not completed")

        if action.entity == "contact" and action.action == "update" and action.operation_data:
            mis_scoped_count = session.scalar(
                select(func.count())
                .select_from(BulkActionLog)
                .outerjoin(Contact, Contact.id == BulkActionLog.entity_id)
                .where(
                    BulkActionLog.action_id == action.id,
                    BulkActionLog.status == "SUCCESS",
                    or_(Contact.id.is_(None), Contact.account_id != action.account_id),
                )
            ) or 0
            if mis_scoped_count:
                problems.append(f"{mis_scoped_count} SUCCESS logs do not point to a contact in the action account")

            mismatch_conditions = or_(
                *(getattr(Contact, field) != value for field, value in action.operation_data.items())
            )
            mismatch_count = session.scalar(
                select(func.count())
                .select_from(BulkActionLog)
                .join(Contact, Contact.id == BulkActionLog.entity_id)
                .where(
                    BulkActionLog.action_id == action.id,
                    BulkActionLog.status == "SUCCESS",
                    Contact.account_id == action.account_id,
                    mismatch_conditions,
                )
            ) or 0
            if mismatch_count:
                problems.append(f"{mismatch_count} SUCCESS contacts do not contain the requested update")

        print(f"Action {action.id}: {action.status}; {stats['processed']}/{stats['total']} processed")
        print(
            f"Results: success={stats['success']} failed={stats['failed']} "
            f"skipped={stats['skipped']} pending={stats['pending']}"
        )
        print(f"Batches: {len(batch_rows)}; configured maximum size={settings.batch_size}")
        print("Worker distribution:")
        if workers:
            for worker_id, count in sorted(workers.items()):
                print(f"  {worker_id}: {count} batches")
        else:
            print("  No worker IDs recorded (batches may predate worker tracking)")

    if problems:
        print("VERIFICATION FAILED:")
        for problem in problems:
            print(f"  - {problem}")
        raise SystemExit(1)
    print("VERIFICATION PASSED: batch totals, entity results, and successful contact updates are consistent.")


if __name__ == "__main__":
    main()
