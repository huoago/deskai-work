from __future__ import annotations

from pathlib import Path

from app.database.models import Task, WorkPlan, WorkPlanStep
from app.security.secrets import SecretStatus


class Phase23SecretStore:
    def get_openai_api_key(self) -> str | None:
        return "sk-test-phase23-123456789012345678901234"

    def status(self) -> SecretStatus:
        return SecretStatus(
            configured=True,
            source="credential_manager",
            credential_store_available=True,
            writable=True,
        )


class Phase23Planner:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    async def draft_work_plan(self, **kwargs):
        return self.payload


def _wire_planner(client, payload: dict) -> None:
    service = client.app.state.work_plan_service
    service.secret_store = Phase23SecretStore()
    service.provider = Phase23Planner(payload)


def _task(client, workspace_id: str, request: str = "Phase 23 durable task") -> dict:
    response = client.post(
        "/tasks",
        json={
            "workspace_id": workspace_id,
            "request": request,
            "execution_mode": "plan",
        },
    )
    assert response.status_code == 201
    return response.json()


def _draft(client, task_id: str) -> dict:
    response = client.post(f"/tasks/{task_id}/work-plan")
    assert response.status_code == 200
    return response.json()


def _process(client, limit: int = 3) -> dict:
    response = client.post("/work-plan-worker/process", params={"limit": limit})
    assert response.status_code == 200
    return response.json()


def _workspace_file(client, tmp_path: Path, name: str = "phase23.md"):
    workspace = client.post("/workspaces", json={"name": "Phase 23"}).json()
    root = tmp_path / f"phase23-{name.replace('.', '-')}"
    root.mkdir()
    source = root / name
    source.write_text("old value\n", encoding="utf-8")
    added = client.post(
        f"/workspaces/{workspace['id']}/roots",
        json={
            "path": str(root),
            "read_allowed": True,
            "write_allowed": True,
            "watch_enabled": False,
            "scan_now": True,
        },
    )
    assert added.status_code == 201
    file = next(
        item
        for item in client.get(f"/files?workspace_id={workspace['id']}").json()
        if item["filename"] == name
    )
    return workspace, file, source


def test_phase23_start_is_nonblocking_queue_and_worker_status_is_visible(client):
    workspace = client.post("/workspaces", json={"name": "Background Queue"}).json()
    task = _task(client, workspace["id"])
    _wire_planner(
        client,
        {
            "plan_title": "Background calculation",
            "summary": "Run outside the request thread.",
            "limitations": [],
            "steps": [
                {
                    "title": "Calculate",
                    "description": "Safe bounded calculation.",
                    "tool_name": "calculate_expression",
                    "arguments_json": '{"expression":"20+22","variables":[]}',
                    "depends_on_positions": [],
                }
            ],
        },
    )
    plan = _draft(client, task["id"])

    status = client.get("/work-plan-worker/status")
    assert status.status_code == 200
    assert status.json()["running"] is False

    started = client.post(f"/work-plans/{plan['id']}/start")
    assert started.status_code == 200
    assert started.json()["status"] == "queued"
    assert client.get(f"/tasks/{task['id']}").json()["status"] == "queued"

    processed = _process(client, limit=1)
    assert processed["processed"] == 1
    completed = client.get(f"/work-plans/{plan['id']}").json()
    assert completed["status"] == "completed"
    assert completed["steps"][0]["result"]["result"] == 42


