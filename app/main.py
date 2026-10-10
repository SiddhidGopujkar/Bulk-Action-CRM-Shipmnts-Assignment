from contextlib import asynccontextmanager
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from fastapi import Depends, FastAPI, HTTPException, Query, Response, status
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import and_, func, select, text
from sqlalchemy.orm import Session

from app.database import engine, get_session, init_db
from app.engine import action_statistics, get_processor, retry_action
from app.models import Account, AccountRateWindow, BulkAction, BulkActionBatch, BulkActionLog, Contact
from app.rate_limit import EVENTS_PER_ACCOUNT_PER_MINUTE, current_window
from app.worker_control import supervisor
from app.schemas import (
    ActionStats,
    ActionSummary,
    AccountCreate,
    BatchPage,
    ContactBulkCreate,
    BulkActionCreate,
    LogPage,
    LogItem,
    RetryResponse,
)


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield
    supervisor.shutdown()


app = FastAPI(
    title="Shipmnts Bulk Action Platform",
    description=(
        "Asynchronous, batch-based CRM operations. The API persists action requests; "
        "independent workers perform selection and execution."
    ),
    version="1.0.0",
    lifespan=lifespan,
)
STATIC_DIR = Path(__file__).resolve().parent / "static"
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", include_in_schema=False)
def dashboard() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


def action_summary(session: Session, action: BulkAction) -> dict:
    stats = action_statistics(session, action)
    return {
        "id": action.id,
        "account_id": action.account_id,
        "entity": action.entity,
        "action": action.action,
        "status": action.status,
        "total": stats["total"],
        "processed": stats["processed"],
        "progress": stats["progress"],
        "created_at": action.created_at,
        "scheduled_at": action.scheduled_at,
        "started_at": action.started_at,
        "completed_at": action.completed_at,
    }


@app.get("/health", tags=["system"])
def health(session: Session = Depends(get_session)) -> dict[str, str]:
    session.execute(text("SELECT 1"))
    return {"status": "ok"}


@app.post("/accounts", status_code=status.HTTP_201_CREATED, tags=["accounts"])
def create_account(request: AccountCreate, session: Session = Depends(get_session)) -> dict:
    if session.get(Account, request.id) is not None:
        raise HTTPException(status_code=409, detail="Account already exists")
    account = Account(id=request.id, name=request.name.strip())
    session.add(account)
    session.commit()
    session.refresh(account)
    return {"id": account.id, "name": account.name, "createdAt": account.created_at}


@app.get("/accounts", tags=["accounts"])
def list_accounts(session: Session = Depends(get_session)) -> list[dict]:
    accounts = session.scalars(select(Account).order_by(Account.created_at, Account.id)).all()
    return [{"id": account.id, "name": account.name, "createdAt": account.created_at} for account in accounts]


@app.get("/api/accounts/overview", tags=["dashboard"])
def accounts_overview(session: Session = Depends(get_session)) -> dict:
    window_start = current_window()
    rows = session.execute(
        select(
            Account.id,
            Account.name,
            func.count(Contact.id),
            func.coalesce(AccountRateWindow.events_reserved, 0),
        )
        .outerjoin(Contact, Contact.account_id == Account.id)
        .outerjoin(
            AccountRateWindow,
            and_(
                AccountRateWindow.account_id == Account.id,
                AccountRateWindow.window_start == window_start,
            ),
        )
        .group_by(Account.id, Account.name, AccountRateWindow.events_reserved)
        .order_by(Account.name, Account.id)
    ).all()
    return {
        "windowStart": window_start,
        "resetsAt": window_start + timedelta(minutes=1),
        "limitPerMinute": EVENTS_PER_ACCOUNT_PER_MINUTE,
        "items": [
            {
                "accountId": account_id,
                "name": name,
                "contacts": contacts,
                "eventsReserved": reserved,
                "remaining": max(EVENTS_PER_ACCOUNT_PER_MINUTE - reserved, 0),
            }
            for account_id, name, contacts, reserved in rows
        ],
    }


