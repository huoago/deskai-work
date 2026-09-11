from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select

from app.database.models import AgentRun, AuditLog, Task

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class AgentWorkerSnapshot:
    running: bool
    processed: int
    failed: int
    blocked: int
    last_task_id: str | None
    last_completed_at: str | None
    last_error: str | None

    def as_dict(self) -> dict:
        return {
            "running": self.running,
            "processed": self.processed,
            "failed": self.failed,
            "blocked": self.blocked,
            "last_task_id": self.last_task_id,
            "last_completed_at": self.last_completed_at,
            "last_error": self.last_error,
        }


class AgentWorker:
    def __init__(self, database, orchestrator, *, interval_seconds: float = 1.0) -> None:
        self.database = database
        self.orchestrator = orchestrator
        self.interval_seconds = max(interval_seconds, 0.5)
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self._process_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._processed = 0
        self._failed = 0
        self._blocked = 0
        self._last_task_id: str | None = None
        self._last_completed_at: str | None = None
        self._last_error: str | None = None

    def start(self) -> None:
        with self._state_lock:
            if self._thread and self._thread.is_alive():
                return
            self._recover_interrupted()
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._run,
                name="deskai-agent-worker",
                daemon=True,
            )
            self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        self._wake.set()
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=timeout)

    def wake(self) -> None:
        self._wake.set()

    def snapshot(self) -> AgentWorkerSnapshot:
        thread = self._thread
        with self._state_lock:
            return AgentWorkerSnapshot(
                running=bool(thread and thread.is_alive() and not self._stop.is_set()),
                processed=self._processed,
                failed=self._failed,
                blocked=self._blocked,
                last_task_id=self._last_task_id,
                last_completed_at=self._last_completed_at,
                last_error=self._last_error,
            )

    def process_available(self, limit: int = 10) -> int:
        count = 0
        for _ in range(max(limit, 0)):
            if not self.process_next():
                break
            count += 1
        return count

    def process_next(self) -> bool:
        with self._process_lock:
            task_id = self._claim_next()
            if task_id is None:
                return False
            try:
                self.orchestrator.run_task(task_id)
                self._record_outcome(task_id)
            except Exception as exc:
                logger.error(
                    "Agent worker crashed for task %s: %s",
                    task_id,
                    exc,
                    exc_info=(type(exc), exc, exc.__traceback__),
                )
                self._mark_worker_failure(task_id, exc)
            return True

    def retry(self, task_id: str) -> bool:
        with self.database.session() as session:
            task = session.get(Task, task_id)
            if task is None or task.status not in {"failed", "blocked"}:
                return False
            task.status = "pending"
            task.progress = 0.0
            task.error_message = None
            task.result_text = None
            task.started_at = None
            task.completed_at = None
        self.wake()
        return True

    def _claim_next(self) -> str | None:
        with self.database.session() as session:
            task = session.scalar(
                select(Task)
                .where(Task.status == "pending")
                .order_by(Task.created_at, Task.id)
                .limit(1)
            )
            if task is None:
                return None
            task.status = "running"
            task.progress = max(task.progress, 0.03)
            task.started_at = task.started_at or datetime.now(timezone.utc)
            return task.id

    def _recover_interrupted(self) -> None:
        with self.database.session() as session:
            running_tasks = session.scalars(
                select(Task).where(Task.status == "running")
            ).all()
            for task in running_tasks:
                task.status = "pending"
                task.progress = min(task.progress, 0.05)
                task.error_message = "Recovered after interrupted Agent process"
                task.completed_at = None

            running_runs = session.scalars(
                select(AgentRun).where(AgentRun.status == "running")
            ).all()
            for run in running_runs:
                run.status = "interrupted"
                run.completed_at = datetime.now(timezone.utc)
                session.add(
                    AuditLog(
                        task_id=run.task_id,
                        agent_run_id=run.id,
                        action="agent_interrupted",
                        result="Recovered on Engine startup",
                        risk_level=0,
                    )
                )

    def _record_outcome(self, task_id: str) -> None:
        with self.database.session() as session:
            task = session.get(Task, task_id)
            status = task.status if task is not None else "failed"
            error = task.error_message if task is not None else "Task disappeared"
        with self._state_lock:
            self._last_task_id = task_id
            self._last_completed_at = datetime.now(timezone.utc).isoformat()
            self._last_error = error
            if status == "completed":
                self._processed += 1
                self._last_error = None
            elif status == "blocked":
                self._blocked += 1
            else:
                self._failed += 1

    def _mark_worker_failure(self, task_id: str, exc: Exception) -> None:
        error = f"{type(exc).__name__}: {exc}"[:4000]
        with self.database.session() as session:
            task = session.get(Task, task_id)
            if task is not None:
                task.status = "failed"
                task.error_message = error
                task.completed_at = datetime.now(timezone.utc)
            session.add(
                AuditLog(
                    task_id=task_id,
                    action="worker_failed",
                    result=error,
                    risk_level=0,
                )
            )
        with self._state_lock:
            self._failed += 1
            self._last_task_id = task_id
            self._last_completed_at = datetime.now(timezone.utc).isoformat()
            self._last_error = error

    def _run(self) -> None:
        while not self._stop.is_set():
            processed = self.process_available(limit=3)
            if processed == 0:
                self._wake.wait(self.interval_seconds)
                self._wake.clear()
