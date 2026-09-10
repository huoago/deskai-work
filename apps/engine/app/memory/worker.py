from __future__ import annotations

import asyncio
import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select

from app.api.settings import DEFAULTS
from app.database.models import Conversation, MemoryLearningJob, Message, Setting
from app.database.session import Database
from app.memory.service import MemoryService

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class MemoryWorkerSnapshot:
    running: bool
    processed: int
    failed: int
    blocked: int
    last_job_id: str | None
    last_completed_at: str | None
    last_error: str | None

    def as_dict(self) -> dict:
        return {
            "running": self.running,
            "processed": self.processed,
            "failed": self.failed,
            "blocked": self.blocked,
            "last_job_id": self.last_job_id,
            "last_completed_at": self.last_completed_at,
            "last_error": self.last_error,
        }


class MemoryWorker:
    def __init__(
        self,
        database: Database,
        memory_service: MemoryService,
        secret_store,
        provider,
        *,
        interval_seconds: float = 1.25,
    ) -> None:
        self.database = database
        self.memory_service = memory_service
        self.secret_store = secret_store
        self.provider = provider
        self.interval_seconds = max(interval_seconds, 0.5)
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self._process_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._processed = 0
        self._failed = 0
        self._blocked = 0
        self._last_job_id: str | None = None
        self._last_completed_at: str | None = None
        self._last_error: str | None = None

    def start(self) -> None:
        with self._state_lock:
            if self._thread and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._run,
                name="deskai-memory-worker",
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

    def snapshot(self) -> MemoryWorkerSnapshot:
        thread = self._thread
        with self._state_lock:
            return MemoryWorkerSnapshot(
                running=bool(thread and thread.is_alive() and not self._stop.is_set()),
                processed=self._processed,
                failed=self._failed,
                blocked=self._blocked,
                last_job_id=self._last_job_id,
                last_completed_at=self._last_completed_at,
                last_error=self._last_error,
            )

    def process_available(self, limit: int = 20) -> int:
        count = 0
        for _ in range(max(0, limit)):
            if not self.process_next():
                break
            count += 1
        return count

    def process_next(self) -> bool:
        with self._process_lock:
            claim = self._claim_next()
            if claim is None:
                return False
            job_id = claim["job_id"]
            try:
                api_key = self.secret_store.get_openai_api_key()
                if not claim["auto_learn"]:
                    self._complete(job_id, candidate_count=0, saved_count=0, status="skipped")
                    return True
                if claim["privacy_mode"] == "local" or not api_key:
                    self._block(job_id, "Memory learning requires a configured cloud provider in Hybrid/Cloud mode")
                    return True

                extracted = asyncio.run(
                    self.provider.extract_memories(
                        api_key=api_key,
                        model=claim["model"],
                        current_user_message=claim["current_user_message"],
                        conversation_context=claim["conversation_context"],
                    )
                )
                accepted, saved = self.memory_service.apply_candidates(
                    workspace_id=claim["workspace_id"],
                    source_message_id=claim["source_message_id"],
                    candidates=extracted,
                    min_confidence=claim["min_confidence"],
                )
                self._complete(
                    job_id,
                    candidate_count=len(extracted),
                    saved_count=saved,
                    status="completed",
                )
                self._record_success(job_id)
            except Exception as exc:
                self._fail(job_id, exc)
                self._record_failure(job_id, exc)
            return True

    def retry_failed(self) -> int:
        count = 0
        with self.database.session() as session:
            jobs = session.scalars(
                select(MemoryLearningJob).where(
                    MemoryLearningJob.status.in_(["failed", "blocked"])
                )
            ).all()
            for job in jobs:
                job.status = "queued"
                job.error_message = None
                count += 1
        if count:
            self.wake()
        return count

    def _claim_next(self) -> dict | None:
        with self.database.session() as session:
            while True:
                job = session.scalar(
                    select(MemoryLearningJob)
                    .where(MemoryLearningJob.status == "queued")
                    .order_by(MemoryLearningJob.created_at, MemoryLearningJob.id)
                    .limit(1)
                )
                if job is None:
                    return None

                message = session.get(Message, job.source_message_id)
                conversation = session.get(Conversation, job.conversation_id)
                if message is None or conversation is None:
                    job.status = "failed"
                    job.error_message = "Source conversation or message no longer exists"
                    job.attempts += 1
                    continue

                settings = _settings(session)
                job.status = "processing"
                job.attempts += 1
                history = session.scalars(
                    select(Message)
                    .where(Message.conversation_id == job.conversation_id)
                    .order_by(Message.created_at, Message.id)
                ).all()
                previous: list[str] = []
                for item in history:
                    if item.id == message.id:
                        break
                    if item.role in {"user", "assistant"}:
                        previous.append(f"{item.role}: {item.content}")
                context = "\n".join(previous[-6:])[-12000:]

                return {
                    "job_id": job.id,
                    "workspace_id": job.workspace_id,
                    "source_message_id": job.source_message_id,
                    "current_user_message": message.content,
                    "conversation_context": context,
                    "privacy_mode": str(settings["privacy_mode"]),
                    "model": str(settings["default_model"]),
                    "auto_learn": bool(settings["memory_auto_learn"]),
                    "min_confidence": float(settings["memory_min_confidence"]),
                }

    def _complete(
        self,
        job_id: str,
        *,
        candidate_count: int,
        saved_count: int,
        status: str,
    ) -> None:
        with self.database.session() as session:
            job = session.get(MemoryLearningJob, job_id)
            if job is None:
                return
            job.status = status
            job.candidate_count = candidate_count
            job.saved_count = saved_count
            job.error_message = None
            job.completed_at = datetime.now(timezone.utc)

    def _block(self, job_id: str, message: str) -> None:
        with self.database.session() as session:
            job = session.get(MemoryLearningJob, job_id)
            if job is None:
                return
            job.status = "blocked"
            job.error_message = message
        with self._state_lock:
            self._blocked += 1
            self._last_job_id = job_id
            self._last_error = message

    def _fail(self, job_id: str, exc: Exception) -> None:
        with self.database.session() as session:
            job = session.get(MemoryLearningJob, job_id)
            if job is None:
                return
            job.status = "failed"
            job.error_message = f"{type(exc).__name__}: {exc}"[:4000]

    def _record_success(self, job_id: str) -> None:
        with self._state_lock:
            self._processed += 1
            self._last_job_id = job_id
            self._last_completed_at = datetime.now(timezone.utc).isoformat()
            self._last_error = None

    def _record_failure(self, job_id: str, exc: Exception) -> None:
        logger.error(
            "Memory worker failed for %s: %s",
            job_id,
            exc,
            exc_info=(type(exc), exc, exc.__traceback__),
        )
        with self._state_lock:
            self._failed += 1
            self._last_job_id = job_id
            self._last_completed_at = datetime.now(timezone.utc).isoformat()
            self._last_error = f"{type(exc).__name__}: {exc}"

    def _run(self) -> None:
        while not self._stop.is_set():
            processed = self.process_available(limit=4)
            if processed == 0:
                self._wake.wait(self.interval_seconds)
                self._wake.clear()


def _settings(session) -> dict:
    values = dict(DEFAULTS)
    for key in ("privacy_mode", "default_model", "memory_auto_learn", "memory_min_confidence"):
        item = session.get(Setting, key)
        if item is not None:
            values[key] = item.value_json
    return values
