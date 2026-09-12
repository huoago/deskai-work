from __future__ import annotations

from pathlib import Path

from app.database.models import Task, WorkPlan, WorkPlanStep
from app.security.secrets import SecretStatus


class PlanSecretStore:
    def get_openai_api_key(self) -> str | None:
        return "sk-test-plan-123456789012345678901234"

    def status(self) -> SecretStatus:
        return SecretStatus(
            configured=True,
            source="credential_manager",
            credential_store_available=True,
            writable=True,
        )


class FakePlanProvider:
    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.calls: list[dict] = []

    async def draft_work_plan(self, **kwargs):
        self.calls.append(kwargs)
        return self.payload


def _wire_planner(client, payload: dict) -> FakePlanProvider:
    provider = FakePlanProvider(payload)
    client.app.state.work_plan_service.secret_store = PlanSecretStore()
    client.app.state.work_plan_service.provider = provider
    return provider


def _plan_task(client, workspace_id: str, request: str = "Run a controlled plan"):
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


def _workspace_file(client, tmp_path: Path, name: str = "notes.md"):
    workspace = client.post("/workspaces", json={"name": "Phase 22 Plan"}).json()
    root = tmp_path / f"phase22-{name.replace('.', '-')}"
    root.mkdir()
    source = root / name
    source.write_text("old value\n", encoding="utf-8")
    response = client.post(
        f"/workspaces/{workspace['id']}/roots",
        json={
            "path": str(root),
            "read_allowed": True,
            "write_allowed": True,
            "watch_enabled": False,
            "scan_now": True,
        },
    )
    assert response.status_code == 201
    file = next(
        item
        for item in client.get(f"/files?workspace_id={workspace['id']}").json()
        if item["filename"] == name
    )
    return workspace, file, source


def test_phase22_plan_mode_task_is_not_claimed_by_legacy_agent_worker(client):
    workspace = client.post("/workspaces", json={"name": "Plan Queue"}).json()
    task = _plan_task(client, workspace["id"])

    processed = client.post("/agent/process", params={"limit": 5})
    assert processed.status_code == 200
    assert processed.json()["processed"] == 0

    current = client.get(f"/tasks/{task['id']}").json()
    assert current["execution_mode"] == "plan"
    assert current["status"] == "planning"


def test_phase22_draft_and_execute_dependency_result_reference(client):
    workspace = client.post("/workspaces", json={"name": "Plan Dependency"}).json()
    task = _plan_task(client, workspace["id"], "Calculate and create a Word result")
    provider = _wire_planner(
        client,
        {
            "plan_title": "Calculation report",
            "summary": "Calculate a value, then place it in a Word artifact.",
            "limitations": [],
            "steps": [
                {
                    "title": "Calculate",
                    "description": "Compute a bounded expression.",
                    "tool_name": "calculate_expression",
                    "arguments_json": '{"expression":"2+3","variables":[]}',
                    "depends_on_positions": [],
                },
                {
                    "title": "Create report",
                    "description": "Write the calculated value into a new Word artifact.",
                    "tool_name": "create_word_document",
                    "arguments_json": (
                        '{"filename":"phase22-result.docx","title":"Phase 22 Result",'
                        '"sections":[{"heading":"Result","body":"Computed '
                        '{{step:1.result.result}}"}]}'
                    ),
                    "depends_on_positions": [1],
                },
            ],
        },
    )

    drafted = client.post(f"/tasks/{task['id']}/work-plan")
    assert drafted.status_code == 200
    plan = drafted.json()
    assert plan["status"] == "ready"
    assert len(plan["steps"]) == 2
    assert plan["steps"][0]["risk_level"] == 1
    assert plan["steps"][1]["risk_level"] == 3
    assert provider.calls
    assert provider.calls[0]["workspace_context"] == []

    started = client.post(f"/work-plans/{plan['id']}/start")
    assert started.status_code == 200
    finished = started.json()
    assert finished["status"] == "completed"
    assert [item["status"] for item in finished["steps"]] == ["completed", "completed"]
    assert finished["steps"][0]["result"]["result"] == 5
    assert finished["steps"][1]["result"]["kind"] == "docx"

    detail = client.get(f"/tasks/{task['id']}").json()
    assert detail["status"] == "completed"
    assert detail["progress"] == 1.0
    assert len(detail["artifacts"]) == 1
    assert detail["artifacts"][0]["filename"] == "phase22-result.docx"
    assert len(detail["work_plans"]) == 1


