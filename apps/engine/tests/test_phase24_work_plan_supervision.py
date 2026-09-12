from __future__ import annotations

from app.security.secrets import SecretStatus


class Phase24SecretStore:
    def get_openai_api_key(self) -> str | None:
        return "sk-test-phase24-123456789012345678901234"

    def status(self) -> SecretStatus:
        return SecretStatus(
            configured=True,
            source="credential_manager",
            credential_store_available=True,
            writable=True,
        )


class Phase24Planner:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    async def draft_work_plan(self, **kwargs):
        return self.payload


def _wire_planner(client, steps: list[dict], summary: str = "Phase 24 supervised plan"):
    service = client.app.state.work_plan_service
    service.secret_store = Phase24SecretStore()
    service.provider = Phase24Planner(
        {
            "plan_title": "Phase 24 supervision",
            "summary": summary,
            "limitations": [],
            "steps": steps,
        }
    )


def _workspace_task(client):
    workspace = client.post("/workspaces", json={"name": "Phase 24"}).json()
    task = client.post(
        "/tasks",
        json={
            "workspace_id": workspace["id"],
            "request": "Run a supervised work plan",
            "execution_mode": "plan",
        },
    ).json()
    return workspace, task


def _calc_step(title: str, expression: str, depends: list[int] | None = None):
    return {
        "title": title,
        "description": title,
        "tool_name": "calculate_expression",
        "arguments_json": (
            '{"expression":"' + expression + '","variables":[]}'
        ),
        "depends_on_positions": depends or [],
    }


def _draft(client, task_id: str):
    response = client.post(f"/tasks/{task_id}/work-plan")
    assert response.status_code == 200
    return response.json()


def _process(client, limit: int = 3):
    response = client.post("/work-plan-worker/process", params={"limit": limit})
    assert response.status_code == 200
    return response.json()


def test_phase24_pause_queued_plan_then_continue(client):
    _, task = _workspace_task(client)
    _wire_planner(client, [_calc_step("Calculate", "20+22")])
    plan = _draft(client, task["id"])

    assert client.post(f"/work-plans/{plan['id']}/start").json()["status"] == "queued"
    paused = client.post(f"/work-plans/{plan['id']}/pause")
    assert paused.status_code == 200
    assert paused.json()["status"] == "paused"
    assert _process(client, limit=1)["processed"] == 0

    continued = client.post(f"/work-plans/{plan['id']}/continue")
    assert continued.status_code == 200
    assert continued.json()["status"] == "queued"
    assert _process(client, limit=1)["processed"] == 1
    completed = client.get(f"/work-plans/{plan['id']}").json()
    assert completed["status"] == "completed"
    assert completed["steps"][0]["result"]["result"] == 42


def test_phase24_pause_during_running_tool_stops_at_step_boundary(client):
    _, task = _workspace_task(client)
    _wire_planner(
        client,
        [
            _calc_step("First", "6*7"),
            _calc_step("Second", "100+1", [1]),
        ],
    )
    plan = _draft(client, task["id"])
    assert client.post(f"/work-plans/{plan['id']}/start").json()["status"] == "queued"

    registry = client.app.state.tool_registry
    original_execute = registry.execute

    def pause_then_execute(**kwargs):
        paused = client.app.state.work_plan_service.pause(plan["id"])
        assert paused["status"] == "pausing"
        return original_execute(**kwargs)

    registry.execute = pause_then_execute
    try:
        assert _process(client, limit=1)["processed"] == 1
    finally:
        registry.execute = original_execute

    paused = client.get(f"/work-plans/{plan['id']}").json()
    assert paused["status"] == "paused"
    assert paused["steps"][0]["status"] == "completed"
    assert paused["steps"][1]["status"] == "pending"

    assert client.post(f"/work-plans/{plan['id']}/continue").json()["status"] == "queued"
    assert _process(client, limit=1)["processed"] == 1
    assert client.get(f"/work-plans/{plan['id']}").json()["status"] == "completed"


