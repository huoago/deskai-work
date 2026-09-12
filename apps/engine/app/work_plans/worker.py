from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select

from app.database.models import AuditLog, Task, WorkPlan

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class WorkPlanWorkerSnapshot:
    running: bool
    processed: int
    completed: int
    failed: int
    blocked: int
    awaiting_confirmation: int
    awaiting_step_approval: int
    paused: int
    cancelled: int
    last_plan_id: str | None
    last_completed_at: str | None
    last_error: str | None

    def as_dict(self) -> dict:
        return {
            "running": self.running,
            "processed": self.processed,
            "completed": self.completed,
            "failed": self.failed,
            "blocked": self.blocked,
            "awaiting_confirmation": self.awaiting_confirmation,
            "awaiting_step_approval": self.awaiting_step_approval,
            "paused": self.paused,
            "cancelled": self.cancelled,
            "last_plan_id": self.last_plan_id,
            "last_completed_at": self.last_completed_at,
            "last_error": self.last_error,
        }


class WorkPlanWorker:
    """Background runner for persistent supervised work plans."""

    def __init__(self, database, service, *, interval_seconds: float = 1.0) -> None:
        self.database = database
        self.service = service
        self.interval_seconds = max(interval_seconds, 0.5)
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self._process_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._processed = 0
        self._completed = 0
        self._failed = 0
        self._blocked = 0
        self._awaiting_confirmation = 0
        self._awaiting_step_approval = 0
        self._paused = 0
        self._cancelled = 0
        self._last_plan_id: str | None = None
        self._last_completed_at: str | None = None
        self._last_error: str | None = None

    def start(self) -> None:
        with self._state_lock:
            if self._thread and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._run,
                name="deskai-work-plan-worker",
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

    def snapshot(self) -> WorkPlanWorkerSnapshot:
        thread = self._thread
        with self._state_lock:
            return WorkPlanWorkerSnapshot(
                running=bool(thread and thread.is_alive() and not self._stop.is_set()),
                processed=self._processed,
                completed=self._completed,
                failed=self._failed,
                blocked=self._blocked,
                awaiting_confirmation=self._awaiting_confirmation,
                awaiting_step_approval=self._awaiting_step_approval,
                paused=self._paused,
                cancelled=self._cancelled,
                last_plan_id=self._last_plan_id,
                last_completed_at=self._last_completed_at,
                last_error=self._last_error,
            )

    def process_available(self, limit: int = 3) -> int:
        count = 0
        for _ in range(max(limit, 0)):
            if not self.process_next():
                break
            count += 1
        return count

    def process_next(self) -> bool:
        with self._process_lock:
            plan_id = self._claim_next()
            if plan_id is None:
                return False
            try:
                result = self.service.execute_claimed(plan_id)
                self._record_outcome(plan_id, str(result.get("status") or "unknown"))
            except Exception as exc:
                logger.error(
                    "Work-plan worker crashed for plan %s: %s",
                    plan_id,
                    exc,
                    exc_info=(type(exc), exc, exc.__traceback__),
                )
                self.service.mark_worker_failure(plan_id, exc)
                self._record_outcome(plan_id, "paused", error=str(exc))
            return True

    def _claim_next(self) -> str | None:
        now = datetime.now(timezone.utc)
        with self.database.session() as session:
            plan = session.scalar(
                select(WorkPlan)
                .where(WorkPlan.status == "queued")
                .order_by(WorkPlan.created_at, WorkPlan.id)
                .limit(1)
            )
            if plan is None:
                return None
            plan.status = "running"
            plan.started_at = plan.started_at or now
            plan.error_message = None
            task = session.get(Task, plan.task_id)
            if task is not None:
                task.status = "running"
                task.started_at = task.started_at or now
                task.completed_at = None
                task.error_message = None
            session.add(
                AuditLog(
                    task_id=plan.task_id,
                    action="work_plan_worker_claimed",
                    target=plan.id,
                    result="Queued work plan claimed by durable background runner",
                    risk_level=0,
                )
            )
            return plan.id

    def _record_outcome(
        self,
        plan_id: str,
        status: str,
        *,
        error: str | None = None,
    ) -> None:
        with self._state_lock:
            self._processed += 1
            if status == "completed":
                self._completed += 1
            elif status == "failed":
                self._failed += 1
            elif status == "blocked":
                self._blocked += 1
            elif status == "awaiting_confirmation":
                self._awaiting_confirmation += 1
            elif status == "awaiting_step_approval":
                self._awaiting_step_approval += 1
            elif status == "paused":
                self._paused += 1
            elif status == "cancelled":
                self._cancelled += 1
            self._last_plan_id = plan_id
            self._last_completed_at = datetime.now(timezone.utc).isoformat()
            self._last_error = error

    def _run(self) -> None:
        while not self._stop.is_set():
            processed = self.process_available(limit=3)
            if processed:
                continue
            self._wake.wait(self.interval_seconds)
            self._wake.clear()