def test_phase22_file_proposal_pauses_then_resumes_after_existing_confirmation(
    client,
    tmp_path,
):
    workspace, file, source = _workspace_file(client, tmp_path)
    task = _plan_task(client, workspace["id"], "Update notes then calculate")
    _wire_planner(
        client,
        {
            "plan_title": "Edit with confirmation gate",
            "summary": "Stage a source edit, wait for confirmation, then continue.",
            "limitations": [],
            "steps": [
                {
                    "title": "Stage edit",
                    "description": "Create the existing protected source-edit proposal.",
                    "tool_name": "propose_source_file_edit",
                    "arguments_json": (
                        '{"file_id":"'
                        + file["id"]
                        + '","mode":"text_replace","summary":"Phase 22 staged edit",'
                        '"replacements":[{"find":"old","replace":"new","replace_all":true}],'
                        '"cell_edits":[]}'
                    ),
                    "depends_on_positions": [],
                },
                {
                    "title": "Continue calculation",
                    "description": "Only run after the edit was confirmed.",
                    "tool_name": "calculate_expression",
                    "arguments_json": '{"expression":"10+5","variables":[]}',
                    "depends_on_positions": [1],
                },
            ],
        },
    )
    plan = client.post(f"/tasks/{task['id']}/work-plan").json()

    paused = client.post(f"/work-plans/{plan['id']}/start")
    assert paused.status_code == 200
    paused_plan = paused.json()
    assert paused_plan["status"] == "awaiting_confirmation"
    gate = paused_plan["steps"][0]
    assert gate["status"] == "awaiting_confirmation"
    assert gate["external_entity_type"] == "source_edit"
    assert gate["external_entity_id"]
    assert source.read_text(encoding="utf-8") == "old value\n"
    assert paused_plan["steps"][1]["status"] == "pending"

    proposal = client.get(f"/file-edits/{gate['external_entity_id']}").json()
    assert proposal["status"] == "pending"
    assert client.post(f"/file-edits/{gate['external_entity_id']}/confirm").status_code == 200
    assert source.read_text(encoding="utf-8") == "new value\n"

    resumed = client.post(f"/work-plans/{plan['id']}/resume")
    assert resumed.status_code == 200
    completed = resumed.json()
    assert completed["status"] == "completed"
    assert completed["steps"][0]["status"] == "completed"
    assert completed["steps"][1]["status"] == "completed"
    assert completed["steps"][1]["result"]["result"] == 15


def test_phase22_cancel_preserves_existing_pending_file_proposal(client, tmp_path):
    workspace, file, source = _workspace_file(client, tmp_path, "cancel.md")
    task = _plan_task(client, workspace["id"], "Stage then cancel")
    _wire_planner(
        client,
        {
            "plan_title": "Cancelable proposal plan",
            "summary": "Create a proposal and pause.",
            "limitations": [],
            "steps": [
                {
                    "title": "Stage edit",
                    "description": "Stage only.",
                    "tool_name": "propose_source_file_edit",
                    "arguments_json": (
                        '{"file_id":"'
                        + file["id"]
                        + '","mode":"text_replace","summary":"Keep proposal",'
                        '"replacements":[{"find":"old","replace":"new","replace_all":true}],'
                        '"cell_edits":[]}'
                    ),
                    "depends_on_positions": [],
                }
            ],
        },
    )
    plan = client.post(f"/tasks/{task['id']}/work-plan").json()
    paused = client.post(f"/work-plans/{plan['id']}/start").json()
    proposal_id = paused["steps"][0]["external_entity_id"]

    cancelled = client.post(f"/work-plans/{plan['id']}/cancel")
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    assert source.read_text(encoding="utf-8") == "old value\n"
    assert client.get(f"/file-edits/{proposal_id}").json()["status"] == "pending"

    detail = client.get(f"/tasks/{task['id']}").json()
    assert detail["status"] == "cancelled"


