from collections import Counter
from datetime import datetime, timedelta, timezone
import logging
import os
import socket
from uuid import uuid4

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.config import settings
from app.database import SessionLocal
from app.models import BulkAction, BulkActionBatch, BulkActionLog
from app.processors import registry
from app.processors.base import ActionProcessor
from app.rate_limit import reserve_account_events

logger = logging.getLogger("bulk-action-engine")


def worker_identity() -> str:
    return settings.worker_id or f"{socket.gethostname()}-{os.getpid()}"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def get_processor(entity: str, action: str) -> ActionProcessor:
    return registry.get(entity, action)


def action_statistics(session: Session, action: BulkAction) -> dict[str, int | float | str]:
    grouped = session.execute(
        select(BulkActionLog.status, func.count())
        .where(BulkActionLog.action_id == action.id)
        .group_by(BulkActionLog.status)
    ).all()
    counts = Counter({status: count for status, count in grouped})
    success = counts["SUCCESS"]
    failed = counts["FAILED"]
    skipped = counts["SKIPPED"]
    pending_logs = counts["PENDING"]
    processed = success + failed + skipped
    total = action.total
    in_progress = session.scalar(
        select(func.count())
        .select_from(BulkActionLog)
        .join(BulkActionBatch, BulkActionBatch.id == BulkActionLog.batch_id)
        .where(
            BulkActionLog.action_id == action.id,
            BulkActionLog.status == "PENDING",
            BulkActionBatch.status == "RUNNING",
        )
    ) or 0
    pending = max(total - processed - in_progress, 0)
    if action.selection_complete and total == 0:
        progress = 100.0
    else:
        progress = round((processed / total * 100) if total else 0.0, 2)
    elapsed = 0.0
    if action.started_at is not None:
        ended_at = action.completed_at or utcnow()
        started_at = action.started_at
        if started_at.tzinfo is None and ended_at.tzinfo is not None:
            started_at = started_at.replace(tzinfo=ended_at.tzinfo)
        elif started_at.tzinfo is not None and ended_at.tzinfo is None:
            ended_at = ended_at.replace(tzinfo=started_at.tzinfo)
        elapsed = max((ended_at - started_at).total_seconds(), 0.0)
    return {
        "action_id": action.id,
        "total": total,
        "processed": processed,
        "success": success,
        "failed": failed,
        "skipped": skipped,
        "pending": pending,
        "in_progress": in_progress,
        "staged": action.staged_total,
        "count_ready": action.selection_counted,
        "selection_complete": action.selection_complete,
        "completion_percentage": round(processed / total * 100, 2) if total else (100.0 if action.selection_complete else 0.0),
        "pending_percentage": round(pending / total * 100, 2) if total else 0.0,
        "in_progress_percentage": round(in_progress / total * 100, 2) if total else 0.0,
        "elapsed_seconds": round(elapsed, 2),
        "records_per_second": round(processed / elapsed, 2) if elapsed else 0.0,
        "progress": progress,
    }


