from datetime import datetime
from typing import Any

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator


class BulkActionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    account_id: str = Field(alias="accountId", min_length=1, max_length=100)
    entity: str = Field(default="contact", min_length=1, max_length=50)
    action: str = Field(default="update", min_length=1, max_length=50)
    filters: dict[str, Any] = Field(default_factory=dict, alias="filter")
    operation_data: dict[str, Any] = Field(
        default_factory=dict,
        validation_alias=AliasChoices("operationData", "updates"),
    )
    scheduled_at: datetime | None = Field(default=None, alias="scheduledAt")

    @field_validator("scheduled_at")
    @classmethod
    def scheduled_time_must_include_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("scheduledAt must include a timezone, for example 2026-11-22T23:15:00+05:30")
        return value

    @field_validator("filters")
    @classmethod
    def filter_is_object(cls, value: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise ValueError("filter must be an object")
        return value


class ActionSummary(BaseModel):
    id: str = Field(serialization_alias="actionId")
    account_id: str = Field(serialization_alias="accountId")
    entity: str
    action: str
    status: str
    total: int
    processed: int
    progress: float
    created_at: datetime = Field(serialization_alias="createdAt")
    scheduled_at: datetime | None = Field(serialization_alias="scheduledAt")
    started_at: datetime | None = Field(serialization_alias="startedAt")
    completed_at: datetime | None = Field(serialization_alias="completedAt")


class AccountCreate(BaseModel):
    id: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=200)


class ContactCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    email: str = Field(min_length=3, max_length=320)
    age: int | None = None
    status: str = Field(default="active", min_length=1, max_length=50)


class ContactBulkCreate(BaseModel):
    contacts: list[ContactCreate] = Field(min_length=1, max_length=5000)


class ActionStats(BaseModel):
    action_id: str = Field(serialization_alias="actionId")
    total: int
    processed: int
    success: int
    failed: int
    skipped: int
    pending: int
    in_progress: int = Field(serialization_alias="inProgress")
    staged: int
    count_ready: bool = Field(serialization_alias="countReady")
    selection_complete: bool = Field(serialization_alias="selectionComplete")
    completion_percentage: float = Field(serialization_alias="completionPercentage")
    pending_percentage: float = Field(serialization_alias="pendingPercentage")
    in_progress_percentage: float = Field(serialization_alias="inProgressPercentage")
    elapsed_seconds: float = Field(serialization_alias="elapsedSeconds")
    records_per_second: float = Field(serialization_alias="recordsPerSecond")
    progress: float


class LogItem(BaseModel):
    entity_id: int = Field(serialization_alias="entityId")
    batch_id: str = Field(serialization_alias="batchId")
    status: str
    error_message: str | None = Field(serialization_alias="errorMessage")
    attempt_count: int = Field(serialization_alias="attemptCount")
    processed_at: datetime | None = Field(serialization_alias="processedAt")


class LogPage(BaseModel):
    action_id: str = Field(serialization_alias="actionId")
    items: list[LogItem]
    limit: int
    offset: int
    count: int


class BatchItem(BaseModel):
    batch_id: str = Field(serialization_alias="batchId")
    sequence: int
    item_count: int = Field(serialization_alias="itemCount")
    status: str
    attempt_count: int = Field(serialization_alias="attemptCount")
    worker_id: str | None = Field(serialization_alias="workerId")
    success: int
    failed: int
    skipped: int
    pending: int
    last_error: str | None = Field(serialization_alias="lastError")
    created_at: datetime = Field(serialization_alias="createdAt")
    completed_at: datetime | None = Field(serialization_alias="completedAt")


class BatchPage(BaseModel):
    action_id: str = Field(serialization_alias="actionId")
    items: list[BatchItem]
    limit: int
    offset: int
    count: int


class RetryResponse(BaseModel):
    action_id: str = Field(serialization_alias="actionId")
    requeued_batches: int = Field(serialization_alias="requeuedBatches")
    requeued_entities: int = Field(serialization_alias="requeuedEntities")
    status: str