def test_phase24_risk_threshold_requires_pre_step_human_approval(client):
    _, task = _workspace_task(client)
    _wire_planner(client, [_calc_step("Risk gated calculation", "2+3")])
    plan = _draft(client, task["id"])

    updated = client.patch(
        f"/work-plans/{plan['id']}/supervision",
        json={"approval_risk_threshold": 1},
    )
    assert updated.status_code == 200
    assert updated.json()["supervision"]["approval_risk_threshold"] == 1

    assert client.post(f"/work-plans/{plan['id']}/start").json()["status"] == "queued"
    assert _process(client, limit=1)["processed"] == 1
    waiting = client.get(f"/work-plans/{plan['id']}").json()
    assert waiting["status"] == "awaiting_step_approval"
    assert waiting["steps"][0]["status"] == "awaiting_step_approval"
    assert client.get(f"/tasks/{task['id']}").json()["tool_calls"] == []
    assert waiting["unread_notifications"] >= 1

    notifications = client.get(
        f"/work-plans/{plan['id']}/notifications"
    ).json()
    assert any(item["event_type"] == "step_approval_required" for item in notifications)
    event = next(
        item for item in notifications if item["event_type"] == "step_approval_required"
    )
    acknowledged = client.post(
        f"/work-plans/{plan['id']}/events/{event['id']}/acknowledge"
    )
    assert acknowledged.status_code == 200
    assert acknowledged.json()["acknowledged_at"] is not None

    approved = client.post(
        f"/work-plans/{plan['id']}/steps/{waiting['steps'][0]['id']}/approve"
    )
    assert approved.status_code == 200
    assert approved.json()["status"] == "queued"
    assert _process(client, limit=1)["processed"] == 1
    completed = client.get(f"/work-plans/{plan['id']}").json()
    assert completed["status"] == "completed"
    assert completed["steps"][0]["approved_at"] is not None


def test_phase24_skip_is_allowed_only_without_active_dependents(client):
    _, task = _workspace_task(client)
    _wire_planner(
        client,
        [
            _calc_step("Optional independent", "1+1"),
            _calc_step("Required independent", "3+4"),
        ],
    )
    plan = _draft(client, task["id"])
    first = plan["steps"][0]

    skipped = client.post(
        f"/work-plans/{plan['id']}/steps/{first['id']}/skip",
        json={"reason": "Supervisor decided this optional calculation is unnecessary"},
    )
    assert skipped.status_code == 200
    assert skipped.json()["steps"][0]["status"] == "skipped"
    assert skipped.json()["steps"][0]["skip_reason"]

    assert client.post(f"/work-plans/{plan['id']}/start").json()["status"] == "queued"
    assert _process(client, limit=1)["processed"] == 1
    completed = client.get(f"/work-plans/{plan['id']}").json()
    assert completed["status"] == "completed"
    assert completed["estimate"]["skipped_steps"] == 1

    _, task2 = _workspace_task(client)
    _wire_planner(
        client,
        [
            _calc_step("Required parent", "5+5"),
            _calc_step("Dependent", "9+1", [1]),
        ],
    )
    plan2 = _draft(client, task2["id"])
    denied = client.post(
        f"/work-plans/{plan2['id']}/steps/{plan2['steps'][0]['id']}/skip",
        json={"reason": "Try to bypass dependency"},
    )
    assert denied.status_code == 409
    assert "depend" in denied.json()["detail"].lower()


def test_phase24_step_budget_pauses_then_can_be_extended(client):
    _, task = _workspace_task(client)
    _wire_planner(
        client,
        [
            _calc_step("First", "10+1"),
            _calc_step("Second", "20+2", [1]),
        ],
    )
    plan = _draft(client, task["id"])
    assert client.patch(
        f"/work-plans/{plan['id']}/supervision",
        json={"max_auto_steps": 1},
    ).status_code == 200

    assert client.post(f"/work-plans/{plan['id']}/start").json()["status"] == "queued"
    assert _process(client, limit=1)["processed"] == 1
    paused = client.get(f"/work-plans/{plan['id']}").json()
    assert paused["status"] == "paused"
    assert paused["steps"][0]["status"] == "completed"
    assert paused["steps"][1]["status"] == "pending"
    assert paused["supervision"]["auto_steps_remaining"] == 0
    assert "budget" in (paused["pause_reason"] or "").lower()

    assert client.patch(
        f"/work-plans/{plan['id']}/supervision",
        json={"max_auto_steps": 2},
    ).status_code == 200
    assert client.post(f"/work-plans/{plan['id']}/continue").json()["status"] == "queued"
    assert _process(client, limit=1)["processed"] == 1
    assert client.get(f"/work-plans/{plan['id']}").json()["status"] == "completed"