def claim_action() -> tuple[str, str] | None:
    now = utcnow()
    owner = uuid4().hex
    with SessionLocal() as session, session.begin():
        action = session.scalar(
            select(BulkAction)
            .where(
                BulkAction.selection_complete.is_(False),
                BulkAction.status.in_(["SCHEDULED", "QUEUED", "RUNNING"]),
                or_(BulkAction.scheduled_at.is_(None), BulkAction.scheduled_at <= now),
                or_(BulkAction.orchestration_lease_until.is_(None), BulkAction.orchestration_lease_until <= now),
            )
            .order_by(BulkAction.created_at)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if action is None:
            return None
        if not action.selection_counted:
            processor = get_processor(action.entity, action.action)
            action.total = processor.count_matches(session, action.account_id, action.filters)
            action.selection_counted = True
        action.status = "RUNNING"
        action.started_at = action.started_at or now
        action.orchestration_owner = owner
        action.orchestration_lease_until = now + timedelta(seconds=settings.lease_seconds)
        action.error_message = None
        return action.id, owner


def stage_next_page(action_id: str, owner: str) -> bool:
    """Materialize one bounded page of matching IDs into a durable batch and logs."""
    now = utcnow()
    with SessionLocal() as session, session.begin():
        action = session.scalar(
            select(BulkAction).where(BulkAction.id == action_id).with_for_update()
        )
        if action is None or action.selection_complete or action.orchestration_owner != owner:
            return False

        processor = get_processor(action.entity, action.action)
        entity_ids = processor.select_ids(
            session,
            action.account_id,
            action.filters,
            action.cursor_after_entity_id,
            settings.selection_page_size,
        )
        if not entity_ids:
            action.selection_complete = True
            action.total = action.staged_total
            action.orchestration_owner = None
            action.orchestration_lease_until = None
            return True

        last_sequence = session.scalar(
            select(func.coalesce(func.max(BulkActionBatch.sequence), 0)).where(
                BulkActionBatch.action_id == action.id
            )
        )
        for offset in range(0, len(entity_ids), settings.batch_size):
            batch_ids = entity_ids[offset : offset + settings.batch_size]
            sequence = last_sequence + offset // settings.batch_size + 1
            batch_id = f"{action.id}-B{sequence:06d}"
            session.add(
                BulkActionBatch(
                    id=batch_id,
                    action_id=action.id,
                    sequence=sequence,
                    item_count=len(batch_ids),
                    status="QUEUED",
                    available_at=now,
                )
            )
            session.add_all(
                BulkActionLog(
                    action_id=action.id,
                    batch_id=batch_id,
                    account_id=action.account_id,
                    entity_id=entity_id,
                    status="PENDING",
                )
                for entity_id in batch_ids
            )
        action.staged_total += len(entity_ids)
        action.cursor_after_entity_id = entity_ids[-1]
        action.orchestration_owner = None
        action.orchestration_lease_until = None
        return True


def claim_batch() -> tuple[str, str] | None:
    now = utcnow()
    owner = uuid4().hex
    claimed: tuple[str, str] | None = None
    terminal_action_id: str | None = None
    with SessionLocal() as session, session.begin():
        batch = session.scalar(
            select(BulkActionBatch)
            .join(BulkAction, BulkAction.id == BulkActionBatch.action_id)
            .where(
                or_(BulkAction.scheduled_at.is_(None), BulkAction.scheduled_at <= now),
                or_(
                    (BulkActionBatch.status == "QUEUED") & (BulkActionBatch.available_at <= now),
                    (BulkActionBatch.status == "RUNNING") & (BulkActionBatch.lease_until <= now),
                )
            )
            .order_by(BulkActionBatch.created_at, BulkActionBatch.sequence)
            .limit(1)
            .with_for_update(skip_locked=True, of=BulkActionBatch)
        )
        if batch is not None:
            if batch.attempt_count >= settings.max_batch_attempts:
                batch.status = "FAILED"
                batch.completed_at = now
                batch.lease_owner = None
                batch.lease_until = None
                batch.last_error = batch.last_error or "Worker lease expired after the maximum attempts"
                terminal_action_id = batch.action_id
            else:
                batch.status = "RUNNING"
                batch.attempt_count += 1
                batch.lease_owner = owner
                batch.worker_id = worker_identity()
                batch.lease_until = now + timedelta(seconds=settings.lease_seconds)
                batch.last_error = None
                claimed = batch.id, owner
    if terminal_action_id:
        finalize_action(terminal_action_id)
    return claimed


def reserve_batch_rate_limit(batch_id: str, owner: str) -> bool:
    """Reserve account-wide event capacity before processing a claimed batch."""
    with SessionLocal() as session, session.begin():
        batch = session.scalar(
            select(BulkActionBatch).where(BulkActionBatch.id == batch_id).with_for_update()
        )
        if batch is None or batch.status != "RUNNING" or batch.lease_owner != owner:
            return False
        action = session.get(BulkAction, batch.action_id)
        if action is None:
            return False

        pending_count = session.scalar(
            select(func.count())
            .select_from(BulkActionLog)
            .where(BulkActionLog.batch_id == batch.id, BulkActionLog.status == "PENDING")
        ) or 0
        next_window = reserve_account_events(session, action.account_id, pending_count)
        if next_window is None:
            return True

        # Rate deferrals are not worker failures and must not consume retry attempts.
        batch.status = "QUEUED"
        batch.available_at = next_window
        batch.attempt_count = max(batch.attempt_count - 1, 0)
        batch.lease_owner = None
        batch.lease_until = None
        return False


def process_batch(batch_id: str, owner: str) -> None:
    """Apply one bounded batch and write its entity results in the same transaction."""
    outcomes: Counter[str] = Counter()
    action_id: str | None = None
    batch_size = 0
    worker_id = worker_identity()
    with SessionLocal() as session, session.begin():
        batch = session.scalar(
            select(BulkActionBatch).where(BulkActionBatch.id == batch_id).with_for_update()
        )
        if batch is None or batch.status != "RUNNING" or batch.lease_owner != owner:
            return
        batch.lease_until = utcnow() + timedelta(seconds=settings.lease_seconds)
        action = session.get(BulkAction, batch.action_id)
        if action is None:
            raise RuntimeError(f"Bulk action {batch.action_id} does not exist")
        action_id = action.id

        logs = list(
            session.scalars(
                select(BulkActionLog)
                .where(BulkActionLog.batch_id == batch.id, BulkActionLog.status == "PENDING")
                .order_by(BulkActionLog.entity_id)
                .with_for_update()
            )
        )
        if not logs:
            batch.status = "COMPLETED"
            batch.completed_at = utcnow()
            batch.lease_owner = None
            batch.lease_until = None
        else:
            batch_size = len(logs)
            entity_ids = [log.entity_id for log in logs]
            processor = get_processor(action.entity, action.action)
            entities = processor.load_for_update(session, entity_ids)
            duplicate_reasons = processor.duplicate_reasons(session, list(entities.values()), action.account_id)
            completed_at = utcnow()
            for log in logs:
                log.attempt_count += 1
                entity = entities.get(log.entity_id)
                if entity is None or not processor.belongs_to_account(entity, action.account_id):
                    log.status = "FAILED"
                    log.error_message = f"{action.entity} no longer exists in the selected account"
                elif (duplicate_reason := duplicate_reasons.get(log.entity_id)):
                    log.status = "SKIPPED"
                    log.error_message = duplicate_reason
                elif not processor.matches(entity, action.account_id, action.filters):
                    log.status = "SKIPPED"
                    log.error_message = f"{action.entity} no longer matches the action filter"
                else:
                    try:
                        with session.begin_nested():
                            processor.apply(entity, action.operation_data)
                    except Exception as exc:  # A record-level failure must not stop the batch.
                        log.status = "FAILED"
                        log.error_message = str(exc)[:2000]
                    else:
                        log.status = "SUCCESS"
                        log.error_message = None
                log.processed_at = completed_at
                outcomes[log.status] += 1

            batch.status = "COMPLETED"
            batch.completed_at = completed_at
            batch.lease_owner = None
            batch.lease_until = None

    logger.info(
        "batch completed worker_id=%s action_id=%s batch_id=%s entities=%d success=%d failed=%d skipped=%d",
        worker_id,
        action_id,
        batch_id,
        batch_size,
        outcomes["SUCCESS"],
        outcomes["FAILED"],
        outcomes["SKIPPED"],
    )


def handle_batch_error(batch_id: str, owner: str, error: Exception) -> None:
    now = utcnow()
    action_id: str | None = None
    with SessionLocal() as session, session.begin():
        batch = session.scalar(
            select(BulkActionBatch).where(BulkActionBatch.id == batch_id).with_for_update()
        )
        if batch is None or batch.lease_owner != owner:
            return
        action_id = batch.action_id
        batch.last_error = str(error)[:2000]
        batch.lease_owner = None
        batch.lease_until = None
        if batch.attempt_count >= settings.max_batch_attempts:
            batch.status = "FAILED"
            batch.completed_at = now
        else:
            batch.status = "QUEUED"
            delay = min(2 ** max(batch.attempt_count - 1, 0), 60)
            batch.available_at = now + timedelta(seconds=delay)
    if action_id:
        finalize_action(action_id)


def finalize_action(action_id: str) -> None:
    now = utcnow()
    with SessionLocal() as session, session.begin():
        action = session.scalar(select(BulkAction).where(BulkAction.id == action_id).with_for_update())
        if action is None or not action.selection_complete:
            return
        active_count = session.scalar(
            select(func.count()).select_from(BulkActionBatch).where(
                BulkActionBatch.action_id == action.id,
                BulkActionBatch.status.in_(["QUEUED", "RUNNING"]),
            )
        )
        if active_count:
            return
        failed_count = session.scalar(
            select(func.count()).select_from(BulkActionBatch).where(
                BulkActionBatch.action_id == action.id, BulkActionBatch.status == "FAILED"
            )
        )
        action.status = "FAILED" if failed_count else "COMPLETED"
        action.completed_at = now
        action.error_message = "One or more batches exhausted infrastructure retries" if failed_count else None


def run_once() -> bool:
    """Advance selection and process one batch; return whether work was found."""
    work_done = False
    action_claim = claim_action()
    if action_claim:
        action_id, owner = action_claim
        try:
            stage_next_page(action_id, owner)
            finalize_action(action_id)
        except Exception as exc:
            with SessionLocal() as session, session.begin():
                action = session.scalar(
                    select(BulkAction).where(BulkAction.id == action_id).with_for_update()
                )
                if action and action.orchestration_owner == owner:
                    action.orchestration_owner = None
                    action.orchestration_lease_until = utcnow() + timedelta(seconds=5)
                    action.error_message = str(exc)[:2000]
        work_done = True

    batch_claim = claim_batch()
    if batch_claim:
        batch_id, owner = batch_claim
        try:
            if reserve_batch_rate_limit(batch_id, owner):
                process_batch(batch_id, owner)
        except Exception as exc:
            handle_batch_error(batch_id, owner, exc)
        with SessionLocal() as session:
            batch = session.get(BulkActionBatch, batch_id)
            if batch:
                finalize_action(batch.action_id)
        work_done = True
    return work_done


def retry_action(action_id: str) -> tuple[int, int] | None:
    now = utcnow()
    with SessionLocal() as session, session.begin():
        action = session.scalar(select(BulkAction).where(BulkAction.id == action_id).with_for_update())
        if action is None:
            return None
        retryable_batch_ids = list(
            session.scalars(
                select(BulkActionBatch.id)
                .join(BulkActionLog, BulkActionLog.batch_id == BulkActionBatch.id)
                .where(
                    BulkActionBatch.action_id == action.id,
                    or_(
                        BulkActionBatch.status == "FAILED",
                        (BulkActionBatch.status == "COMPLETED") & (BulkActionLog.status == "FAILED"),
                    ),
                )
                .distinct()
            )
        )
        if not retryable_batch_ids:
            return 0, 0
        logs = list(
            session.scalars(
                select(BulkActionLog)
                .where(
                    BulkActionLog.batch_id.in_(retryable_batch_ids),
                    or_(
                        BulkActionBatch.status == "FAILED",
                        (BulkActionBatch.status == "COMPLETED") & (BulkActionLog.status == "FAILED"),
                    ),
                )
                .join(BulkActionBatch, BulkActionBatch.id == BulkActionLog.batch_id)
                .with_for_update()
            )
        )
        for log in logs:
            log.status = "PENDING"
            log.error_message = None
            log.processed_at = None
        batches = list(
            session.scalars(
                select(BulkActionBatch)
                .where(BulkActionBatch.id.in_(retryable_batch_ids))
                .with_for_update()
            )
        )
        for batch in batches:
            batch.status = "QUEUED"
            batch.attempt_count = 0
            batch.available_at = now
            batch.lease_owner = None
            batch.lease_until = None
            batch.completed_at = None
            batch.last_error = None
        action.status = "RUNNING"
        action.completed_at = None
        action.error_message = None
        return len(batches), len(logs)
