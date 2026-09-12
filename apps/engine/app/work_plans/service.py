from __future__ import annotations

import asyncio
import json
import re
import time
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from app.api.settings import DEFAULTS
from app.database.models import (
    AgentRun,
    AuditLog,
    File,
    Setting,
    Task,
    WorkPlan,
    WorkPlanEvent,
    WorkPlanStep,
)
from app.database.session import Database

REFERENCE_RE = re.compile(
    r"\{\{step:(\d+)\.result(?:\.([A-Za-z0-9_.-]+))?\}\}"
)

PROPOSAL_ENTITY_TYPES = {
    "propose_source_file_edit": "source_edit",
    "propose_source_file_edit_batch": "source_edit_batch",
    "propose_file_organization": "file_organization",
    "propose_file_organization_batch": "file_organization_batch",
    "propose_file_recycle": "file_recycle",
    "propose_file_recycle_batch": "file_recycle_batch",
}


class WorkPlanService:
    """Persistent, dependency-aware orchestration over the existing safe ToolRegistry."""

    def __init__(
        self,
        database: Database,
        secret_store,
        provider,
        tool_registry,
        source_edit_service,
        source_edit_batch_service,
        file_organization_service,
        file_organization_batch_service,
        file_recycle_service,
        file_recycle_batch_service,
    ) -> None:
        self.database = database
        self.secret_store = secret_store
        self.provider = provider
        self.tool_registry = tool_registry
        self.source_edit_service = source_edit_service
        self.source_edit_batch_service = source_edit_batch_service
        self.file_organization_service = file_organization_service
        self.file_organization_batch_service = file_organization_batch_service
        self.file_recycle_service = file_recycle_service
        self.file_recycle_batch_service = file_recycle_batch_service

    def draft(self, task_id: str) -> dict[str, Any]:
        claim = self._task_claim(task_id)
        if claim["execution_mode"] != "plan":
            raise ValueError("Only plan-mode tasks can draft a persistent work plan")

        existing = self.list(task_id=task_id, limit=10)
        if existing:
            return existing[0]

        if claim["privacy_mode"] == "local":
            self._planning_failed(task_id, "Local Only mode has no local planning model configured")
            raise ValueError("Local Only mode has no local planning model configured")
        api_key = self.secret_store.get_openai_api_key()
        if not api_key:
            self._planning_failed(task_id, "Work-plan drafting requires a configured OpenAI API key")
            raise ValueError("Work-plan drafting requires a configured OpenAI API key")

        catalog = self.tool_registry.planning_catalog(
            workspace_id=claim["workspace_id"],
        )
        if not catalog:
            self._planning_failed(task_id, "No permitted tools are available for work-plan drafting")
            raise ValueError("No permitted tools are available for work-plan drafting")

        workspace_context = self._workspace_context(claim["workspace_id"])
        try:
            raw = asyncio.run(
                self.provider.draft_work_plan(
                    api_key=api_key,
                    model=claim["model"],
                    reasoning_effort=claim["reasoning_effort"],
                    user_request=claim["user_request"],
                    tool_catalog=catalog,
                    workspace_context=workspace_context,
                )
            )
            normalized = self._normalize_plan(raw, catalog)
        except Exception as exc:
            message = f"{type(exc).__name__}: {exc}"[:4000]
            self._planning_failed(task_id, message)
            raise ValueError(message) from exc

        plan = WorkPlan(
            task_id=task_id,
            workspace_id=claim["workspace_id"],
            title=normalized["title"],
            summary=normalized["summary"],
            limitations_json=normalized["limitations"],
            status="ready",
        )
        with self.database.session() as session:
            task = session.get(Task, task_id)
            if task is None:
                raise ValueError("Task not found")
            if task.execution_mode != "plan":
                raise ValueError("Task execution mode changed during planning")
            session.add(plan)
            session.flush()
            for item in normalized["steps"]:
                session.add(
                    WorkPlanStep(
                        plan_id=plan.id,
                        position=item["position"],
                        title=item["title"],
                        description=item["description"],
                        tool_name=item["tool_name"],
                        arguments_json=item["arguments"],
                        dependencies_json=item["dependencies"],
                        risk_level=item["risk_level"],
                        execution_mode=item["execution_mode"],
                        status="pending",
                    )
                )
            task.status = "planned"
            task.progress = 0.08
            task.error_message = None
            session.add(
                AuditLog(
                    task_id=task_id,
                    action="work_plan_drafted",
                    target=plan.id,
                    result=(
                        f"steps={len(normalized['steps'])}; "
                        f"max_risk={max(item['risk_level'] for item in normalized['steps'])}; "
                        "filesystem_mutation=false"
                    ),
                    risk_level=max(item["risk_level"] for item in normalized["steps"]),
                )
            )
        return self.get(plan.id)

    def start(self, plan_id: str) -> dict[str, Any]:
        plan = self._plan_record(plan_id)
        if plan.status != "ready":
            raise ValueError("Only a ready work plan can be started")
        self._queue_plan(
            plan_id,
            action="work_plan_queued",
            result="Work plan queued for durable background execution",
        )
        return self.get(plan_id)

    def resume(self, plan_id: str) -> dict[str, Any]:
        plan = self._plan_record(plan_id)
        if plan.status != "awaiting_confirmation":
            raise ValueError("Work plan is not waiting for a confirmation gate")

        gate = self._awaiting_gate(plan_id)
        if gate is None:
            raise ValueError("Work plan has no pending confirmation step")
        state = self._proposal_state(gate.tool_name, str(gate.external_entity_id or ""))
        accepted = self._accepted_proposal_status(gate.tool_name)
        if state == accepted:
            now = datetime.now(timezone.utc)
            with self.database.session() as session:
                step = session.get(WorkPlanStep, gate.id)
                plan_row = session.get(WorkPlan, plan_id)
                task = session.get(Task, plan.task_id)
                if step is None or plan_row is None:
                    raise ValueError("Work plan disappeared")
                step.status = "completed"
                step.completed_at = now
                step.error_message = None
                step.result_summary = (
                    (step.result_summary or "")
                    + f" | human_confirmation_status={state}"
                )[:4000]
                if isinstance(step.result_json, dict):
                    step.result_json = {
                        **step.result_json,
                        "status": state,
                        "human_confirmation_status": state,
                    }
                plan_row.status = "queued"
                plan_row.error_message = None
                if task is not None:
                    task.status = "queued"
                    task.error_message = None
                    task.completed_at = None
                session.add(
                    AuditLog(
                        task_id=plan.task_id,
                        action="work_plan_confirmation_observed",
                        target=step.id,
                        result=(
                            f"tool={step.tool_name}; entity={step.external_entity_type}; "
                            f"entity_id={step.external_entity_id}; status={state}; "
                            "requeued=true"
                        ),
                        risk_level=step.risk_level,
                    )
                )
            return self.get(plan_id)

        if state in {"pending", "applying", "recycling"}:
            raise ValueError("The existing file proposal is still waiting for human confirmation")

        self._block_after_gate(
            plan_id,
            gate.id,
            (
                f"Required proposal did not reach {accepted}; current status={state}. "
                "DeskAI will not automatically re-plan around a rejected or ambiguous file action."
            ),
        )
        return self.get(plan_id)

    def retry(self, plan_id: str) -> dict[str, Any]:
        plan = self._plan_record(plan_id)
        if plan.status not in {"failed", "paused"}:
            raise ValueError("Only failed or paused work plans can retry a step")

        with self.database.session() as session:
            steps = list(
                session.scalars(
                    select(WorkPlanStep)
                    .where(WorkPlanStep.plan_id == plan_id)
                    .order_by(WorkPlanStep.position)
                ).all()
            )
            step = next(
                (item for item in steps if item.status in {"failed", "interrupted"}),
                None,
            )
            if step is None:
                raise ValueError("No failed/interrupted work-plan step is available to retry")
            if step.external_entity_id:
                raise ValueError(
                    "A step that already created a file proposal cannot be retried automatically"
                )
            step.status = "pending"
            step.error_message = None
            step.result_summary = None
            step.result_json = None
            step.tool_call_id = None
            step.started_at = None
            step.completed_at = None

            plan_row = session.get(WorkPlan, plan_id)
            task = session.get(Task, plan.task_id)
            if plan_row is not None:
                plan_row.status = "queued"
                plan_row.error_message = None
            if task is not None:
                task.status = "queued"
                task.error_message = None
                task.completed_at = None
            session.add(
                AuditLog(
                    task_id=plan.task_id,
                    action="work_plan_retry_queued",
                    target=step.id,
                    result=f"position={step.position}; tool={step.tool_name}",
                    risk_level=step.risk_level,
                )
            )
        return self.get(plan_id)

    def cancel(self, plan_id: str) -> dict[str, Any]:
        plan = self._plan_record(plan_id)
        if plan.status in {"completed", "cancelled"}:
            raise ValueError("Work plan is already finished")
        if plan.status == "cancelling":
            return self.get(plan_id)
        if plan.status == "running":
            with self.database.session() as session:
                plan_row = session.get(WorkPlan, plan_id)
                if plan_row is None:
                    raise ValueError("Work plan not found")
                task = session.get(Task, plan_row.task_id)
                plan_row.status = "cancelling"
                plan_row.error_message = (
                    "Cancellation requested; the current tool is allowed to finish safely."
                )
                if task is not None:
                    task.status = "cancelling"
                    task.error_message = plan_row.error_message
                session.add(
                    AuditLog(
                        task_id=plan_row.task_id,
                        action="work_plan_cancel_requested",
                        target=plan_id,
                        result=(
                            "Cancellation requested during active execution; "
                            "no force-kill or rollback was attempted"
                        ),
                        risk_level=0,
                    )
                )
            return self.get(plan_id)

        self._finalize_cancel(
            plan_id,
            reason="Work plan cancelled before another background step was claimed",
        )
        return self.get(plan_id)

    def update_supervision(
        self,
        plan_id: str,
        *,
        max_auto_steps: int | None = None,
        runtime_budget_seconds: int | None = None,
        step_timeout_seconds: int | None = None,
        failure_policy: str | None = None,
        approval_risk_threshold: int | None = None,
    ) -> dict[str, Any]:
        with self.database.session() as session:
            plan = session.get(WorkPlan, plan_id)
            if plan is None:
                raise ValueError("Work plan not found")
            if plan.status in {"completed", "cancelled"}:
                raise ValueError("Finished work plans cannot change supervision settings")

            changes: dict[str, Any] = {}
            if max_auto_steps is not None:
                if not 1 <= int(max_auto_steps) <= 100:
                    raise ValueError("max_auto_steps must be between 1 and 100")
                plan.max_auto_steps = int(max_auto_steps)
                changes["max_auto_steps"] = plan.max_auto_steps
            if runtime_budget_seconds is not None:
                if not 30 <= int(runtime_budget_seconds) <= 86400:
                    raise ValueError("runtime_budget_seconds must be between 30 and 86400")
                plan.runtime_budget_seconds = int(runtime_budget_seconds)
                changes["runtime_budget_seconds"] = plan.runtime_budget_seconds
            if step_timeout_seconds is not None:
                if not 5 <= int(step_timeout_seconds) <= 3600:
                    raise ValueError("step_timeout_seconds must be between 5 and 3600")
                plan.step_timeout_seconds = int(step_timeout_seconds)
                changes["step_timeout_seconds"] = plan.step_timeout_seconds
            if failure_policy is not None:
                if failure_policy not in {"pause", "stop"}:
                    raise ValueError("failure_policy must be pause or stop")
                plan.failure_policy = failure_policy
                changes["failure_policy"] = plan.failure_policy
            if approval_risk_threshold is not None:
                if not 0 <= int(approval_risk_threshold) <= 4:
                    raise ValueError("approval_risk_threshold must be between 0 and 4")
                plan.approval_risk_threshold = int(approval_risk_threshold)
                changes["approval_risk_threshold"] = plan.approval_risk_threshold
            if not changes:
                raise ValueError("No supervision settings were supplied")

            self._event(
                session,
                plan,
                event_type="supervision_updated",
                severity="info",
                message="Work-plan supervision settings were updated",
                data=changes,
            )
            session.add(
                AuditLog(
                    task_id=plan.task_id,
                    action="work_plan_supervision_updated",
                    target=plan.id,
                    result=json.dumps(changes, sort_keys=True),
                    risk_level=0,
                )
            )
        return self.get(plan_id)

    def pause(self, plan_id: str) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        with self.database.session() as session:
            plan = session.get(WorkPlan, plan_id)
            if plan is None:
                raise ValueError("Work plan not found")
            if plan.status in {"paused", "pausing"}:
                return self._payload(session, plan)
            if plan.status in {"awaiting_confirmation", "awaiting_step_approval"}:
                raise ValueError("Work plan is already stopped at a human-control gate")
            if plan.status not in {"queued", "running"}:
                raise ValueError("Only queued or running work plans can be paused")

            task = session.get(Task, plan.task_id)
            if plan.status == "running":
                plan.status = "pausing"
                plan.pause_reason = "User requested pause while a tool was running"
                if task is not None:
                    task.status = "pausing"
                    task.error_message = plan.pause_reason
                message = "Pause requested; current tool may finish, but no later step will start"
                action = "work_plan_pause_requested"
            else:
                plan.status = "paused"
                plan.paused_at = now
                plan.pause_reason = "Paused by user before the next tool started"
                if task is not None:
                    task.status = "paused"
                    task.error_message = plan.pause_reason
                message = "Queued work plan paused before Worker claim"
                action = "work_plan_paused"

            self._event(
                session,
                plan,
                event_type=action,
                severity="action",
                message=message,
                data={},
            )
            session.add(
                AuditLog(
                    task_id=plan.task_id,
                    action=action,
                    target=plan.id,
                    result=message,
                    risk_level=0,
                )
            )
        return self.get(plan_id)

    def continue_plan(self, plan_id: str) -> dict[str, Any]:
        with self.database.session() as session:
            plan = session.get(WorkPlan, plan_id)
            if plan is None:
                raise ValueError("Work plan not found")
            if plan.status != "paused":
                raise ValueError("Only a paused work plan can continue")
            unresolved = list(
                session.scalars(
                    select(WorkPlanStep).where(
                        WorkPlanStep.plan_id == plan_id,
                        WorkPlanStep.status.in_(("failed", "interrupted")),
                    )
                ).all()
            )
            if unresolved:
                raise ValueError(
                    "Paused plan has failed/interrupted steps; use Retry instead of Continue"
                )
            task = session.get(Task, plan.task_id)
            plan.status = "queued"
            plan.pause_reason = None
            plan.paused_at = None
            plan.error_message = None
            if task is not None:
                task.status = "queued"
                task.error_message = None
                task.completed_at = None
            self._event(
                session,
                plan,
                event_type="continued",
                severity="info",
                message="Paused work plan requeued for background execution",
                data={},
            )
            session.add(
                AuditLog(
                    task_id=plan.task_id,
                    action="work_plan_continued",
                    target=plan.id,
                    result="Paused work plan requeued",
                    risk_level=0,
                )
            )
        return self.get(plan_id)

    def approve_step(self, plan_id: str, step_id: str) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        with self.database.session() as session:
            plan = session.get(WorkPlan, plan_id)
            step = session.get(WorkPlanStep, step_id)
            if plan is None or step is None or step.plan_id != plan_id:
                raise ValueError("Work plan step not found")
            if plan.status != "awaiting_step_approval" or step.status != "awaiting_step_approval":
                raise ValueError("Step is not waiting for supervision approval")
            if step.execution_mode != "auto":
                raise ValueError("Proposal-gate steps use their original confirmation flow")

            step.approved_at = now
            step.status = "pending"
            plan.status = "queued"
            plan.error_message = None
            task = session.get(Task, plan.task_id)
            if task is not None:
                task.status = "queued"
                task.error_message = None
            self._event(
                session,
                plan,
                step_id=step.id,
                event_type="step_approved",
                severity="info",
                message=f"Step {step.position} approved for execution",
                data={"risk_level": step.risk_level, "tool_name": step.tool_name},
            )
            session.add(
                AuditLog(
                    task_id=plan.task_id,
                    action="work_plan_step_approved",
                    target=step.id,
                    result=f"position={step.position}; risk={step.risk_level}",
                    risk_level=step.risk_level,
                )
            )
        return self.get(plan_id)

    def skip_step(self, plan_id: str, step_id: str, *, reason: str) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        clean_reason = reason.strip()[:2000]
        if not clean_reason:
            raise ValueError("Skip requires a human-supplied reason")
        with self.database.session() as session:
            plan = session.get(WorkPlan, plan_id)
            step = session.get(WorkPlanStep, step_id)
            if plan is None or step is None or step.plan_id != plan_id:
                raise ValueError("Work plan step not found")
            if step.status not in {"pending", "awaiting_step_approval"}:
                raise ValueError("Only pending or approval-waiting steps can be skipped")
            if step.execution_mode != "auto" or step.external_entity_id:
                raise ValueError("File proposal / confirmation-gate steps cannot be skipped")

            later = list(
                session.scalars(
                    select(WorkPlanStep).where(
                        WorkPlanStep.plan_id == plan_id,
                        WorkPlanStep.position > step.position,
                    )
                ).all()
            )
            dependents = [
                item.position
                for item in later
                if step.position in {int(v) for v in (item.dependencies_json or [])}
                and item.status not in {"completed", "skipped", "cancelled"}
            ]
            if dependents:
                raise ValueError(
                    "Step cannot be skipped because active later steps depend on it: "
                    + ",".join(str(value) for value in dependents)
                )

            step.status = "skipped"
            step.skipped_at = now
            step.completed_at = now
            step.skip_reason = clean_reason
            if plan.status == "awaiting_step_approval":
                plan.status = "queued"
                task = session.get(Task, plan.task_id)
                if task is not None:
                    task.status = "queued"
                    task.error_message = None
            self._event(
                session,
                plan,
                step_id=step.id,
                event_type="step_skipped",
                severity="warning",
                message=f"Step {step.position} skipped by human supervisor",
                data={"reason": clean_reason, "tool_name": step.tool_name},
            )
            session.add(
                AuditLog(
                    task_id=plan.task_id,
                    action="work_plan_step_skipped",
                    target=step.id,
                    result=f"position={step.position}; reason={clean_reason}",
                    risk_level=step.risk_level,
                )
            )
        return self.get(plan_id)

    def list_events(
        self,
        plan_id: str,
        *,
        unread_only: bool = False,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        with self.database.session() as session:
            if session.get(WorkPlan, plan_id) is None:
                raise ValueError("Work plan not found")
            statement = (
                select(WorkPlanEvent)
                .where(WorkPlanEvent.plan_id == plan_id)
                .order_by(WorkPlanEvent.created_at.desc(), WorkPlanEvent.id.desc())
                .limit(max(1, min(int(limit), 500)))
            )
            if unread_only:
                statement = statement.where(WorkPlanEvent.acknowledged_at.is_(None))
            rows = list(session.scalars(statement).all())
            return [self._event_payload(item) for item in rows]

    def acknowledge_event(self, plan_id: str, event_id: str) -> dict[str, Any]:
        with self.database.session() as session:
            event = session.get(WorkPlanEvent, event_id)
            if event is None or event.plan_id != plan_id:
                raise ValueError("Work plan event not found")
            if event.acknowledged_at is None:
                event.acknowledged_at = datetime.now(timezone.utc)
            return self._event_payload(event)

    def recover_interrupted(self) -> int:
        now = datetime.now(timezone.utc)
        recovered = 0
        with self.database.session() as session:
            plans = list(
                session.scalars(
                    select(WorkPlan).where(
                        WorkPlan.status.in_(("running", "cancelling", "pausing"))
                    )
                ).all()
            )
            for plan in plans:
                recovered += 1
                steps = list(
                    session.scalars(
                        select(WorkPlanStep).where(WorkPlanStep.plan_id == plan.id)
                    ).all()
                )
                running_steps = [step for step in steps if step.status == "running"]
                task = session.get(Task, plan.task_id)

                if running_steps:
                    plan.status = "paused"
                    plan.paused_at = now
                    plan.pause_reason = (
                        "Engine stopped while a supervised work-plan tool was running"
                    )
                    plan.error_message = (
                        "That step was not replayed automatically; explicit retry is required."
                    )
                    for step in running_steps:
                        step.status = "interrupted"
                        step.error_message = (
                            "Interrupted before DeskAI could prove tool completion"
                        )
                    if task is not None:
                        task.status = "blocked"
                        task.error_message = plan.error_message
                    self._event(
                        session,
                        plan,
                        event_type="interrupted",
                        severity="action",
                        message=plan.error_message,
                        data={"running_steps": [step.position for step in running_steps]},
                    )
                    session.add(
                        AuditLog(
                            task_id=plan.task_id,
                            action="work_plan_interrupted",
                            target=plan.id,
                            result=plan.error_message,
                            risk_level=0,
                        )
                    )
                    continue

                if plan.status == "cancelling":
                    for step in steps:
                        if step.status in {
                            "pending",
                            "awaiting_confirmation",
                            "awaiting_step_approval",
                            "failed",
                            "interrupted",
                        }:
                            step.status = "cancelled"
                            step.completed_at = step.completed_at or now
                    plan.status = "cancelled"
                    plan.cancelled_at = now
                    plan.completed_at = now
                    plan.error_message = None
                    if task is not None:
                        task.status = "cancelled"
                        task.completed_at = now
                        task.error_message = None
                    self._event(
                        session,
                        plan,
                        event_type="startup_cancelled",
                        severity="info",
                        message="Recovered cancellation request during Engine startup",
                        data={},
                    )
                    session.add(
                        AuditLog(
                            task_id=plan.task_id,
                            action="work_plan_startup_cancelled",
                            target=plan.id,
                            result=(
                                "Recovered cancellation request with no ambiguous "
                                "running step; pending execution remains stopped"
                            ),
                            risk_level=0,
                        )
                    )
                    continue

                if plan.status == "pausing":
                    plan.status = "paused"
                    plan.paused_at = now
                    plan.pause_reason = plan.pause_reason or (
                        "Recovered a pending pause request during Engine startup"
                    )
                    plan.error_message = plan.pause_reason
                    if task is not None:
                        task.status = "paused"
                        task.error_message = plan.pause_reason
                    self._event(
                        session,
                        plan,
                        event_type="startup_paused",
                        severity="info",
                        message=plan.pause_reason,
                        data={},
                    )
                    session.add(
                        AuditLog(
                            task_id=plan.task_id,
                            action="work_plan_startup_paused",
                            target=plan.id,
                            result=plan.pause_reason,
                            risk_level=0,
                        )
                    )
                    continue

                plan.status = "queued"
                plan.error_message = None
                if task is not None:
                    task.status = "queued"
                    task.error_message = None
                    task.completed_at = None
                self._event(
                    session,
                    plan,
                    event_type="startup_requeued",
                    severity="info",
                    message=(
                        "Safe checkpoint recovered; completed results retained and "
                        "remaining work requeued"
                    ),
                    data={},
                )
                session.add(
                    AuditLog(
                        task_id=plan.task_id,
                        action="work_plan_startup_requeued",
                        target=plan.id,
                        result=(
                            "Previous completed checkpoints were retained; "
                            "no running step was found, so remaining work was safely requeued"
                        ),
                        risk_level=0,
                    )
                )

            runs = list(
                session.scalars(
                    select(AgentRun).where(
                        AgentRun.status == "running",
                        AgentRun.model == "work-plan-executor",
                    )
                ).all()
            )
            for run in runs:
                run.status = "interrupted"
                run.completed_at = now
        return recovered

    def execute_claimed(self, plan_id: str) -> dict[str, Any]:
        plan = self._plan_record(plan_id)
        if plan.status == "cancelling":
            self._finalize_cancel(
                plan_id,
                reason="Cancellation was requested before background execution started",
            )
            return self.get(plan_id)
        if plan.status != "running":
            raise ValueError("Work plan must be claimed by the background worker first")
        return self._advance(plan_id, event="work_plan_worker_started")

    def mark_worker_failure(self, plan_id: str, exc: Exception) -> None:
        message = f"{type(exc).__name__}: {exc}"[:4000]
        now = datetime.now(timezone.utc)
        with self.database.session() as session:
            plan = session.get(WorkPlan, plan_id)
            if plan is None or plan.status in {"completed", "cancelled"}:
                return
            steps = list(
                session.scalars(
                    select(WorkPlanStep).where(WorkPlanStep.plan_id == plan_id)
                ).all()
            )
            for step in steps:
                if step.status == "running":
                    step.status = "interrupted"
                    step.error_message = (
                        "Background worker failed before DeskAI could prove tool completion"
                    )
            plan.status = "paused"
            plan.error_message = (
                "Background work-plan runner failed. The current step was not replayed."
            )
            task = session.get(Task, plan.task_id)
            if task is not None:
                task.status = "blocked"
                task.error_message = plan.error_message
            runs = list(
                session.scalars(
                    select(AgentRun).where(
                        AgentRun.task_id == plan.task_id,
                        AgentRun.status == "running",
                        AgentRun.model == "work-plan-executor",
                    )
                ).all()
            )
            for run in runs:
                run.status = "interrupted"
                run.completed_at = now
            session.add(
                AuditLog(
                    task_id=plan.task_id,
                    action="work_plan_worker_failed",
                    target=plan_id,
                    result=message,
                    risk_level=0,
                )
            )

    def list(
        self,
        *,
        workspace_id: str | None = None,
        task_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        with self.database.session() as session:
            statement = (
                select(WorkPlan)
                .order_by(WorkPlan.created_at.desc())
                .limit(max(1, min(int(limit), 500)))
            )
            if workspace_id:
                statement = statement.where(WorkPlan.workspace_id == workspace_id)
            if task_id:
                statement = statement.where(WorkPlan.task_id == task_id)
            plans = list(session.scalars(statement).all())
            return [self._payload(session, plan) for plan in plans]

    def get(self, plan_id: str) -> dict[str, Any]:
        with self.database.session() as session:
            plan = session.get(WorkPlan, plan_id)
            if plan is None:
                raise ValueError("Work plan not found")
            return self._payload(session, plan)

    def _advance(self, plan_id: str, *, event: str) -> dict[str, Any]:
        run_id = self._start_execution(plan_id, event=event)
        try:
            for _ in range(100):
                current_plan = self._plan_record(plan_id)
                if current_plan.status == "cancelling":
                    self._finalize_cancel(
                        plan_id,
                        run_id=run_id,
                        reason="Cancellation request observed by background runner",
                    )
                    return self.get(plan_id)
                if current_plan.status == "pausing":
                    self._finalize_pause(
                        plan_id,
                        run_id=run_id,
                        reason=current_plan.pause_reason or "Pause requested by user",
                    )
                    return self.get(plan_id)

                pause_for_budget = False
                awaiting_approval = False
                with self.database.session() as session:
                    plan = session.get(WorkPlan, plan_id)
                    if plan is None:
                        raise RuntimeError("Work plan disappeared")
                    task = session.get(Task, plan.task_id)
                    steps = list(
                        session.scalars(
                            select(WorkPlanStep)
                            .where(WorkPlanStep.plan_id == plan_id)
                            .order_by(WorkPlanStep.position)
                        ).all()
                    )
                    terminal = {
                        step.position
                        for step in steps
                        if step.status in {"completed", "skipped"}
                    }
                    if steps and len(terminal) == len(steps):
                        pass_complete = True
                        step_id = None
                    else:
                        pass_complete = False
                        waiting = next(
                            (
                                step
                                for step in steps
                                if step.status == "awaiting_confirmation"
                            ),
                            None,
                        )
                        if waiting is not None:
                            self._finish_run(run_id, "awaiting_confirmation")
                            return self._payload(session, plan)

                        approval_wait = next(
                            (
                                step
                                for step in steps
                                if step.status == "awaiting_step_approval"
                            ),
                            None,
                        )
                        if approval_wait is not None:
                            self._finish_run(run_id, "awaiting_step_approval")
                            return self._payload(session, plan)

                        budget_reason = self._budget_exhaustion_reason(plan)
                        if budget_reason:
                            plan.status = "paused"
                            plan.pause_reason = budget_reason
                            plan.paused_at = datetime.now(timezone.utc)
                            plan.error_message = budget_reason
                            if task is not None:
                                task.status = "paused"
                                task.error_message = budget_reason
                            self._event(
                                session,
                                plan,
                                event_type="budget_exhausted",
                                severity="action",
                                message=budget_reason,
                                data={
                                    "auto_steps_used": plan.auto_steps_used,
                                    "max_auto_steps": plan.max_auto_steps,
                                    "runtime_seconds_used": plan.runtime_seconds_used,
                                    "runtime_budget_seconds": plan.runtime_budget_seconds,
                                },
                            )
                            session.add(
                                AuditLog(
                                    task_id=plan.task_id,
                                    agent_run_id=run_id,
                                    action="work_plan_budget_exhausted",
                                    target=plan.id,
                                    result=budget_reason,
                                    risk_level=0,
                                )
                            )
                            pause_for_budget = True
                            step_id = None
                        else:
                            step = next(
                                (
                                    item
                                    for item in steps
                                    if item.status == "pending"
                                    and set(
                                        int(value)
                                        for value in (item.dependencies_json or [])
                                    ).issubset(terminal)
                                ),
                                None,
                            )
                            if step is None:
                                raise RuntimeError(
                                    "Work plan has pending steps with unsatisfied dependencies"
                                )

                            if self._step_needs_approval(plan, step):
                                step.status = "awaiting_step_approval"
                                plan.status = "awaiting_step_approval"
                                plan.error_message = (
                                    f"Step {step.position} requires human approval before execution"
                                )
                                if task is not None:
                                    task.status = "awaiting_step_approval"
                                    task.error_message = plan.error_message
                                self._event(
                                    session,
                                    plan,
                                    step_id=step.id,
                                    event_type="step_approval_required",
                                    severity="action",
                                    message=plan.error_message,
                                    data={
                                        "position": step.position,
                                        "tool_name": step.tool_name,
                                        "risk_level": step.risk_level,
                                        "approval_risk_threshold": plan.approval_risk_threshold,
                                    },
                                )
                                session.add(
                                    AuditLog(
                                        task_id=plan.task_id,
                                        agent_run_id=run_id,
                                        action="work_plan_step_approval_required",
                                        target=step.id,
                                        result=plan.error_message,
                                        risk_level=step.risk_level,
                                    )
                                )
                                awaiting_approval = True
                                step_id = None
                            else:
                                step_id = step.id

                if pass_complete:
                    self._complete(plan_id, run_id)
                    return self.get(plan_id)
                if pause_for_budget:
                    self._finish_run(run_id, "paused")
                    return self.get(plan_id)
                if awaiting_approval:
                    self._finish_run(run_id, "awaiting_step_approval")
                    return self.get(plan_id)
                if step_id is None:
                    raise RuntimeError("Work plan did not resolve a runnable step")

                self._execute_step(plan_id, step_id, run_id)
                current = self.get(plan_id)
                if current["status"] == "cancelling":
                    self._finalize_cancel(
                        plan_id,
                        run_id=run_id,
                        reason="Cancellation request observed after current tool completed",
                    )
                    return self.get(plan_id)
                if current["status"] == "pausing":
                    self._finalize_pause(
                        plan_id,
                        run_id=run_id,
                        reason=current.get("pause_reason") or "Pause requested by user",
                    )
                    return self.get(plan_id)
                if current["status"] in {
                    "awaiting_confirmation",
                    "awaiting_step_approval",
                    "failed",
                    "blocked",
                    "cancelled",
                    "paused",
                }:
                    return current

            raise RuntimeError("Work plan exceeded the supervised execution loop limit")
        except Exception as exc:
            self._fail(plan_id, run_id, f"{type(exc).__name__}: {exc}")
            return self.get(plan_id)

    def _execute_step(self, plan_id: str, step_id: str, run_id: str) -> None:
        now = datetime.now(timezone.utc)
        resolve_error: str | None = None
        arguments: dict[str, Any] = {}
        stop_before_step: str | None = None
        with self.database.session() as session:
            plan = session.get(WorkPlan, plan_id)
            step = session.get(WorkPlanStep, step_id)
            if plan is None or step is None:
                raise RuntimeError("Work plan step disappeared")
            if plan.status in {"cancelling", "pausing"}:
                stop_before_step = plan.status
            else:
                step.status = "running"
                step.started_at = now
                step.timeout_exceeded = False
                plan.auto_steps_used += 1
            task_id = plan.task_id
            workspace_id = plan.workspace_id
            tool_name = step.tool_name
            execution_mode = step.execution_mode
            risk_level = step.risk_level
            if stop_before_step is None:
                try:
                    arguments = self._resolve_arguments(session, step)
                except Exception as exc:
                    resolve_error = f"{type(exc).__name__}: {exc}"[:4000]
                    step.status = "failed"
                    step.error_message = resolve_error
                    step.completed_at = datetime.now(timezone.utc)

        if stop_before_step is not None:
            return

        if resolve_error is not None:
            self._fail_step(plan_id, step_id, run_id, resolve_error)
            return

        started = time.monotonic()
        result = self.tool_registry.execute(
            agent_run_id=run_id,
            task_id=task_id,
            workspace_id=workspace_id,
            tool_name=tool_name,
            arguments=arguments,
        )
        duration = max(0.0, time.monotonic() - started)
        timeout_exceeded = self._record_attempt_runtime(
            plan_id,
            step_id,
            duration,
            run_id=run_id,
        )

        if not result.get("ok"):
            message = str(result.get("error") or "Tool execution failed")
            self._fail_step(plan_id, step_id, run_id, message)
            return

        actual = result.get("result")
        summary = json.dumps(
            actual,
            ensure_ascii=False,
            separators=(",", ":"),
            default=str,
        )[:4000]
        tool_call_id = str(result.get("tool_call_id") or "") or None

        if execution_mode == "proposal_gate":
            if not isinstance(actual, dict) or not actual.get("id"):
                self._fail_step(
                    plan_id,
                    step_id,
                    run_id,
                    "Proposal tool did not return a persistent proposal id",
                )
                return
            external_type = PROPOSAL_ENTITY_TYPES.get(tool_name)
            if not external_type:
                self._fail_step(plan_id, step_id, run_id, "Unknown proposal-gate tool")
                return
            with self.database.session() as session:
                plan_row = session.get(WorkPlan, plan_id)
                step = session.get(WorkPlanStep, step_id)
                task = session.get(Task, task_id)
                if plan_row is None or step is None:
                    raise RuntimeError("Work plan disappeared after proposal creation")
                cancel_requested = plan_row.status == "cancelling"
                step.status = "completed" if cancel_requested else "awaiting_confirmation"
                step.completed_at = (
                    datetime.now(timezone.utc) if cancel_requested else None
                )
                step.result_summary = summary
                step.result_json = actual
                step.tool_call_id = tool_call_id
                step.external_entity_type = external_type
                step.external_entity_id = str(actual["id"])
                if not cancel_requested:
                    plan_row.status = "awaiting_confirmation"
                    plan_row.error_message = None
                    plan_row.pause_reason = None
                if task is not None:
                    task.progress = self._task_progress(session, plan_id)
                    if not cancel_requested:
                        task.status = "awaiting_confirmation"
                        task.error_message = (
                            "Work plan paused at an existing file-transaction confirmation gate"
                        )
                self._event(
                    session,
                    plan_row,
                    step_id=step.id,
                    event_type=(
                        "proposal_created_after_cancel_request"
                        if cancel_requested
                        else "proposal_confirmation_required"
                    ),
                    severity="action",
                    message=(
                        "Protected file proposal created; original confirmation is required"
                    ),
                    data={
                        "entity_type": external_type,
                        "entity_id": str(actual["id"]),
                        "timeout_exceeded": timeout_exceeded,
                    },
                )
                session.add(
                    AuditLog(
                        task_id=task_id,
                        agent_run_id=run_id,
                        action=(
                            "work_plan_step_completed_after_cancel_request"
                            if cancel_requested
                            else "work_plan_waiting_confirmation"
                        ),
                        target=step_id,
                        result=(
                            f"tool={tool_name}; entity_type={external_type}; "
                            f"entity_id={actual['id']}; automatic_file_mutation=false; "
                            f"cancel_requested={str(cancel_requested).lower()}; "
                            f"duration_seconds={duration:.3f}; "
                            f"timeout_exceeded={str(timeout_exceeded).lower()}"
                        ),
                        risk_level=risk_level,
                    )
                )
            if cancel_requested:
                return
            self._finish_run(run_id, "awaiting_confirmation")
            return

        with self.database.session() as session:
            plan_row = session.get(WorkPlan, plan_id)
            step = session.get(WorkPlanStep, step_id)
            task = session.get(Task, task_id)
            if plan_row is None or step is None:
                raise RuntimeError("Work plan step disappeared after tool execution")
            cancel_requested = plan_row.status == "cancelling"
            pause_requested = plan_row.status == "pausing"
            step.status = "completed"
            step.completed_at = datetime.now(timezone.utc)
            step.result_summary = summary
            step.result_json = actual
            step.tool_call_id = tool_call_id
            step.error_message = None

            if timeout_exceeded and not cancel_requested and not pause_requested:
                plan_row.status = "paused"
                plan_row.paused_at = datetime.now(timezone.utc)
                plan_row.pause_reason = (
                    f"Step {step.position} exceeded the supervised timeout "
                    f"of {plan_row.step_timeout_seconds} seconds"
                )
                plan_row.error_message = plan_row.pause_reason
                if task is not None:
                    task.status = "paused"
                    task.error_message = plan_row.pause_reason
                self._event(
                    session,
                    plan_row,
                    step_id=step.id,
                    event_type="step_timeout",
                    severity="action",
                    message=plan_row.pause_reason,
                    data={
                        "duration_seconds": duration,
                        "step_timeout_seconds": plan_row.step_timeout_seconds,
                        "tool_name": step.tool_name,
                    },
                )

            if task is not None:
                task.progress = self._task_progress(session, plan_id)
            self._event(
                session,
                plan_row,
                step_id=step.id,
                event_type="step_completed",
                severity="info",
                message=f"Step {step.position} completed",
                data={
                    "tool_name": tool_name,
                    "duration_seconds": duration,
                    "timeout_exceeded": timeout_exceeded,
                },
            )
            session.add(
                AuditLog(
                    task_id=task_id,
                    agent_run_id=run_id,
                    action=(
                        "work_plan_step_completed_after_cancel_request"
                        if cancel_requested
                        else "work_plan_step_completed"
                    ),
                    target=step_id,
                    result=(
                        f"position={step.position}; tool={tool_name}; "
                        f"cancel_requested={str(cancel_requested).lower()}; "
                        f"duration_seconds={duration:.3f}; "
                        f"timeout_exceeded={str(timeout_exceeded).lower()}"
                    ),
                    risk_level=risk_level,
                )
            )

    def _resolve_arguments(self, session, step: WorkPlanStep) -> dict[str, Any]:
        dependencies = {int(value) for value in (step.dependencies_json or [])}
        prior: dict[int, WorkPlanStep] = {}
        if dependencies:
            prior = {
                item.position: item
                for item in session.scalars(
                    select(WorkPlanStep).where(
                        WorkPlanStep.plan_id == step.plan_id,
                        WorkPlanStep.position.in_(dependencies),
                    )
                ).all()
            }

        def lookup(position: int, path: str | None) -> Any:
            if position not in dependencies:
                raise ValueError(
                    f"Step {step.position} references step {position} without declaring it as a dependency"
                )
            source = prior.get(position)
            if source is None or source.status != "completed":
                raise ValueError(f"Dependency step {position} is not completed")
            value: Any = source.result_json
            if path:
                for token in path.split("."):
                    if isinstance(value, list):
                        try:
                            value = value[int(token)]
                        except (ValueError, IndexError) as exc:
                            raise ValueError(
                                f"Invalid list path in step reference: {path}"
                            ) from exc
                    elif isinstance(value, dict) and token in value:
                        value = value[token]
                    else:
                        raise ValueError(f"Missing value in step reference: {path}")
            return value

        def resolve(value: Any) -> Any:
            if isinstance(value, dict):
                return {key: resolve(item) for key, item in value.items()}
            if isinstance(value, list):
                return [resolve(item) for item in value]
            if not isinstance(value, str):
                return value

            matches = list(REFERENCE_RE.finditer(value))
            if not matches:
                return value
            if len(matches) == 1 and matches[0].span() == (0, len(value)):
                match = matches[0]
                return lookup(int(match.group(1)), match.group(2))

            output = value
            for match in matches:
                resolved = lookup(int(match.group(1)), match.group(2))
                replacement = (
                    resolved
                    if isinstance(resolved, str)
                    else json.dumps(resolved, ensure_ascii=False, separators=(",", ":"), default=str)
                )
                output = output.replace(match.group(0), str(replacement))
            return output

        resolved = resolve(step.arguments_json)
        if not isinstance(resolved, dict):
            raise ValueError("Resolved work-plan tool arguments must be an object")
        return resolved

    def _normalize_plan(
        self,
        raw: dict[str, Any],
        catalog: list[dict[str, Any]],
    ) -> dict[str, Any]:
        if not isinstance(raw, dict):
            raise ValueError("Planner output must be an object")
        raw_steps = raw.get("steps")
        if not isinstance(raw_steps, list) or not 1 <= len(raw_steps) <= 12:
            raise ValueError("Work plan must contain between 1 and 12 steps")

        tools = {str(item["name"]): item for item in catalog}
        steps: list[dict[str, Any]] = []
        for position, raw_step in enumerate(raw_steps, start=1):
            if not isinstance(raw_step, dict):
                raise ValueError("Every work-plan step must be an object")
            tool_name = str(raw_step.get("tool_name") or "")
            metadata = tools.get(tool_name)
            if metadata is None:
                raise ValueError(f"Planner selected unavailable tool: {tool_name}")

            raw_arguments = raw_step.get("arguments_json")
            if not isinstance(raw_arguments, str):
                raise ValueError("arguments_json must be a JSON string")
            try:
                arguments = json.loads(raw_arguments)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"Step {position} arguments_json is invalid: {exc}"
                ) from exc
            if not isinstance(arguments, dict):
                raise ValueError("Each step's arguments_json must decode to an object")

            raw_dependencies = raw_step.get("depends_on_positions") or []
            if not isinstance(raw_dependencies, list):
                raise ValueError("depends_on_positions must be a list")
            dependencies = sorted({int(value) for value in raw_dependencies})
            if any(value < 1 or value >= position for value in dependencies):
                raise ValueError(
                    f"Step {position} dependencies must point only to earlier steps"
                )

            self._validate_references(arguments, position, set(dependencies))
            steps.append(
                {
                    "position": position,
                    "title": str(raw_step.get("title") or f"Step {position}")[:1024],
                    "description": str(raw_step.get("description") or "")[:4000],
                    "tool_name": tool_name,
                    "arguments": arguments,
                    "dependencies": dependencies,
                    "risk_level": int(metadata["risk_level"]),
                    "execution_mode": str(metadata["execution_mode"]),
                }
            )

        limitations = raw.get("limitations") or []
        if not isinstance(limitations, list):
            limitations = []
        return {
            "title": str(raw.get("plan_title") or "DeskAI work plan")[:1024],
            "summary": str(raw.get("summary") or "")[:6000],
            "limitations": [str(value)[:1000] for value in limitations[:8]],
            "steps": steps,
        }

    @staticmethod
    def _validate_references(
        value: Any,
        position: int,
        dependencies: set[int],
    ) -> None:
        if isinstance(value, dict):
            for item in value.values():
                WorkPlanService._validate_references(item, position, dependencies)
            return
        if isinstance(value, list):
            for item in value:
                WorkPlanService._validate_references(item, position, dependencies)
            return
        if not isinstance(value, str):
            return
        for match in REFERENCE_RE.finditer(value):
            reference = int(match.group(1))
            if reference >= position or reference not in dependencies:
                raise ValueError(
                    f"Step {position} contains a result reference to undeclared dependency step {reference}"
                )

    def _workspace_context(self, workspace_id: str) -> list[dict[str, Any]]:
        with self.database.session() as session:
            files = list(
                session.scalars(
                    select(File)
                    .where(
                        File.workspace_id == workspace_id,
                        File.status.notin_({"deleted", "revoked"}),
                    )
                    .order_by(File.filename, File.id)
                    .limit(100)
                ).all()
            )
            return [
                {
                    "file_id": item.id,
                    "filename": item.filename,
                    "extension": item.extension,
                    "status": item.status,
                    "size": item.size,
                    "sha256": item.sha256,
                }
                for item in files
            ]

    def _task_claim(self, task_id: str) -> dict[str, Any]:
        with self.database.session() as session:
            task = session.get(Task, task_id)
            if task is None or not task.workspace_id:
                raise ValueError("Task not found or has no active Workspace")
            settings = dict(DEFAULTS)
            for key in ("privacy_mode", "default_model", "reasoning_level"):
                item = session.get(Setting, key)
                if item is not None:
                    settings[key] = item.value_json
            return {
                "task_id": task.id,
                "workspace_id": task.workspace_id,
                "user_request": task.user_request,
                "execution_mode": task.execution_mode,
                "privacy_mode": str(settings["privacy_mode"]),
                "model": str(settings["default_model"]),
                "reasoning_effort": str(settings["reasoning_level"]),
            }

    def _plan_record(self, plan_id: str) -> WorkPlan:
        with self.database.session() as session:
            plan = session.get(WorkPlan, plan_id)
            if plan is None:
                raise ValueError("Work plan not found")
            session.expunge(plan)
            return plan

    def _awaiting_gate(self, plan_id: str) -> WorkPlanStep | None:
        with self.database.session() as session:
            step = session.scalar(
                select(WorkPlanStep)
                .where(
                    WorkPlanStep.plan_id == plan_id,
                    WorkPlanStep.status == "awaiting_confirmation",
                )
                .order_by(WorkPlanStep.position)
            )
            if step is not None:
                session.expunge(step)
            return step

    def _proposal_state(self, tool_name: str, entity_id: str) -> str:
        service = {
            "propose_source_file_edit": self.source_edit_service,
            "propose_source_file_edit_batch": self.source_edit_batch_service,
            "propose_file_organization": self.file_organization_service,
            "propose_file_organization_batch": self.file_organization_batch_service,
            "propose_file_recycle": self.file_recycle_service,
            "propose_file_recycle_batch": self.file_recycle_batch_service,
        }.get(tool_name)
        if service is None:
            raise ValueError("Work-plan proposal gate references an unsupported tool")
        try:
            payload = service.get(entity_id)
        except ValueError as exc:
            raise ValueError("The gated file proposal no longer exists") from exc
        return str(payload.get("status") or "unknown")

    @staticmethod
    def _accepted_proposal_status(tool_name: str) -> str:
        if tool_name in {
            "propose_source_file_edit",
            "propose_source_file_edit_batch",
            "propose_file_organization",
            "propose_file_organization_batch",
        }:
            return "applied"
        if tool_name in {"propose_file_recycle", "propose_file_recycle_batch"}:
            return "recycled"
        raise ValueError("Unsupported proposal-gate tool")

    @staticmethod
    def _budget_exhaustion_reason(plan: WorkPlan) -> str | None:
        if plan.auto_steps_used >= plan.max_auto_steps:
            return (
                f"Automatic step budget exhausted: "
                f"{plan.auto_steps_used}/{plan.max_auto_steps}"
            )
        if plan.runtime_seconds_used >= plan.runtime_budget_seconds:
            return (
                f"Runtime budget exhausted: "
                f"{plan.runtime_seconds_used:.1f}/"
                f"{plan.runtime_budget_seconds} seconds"
            )
        return None

    @staticmethod
    def _step_needs_approval(plan: WorkPlan, step: WorkPlanStep) -> bool:
        if step.execution_mode != "auto":
            return False
        threshold = int(plan.approval_risk_threshold)
        if threshold >= 4:
            return False
        return step.risk_level >= threshold and step.approved_at is None

    def _record_attempt_runtime(
        self,
        plan_id: str,
        step_id: str,
        duration: float,
        *,
        run_id: str,
    ) -> bool:
        with self.database.session() as session:
            plan = session.get(WorkPlan, plan_id)
            step = session.get(WorkPlanStep, step_id)
            if plan is None or step is None:
                raise RuntimeError("Work plan step disappeared while recording runtime")
            step.duration_seconds = float(duration)
            plan.runtime_seconds_used += float(duration)
            timeout_exceeded = duration > float(plan.step_timeout_seconds)
            step.timeout_exceeded = timeout_exceeded
            if timeout_exceeded:
                self._event(
                    session,
                    plan,
                    step_id=step.id,
                    event_type="step_timeout_detected",
                    severity="warning",
                    message=(
                        f"Step {step.position} ran for {duration:.1f}s, exceeding "
                        f"the {plan.step_timeout_seconds}s supervised timeout"
                    ),
                    data={
                        "duration_seconds": duration,
                        "step_timeout_seconds": plan.step_timeout_seconds,
                        "tool_name": step.tool_name,
                    },
                )
                session.add(
                    AuditLog(
                        task_id=plan.task_id,
                        agent_run_id=run_id,
                        action="work_plan_step_timeout_detected",
                        target=step.id,
                        result=(
                            f"duration_seconds={duration:.3f}; "
                            f"limit={plan.step_timeout_seconds}"
                        ),
                        risk_level=step.risk_level,
                    )
                )
            return timeout_exceeded

    def _finalize_pause(
        self,
        plan_id: str,
        *,
        run_id: str | None = None,
        reason: str,
    ) -> None:
        now = datetime.now(timezone.utc)
        with self.database.session() as session:
            plan = session.get(WorkPlan, plan_id)
            if plan is None or plan.status == "paused":
                return
            if plan.status in {"completed", "cancelled"}:
                return
            task = session.get(Task, plan.task_id)
            plan.status = "paused"
            plan.paused_at = now
            plan.pause_reason = reason[:4000]
            plan.error_message = plan.pause_reason
            if task is not None:
                task.status = "paused"
                task.error_message = plan.pause_reason
            if run_id:
                run = session.get(AgentRun, run_id)
                if run is not None:
                    run.status = "paused"
                    run.completed_at = now
            self._event(
                session,
                plan,
                event_type="paused",
                severity="action",
                message=plan.pause_reason,
                data={},
            )
            session.add(
                AuditLog(
                    task_id=plan.task_id,
                    agent_run_id=run_id,
                    action="work_plan_paused",
                    target=plan.id,
                    result=plan.pause_reason,
                    risk_level=0,
                )
            )

    @staticmethod
    def _event(
        session,
        plan: WorkPlan,
        *,
        event_type: str,
        severity: str,
        message: str,
        data: dict[str, Any],
        step_id: str | None = None,
    ) -> WorkPlanEvent:
        event = WorkPlanEvent(
            plan_id=plan.id,
            step_id=step_id,
            event_type=event_type,
            severity=severity,
            message=message[:4000],
            data_json=data,
        )
        session.add(event)
        return event

    @staticmethod
    def _event_payload(event: WorkPlanEvent) -> dict[str, Any]:
        return {
            "id": event.id,
            "plan_id": event.plan_id,
            "step_id": event.step_id,
            "event_type": event.event_type,
            "severity": event.severity,
            "message": event.message,
            "data": event.data_json or {},
            "created_at": event.created_at.isoformat(),
            "acknowledged_at": (
                event.acknowledged_at.isoformat()
                if event.acknowledged_at
                else None
            ),
        }

    def _start_execution(self, plan_id: str, *, event: str) -> str:
        now = datetime.now(timezone.utc)
        with self.database.session() as session:
            plan = session.get(WorkPlan, plan_id)
            if plan is None:
                raise ValueError("Work plan not found")
            if plan.status not in {"running", "cancelling"}:
                raise ValueError("Background runner no longer owns this work plan")
            task = session.get(Task, plan.task_id)
            plan.started_at = plan.started_at or now
            if plan.status == "running":
                plan.error_message = None
            if task is not None:
                task.started_at = task.started_at or now
                task.completed_at = None
                if plan.status == "running":
                    task.status = "running"
                    task.error_message = None
            run = AgentRun(
                task_id=plan.task_id,
                model="work-plan-executor",
                status="running",
            )
            session.add(run)
            session.flush()
            session.add(
                AuditLog(
                    task_id=plan.task_id,
                    agent_run_id=run.id,
                    action=event,
                    target=plan.id,
                    result="Durable background work-plan execution started",
                    risk_level=0,
                )
            )
            return run.id

    def _queue_plan(self, plan_id: str, *, action: str, result: str) -> None:
        with self.database.session() as session:
            plan = session.get(WorkPlan, plan_id)
            if plan is None:
                raise ValueError("Work plan not found")
            task = session.get(Task, plan.task_id)
            plan.status = "queued"
            plan.error_message = None
            plan.pause_reason = None
            plan.paused_at = None
            if task is not None:
                task.status = "queued"
                task.error_message = None
                task.completed_at = None
            self._event(
                session,
                plan,
                event_type="queued",
                severity="info",
                message=result,
                data={},
            )
            session.add(
                AuditLog(
                    task_id=plan.task_id,
                    action=action,
                    target=plan.id,
                    result=result,
                    risk_level=0,
                )
            )

    def _finalize_cancel(
        self,
        plan_id: str,
        *,
        run_id: str | None = None,
        reason: str,
    ) -> None:
        now = datetime.now(timezone.utc)
        with self.database.session() as session:
            plan = session.get(WorkPlan, plan_id)
            if plan is None or plan.status == "cancelled":
                return
            steps = list(
                session.scalars(
                    select(WorkPlanStep).where(WorkPlanStep.plan_id == plan_id)
                ).all()
            )
            preserved: list[str] = []
            for step in steps:
                if step.external_entity_id:
                    preserved.append(
                        f"{step.external_entity_type}:{step.external_entity_id}"
                    )
                if step.status in {
                    "pending",
                    "awaiting_confirmation",
                    "failed",
                    "interrupted",
                }:
                    step.status = "cancelled"
                    step.completed_at = step.completed_at or now
            plan.status = "cancelled"
            plan.cancelled_at = now
            plan.completed_at = now
            plan.error_message = None
            task = session.get(Task, plan.task_id)
            if task is not None:
                task.status = "cancelled"
                task.completed_at = now
                task.error_message = None
            if run_id:
                run = session.get(AgentRun, run_id)
                if run is not None:
                    run.status = "cancelled"
                    run.completed_at = now
            session.add(
                AuditLog(
                    task_id=plan.task_id,
                    agent_run_id=run_id,
                    action="work_plan_cancelled",
                    target=plan_id,
                    result=(
                        f"{reason}; preserved proposals="
                        + (",".join(preserved) if preserved else "none")
                    )[:4000],
                    risk_level=0,
                )
            )

    def _complete(self, plan_id: str, run_id: str) -> None:
        now = datetime.now(timezone.utc)
        with self.database.session() as session:
            plan = session.get(WorkPlan, plan_id)
            if plan is None:
                raise ValueError("Work plan not found")
            plan.status = "completed"
            plan.completed_at = now
            plan.error_message = None
            plan.pause_reason = None
            task = session.get(Task, plan.task_id)
            if task is not None:
                task.status = "completed"
                task.progress = 1.0
                task.result_text = f"Work plan completed: {plan.summary}"[:12000]
                task.error_message = None
                task.completed_at = now
            run = session.get(AgentRun, run_id)
            if run is not None:
                run.status = "completed"
                run.completed_at = now
            self._event(
                session,
                plan,
                event_type="completed",
                severity="info",
                message="Work plan completed",
                data={
                    "auto_steps_used": plan.auto_steps_used,
                    "runtime_seconds_used": plan.runtime_seconds_used,
                },
            )
            session.add(
                AuditLog(
                    task_id=plan.task_id,
                    agent_run_id=run_id,
                    action="work_plan_completed",
                    target=plan_id,
                    result=plan.summary[:4000],
                    risk_level=0,
                )
            )

    def _fail_step(
        self,
        plan_id: str,
        step_id: str,
        run_id: str,
        message: str,
    ) -> None:
        message = message[:4000]
        now = datetime.now(timezone.utc)
        with self.database.session() as session:
            plan = session.get(WorkPlan, plan_id)
            step = session.get(WorkPlanStep, step_id)
            if plan is None or step is None:
                return
            step.status = "failed"
            step.error_message = message
            step.completed_at = now
            task = session.get(Task, plan.task_id)
            run = session.get(AgentRun, run_id)

            if plan.failure_policy == "pause":
                plan.status = "paused"
                plan.paused_at = now
                plan.pause_reason = (
                    f"Step {step.position} failed and the plan failure policy is pause"
                )
                plan.error_message = message
                if task is not None:
                    task.status = "blocked"
                    task.error_message = message
                if run is not None:
                    run.status = "paused"
                    run.completed_at = now
            else:
                plan.status = "failed"
                plan.error_message = message
                if task is not None:
                    task.status = "failed"
                    task.error_message = message
                    task.completed_at = now
                if run is not None:
                    run.status = "failed"
                    run.completed_at = now

            self._event(
                session,
                plan,
                step_id=step.id,
                event_type="step_failed",
                severity="action",
                message=f"Step {step.position} failed: {message}",
                data={
                    "failure_policy": plan.failure_policy,
                    "tool_name": step.tool_name,
                },
            )
            session.add(
                AuditLog(
                    task_id=plan.task_id,
                    agent_run_id=run_id,
                    action="work_plan_step_failed",
                    target=step_id,
                    result=(
                        f"{message}; failure_policy={plan.failure_policy}"
                    )[:4000],
                    risk_level=step.risk_level,
                )
            )

    def _fail(self, plan_id: str, run_id: str, message: str) -> None:
        message = message[:4000]
        now = datetime.now(timezone.utc)
        with self.database.session() as session:
            plan = session.get(WorkPlan, plan_id)
            if plan is None:
                return
            plan.status = "failed"
            plan.error_message = message
            task = session.get(Task, plan.task_id)
            if task is not None:
                task.status = "failed"
                task.error_message = message
                task.completed_at = now
            run = session.get(AgentRun, run_id)
            if run is not None:
                run.status = "failed"
                run.completed_at = now
            session.add(
                AuditLog(
                    task_id=plan.task_id,
                    agent_run_id=run_id,
                    action="work_plan_failed",
                    target=plan_id,
                    result=message,
                    risk_level=0,
                )
            )

    def _block_after_gate(self, plan_id: str, step_id: str, message: str) -> None:
        now = datetime.now(timezone.utc)
        with self.database.session() as session:
            plan = session.get(WorkPlan, plan_id)
            step = session.get(WorkPlanStep, step_id)
            if plan is None or step is None:
                return
            step.status = "failed"
            step.error_message = message[:4000]
            step.completed_at = now
            plan.status = "blocked"
            plan.error_message = message[:4000]
            task = session.get(Task, plan.task_id)
            if task is not None:
                task.status = "blocked"
                task.error_message = message[:4000]
                task.completed_at = now
            session.add(
                AuditLog(
                    task_id=plan.task_id,
                    action="work_plan_gate_blocked",
                    target=step_id,
                    result=message[:4000],
                    risk_level=step.risk_level,
                )
            )

    def _planning_failed(self, task_id: str, message: str) -> None:
        with self.database.session() as session:
            task = session.get(Task, task_id)
            if task is not None:
                task.status = "failed"
                task.error_message = message[:4000]
                task.completed_at = datetime.now(timezone.utc)
            session.add(
                AuditLog(
                    task_id=task_id,
                    action="work_plan_draft_failed",
                    result=message[:4000],
                    risk_level=0,
                )
            )

    def _finish_run(self, run_id: str, status: str) -> None:
        with self.database.session() as session:
            run = session.get(AgentRun, run_id)
            if run is not None:
                run.status = status
                run.completed_at = datetime.now(timezone.utc)

    @staticmethod
    def _task_progress(session, plan_id: str) -> float:
        steps = list(
            session.scalars(
                select(WorkPlanStep).where(WorkPlanStep.plan_id == plan_id)
            ).all()
        )
        if not steps:
            return 0.08
        resolved = sum(
            1 for item in steps if item.status in {"completed", "skipped"}
        )
        return min(0.95, 0.1 + (resolved / len(steps)) * 0.85)

    @staticmethod
    def _payload(session, plan: WorkPlan) -> dict[str, Any]:
        steps = list(
            session.scalars(
                select(WorkPlanStep)
                .where(WorkPlanStep.plan_id == plan.id)
                .order_by(WorkPlanStep.position)
            ).all()
        )
        resolved = [step for step in steps if step.status in {"completed", "skipped"}]
        completed = [step for step in steps if step.status == "completed"]
        skipped = [step for step in steps if step.status == "skipped"]
        remaining = [
            step
            for step in steps
            if step.status not in {"completed", "skipped", "cancelled"}
        ]
        durations = [
            float(step.duration_seconds)
            for step in completed
            if step.duration_seconds is not None and step.duration_seconds >= 0
        ]
        average_duration = (
            sum(durations) / len(durations)
            if durations
            else None
        )
        estimated_remaining = (
            average_duration * len(remaining)
            if average_duration is not None
            else None
        )
        unread_notifications = int(
            session.scalar(
                select(func.count(WorkPlanEvent.id)).where(
                    WorkPlanEvent.plan_id == plan.id,
                    WorkPlanEvent.acknowledged_at.is_(None),
                    WorkPlanEvent.severity.in_(("warning", "action")),
                )
            )
            or 0
        )
        latest_events = list(
            session.scalars(
                select(WorkPlanEvent)
                .where(WorkPlanEvent.plan_id == plan.id)
                .order_by(WorkPlanEvent.created_at.desc(), WorkPlanEvent.id.desc())
                .limit(12)
            ).all()
        )
        retryable = any(
            step.status in {"failed", "interrupted"}
            and not step.external_entity_id
            for step in steps
        )
        return {
            "id": plan.id,
            "task_id": plan.task_id,
            "workspace_id": plan.workspace_id,
            "title": plan.title,
            "summary": plan.summary,
            "limitations": list(plan.limitations_json or []),
            "status": plan.status,
            "error_message": plan.error_message,
            "pause_reason": plan.pause_reason,
            "created_at": plan.created_at.isoformat(),
            "started_at": plan.started_at.isoformat() if plan.started_at else None,
            "completed_at": plan.completed_at.isoformat() if plan.completed_at else None,
            "cancelled_at": plan.cancelled_at.isoformat() if plan.cancelled_at else None,
            "paused_at": plan.paused_at.isoformat() if plan.paused_at else None,
            "progress": len(resolved) / len(steps) if steps else 0.0,
            "requires_confirmation": plan.status == "awaiting_confirmation",
            "requires_step_approval": plan.status == "awaiting_step_approval",
            "can_start": plan.status == "ready",
            "can_resume": plan.status == "awaiting_confirmation",
            "can_pause": plan.status in {"queued", "running"},
            "can_continue": plan.status == "paused" and not retryable,
            "can_retry": plan.status in {"failed", "paused"} and retryable,
            "can_cancel": plan.status not in {"completed", "cancelled"},
            "supervision": {
                "max_auto_steps": plan.max_auto_steps,
                "auto_steps_used": plan.auto_steps_used,
                "auto_steps_remaining": max(
                    0,
                    plan.max_auto_steps - plan.auto_steps_used,
                ),
                "runtime_budget_seconds": plan.runtime_budget_seconds,
                "runtime_seconds_used": plan.runtime_seconds_used,
                "runtime_seconds_remaining": max(
                    0.0,
                    plan.runtime_budget_seconds - plan.runtime_seconds_used,
                ),
                "step_timeout_seconds": plan.step_timeout_seconds,
                "failure_policy": plan.failure_policy,
                "approval_risk_threshold": plan.approval_risk_threshold,
            },
            "estimate": {
                "total_steps": len(steps),
                "completed_steps": len(completed),
                "skipped_steps": len(skipped),
                "remaining_steps": len(remaining),
                "average_completed_step_seconds": average_duration,
                "estimated_remaining_seconds": estimated_remaining,
            },
            "unread_notifications": unread_notifications,
            "latest_events": [
                WorkPlanService._event_payload(item)
                for item in latest_events
            ],
            "steps": [
                {
                    "id": step.id,
                    "position": step.position,
                    "title": step.title,
                    "description": step.description,
                    "tool_name": step.tool_name,
                    "arguments": step.arguments_json,
                    "dependencies": list(step.dependencies_json or []),
                    "risk_level": step.risk_level,
                    "execution_mode": step.execution_mode,
                    "status": step.status,
                    "result_summary": step.result_summary,
                    "result": step.result_json,
                    "tool_call_id": step.tool_call_id,
                    "external_entity_type": step.external_entity_type,
                    "external_entity_id": step.external_entity_id,
                    "error_message": step.error_message,
                    "approved_at": (
                        step.approved_at.isoformat()
                        if step.approved_at
                        else None
                    ),
                    "skipped_at": (
                        step.skipped_at.isoformat()
                        if step.skipped_at
                        else None
                    ),
                    "skip_reason": step.skip_reason,
                    "duration_seconds": step.duration_seconds,
                    "timeout_exceeded": bool(step.timeout_exceeded),
                    "created_at": step.created_at.isoformat(),
                    "started_at": step.started_at.isoformat() if step.started_at else None,
                    "completed_at": step.completed_at.isoformat() if step.completed_at else None,
                }
                for step in steps
            ],
        }