@app.post("/accounts/{account_id}/contacts", status_code=status.HTTP_201_CREATED, tags=["accounts"])
def add_account_contacts(
    account_id: str,
    request: ContactBulkCreate,
    session: Session = Depends(get_session),
) -> dict:
    if session.get(Account, account_id) is None:
        raise HTTPException(status_code=404, detail="Account not found; create it first with POST /accounts")
    contacts = [
        Contact(account_id=account_id, name=item.name, email=item.email, age=item.age, status=item.status)
        for item in request.contacts
    ]
    session.add_all(contacts)
    session.commit()
    return {"accountId": account_id, "inserted": len(contacts), "contactIds": [item.id for item in contacts]}


@app.get("/accounts/{account_id}/contacts", tags=["accounts"])
def list_account_contacts(
    account_id: str,
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(get_session),
) -> dict:
    if session.get(Account, account_id) is None:
        raise HTTPException(status_code=404, detail="Account not found")
    query = select(Contact).where(Contact.account_id == account_id).order_by(Contact.id)
    contacts = session.scalars(query.limit(limit).offset(offset)).all()
    total = session.scalar(select(func.count()).select_from(Contact).where(Contact.account_id == account_id)) or 0
    return {
        "accountId": account_id,
        "items": [
            {"contactId": item.id, "name": item.name, "email": item.email, "age": item.age, "status": item.status}
            for item in contacts
        ],
        "limit": limit,
        "offset": offset,
        "count": total,
    }


@app.get("/accounts/{account_id}/rate-limit", tags=["bulk actions"])
def get_account_rate_limit(account_id: str, session: Session = Depends(get_session)) -> dict:
    window_start = current_window()
    row = session.get(AccountRateWindow, account_id)
    reserved = row.events_reserved if row and row.window_start == window_start else 0
    return {
        "accountId": account_id,
        "limitPerMinute": EVENTS_PER_ACCOUNT_PER_MINUTE,
        "windowStart": window_start,
        "resetsAt": window_start + timedelta(minutes=1),
        "eventsReserved": reserved,
        "remaining": max(EVENTS_PER_ACCOUNT_PER_MINUTE - reserved, 0),
    }


@app.get("/api/dashboard", tags=["dashboard"])
def dashboard_state(account_id: str | None = Query(default=None, alias="accountId"), session: Session = Depends(get_session)) -> dict:
    # The optional local dashboard currently reports Contact source volume.
    from app.models import Contact

    queue_rows = session.execute(
        select(BulkActionBatch.status, func.count()).group_by(BulkActionBatch.status)
    ).all()
    action_rows = session.scalars(select(BulkAction).order_by(BulkAction.created_at.desc()).limit(50)).all()
    contact_query = select(func.count()).select_from(Contact)
    if account_id:
        contact_query = contact_query.where(Contact.account_id == account_id)
    return {
        "database": engine.dialect.name,
        "multiWorkerSupported": True,
        "contacts": session.scalar(contact_query) or 0,
        "queue": {state.lower(): count for state, count in queue_rows},
        "actions": [
            {
                "actionId": action.id,
                "accountId": action.account_id,
                "status": action.status,
                "scheduledAt": action.scheduled_at.isoformat() if action.scheduled_at else None,
                "createdAt": action.created_at.isoformat(),
            }
            for action in action_rows
        ],
        "workers": supervisor.status(),
        "refreshedAt": utcnow_dashboard(),
    }


def utcnow_dashboard() -> str:
    return datetime.now(timezone.utc).isoformat()


def utcnow_dashboard_dt() -> datetime:
    return datetime.now(timezone.utc)


@app.get("/api/workers", tags=["dashboard"])
def get_workers() -> dict:
    return supervisor.status()