def test_phase22_rejecting_gate_blocks_without_automatic_replanning(client, tmp_path):
    workspace, file, source = _workspace_file(client, tmp_path, "reject.md")
    task = _plan_task(client, workspace["id"], "Stage and reject")
    _wire_planner(
        client,
        {
            "plan_title": "Rejected gate",
            "summary": "Do not work around a rejected edit.",
            "limitations": [],
            "steps": [
                {
                    "title": "Stage edit",
                    "description": "Stage only.",
                    "tool_name": "propose_source_file_edit",
                    "arguments_json": (
                        '{"file_id":"'
                        + file["id"]
                        + '","mode":"text_replace","summary":"Reject me",'
                        '"replacements":[{"find":"old","replace":"new","replace_all":true}],'
                        '"cell_edits":[]}'
                    ),
                    "depends_on_positions": [],
                },
                {
                    "title": "Later step",
                    "description": "Must never auto-run after rejection.",
                    "tool_name": "calculate_expression",
                    "arguments_json": '{"expression":"1+1","variables":[]}',
                    "depends_on_positions": [1],
                },
            ],
        },
    )
    plan = client.post(f"/tasks/{task['id']}/work-plan").json()
    paused = client.post(f"/work-plans/{plan['id']}/start").json()
    proposal_id = paused["steps"][0]["external_entity_id"]
    assert client.post(f"/file-edits/{proposal_id}/reject").status_code == 200

    resumed = client.post(f"/work-plans/{plan['id']}/resume")
    assert resumed.status_code == 200
    blocked = resumed.json()
    assert blocked["status"] == "blocked"
    assert blocked["steps"][0]["status"] == "failed"
    assert blocked["steps"][1]["status"] == "pending"
    assert source.read_text(encoding="utf-8") == "old value\n"


def test_phase22_invalid_forward_dependency_is_rejected(client):
    workspace = client.post("/workspaces", json={"name": "Invalid Plan"}).json()
    task = _plan_task(client, workspace["id"])
    _wire_planner(
        client,
        {
            "plan_title": "Invalid",
            "summary": "Invalid forward dependency.",
            "limitations": [],
            "steps": [
                {
                    "title": "Bad step",
                    "description": "Cannot depend on itself.",
                    "tool_name": "calculate_expression",
                    "arguments_json": '{"expression":"1+1","variables":[]}',
                    "depends_on_positions": [1],
                }
            ],
        },
    )

    response = client.post(f"/tasks/{task['id']}/work-plan")
    assert response.status_code == 409
    detail = client.get(f"/tasks/{task['id']}").json()
    assert detail["status"] == "failed"
    assert detail["work_plans"] == []


def test_phase22_interrupted_step_freezes_until_explicit_retry(client):
    workspace = client.post("/workspaces", json={"name": "Interrupted Plan"}).json()
    task = _plan_task(client, workspace["id"])
    _wire_planner(
        client,
        {
            "plan_title": "Interrupted",
            "summary": "Recover without automatic replay.",
            "limitations": [],
            "steps": [
                {
                    "title": "Calculate",
                    "description": "Safe but should not auto-replay after interruption.",
                    "tool_name": "calculate_expression",
                    "arguments_json": '{"expression":"4*4","variables":[]}',
                    "depends_on_positions": [],
                }
            ],
        },
    )
    plan = client.post(f"/tasks/{task['id']}/work-plan").json()

    with client.app.state.database.session() as session:
        plan_row = session.get(WorkPlan, plan["id"])
        step_row = session.get(WorkPlanStep, plan["steps"][0]["id"])
        task_row = session.get(Task, task["id"])
        assert plan_row is not None and step_row is not None and task_row is not None
        plan_row.status = "running"
        step_row.status = "running"
        task_row.status = "running"

    assert client.app.state.work_plan_service.recover_interrupted() == 1
    frozen = client.get(f"/work-plans/{plan['id']}").json()
    assert frozen["status"] == "paused"
    assert frozen["steps"][0]["status"] == "interrupted"
    assert client.get(f"/tasks/{task['id']}").json()["status"] == "blocked"

    retried = client.post(f"/work-plans/{plan['id']}/retry")
    assert retried.status_code == 200
    assert retried.json()["status"] == "completed"
    assert retried.json()["steps"][0]["result"]["result"] == 16
