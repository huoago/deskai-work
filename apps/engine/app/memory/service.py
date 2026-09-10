from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.database.models import Memory, MemoryLearningJob, MemoryVersion
from app.database.session import Database
from app.knowledge.embedding import embed_text
from app.memory.safety import is_sensitive_memory, safe_memory_text

ALLOWED_AUTO_TYPES = {
    "preference",
    "decision",
    "constraint",
    "correction",
    "project_state",
    "workflow",
    "person_role",
}
GLOBAL_PERSIST_TYPES = {"preference", "constraint", "workflow"}
NORMALIZE_RE = re.compile(r"\s+")


@dataclass(slots=True)
class MemoryCandidate:
    scope: str
    type: str
    subject: str
    predicate: str
    value: Any
    confidence: float
    importance: float

    @classmethod
    def from_dict(cls, item: dict[str, Any]) -> "MemoryCandidate":
        return cls(
            scope=str(item.get("scope") or "workspace"),
            type=str(item.get("type") or ""),
            subject=str(item.get("subject") or "").strip(),
            predicate=str(item.get("predicate") or "").strip(),
            value=item.get("value"),
            confidence=max(0.0, min(float(item.get("confidence") or 0.0), 1.0)),
            importance=max(0.0, min(float(item.get("importance") or 0.0), 1.0)),
        )