@app.post("/api/workers/scale", tags=["dashboard"])
def scale_workers(payload: dict) -> dict:
    try:
        desired = int(payload.get("count"))
        return supervisor.scale(desired)
    except (ValueError, TypeError, RuntimeError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.get("/bulk-actions", response_model=list[ActionSummary], tags=["bulk actions"])
def list_bulk_actions(
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(get_session),
) -> list[dict]:
    if status_filter and status_filter not in {"SCHEDULED", "QUEUED", "RUNNING", "COMPLETED", "FAILED"}:
        raise HTTPException(status_code=422, detail="status must be SCHEDULED, QUEUED, RUNNING, COMPLETED, or FAILED")
    query = select(BulkAction)
    if status_filter:
        query = query.where(BulkAction.status == status_filter)
    actions = session.scalars(query.order_by(BulkAction.created_at.desc()).limit(limit).offset(offset)).all()
    return [action_summary(session, action) for action in actions]


@app.post(
    "/bulk-actions",
    response_model=ActionSummary,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["bulk actions"],
)
def create_bulk_action(
    request: BulkActionCreate,
    response: Response,
    session: Session = Depends(get_session),
) -> dict:
    if session.get(Account, request.account_id) is None:
        raise HTTPException(status_code=404, detail="Account not found; create it first with POST /accounts")
    try:
        processor = get_processor(request.entity, request.action)
        processor.validate(request.filters, request.operation_data)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    from uuid import uuid4

    action = BulkAction(
        id=f"BA-{uuid4().hex[:12].upper()}",
        account_id=request.account_id,
        entity=request.entity,
        action=request.action,
        filters=request.filters,
        operation_data=request.operation_data,
        status="SCHEDULED" if request.scheduled_at and request.scheduled_at > utcnow_dashboard_dt() else "QUEUED",
        total=0,
        cursor_after_entity_id=0,
        selection_complete=False,
        scheduled_at=request.scheduled_at,
    )
    session.add(action)
    session.commit()
    session.refresh(action)
    response.headers["Location"] = f"/bulk-actions/{action.id}"
    return action_summary(session, action)


@app.get("/bulk-actions/{action_id}", response_model=ActionSummary, tags=["bulk actions"])
def get_bulk_action(action_id: str, session: Session = Depends(get_session)) -> dict:
    action = session.get(BulkAction, action_id)
    if action is None:
        raise HTTPException(status_code=404, detail="Bulk action not found")
    return action_summary(session, action)


@app.get("/bulk-actions/{action_id}/stats", response_model=ActionStats, tags=["bulk actions"])
def get_bulk_action_stats(action_id: str, session: Session = Depends(get_session)) -> dict:
    action = session.get(BulkAction, action_id)
    if action is None:
        raise HTTPException(status_code=404, detail="Bulk action not found")
    return action_statistics(session, action)


@app.get("/bulk-actions/{action_id}/batches", response_model=BatchPage, tags=["bulk actions"])
def get_bulk_action_batches(
    action_id: str,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(get_session),
) -> dict:
    if session.get(BulkAction, action_id) is None:
        raise HTTPException(status_code=404, detail="Bulk action not found")
    batches = session.scalars(
        select(BulkActionBatch)
        .where(BulkActionBatch.action_id == action_id)
        .order_by(BulkActionBatch.sequence.desc())
        .limit(limit)
        .offset(offset)
    ).all()
    total = session.scalar(
        select(func.count()).select_from(BulkActionBatch).where(BulkActionBatch.action_id == action_id)
    ) or 0
    batch_ids = [batch.id for batch in batches]
    grouped = session.execute(
        select(BulkActionLog.batch_id, BulkActionLog.status, func.count())
        .where(BulkActionLog.batch_id.in_(batch_ids))
        .group_by(BulkActionLog.batch_id, BulkActionLog.status)
    ).all() if batch_ids else []
    counts_by_batch: dict[str, Counter] = {}
    for batch_id, result_status, count in grouped:
        counts_by_batch.setdefault(batch_id, Counter())[result_status] = count
    return {
        "action_id": action_id,
        "items": [
            {
                "batch_id": batch.id,
                "sequence": batch.sequence,
                "item_count": batch.item_count,
                "status": batch.status,
                "attempt_count": batch.attempt_count,
                "worker_id": batch.worker_id,
                "success": counts_by_batch.get(batch.id, {}).get("SUCCESS", 0),
                "failed": counts_by_batch.get(batch.id, {}).get("FAILED", 0),
                "skipped": counts_by_batch.get(batch.id, {}).get("SKIPPED", 0),
                "pending": counts_by_batch.get(batch.id, {}).get("PENDING", 0),
                "last_error": batch.last_error,
                "created_at": batch.created_at,
                "completed_at": batch.completed_at,
            }
            for batch in batches
        ],
        "limit": limit,
        "offset": offset,
        "count": total,
    }


@app.get("/bulk-actions/{action_id}/queue", tags=["bulk actions"])
def get_bulk_action_queue(
    action_id: str,
    limit: int = Query(default=100, ge=1, le=500),
    session: Session = Depends(get_session),
) -> dict:
    if session.get(BulkAction, action_id) is None:
        raise HTTPException(status_code=404, detail="Bulk action not found")
    rows = session.execute(
        select(BulkActionLog, BulkActionBatch)
        .join(BulkActionBatch, BulkActionBatch.id == BulkActionLog.batch_id)
        .where(
            BulkActionLog.action_id == action_id,
            BulkActionLog.status == "PENDING",
            BulkActionBatch.status.in_(["QUEUED", "RUNNING"]),
        )
        .order_by(BulkActionBatch.sequence.desc(), BulkActionLog.entity_id)
        .limit(limit)
    ).all()
    return {
        "actionId": action_id,
        "items": [
            {
                "entityId": log.entity_id,
                "batchId": batch.id,
                "batchStatus": batch.status,
                "workerId": batch.worker_id,
                "attemptCount": log.attempt_count,
            }
            for log, batch in rows
        ],
        "limit": limit,
    }


@app.get("/bulk-actions/{action_id}/logs", response_model=LogPage, tags=["bulk actions"])
def get_bulk_action_logs(
    action_id: str,
    log_status: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(get_session),
) -> dict:
    if session.get(BulkAction, action_id) is None:
        raise HTTPException(status_code=404, detail="Bulk action not found")
    if log_status and log_status not in {"PENDING", "SUCCESS", "FAILED", "SKIPPED"}:
        raise HTTPException(status_code=422, detail="status must be PENDING, SUCCESS, FAILED, or SKIPPED")
    query = select(BulkActionLog).where(BulkActionLog.action_id == action_id)
    count_query = select(func.count()).select_from(BulkActionLog).where(BulkActionLog.action_id == action_id)
    if log_status:
        query = query.where(BulkActionLog.status == log_status)
        count_query = count_query.where(BulkActionLog.status == log_status)
    logs = session.scalars(query.order_by(BulkActionLog.entity_id).limit(limit).offset(offset)).all()
    count = session.scalar(count_query) or 0
    return {
        "action_id": action_id,
        "items": [
            {
                "entity_id": log.entity_id,
                "batch_id": log.batch_id,
                "status": log.status,
                "error_message": log.error_message,
                "attempt_count": log.attempt_count,
                "processed_at": log.processed_at,
            }
            for log in logs
        ],
        "limit": limit,
        "offset": offset,
        "count": count,
    }


@app.post("/bulk-actions/{action_id}/retry", response_model=RetryResponse, tags=["bulk actions"])
def retry_bulk_action(action_id: str) -> dict:
    result = retry_action(action_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Bulk action not found")
    batches, entities = result
    if batches == 0:
        raise HTTPException(status_code=409, detail="This action has no failed or pending work to retry")
    return {
        "action_id": action_id,
        "requeued_batches": batches,
        "requeued_entities": entities,
        "status": "RUNNING",
    }
