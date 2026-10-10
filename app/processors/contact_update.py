from typing import Any

from sqlalchemy import Select, and_, func, select
from sqlalchemy.orm import aliased
from sqlalchemy.orm import Session

from app.models import Contact
from app.processors.base import ActionProcessor, registry

FILTER_FIELDS = {"status", "name", "email", "age"}
UPDATE_FIELDS = {"status", "name", "email", "age"}


class ContactUpdateProcessor(ActionProcessor):
    entity = "contact"
    action = "update"

    @property
    def id_column(self):
        return Contact.id

    def validate(self, filters: dict[str, Any], operation_data: dict[str, Any]) -> None:
        unknown_filters = set(filters) - FILTER_FIELDS
        unknown_updates = set(operation_data) - UPDATE_FIELDS
        if not operation_data:
            raise ValueError("Contact update operationData must contain at least one field")
        if unknown_filters:
            raise ValueError(f"Unsupported contact filter fields: {', '.join(sorted(unknown_filters))}")
        if unknown_updates:
            raise ValueError(f"Unsupported contact update fields: {', '.join(sorted(unknown_updates))}")
        for field, value in operation_data.items():
            if field in {"status", "name", "email"} and not isinstance(value, str):
                raise ValueError(f"updates.{field} must be a string")
            if field == "age" and value is not None and (not isinstance(value, int) or isinstance(value, bool)):
                raise ValueError("updates.age must be an integer or null")
        for field in {"status", "name", "email"} & set(filters):
            if not isinstance(filters[field], str):
                raise ValueError(f"filter.{field} must be a string")
        if "age" in filters and filters["age"] is not None and (
            not isinstance(filters["age"], int) or isinstance(filters["age"], bool)
        ):
            raise ValueError("filter.age must be an integer")

    def selection_query(self, account_id: str, filters: dict[str, Any]) -> Select:
        conditions = [Contact.account_id == account_id]
        for field, value in filters.items():
            if field not in FILTER_FIELDS:
                raise ValueError(f"Unsupported contact filter field: {field}")
            conditions.append(getattr(Contact, field) == value)
        return select(Contact.id).where(*conditions).order_by(Contact.id)

    def load_for_update(self, session: Session, entity_ids: list[int]) -> dict[int, Contact]:
        return {
            contact.id: contact
            for contact in session.scalars(
                select(Contact)
                .where(Contact.id.in_(entity_ids))
                .order_by(Contact.id)
                .with_for_update()
            )
        }

    def belongs_to_account(self, entity: Contact, account_id: str) -> bool:
        return entity.account_id == account_id

    def matches(self, entity: Contact, account_id: str, filters: dict[str, Any]) -> bool:
        if not self.belongs_to_account(entity, account_id):
            return False
        return all(getattr(entity, field) == value for field, value in filters.items())

    def duplicate_reasons(self, session: Session, entities: list[Contact], account_id: str) -> dict[int, str]:
        if not entities:
            return {}
        source = aliased(Contact)
        earlier = aliased(Contact)
        duplicates = session.execute(
            select(source.id, func.min(earlier.id))
            .select_from(source)
            .join(
                earlier,
                and_(
                    earlier.account_id == account_id,
                    earlier.id < source.id,
                    func.lower(func.trim(earlier.email)) == func.lower(func.trim(source.email)),
                ),
            )
            .where(source.id.in_([entity.id for entity in entities]), source.account_id == account_id)
            .group_by(source.id)
        ).all()
        contacts_by_id = {entity.id: entity for entity in entities}
        return {
            entity_id: (
                f"Duplicate email '{contacts_by_id[entity_id].email.strip().lower()}' in account {account_id}; "
                f"canonical contact is {canonical_id}"
            )
            for entity_id, canonical_id in duplicates
        }

    def apply(self, entity: Contact, operation_data: dict[str, Any]) -> None:
        for field, value in operation_data.items():
            setattr(entity, field, value)


registry.register(ContactUpdateProcessor())
