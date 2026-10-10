from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    false,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

ID_TYPE = BigInteger()


class Contact(Base):
    __tablename__ = "contacts"
    __table_args__ = (
        Index("ix_contacts_account_status_id", "account_id", "status", "id"),
        Index("ix_contacts_account_id_id", "account_id", "id"),
        UniqueConstraint("id", "account_id", name="uq_contacts_id_account"),
    )

    id: Mapped[int] = mapped_column(ID_TYPE, primary_key=True, autoincrement=True)
    account_id: Mapped[str] = mapped_column(String(100), ForeignKey("accounts.id", ondelete="RESTRICT"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    email: Mapped[str] = mapped_column(String(320), index=True)
    age: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(50), index=True, default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    account: Mapped["Account"] = relationship(back_populates="contacts")


class BulkAction(Base):
    __tablename__ = "bulk_actions"
    __table_args__ = (
        Index("ix_bulk_actions_queue", "status", "selection_complete"),
        UniqueConstraint("id", "account_id", name="uq_bulk_actions_id_account"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    account_id: Mapped[str] = mapped_column(String(100), ForeignKey("accounts.id", ondelete="RESTRICT"), index=True)
    entity: Mapped[str] = mapped_column(String(50))
    action: Mapped[str] = mapped_column(String(50))
    filters: Mapped[dict[str, Any]] = mapped_column(JSON)
    operation_data: Mapped[dict[str, Any]] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(20), default="QUEUED", index=True)
    total: Mapped[int] = mapped_column(BigInteger, default=0)
    staged_total: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    selection_counted: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    cursor_after_entity_id: Mapped[int] = mapped_column(BigInteger, default=0)
    selection_complete: Mapped[bool] = mapped_column(Boolean, default=False)
    orchestration_owner: Mapped[str | None] = mapped_column(String(40), nullable=True)
    orchestration_lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    batches: Mapped[list["BulkActionBatch"]] = relationship(back_populates="action_record")
    account: Mapped["Account"] = relationship(back_populates="bulk_actions")


class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    contacts: Mapped[list[Contact]] = relationship(back_populates="account")
    bulk_actions: Mapped[list[BulkAction]] = relationship(back_populates="account")


Index(
    "ix_contacts_account_normalized_email_id",
    Contact.account_id,
    func.lower(func.trim(Contact.email)),
    Contact.id,
)


class BulkActionBatch(Base):
    __tablename__ = "bulk_action_batches"
    __table_args__ = (
        UniqueConstraint("action_id", "sequence", name="uq_batch_action_sequence"),
        UniqueConstraint("id", "action_id", name="uq_batches_id_action"),
        Index("ix_batches_claim", "status", "available_at"),
    )

    id: Mapped[str] = mapped_column(String(48), primary_key=True)
    action_id: Mapped[str] = mapped_column(ForeignKey("bulk_actions.id", ondelete="CASCADE"), index=True)
    sequence: Mapped[int] = mapped_column(Integer)
    item_count: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(20), default="QUEUED", index=True)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    lease_owner: Mapped[str | None] = mapped_column(String(40), nullable=True)
    worker_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    action_record: Mapped[BulkAction] = relationship(back_populates="batches")
    logs: Mapped[list["BulkActionLog"]] = relationship(back_populates="batch")


class BulkActionLog(Base):
    __tablename__ = "bulk_action_logs"
    __table_args__ = (
        UniqueConstraint("action_id", "entity_id", name="uq_log_action_entity"),
        ForeignKeyConstraint(
            ["action_id", "account_id"], ["bulk_actions.id", "bulk_actions.account_id"],
            name="fk_logs_action_account", ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["batch_id", "action_id"], ["bulk_action_batches.id", "bulk_action_batches.action_id"],
            name="fk_logs_batch_action", ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["entity_id", "account_id"], ["contacts.id", "contacts.account_id"],
            name="fk_logs_contact_account", ondelete="RESTRICT",
        ),
        Index("ix_logs_action_status", "action_id", "status"),
        Index("ix_logs_batch_status", "batch_id", "status"),
    )

    id: Mapped[int] = mapped_column(ID_TYPE, primary_key=True, autoincrement=True)
    action_id: Mapped[str] = mapped_column(String(40), index=True)
    batch_id: Mapped[str] = mapped_column(String(48), index=True)
    account_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    entity_id: Mapped[int] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(20), default="PENDING", index=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    batch: Mapped[BulkActionBatch] = relationship(back_populates="logs")


class AccountRateWindow(Base):
    """Shared fixed-window event reservations coordinated by PostgreSQL row locks."""

    __tablename__ = "account_rate_windows"

    account_id: Mapped[str] = mapped_column(
        String(100), ForeignKey("accounts.id", ondelete="CASCADE"), primary_key=True
    )
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    events_reserved: Mapped[int] = mapped_column(Integer, default=0)