def test_phase23_safe_startup_checkpoint_is_requeued_and_continues(client):
    workspace = client.post("/workspaces", json={"name": "Checkpoint Recovery"}).json()
    task = _task(client, workspace["id"])
    _wire_planner(
        client,
        {
            "plan_title": "Checkpoint recovery",
            "summary": "Retain completed work after restart.",
            "limitations": [],
            "steps": [
                {
                    "title": "First",
                    "description": "Completed checkpoint.",
                    "tool_name": "calculate_expression",
                    "arguments_json": '{"expression":"5+5","variables":[]}',
                    "depends_on_positions": [],
                },
                {
                    "title": "Second",
                    "description": "Use the completed checkpoint.",
                    "tool_name": "calculate_expression",
                    "arguments_json": (
                        '{"expression":"x+1","variables":'
                        '[{"name":"x","value":"{{step:1.result.result}}"}]}'
                    ),
                    "depends_on_positions": [1],
                },
            ],
        },
    )
    plan = _draft(client, task["id"])

    with client.app.state.database.session() as session:
        plan_row = session.get(WorkPlan, plan["id"])
        task_row = session.get(Task, task["id"])
        first = session.get(WorkPlanStep, plan["steps"][0]["id"])
        assert plan_row and task_row and first
        plan_row.status = "running"
        task_row.status = "running"
        first.status = "completed"
        first.result_json = {
            "expression": "5+5",
            "variables": {},
            "result": 10,
        }
        first.result_summary = '{"result":10}'

    assert client.app.state.work_plan_service.recover_interrupted() == 1
    recovered = client.get(f"/work-plans/{plan['id']}").json()
    assert recovered["status"] == "queued"
    assert recovered["steps"][0]["status"] == "completed"
    assert recovered["steps"][1]["status"] == "pending"

    assert _process(client, limit=1)["processed"] == 1
    completed = client.get(f"/work-plans/{plan['id']}").json()
    assert completed["status"] == "completed"
    assert completed["steps"][1]["result"]["result"] == 11


def test_phase23_ambiguous_running_step_freezes_instead_of_replaying(client):
    workspace = client.post("/workspaces", json={"name": "Ambiguous Recovery"}).json()
    task = _task(client, workspace["id"])
    _wire_planner(
        client,
        {
            "plan_title": "Ambiguous",
            "summary": "Freeze an unproven running step.",
            "limitations": [],
            "steps": [
                {
                    "title": "Unknown completion",
                    "description": "Must not replay automatically.",
                    "tool_name": "calculate_expression",
                    "arguments_json": '{"expression":"7*6","variables":[]}',
                    "depends_on_positions": [],
                }
            ],
        },
    )
    plan = _draft(client, task["id"])

    with client.app.state.database.session() as session:
        plan_row = session.get(WorkPlan, plan["id"])
        task_row = session.get(Task, task["id"])
        step = session.get(WorkPlanStep, plan["steps"][0]["id"])
        assert plan_row and task_row and step
        plan_row.status = "running"
        task_row.status = "running"
        step.status = "running"

    assert client.app.state.work_plan_service.recover_interrupted() == 1
    frozen = client.get(f"/work-plans/{plan['id']}").json()
    assert frozen["status"] == "paused"
    assert frozen["steps"][0]["status"] == "interrupted"
    assert _process(client, limit=1)["processed"] == 0


def test_phase23_cancel_queued_plan_prevents_any_tool_execution(client):
    workspace = client.post("/workspaces", json={"name": "Cancel Queue"}).json()
    task = _task(client, workspace["id"])
    _wire_planner(
        client,
        {
            "plan_title": "Cancel before claim",
            "summary": "Queued work must not run after cancellation.",
            "limitations": [],
            "steps": [
                {
                    "title": "Never run",
                    "description": "This tool should stay uncalled.",
                    "tool_name": "calculate_expression",
                    "arguments_json": '{"expression":"99+1","variables":[]}',
                    "depends_on_positions": [],
                }
            ],
        },
    )
    plan = _draft(client, task["id"])
    assert client.post(f"/work-plans/{plan['id']}/start").json()["status"] == "queued"

    cancelled = client.post(f"/work-plans/{plan['id']}/cancel")
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    assert _process(client, limit=1)["processed"] == 0

    detail = client.get(f"/tasks/{task['id']}").json()
    assert detail["status"] == "cancelled"
    assert detail["tool_calls"] == []


