"""Local demo process supervisor. For development only; production uses an orchestrator."""
import os
from pathlib import Path
import subprocess
import sys
from threading import RLock

from app.config import settings


class LocalWorkerSupervisor:
    def __init__(self) -> None:
        self._processes: dict[str, subprocess.Popen] = {}
        self._lock = RLock()
        self._logs = Path(__file__).resolve().parent.parent / "worker-logs"

    def status(self) -> dict:
        with self._lock:
            items = []
            for worker_id, process in list(self._processes.items()):
                items.append({"workerId": worker_id, "pid": process.pid, "running": process.poll() is None})
                if process.poll() is not None:
                    self._processes.pop(worker_id, None)
                    handle = getattr(process, "_codex_log_handle", None)
                    if handle:
                        handle.close()
            return {
                "enabled": settings.enable_local_worker_control,
                "managedWorkers": items,
                "managedCount": sum(item["running"] for item in items),
                "maxWorkers": settings.max_local_workers,
            }

    def scale(self, desired: int) -> dict:
        if not settings.enable_local_worker_control:
            raise RuntimeError("Set ENABLE_LOCAL_WORKER_CONTROL=true in .env and restart the API to enable UI scaling")
        if desired < 1 or desired > settings.max_local_workers:
            raise ValueError(f"Worker count must be between 1 and {settings.max_local_workers}")
        with self._lock:
            active = [p for p in self._processes.values() if p.poll() is None]
            while len(active) < desired:
                worker_id = f"local-worker-{len(active) + 1}"
                suffix = 1
                base = worker_id
                while worker_id in self._processes and self._processes[worker_id].poll() is None:
                    suffix += 1
                    worker_id = f"{base}-{suffix}"
                self._logs.mkdir(parents=True, exist_ok=True)
                log = (self._logs / f"{worker_id}.log").open("a", encoding="utf-8")
                env = os.environ.copy()
                env["WORKER_ID"] = worker_id
                process = subprocess.Popen(
                    [sys.executable, "-m", "app.worker"],
                    cwd=Path(__file__).resolve().parent.parent,
                    env=env,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
                )
                # Keep the file handle attached to the process object for its lifetime.
                process._codex_log_handle = log
                self._processes[worker_id] = process
                active.append(process)
            while len(active) > desired:
                process = active.pop()
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                if getattr(process, "_codex_log_handle", None):
                    process._codex_log_handle.close()
            return self.status()

    def shutdown(self) -> None:
        with self._lock:
            for process in self._processes.values():
                if process.poll() is None:
                    process.terminate()
            for process in self._processes.values():
                if process.poll() is None:
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
                handle = getattr(process, "_codex_log_handle", None)
                if handle:
                    handle.close()
            self._processes.clear()


supervisor = LocalWorkerSupervisor()
