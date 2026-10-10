from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url

ACCOUNT_EVENTS_PER_MINUTE = 10_000


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://bulk:bulk@localhost:5432/bulk_actions"
    batch_size: int = 500
    selection_page_size: int = 1000
    poll_interval_seconds: float = 1.0
    lease_seconds: int = 120
    max_batch_attempts: int = 5
    worker_id: str | None = None
    enable_local_worker_control: bool = False
    max_local_workers: int = 8

    @field_validator("database_url")
    @classmethod
    def require_postgresql(cls, value: str) -> str:
        url = make_url(value)
        if url.get_backend_name() != "postgresql":
            raise ValueError("PostgreSQL is required; set DATABASE_URL to a PostgreSQL connection string")
        if url.get_driver_name() != "psycopg":
            raise ValueError("Use the psycopg 3 driver, for example postgresql+psycopg://user:password@localhost/database")
        return value

    @field_validator("batch_size")
    @classmethod
    def keep_batch_within_account_limit(cls, value: int) -> int:
        if not 1 <= value <= ACCOUNT_EVENTS_PER_MINUTE:
            raise ValueError(f"BATCH_SIZE must be between 1 and {ACCOUNT_EVENTS_PER_MINUTE}")
        return value


settings = Settings()
