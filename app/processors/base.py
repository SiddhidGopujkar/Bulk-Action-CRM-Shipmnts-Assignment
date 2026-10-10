from abc import ABC, abstractmethod
from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session


class ActionProcessor(ABC):
    """Entity/action adapter used by the shared selection and batch engine."""

    entity: str
    action: str

    @property
    @abstractmethod
    def id_column(self) -> Any:
        """The entity's stable, ordered key used for keyset selection."""

    @abstractmethod
    def selection_query(self, account_id: str, filters: dict[str, Any]) -> Select:
        """Return a query selecting matching entity IDs in ascending key order."""

    @abstractmethod
    def load_for_update(self, session: Session, entity_ids: list[int]) -> dict[int, Any]:
        """Load and lock the selected entities, indexed by their stable key."""

    @abstractmethod
    def belongs_to_account(self, entity: Any, account_id: str) -> bool:
        """Protect account scope when a staged ID is processed."""

    @abstractmethod
    def matches(self, entity: Any, account_id: str, filters: dict[str, Any]) -> bool:
        """Recheck the original filter when the staged item is executed."""

    @abstractmethod
    def apply(self, entity: Any, operation_data: dict[str, Any]) -> None:
        """Apply the operation to one entity. Raise on a record-level error."""

    def validate(self, filters: dict[str, Any], operation_data: dict[str, Any]) -> None:
        """Validate operation-specific input; override in a concrete processor."""

    def duplicate_reasons(self, session: Session, entities: list[Any], account_id: str) -> dict[int, str]:
        """Return skip reasons for duplicate records in this bounded batch."""
        return {}

    def count_matches(self, session: Session, account_id: str, filters: dict[str, Any]) -> int:
        selection = self.selection_query(account_id, filters).order_by(None).subquery()
        return session.scalar(select(func.count()).select_from(selection)) or 0

    def select_ids(
        self,
        session: Session,
        account_id: str,
        filters: dict[str, Any],
        after_id: int,
        limit: int,
    ) -> list[int]:
        query = self.selection_query(account_id, filters)
        query = query.where(self.id_column > after_id).limit(limit)
        return list(session.scalars(query))


class ProcessorRegistry:
    def __init__(self) -> None:
        self._processors: dict[tuple[str, str], ActionProcessor] = {}

    def register(self, processor: ActionProcessor) -> None:
        key = (processor.entity, processor.action)
        if key in self._processors:
            raise ValueError(f"A processor is already registered for {key}")
        self._processors[key] = processor

    def get(self, entity: str, action: str) -> ActionProcessor:
        try:
            return self._processors[(entity, action)]
        except KeyError as exc:
            raise ValueError(f"Unsupported bulk operation: {entity}/{action}") from exc

    def supports(self, entity: str, action: str) -> bool:
        return (entity, action) in self._processors


registry = ProcessorRegistry()
