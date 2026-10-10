import logging
import signal
import time

from app.config import settings
from app.database import engine, init_db
from app.engine import run_once, worker_identity

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("bulk-action-worker")
stopping = False


def stop_worker(*_: object) -> None:
    global stopping
    stopping = True


def main() -> None:
    signal.signal(signal.SIGINT, stop_worker)
    signal.signal(signal.SIGTERM, stop_worker)
    init_db()
    logger.info(
        "Worker started worker_id=%s database=%s; polling for actions and batches",
        worker_identity(),
        engine.dialect.name,
    )
    while not stopping:
        try:
            worked = run_once()
            if not worked:
                time.sleep(settings.poll_interval_seconds)
        except Exception:
            logger.exception("Worker loop failed; will retry")
            time.sleep(settings.poll_interval_seconds)
    logger.info("Worker stopped")


if __name__ == "__main__":
    main()