def test_phase24_soft_timeout_pauses_only_after_tool_returns(client, monkeypatch):
    _, task = _workspace_task(client)
    _wire_planner(client, [_calc_step("Long supervised step", "40+2")])
    plan = _draft(client, task["id"])
    assert client.patch(
        f"/work-plans/{plan['id']}/supervision",
        json={"step_timeout_seconds": 5},
    ).status_code == 200

    ticks = iter([100.0, 106.5])
    monkeypatch.setattr(
        client.app.state.work_plan_service,
        "_monotonic",
        lambda: next(ticks),
    )

    assert client.post(f"/work-plans/{plan['id']}/start").json()["status"] == "queued"
    assert _process(client, limit=1)["processed"] == 1
    paused = client.get(f"/work-plans/{plan['id']}").json()
    assert paused["status"] == "paused"
    assert paused["steps"][0]["status"] == "completed"
    assert paused["steps"][0]["timeout_exceeded"] is True
    assert paused["steps"][0]["duration_seconds"] == 6.5
    assert "timeout" in " ".join(
        event["event_type"] for event in paused["latest_events"]
    )

    assert client.post(f"/work-plans/{plan['id']}/continue").json()["status"] == "queued"
    assert _process(client, limit=1)["processed"] == 1
    assert client.get(f"/work-plans/{plan['id']}").json()["status"] == "completed"


def test_phase24_failure_policy_pause_and_stop(client):
    _, task = _workspace_task(client)
    _wire_planner(client, [_calc_step("Will fail", "1+1")])
    plan = _draft(client, task["id"])

    registry = client.app.state.tool_registry
    original_execute = registry.execute

    def fail_execute(**kwargs):
        return {
            "ok": False,
            "tool_call_id": "phase24-failure",
            "error": "simulated tool failure",
        }

    registry.execute = fail_execute
    try:
        assert client.post(f"/work-plans/{plan['id']}/start").json()["status"] == "queued"
        assert _process(client, limit=1)["processed"] == 1
    finally:
        registry.execute = original_execute

    paused = client.get(f"/work-plans/{plan['id']}").json()
    assert paused["status"] == "paused"
    assert paused["steps"][0]["status"] == "failed"
    assert paused["supervision"]["failure_policy"] == "pause"
    assert paused["can_retry"] is True

    _, task2 = _workspace_task(client)
    _wire_planner(client, [_calc_step("Stop failure", "1+1")])
    plan2 = _draft(client, task2["id"])
    assert client.patch(
        f"/work-plans/{plan2['id']}/supervision",
        json={"failure_policy": "stop"},
    ).status_code == 200

    registry.execute = fail_execute
    try:
        assert client.post(f"/work-plans/{plan2['id']}/start").json()["status"] == "queued"
        assert _process(client, limit=1)["processed"] == 1
    finally:
        registry.execute = original_execute

    stopped = client.get(f"/work-plans/{plan2['id']}").json()
    assert stopped["status"] == "failed"
    assert stopped["steps"][0]["status"] == "failed"
    assert stopped["supervision"]["failure_policy"] == "stop"


def test_phase24_supervision_validation_and_timeline(client):
    _, task = _workspace_task(client)
    _wire_planner(client, [_calc_step("Timeline", "8+8")])
    plan = _draft(client, task["id"])

    invalid = client.patch(
        f"/work-plans/{plan['id']}/supervision",
        json={"runtime_budget_seconds": 10},
    )
    assert invalid.status_code == 422

    updated = client.patch(
        f"/work-plans/{plan['id']}/supervision",
        json={
            "runtime_budget_seconds": 120,
            "step_timeout_seconds": 30,
            "approval_risk_threshold": 4,
            "failure_policy": "pause",
        },
    )
    assert updated.status_code == 200
    payload = updated.json()
    assert payload["supervision"]["runtime_budget_seconds"] == 120
    assert payload["supervision"]["step_timeout_seconds"] == 30

    events = client.get(f"/work-plans/{plan['id']}/events").json()
    assert any(item["event_type"] == "supervision_updated" for item in events)