class MemoryService:
    def __init__(self, database: Database) -> None:
        self.database = database

    def enqueue_learning(
        self,
        *,
        workspace_id: str | None,
        conversation_id: str,
        source_message_id: str,
    ) -> MemoryLearningJob:
        with self.database.session() as session:
            existing = session.scalar(
                select(MemoryLearningJob).where(
                    MemoryLearningJob.source_message_id == source_message_id
                )
            )
            if existing is not None:
                return existing

            # A successful cloud-backed chat proves credentials are currently
            # usable, so previously blocked jobs may be retried.
            for blocked in session.scalars(
                select(MemoryLearningJob).where(MemoryLearningJob.status == "blocked")
            ).all():
                blocked.status = "queued"
                blocked.error_message = None

            job = MemoryLearningJob(
                workspace_id=workspace_id,
                conversation_id=conversation_id,
                source_message_id=source_message_id,
                status="queued",
            )
            session.add(job)
            session.flush()
            return job

    def list_memories(
        self,
        *,
        workspace_id: str | None,
        include_global: bool = True,
        include_inactive: bool = False,
    ) -> list[Memory]:
        with self.database.session() as session:
            statement = select(Memory)
            if workspace_id is None:
                statement = statement.where(Memory.workspace_id.is_(None))
            elif include_global:
                statement = statement.where(
                    or_(Memory.workspace_id == workspace_id, Memory.workspace_id.is_(None))
                )
            else:
                statement = statement.where(Memory.workspace_id == workspace_id)
            if not include_inactive:
                statement = statement.where(Memory.status == "active")
            statement = statement.order_by(
                Memory.importance.desc(),
                Memory.updated_at.desc(),
            )
            return list(session.scalars(statement).all())

    def create_manual(
        self,
        *,
        workspace_id: str | None,
        type: str,
        subject: str,
        predicate: str,
        value: Any,
        importance: float = 0.8,
    ) -> Memory:
        if is_sensitive_memory(subject=subject, predicate=predicate, value=value):
            raise ValueError("Sensitive personal information or credentials cannot be stored in Memory")
        with self.database.session() as session:
            memory = Memory(
                workspace_id=workspace_id,
                type=type,
                subject=subject.strip(),
                predicate=predicate.strip(),
                value_json=value,
                confidence=1.0,
                source_type="manual",
                importance=max(0.0, min(importance, 1.0)),
                valid_from=datetime.now(timezone.utc),
                status="active",
            )
            session.add(memory)
            session.flush()
            return memory

    def update_manual(
        self,
        memory_id: str,
        *,
        type: str | None = None,
        subject: str | None = None,
        predicate: str | None = None,
        value: Any | None = None,
        importance: float | None = None,
        status: str | None = None,
        reason: str = "manual edit",
    ) -> Memory | None:
        with self.database.session() as session:
            memory = session.get(Memory, memory_id)
            if memory is None:
                return None

            next_subject = subject.strip() if subject is not None else memory.subject
            next_predicate = predicate.strip() if predicate is not None else memory.predicate
            next_value = value if value is not None else memory.value_json
            if is_sensitive_memory(
                subject=next_subject,
                predicate=next_predicate,
                value=next_value,
            ):
                raise ValueError("Sensitive personal information or credentials cannot be stored in Memory")

            changed_value = value is not None and not _values_equal(value, memory.value_json)
            if changed_value:
                session.add(
                    MemoryVersion(
                        memory_id=memory.id,
                        value_json=memory.value_json,
                        reason=reason,
                    )
                )
                memory.value_json = value
                memory.valid_from = datetime.now(timezone.utc)

            if type is not None:
                memory.type = type
            if subject is not None:
                memory.subject = next_subject
            if predicate is not None:
                memory.predicate = next_predicate
            if importance is not None:
                memory.importance = max(0.0, min(importance, 1.0))
            if status is not None:
                memory.status = status
                if status != "active":
                    memory.valid_to = datetime.now(timezone.utc)
                else:
                    memory.valid_to = None
            memory.source_type = "manual"
            memory.confidence = 1.0
            session.flush()
            return memory

    def deactivate(self, memory_id: str, *, reason: str = "manual deactivate") -> Memory | None:
        with self.database.session() as session:
            memory = session.get(Memory, memory_id)
            if memory is None:
                return None
            if memory.status == "active":
                session.add(
                    MemoryVersion(
                        memory_id=memory.id,
                        value_json=memory.value_json,
                        reason=reason,
                    )
                )
                memory.status = "inactive"
                memory.valid_to = datetime.now(timezone.utc)
            session.flush()
            return memory

    def versions(self, memory_id: str) -> list[MemoryVersion]:
        with self.database.session() as session:
            return list(
                session.scalars(
                    select(MemoryVersion)
                    .where(MemoryVersion.memory_id == memory_id)
                    .order_by(MemoryVersion.created_at.desc())
                ).all()
            )

    def apply_candidates(
        self,
        *,
        workspace_id: str | None,
        source_message_id: str,
        candidates: list[dict[str, Any]],
        min_confidence: float,
    ) -> tuple[int, int]:
        accepted = 0
        saved = 0
        for raw in candidates:
            candidate = MemoryCandidate.from_dict(raw)
            if not self._candidate_allowed(candidate, min_confidence=min_confidence):
                continue
            accepted += 1
            target_workspace = (
                None
                if candidate.scope == "global" and candidate.type in GLOBAL_PERSIST_TYPES
                else workspace_id
            )
            if self._upsert_candidate(
                workspace_id=target_workspace,
                source_message_id=source_message_id,
                candidate=candidate,
            ):
                saved += 1
        return accepted, saved

    def retrieve(
        self,
        *,
        workspace_id: str | None,
        query: str,
        limit: int = 8,
    ) -> list[dict[str, Any]]:
        memories = self.list_memories(
            workspace_id=workspace_id,
            include_global=True,
            include_inactive=False,
        )
        if not memories:
            return []

        query_vector = embed_text(query)
        scored: list[tuple[float, Memory]] = []
        for memory in memories:
            rendered = _render_memory(memory)
            vector = embed_text(rendered)
            similarity = sum(
                left * right
                for left, right in zip(query_vector, vector, strict=True)
            )
            durable_boost = 0.0
            if memory.type in {"preference", "constraint", "workflow"}:
                durable_boost = 0.10 * memory.importance
            scope_boost = 0.025 if memory.workspace_id == workspace_id and workspace_id else 0.0
            score = similarity + durable_boost + (0.08 * memory.importance) + (0.04 * memory.confidence) + scope_boost
            if similarity >= 0.05 or durable_boost >= 0.07 or memory.importance >= 0.9:
                scored.append((score, memory))

        scored.sort(key=lambda item: (-item[0], -item[1].importance, item[1].updated_at))
        return [
            {
                "id": memory.id,
                "workspace_id": memory.workspace_id,
                "type": memory.type,
                "subject": memory.subject,
                "predicate": memory.predicate,
                "value": memory.value_json,
                "confidence": memory.confidence,
                "importance": memory.importance,
                "source_type": memory.source_type,
                "source_message_id": memory.source_message_id,
                "updated_at": memory.updated_at.isoformat(),
                "score": round(score, 6),
            }
            for score, memory in scored[:limit]
        ]

    def _candidate_allowed(self, candidate: MemoryCandidate, *, min_confidence: float) -> bool:
        if candidate.type not in ALLOWED_AUTO_TYPES:
            return False
        if candidate.scope not in {"global", "workspace"}:
            return False
        if not candidate.subject or not candidate.predicate:
            return False
        if candidate.confidence < min_confidence:
            return False
        if candidate.importance < 0.45:
            return False
        if len(candidate.subject) > 500 or len(candidate.predicate) > 255:
            return False
        if len(safe_memory_text(candidate.value)) > 4000:
            return False
        if is_sensitive_memory(
            subject=candidate.subject,
            predicate=candidate.predicate,
            value=candidate.value,
        ):
            return False
        return True

    def _upsert_candidate(
        self,
        *,
        workspace_id: str | None,
        source_message_id: str,
        candidate: MemoryCandidate,
    ) -> bool:
        with self.database.session() as session:
            memory = _find_matching_memory(
                session,
                workspace_id=workspace_id,
                subject=candidate.subject,
                predicate=candidate.predicate,
            )
            now = datetime.now(timezone.utc)
            if memory is None:
                memory = Memory(
                    workspace_id=workspace_id,
                    type=candidate.type,
                    subject=candidate.subject,
                    predicate=candidate.predicate,
                    value_json=candidate.value,
                    confidence=candidate.confidence,
                    source_type="conversation",
                    source_message_id=source_message_id,
                    importance=candidate.importance,
                    valid_from=now,
                    status="active",
                )
                session.add(memory)
                session.flush()
                return True

            if _values_equal(memory.value_json, candidate.value):
                memory.confidence = max(memory.confidence, candidate.confidence)
                memory.importance = max(memory.importance, candidate.importance)
                memory.source_message_id = source_message_id
                memory.source_type = "conversation"
                if candidate.type == "correction":
                    memory.type = "correction"
                return False

            session.add(
                MemoryVersion(
                    memory_id=memory.id,
                    value_json=memory.value_json,
                    reason=f"updated from conversation message {source_message_id}",
                )
            )
            memory.value_json = candidate.value
            memory.type = candidate.type
            memory.confidence = candidate.confidence
            memory.importance = max(memory.importance, candidate.importance)
            memory.source_type = "conversation"
            memory.source_message_id = source_message_id
            memory.valid_from = now
            memory.valid_to = None
            memory.status = "active"
            return True


def _normalize(value: str) -> str:
    return NORMALIZE_RE.sub(" ", value.strip().casefold())


def _find_matching_memory(
    session: Session,
    *,
    workspace_id: str | None,
    subject: str,
    predicate: str,
) -> Memory | None:
    statement = select(Memory).where(Memory.status == "active")
    if workspace_id is None:
        statement = statement.where(Memory.workspace_id.is_(None))
    else:
        statement = statement.where(Memory.workspace_id == workspace_id)
    candidates = session.scalars(statement).all()
    subject_key = _normalize(subject)
    predicate_key = _normalize(predicate)
    for memory in candidates:
        if (
            _normalize(memory.subject) == subject_key
            and _normalize(memory.predicate) == predicate_key
        ):
            return memory
    return None


def _values_equal(left: Any, right: Any) -> bool:
    return json.dumps(left, sort_keys=True, ensure_ascii=False, default=str) == json.dumps(
        right,
        sort_keys=True,
        ensure_ascii=False,
        default=str,
    )


def _render_memory(memory: Memory) -> str:
    return (
        f"{memory.type} {memory.subject} {memory.predicate} "
        f"{safe_memory_text(memory.value_json)}"
    )
