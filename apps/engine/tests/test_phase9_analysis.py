from __future__ import annotations

import json

import pytest

from app.ai.provider import AgentResponse, AgentToolRequest
from app.database.models import File, FileVersion
from app.security.secrets import SecretStatus


class AgentSecretStore:
    def get_openai_api_key(self) -> str | None:
        return "sk-test-agent-123456789012345678901234"

    def status(self) -> SecretStatus:
        return SecretStatus(
            configured=True,
            source="credential_manager",
            credential_store_available=True,
            writable=True,
        )


class FakeAgentProvider:
    def __init__(self, responses: list[AgentResponse]) -> None:
        self.responses = list(responses)
        self.calls: list[dict] = []

    async def agent_response(self, **kwargs):
        self.calls.append(kwargs)
        if not self.responses:
            raise AssertionError("Fake Agent provider ran out of responses")
        return self.responses.pop(0)


def _wire_provider(client, provider: FakeAgentProvider) -> None:
    secret_store = AgentSecretStore()
    client.app.state.secret_store = secret_store
    client.app.state.agent_orchestrator.secret_store = secret_store
    client.app.state.agent_orchestrator.provider = provider


def _tool_step(name: str, arguments: dict, *, call_id: str) -> AgentResponse:
    return AgentResponse(
        text="",
        tool_calls=[AgentToolRequest(call_id=call_id, name=name, arguments=arguments)],
        output_items=[
            {
                "type": "function_call",
                "call_id": call_id,
                "name": name,
                "arguments": json.dumps(arguments, ensure_ascii=False),
            }
        ],
        response_id=f"resp_{call_id}",
        model="gpt-5.6-sol",
        input_tokens=40,
        output_tokens=10,
    )


def _final(text: str) -> AgentResponse:
    return AgentResponse(
        text=text,
        tool_calls=[],
        output_items=[{"type": "message", "role": "assistant"}],
        response_id="resp_final",
        model="gpt-5.6-sol",
        input_tokens=50,
        output_tokens=20,
    )


def _seed_parsed_table(
    client,
    *,
    workspace_id: str,
    filename: str,
    extension: str,
    sha256: str,
    units: list[dict],
) -> str:
    with client.app.state.database.session() as session:
        file = File(
            workspace_id=workspace_id,
            path=f"/authorized/{workspace_id}/{filename}",
            filename=filename,
            extension=extension,
            mime_type=(
                "text/csv"
                if extension == ".csv"
                else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            ),
            size=100,
            sha256=sha256,
            status="parsed",
        )
        session.add(file)
        session.flush()
        version = FileVersion(
            file_id=file.id,
            sha256=sha256,
            size=100,
            parser_version="test",
            active=True,
        )
        session.add(version)
        session.flush()
        file.current_version_id = version.id
        file_id = file.id

    client.app.state.parser_worker.cache.write(
        sha256,
        {
            "file_id": file_id,
            "sha256": sha256,
            "parser": "test",
            "parser_version": "test",
            "file_type": extension.lstrip("."),
            "title": filename,
            "text": "\n".join(str(unit.get("text") or "") for unit in units),
            "units": units,
            "metadata": {},
        },
    )
    return file_id


def test_phase9_safe_expression_allows_math_but_rejects_python_escape(client):
    service = client.app.state.analysis_service

    result = service.calculate(
        "sqrt(a*a + b*b) + round(2.49)",
        {"a": 3, "b": 4},
    )
    assert result["result"] == 7

    dangerous = [
        "__import__('os').system('whoami')",
        "(1).__class__",
        "open('secret.txt')",
        "[x for x in [1, 2, 3]]",
        "10**1000",
        "x[0]",
    ]
    for expression in dangerous:
        with pytest.raises((ValueError, SyntaxError)):
            service.calculate(expression, {"x": 1})