def test_phase23_cancel_during_active_tool_stops_after_current_checkpoint(client):
    workspace = client.post("/workspaces", json={"name": "Cancel Running"}).json()
    task = _task(client, workspace["id"])
    _wire_planner(
        client,
        {
            "plan_title": "Cancel during tool",
            "summary": "Current tool may finish; later work must stop.",
            "limitations": [],
            "steps": [
                {
                    "title": "Current tool",
                    "description": "Allowed to finish safely.",
                    "tool_name": "calculate_expression",
                    "arguments_json": '{"expression":"6*7","variables":[]}',
                    "depends_on_positions": [],
                },
                {
                    "title": "Later tool",
                    "description": "Must be cancelled.",
                    "tool_name": "calculate_expression",
                    "arguments_json": '{"expression":"100+1","variables":[]}',
                    "depends_on_positions": [1],
                },
            ],
        },
    )
    plan = _draft(client, task["id"])
    assert client.post(f"/work-plans/{plan['id']}/start").json()["status"] == "queued"

    registry = client.app.state.tool_registry
    original_execute = registry.execute

    def cancel_then_execute(**kwargs):
        client.app.state.work_plan_service.cancel(plan["id"])
        return original_execute(**kwargs)

    registry.execute = cancel_then_execute
    try:
        assert _process(client, limit=1)["processed"] == 1
    finally:
        registry.execute = original_execute

    cancelled = client.get(f"/work-plans/{plan['id']}").json()
    assert cancelled["status"] == "cancelled"
    assert cancelled["steps"][0]["status"] == "completed"
    assert cancelled["steps"][0]["result"]["result"] == 42
    assert cancelled["steps"][1]["status"] == "cancelled"
    assert len(client.get(f"/tasks/{task['id']}").json()["tool_calls"]) == 1


def test_phase23_cancel_during_proposal_creation_preserves_proposal_without_source_write(
    client,
    tmp_path,
):
    workspace, file, source = _workspace_file(client, tmp_path)
    task = _task(client, workspace["id"])
    _wire_planner(
        client,
        {
            "plan_title": "Cancel proposal",
            "summary": "A created proposal survives plan cancellation.",
            "limitations": [],
            "steps": [
                {
                    "title": "Stage protected edit",
                    "description": "Proposal only.",
                    "tool_name": "propose_source_file_edit",
                    "arguments_json": (
                        '{"file_id":"'
                        + file["id"]
                        + '","mode":"text_replace","summary":"Phase 23 cancel race",'
                        '"replacements":[{"find":"old","replace":"new","replace_all":true}],'
                        '"cell_edits":[]}'
                    ),
                    "depends_on_positions": [],
                },
                {
                    "title": "Never continue",
                    "description": "Cancelled after proposal creation.",
                    "tool_name": "calculate_expression",
                    "arguments_json": '{"expression":"1+1","variables":[]}',
                    "depends_on_positions": [1],
                },
            ],
        },
    )
    plan = _draft(client, task["id"])
    assert client.post(f"/work-plans/{plan['id']}/start").json()["status"] == "queued"

    registry = client.app.state.tool_registry
    original_execute = registry.execute

    def cancel_then_execute(**kwargs):
        client.app.state.work_plan_service.cancel(plan["id"])
        return original_execute(**kwargs)

    registry.execute = cancel_then_execute
    try:
        assert _process(client, limit=1)["processed"] == 1
    finally:
        registry.execute = original_execute

    cancelled = client.get(f"/work-plans/{plan['id']}").json()
    assert cancelled["status"] == "cancelled"
    proposal_id = cancelled["steps"][0]["external_entity_id"]
    assert proposal_id
    proposal = client.get(f"/file-edits/{proposal_id}").json()
    assert proposal["status"] == "pending"
    assert source.read_text(encoding="utf-8") == "old value\n"
    assert cancelled["steps"][1]["status"] == "cancelled"
