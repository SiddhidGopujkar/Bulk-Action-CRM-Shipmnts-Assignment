from collections.abc import Generator

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import settings


class Base(DeclarativeBase):
    pass


engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_session() -> Generator[Session, None, None]:
    with SessionLocal() as session:
        yield session


def ensure_database_exists() -> None:
    """Create the configured PostgreSQL database when the server permits it."""
    url = make_url(settings.database_url)
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return
    except Exception:
        # A missing target database is the normal first-run case. Connect to
        # PostgreSQL's maintenance database and create it if it does not exist.
        # If the server itself is unavailable, this connection will fail with
        # a useful error rather than masking the original setup problem.
        pass

    database_name = url.database
    if not database_name:
        raise RuntimeError("DATABASE_URL must include a PostgreSQL database name")

    admin_url = url.set(database="postgres")
    admin_engine = create_engine(admin_url, isolation_level="AUTOCOMMIT", pool_pre_ping=True)
    try:
        with admin_engine.connect() as connection:
            found = connection.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :name"), {"name": database_name}
            ).scalar_one_or_none()
            if found is None:
                quoted_name = connection.dialect.identifier_preparer.quote(database_name)
                try:
                    connection.execute(text(f"CREATE DATABASE {quoted_name}"))
                except Exception:
                    # Another app instance may have created it after the
                    # existence check. Treat that as success only if it now exists.
                    created_by_peer = connection.execute(
                        text("SELECT 1 FROM pg_database WHERE datname = :name"), {"name": database_name}
                    ).scalar_one_or_none()
                    if created_by_peer is None:
                        raise
    except Exception as exc:
        raise RuntimeError(
            f"Could not connect to PostgreSQL maintenance database or create '{database_name}'. "
            "Ensure PostgreSQL is running and the DATABASE_URL role has CREATEDB permission, "
            "or create the database manually."
        ) from exc
    finally:
        admin_engine.dispose()