def test_phase9_csv_inspect_summary_and_aggregation_are_deterministic(client):
    workspace = client.post("/workspaces", json={"name": "CSV Analysis"}).json()
    file_id = _seed_parsed_table(
        client,
        workspace_id=workspace["id"],
        filename="sales.csv",
        extension=".csv",
        sha256="a" * 64,
        units=[
            {
                "kind": "csv_rows",
                "text": (
                    "Region\tAmount\tCount\n"
                    "North\t10\t2\n"
                    "South\t20\t3\n"
                    "North\t30\t4\n"
                    "South\t40\t5"
                ),
                "locator": {"row_start": 1, "row_end": 5},
            }
        ],
    )
    service = client.app.state.analysis_service

    preview = service.inspect_table(
        workspace_id=workspace["id"],
        file_id=file_id,
        sheet=None,
        header_row=1,
        max_rows=2,
    )
    assert preview["headers"] == ["Region", "Amount", "Count"]
    assert preview["row_count"] == 4
    assert preview["preview_rows"] == [["North", "10", "2"], ["South", "20", "3"]]
    assert preview["preview_truncated"] is True

    summary = service.summarize_table(
        workspace_id=workspace["id"],
        file_id=file_id,
        sheet=None,
        header_row=1,
        columns=["Amount"],
    )
    amount = summary["columns"][0]
    assert amount["numeric_count"] == 4
    assert amount["min"] == 10
    assert amount["max"] == 40
    assert amount["sum"] == 100
    assert amount["mean"] == 25
    assert amount["median"] == 25

    grouped = service.aggregate_table(
        workspace_id=workspace["id"],
        file_id=file_id,
        sheet=None,
        header_row=1,
        group_by="Region",
        value_column="Amount",
        operation="sum",
        limit=10,
    )
    assert grouped["groups"] == [
        {"group": "South", "value": 60.0, "row_count": 2, "numeric_count": 2},
        {"group": "North", "value": 40.0, "row_count": 2, "numeric_count": 2},
    ]


def test_phase9_xlsx_sheet_selection_and_workspace_isolation(client):
    first = client.post("/workspaces", json={"name": "Workbook A"}).json()
    second = client.post("/workspaces", json={"name": "Workbook B"}).json()
    file_id = _seed_parsed_table(
        client,
        workspace_id=first["id"],
        filename="metrics.xlsx",
        extension=".xlsx",
        sha256="b" * 64,
        units=[
            {
                "kind": "sheet",
                "text": "City\tSales\nLima\t100\nCusco\t50",
                "locator": {"sheet": "Sales", "row_start": 1, "row_end": 3},
            },
            {
                "kind": "sheet",
                "text": "City\tIssues\nLima\t4\nCusco\t2",
                "locator": {"sheet": "Quality", "row_start": 1, "row_end": 3},
            },
        ],
    )
    service = client.app.state.analysis_service

    quality = service.inspect_table(
        workspace_id=first["id"],
        file_id=file_id,
        sheet="Quality",
        header_row=1,
        max_rows=10,
    )
    assert quality["sheet"] == "Quality"
    assert quality["headers"] == ["City", "Issues"]
    assert quality["preview_rows"][0] == ["Lima", "4"]

    with pytest.raises(ValueError, match="active Workspace"):
        service.inspect_table(
            workspace_id=second["id"],
            file_id=file_id,
            sheet="Quality",
            header_row=1,
            max_rows=10,
        )


def test_phase9_agent_can_chain_table_analysis_and_receive_local_results(client):
    workspace = client.post("/workspaces", json={"name": "Agent Analysis"}).json()
    file_id = _seed_parsed_table(
        client,
        workspace_id=workspace["id"],
        filename="water-meters.csv",
        extension=".csv",
        sha256="c" * 64,
        units=[
            {
                "kind": "csv_rows",
                "text": (
                    "Category\tQuantity\n"
                    "OlderThan5\t1802\n"
                    "Under5\t2487\n"
                    "NoMeter\t154\n"
                    "Abnormal\t4"
                ),
                "locator": {"row_start": 1, "row_end": 5},
            }
        ],
    )

    provider = FakeAgentProvider(
        [
            _tool_step(
                "summarize_table",
                {
                    "file_id": file_id,
                    "sheet": "",
                    "header_row": 1,
                    "columns": ["Quantity"],
                },
                call_id="summary_1",
            ),
            _tool_step(
                "calculate_expression",
                {
                    "expression": "older + no_meter + abnormal",
                    "variables": [
                        {"name": "older", "value": 1802},
                        {"name": "no_meter", "value": 154},
                        {"name": "abnormal", "value": 4},
                    ],
                },
                call_id="calc_1",
            ),
            _final("按既定口径，需要更换或新增的数量为1960。"),
        ]
    )
    _wire_provider(client, provider)
    task = client.post(
        "/tasks",
        json={
            "workspace_id": workspace["id"],
            "request": "分析这个水表表格，并计算大于5年、新装和异常的合计。",
        },
    ).json()

    assert client.post("/agent/process", params={"limit": 1}).json()["processed"] == 1
    detail = client.get(f"/tasks/{task['id']}").json()
    assert detail["status"] == "completed"
    assert "1960" in detail["result_text"]
    assert [call["tool_name"] for call in detail["tool_calls"]] == [
        "summarize_table",
        "calculate_expression",
    ]

    second_round = json.dumps(provider.calls[1]["input_items"], ensure_ascii=False)
    third_round = json.dumps(provider.calls[2]["input_items"], ensure_ascii=False)
    assert '"sum":4447.0' in second_round
    assert '"result":1960.0' in third_round