def init_db() -> None:
    from app import models  # noqa: F401

    ensure_database_exists()
    with engine.begin() as connection:
        connection.execute(text("SELECT pg_advisory_xact_lock(734021, 1)"))
        Base.metadata.create_all(bind=connection)
        # Preserve existing local databases while establishing explicit account ownership.
        connection.execute(
            text(
                """
                INSERT INTO accounts (id, name)
                SELECT account_id, account_id FROM (
                    SELECT account_id FROM contacts
                    UNION
                    SELECT account_id FROM bulk_actions
                    UNION
                    SELECT account_id FROM account_rate_windows
                ) AS existing_accounts
                ON CONFLICT (id) DO NOTHING
                """
            )
        )
        columns = {column["name"] for column in inspect(connection).get_columns("bulk_action_batches")}
        if "worker_id" not in columns:
            connection.execute(text("ALTER TABLE bulk_action_batches ADD COLUMN worker_id VARCHAR(100)"))
        action_columns = {column["name"] for column in inspect(connection).get_columns("bulk_actions")}
        if "updates" in action_columns and "operation_data" not in action_columns:
            connection.execute(text("ALTER TABLE bulk_actions RENAME COLUMN updates TO operation_data"))
            action_columns.remove("updates")
            action_columns.add("operation_data")
        if "cursor_after_contact_id" in action_columns and "cursor_after_entity_id" not in action_columns:
            connection.execute(
                text("ALTER TABLE bulk_actions RENAME COLUMN cursor_after_contact_id TO cursor_after_entity_id")
            )
            action_columns.remove("cursor_after_contact_id")
            action_columns.add("cursor_after_entity_id")
        if "cursor_after_entity_id" not in action_columns:
            connection.execute(
                text("ALTER TABLE bulk_actions ADD COLUMN cursor_after_entity_id BIGINT NOT NULL DEFAULT 0")
            )
        if "selection_counted" not in action_columns:
            connection.execute(
                text("ALTER TABLE bulk_actions ADD COLUMN selection_counted BOOLEAN NOT NULL DEFAULT TRUE")
            )
        if "staged_total" not in action_columns:
            connection.execute(text("ALTER TABLE bulk_actions ADD COLUMN staged_total BIGINT NOT NULL DEFAULT 0"))
            connection.execute(text("UPDATE bulk_actions SET staged_total = total"))
        if "scheduled_at" not in action_columns:
            connection.execute(text("ALTER TABLE bulk_actions ADD COLUMN scheduled_at TIMESTAMPTZ NULL"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_bulk_actions_scheduled_at ON bulk_actions (scheduled_at)"))

        log_columns = {column["name"] for column in inspect(connection).get_columns("bulk_action_logs")}
        if "account_id" not in log_columns:
            connection.execute(text("ALTER TABLE bulk_action_logs ADD COLUMN account_id VARCHAR(100)"))
        connection.execute(
            text(
                "UPDATE bulk_action_logs AS logs SET account_id = actions.account_id "
                "FROM bulk_actions AS actions WHERE logs.action_id = actions.id AND logs.account_id IS NULL"
            )
        )
        connection.execute(text("ALTER TABLE bulk_action_logs ALTER COLUMN account_id SET NOT NULL"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_bulk_action_logs_account_id ON bulk_action_logs (account_id)"))
        connection.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_contacts_account_normalized_email_id "
                "ON contacts (account_id, lower(trim(email)), id)"
            )
        )

        # Composite keys keep result rows attached to the same tenant, action, batch, and contact.
        constraints = {
            item["name"] for item in inspect(connection).get_unique_constraints("contacts")
        }
        if "uq_contacts_id_account" not in constraints:
            connection.execute(text("ALTER TABLE contacts ADD CONSTRAINT uq_contacts_id_account UNIQUE (id, account_id)"))
        constraints = {item["name"] for item in inspect(connection).get_unique_constraints("bulk_actions")}
        if "uq_bulk_actions_id_account" not in constraints:
            connection.execute(
                text("ALTER TABLE bulk_actions ADD CONSTRAINT uq_bulk_actions_id_account UNIQUE (id, account_id)")
            )
        constraints = {item["name"] for item in inspect(connection).get_unique_constraints("bulk_action_batches")}
        if "uq_batches_id_action" not in constraints:
            connection.execute(
                text("ALTER TABLE bulk_action_batches ADD CONSTRAINT uq_batches_id_action UNIQUE (id, action_id)")
            )
        # Inspect foreign keys separately because these are relational consistency checks, not CHECK constraints.
        foreign_keys = {
            item["name"] for item in inspect(connection).get_foreign_keys("bulk_action_logs")
        }
        if "fk_logs_action_account" not in foreign_keys:
            connection.execute(
                text(
                    "ALTER TABLE bulk_action_logs ADD CONSTRAINT fk_logs_action_account "
                    "FOREIGN KEY (action_id, account_id) REFERENCES bulk_actions(id, account_id) ON DELETE CASCADE"
                )
            )
        if "fk_logs_batch_action" not in foreign_keys:
            connection.execute(
                text(
                    "ALTER TABLE bulk_action_logs ADD CONSTRAINT fk_logs_batch_action "
                    "FOREIGN KEY (batch_id, action_id) REFERENCES bulk_action_batches(id, action_id) ON DELETE CASCADE"
                )
            )
        if "fk_logs_contact_account" not in foreign_keys:
            connection.execute(
                text(
                    "ALTER TABLE bulk_action_logs ADD CONSTRAINT fk_logs_contact_account "
                    "FOREIGN KEY (entity_id, account_id) REFERENCES contacts(id, account_id) ON DELETE RESTRICT"
                )
            )

        if not any(
            fk["referred_table"] == "accounts" and fk["constrained_columns"] == ["account_id"]
            for fk in inspect(connection).get_foreign_keys("contacts")
        ):
            connection.execute(
                text(
                    "ALTER TABLE contacts ADD CONSTRAINT fk_contacts_account_id_accounts "
                    "FOREIGN KEY (account_id) REFERENCES accounts(id) ON DELETE RESTRICT"
                )
            )
        if not any(
            fk["referred_table"] == "accounts" and fk["constrained_columns"] == ["account_id"]
            for fk in inspect(connection).get_foreign_keys("bulk_actions")
        ):
            connection.execute(
                text(
                    "ALTER TABLE bulk_actions ADD CONSTRAINT fk_bulk_actions_account_id_accounts "
                    "FOREIGN KEY (account_id) REFERENCES accounts(id) ON DELETE RESTRICT"
                )
            )
        if not any(
            fk["referred_table"] == "accounts" and fk["constrained_columns"] == ["account_id"]
            for fk in inspect(connection).get_foreign_keys("account_rate_windows")
        ):
            connection.execute(
                text(
                    "ALTER TABLE account_rate_windows ADD CONSTRAINT fk_account_rate_windows_account_id "
                    "FOREIGN KEY (account_id) REFERENCES accounts(id) ON DELETE CASCADE"
                )
            )
