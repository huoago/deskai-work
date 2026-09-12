from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

from sqlalchemy import select

from app.agent.permissions import PermissionGate
from app.database.models import AuditLog, File, FileVersion, ToolCall
from app.database.session import Database


@dataclass(frozen=True, slots=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]

    def as_openai_tool(self) -> dict[str, Any]:
        return {
            "type": "function",
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
            "strict": True,
        }


class ToolRegistry:
    def __init__(
        self,
        database: Database,
        hybrid_search,
        memory_service,
        parser_cache,
        artifact_service,
        analysis_service,
        web_research_service,
        source_edit_service,
        source_edit_batch_service,
        file_organization_service,
        file_organization_batch_service,
        file_recycle_service,
        file_recycle_batch_service,
    ) -> None:
        self.database = database
        self.hybrid_search = hybrid_search
        self.memory_service = memory_service
        self.parser_cache = parser_cache
        self.artifact_service = artifact_service
        self.analysis_service = analysis_service
        self.web_research_service = web_research_service
        self.source_edit_service = source_edit_service
        self.source_edit_batch_service = source_edit_batch_service
        self.file_organization_service = file_organization_service
        self.file_organization_batch_service = file_organization_batch_service
        self.file_recycle_service = file_recycle_service
        self.file_recycle_batch_service = file_recycle_batch_service
        self.permission_gate = PermissionGate(database)
        self._specs = {spec.name: spec for spec in _tool_specs()}
        self._handlers: dict[
            str,
            Callable[[str, str, dict[str, Any]], dict[str, Any]],
        ] = {
            "search_knowledge": self._search_knowledge,
            "search_memory": self._search_memory,
            "list_workspace_files": self._list_workspace_files,
            "read_parsed_document": self._read_parsed_document,
            "create_word_document": self._create_word_document,
            "create_spreadsheet": self._create_spreadsheet,
            "calculate_expression": self._calculate_expression,
            "inspect_table": self._inspect_table,
            "summarize_table": self._summarize_table,
            "aggregate_table": self._aggregate_table,
            "search_web": self._search_web,
            "propose_source_file_edit": self._propose_source_file_edit,
            "propose_source_file_edit_batch": self._propose_source_file_edit_batch,
            "propose_file_organization": self._propose_file_organization,
            "propose_file_organization_batch": self._propose_file_organization_batch,
            "propose_file_recycle": self._propose_file_recycle,
            "propose_file_recycle_batch": self._propose_file_recycle_batch,
        }

    def planning_catalog(self, *, workspace_id: str | None) -> list[dict[str, Any]]:
        catalog: list[dict[str, Any]] = []
        for spec in self._specs.values():
            decision = self.permission_gate.check(
                workspace_id=workspace_id,
                tool_name=spec.name,
            )
            if not decision.allowed or decision.requires_confirmation:
                continue
            execution_mode = (
                "proposal_gate"
                if spec.name.startswith("propose_")
                else "auto"
            )
            if execution_mode == "auto" and decision.risk_level > 3:
                continue
            catalog.append(
                {
                    "name": spec.name,
                    "description": spec.description,
                    "parameters": spec.parameters,
                    "risk_level": decision.risk_level,
                    "execution_mode": execution_mode,
                }
            )
        return catalog

    def definitions(self, *, workspace_id: str | None) -> list[dict[str, Any]]:
        definitions: list[dict[str, Any]] = []
        for spec in self._specs.values():
            decision = self.permission_gate.check(
                workspace_id=workspace_id,
                tool_name=spec.name,
            )
            if decision.allowed and not decision.requires_confirmation:
                definitions.append(spec.as_openai_tool())
        return definitions

    def execute(
        self,
        *,
        agent_run_id: str,
        task_id: str,
        workspace_id: str | None,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        decision = self.permission_gate.check(
            workspace_id=workspace_id,
            tool_name=tool_name,
        )
        now = datetime.now(timezone.utc)
        with self.database.session() as session:
            call = ToolCall(
                agent_run_id=agent_run_id,
                tool_name=tool_name,
                arguments_json=arguments,
                status="running" if decision.allowed else "denied",
                risk_level=decision.risk_level,
                confirmation_required=decision.requires_confirmation,
                confirmed=False,
                started_at=now,
            )
            session.add(call)
            session.flush()
            call_id = call.id

            if not decision.allowed or decision.requires_confirmation:
                reason = (
                    decision.reason
                    if not decision.allowed
                    else "Tool requires confirmation and cannot run automatically"
                )
                call.result_summary = reason
                call.completed_at = now
                session.add(
                    AuditLog(
                        task_id=task_id,
                        agent_run_id=agent_run_id,
                        tool=tool_name,
                        action="tool_denied",
                        target=workspace_id,
                        result=reason,
                        risk_level=decision.risk_level,
                    )
                )
                return {
                    "ok": False,
                    "error": reason,
                    "tool_call_id": call_id,
                }

        handler = self._handlers.get(tool_name)
        if handler is None:
            return self._record_failure(
                call_id=call_id,
                task_id=task_id,
                agent_run_id=agent_run_id,
                tool_name=tool_name,
                workspace_id=workspace_id,
                risk_level=decision.risk_level,
                error="Unknown tool",
            )

        try:
            result = handler(task_id, str(workspace_id), arguments)
        except Exception as exc:
            return self._record_failure(
                call_id=call_id,
                task_id=task_id,
                agent_run_id=agent_run_id,
                tool_name=tool_name,
                workspace_id=workspace_id,
                risk_level=decision.risk_level,
                error=f"{type(exc).__name__}: {exc}",
            )

        summary = _compact_json(result, max_chars=4000)
        with self.database.session() as session:
            call = session.get(ToolCall, call_id)
            if call is not None:
                call.status = "completed"
                call.result_summary = summary
                call.completed_at = datetime.now(timezone.utc)
            session.add(
                AuditLog(
                    task_id=task_id,
                    agent_run_id=agent_run_id,
                    tool=tool_name,
                    action="tool_completed",
                    target=workspace_id,
                    result=summary,
                    risk_level=decision.risk_level,
                )
            )
        return {"ok": True, "tool_call_id": call_id, "result": result}

    def _record_failure(
        self,
        *,
        call_id: str,
        task_id: str,
        agent_run_id: str,
        tool_name: str,
        workspace_id: str | None,
        risk_level: int,
        error: str,
    ) -> dict[str, Any]:
        error = error[:4000]
        with self.database.session() as session:
            call = session.get(ToolCall, call_id)
            if call is not None:
                call.status = "failed"
                call.result_summary = error
                call.completed_at = datetime.now(timezone.utc)
            session.add(
                AuditLog(
                    task_id=task_id,
                    agent_run_id=agent_run_id,
                    tool=tool_name,
                    action="tool_failed",
                    target=workspace_id,
                    result=error,
                    risk_level=risk_level,
                )
            )
        return {"ok": False, "error": error, "tool_call_id": call_id}

    def _search_knowledge(
        self,
        _task_id: str,
        workspace_id: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        query = str(arguments.get("query") or "").strip()
        limit = _bounded_int(arguments.get("limit"), default=6, minimum=1, maximum=8)
        if not query:
            raise ValueError("query is required")
        hits = self.hybrid_search.search(workspace_id, query, limit=limit)
        distilled = []
        for hit in hits:
            distilled.append(
                {
                    "chunk_id": hit["chunk_id"],
                    "file_id": hit["file_id"],
                    "filename": hit["filename"],
                    "citation_label": hit["citation_label"],
                    "locator": hit["locator"],
                    "score": hit["score"],
                    "content": str(hit["content"])[:3500],
                }
            )
        return {"query": query, "count": len(distilled), "results": distilled}

    def _search_memory(
        self,
        _task_id: str,
        workspace_id: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        query = str(arguments.get("query") or "").strip()
        limit = _bounded_int(arguments.get("limit"), default=6, minimum=1, maximum=8)
        if not query:
            raise ValueError("query is required")
        results = self.memory_service.retrieve(
            workspace_id=workspace_id,
            query=query,
            limit=limit,
        )
        return {"query": query, "count": len(results), "results": results}

    def _list_workspace_files(
        self,
        _task_id: str,
        workspace_id: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        status = str(arguments.get("status") or "any")
        limit = _bounded_int(arguments.get("limit"), default=50, minimum=1, maximum=100)
        with self.database.session() as session:
            statement = (
                select(File)
                .where(File.workspace_id == workspace_id)
                .order_by(File.filename)
                .limit(limit)
            )
            if status != "any":
                statement = statement.where(File.status == status)
            files = session.scalars(statement).all()
            rows = [
                {
                    "file_id": item.id,
                    "filename": item.filename,
                    "extension": item.extension,
                    "mime_type": item.mime_type,
                    "size": item.size,
                    "status": item.status,
                    "modified_at": item.modified_at.isoformat() if item.modified_at else None,
                    "sha256": item.sha256,
                }
                for item in files
            ]
        return {"status_filter": status, "count": len(rows), "files": rows}

    def _read_parsed_document(
        self,
        _task_id: str,
        workspace_id: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        file_id = str(arguments.get("file_id") or "").strip()
        max_chars = _bounded_int(arguments.get("max_chars"), default=12000, minimum=1000, maximum=20000)
        max_units = _bounded_int(arguments.get("max_units"), default=20, minimum=1, maximum=40)
        if not file_id:
            raise ValueError("file_id is required")

        with self.database.session() as session:
            file = session.get(File, file_id)
            if file is None or file.workspace_id != workspace_id:
                raise ValueError("File is not available in the active Workspace")
            if file.status not in {"parsed", "indexed"} or not file.current_version_id:
                raise ValueError("File has not completed parsing")
            version = session.get(FileVersion, file.current_version_id)
            if version is None:
                raise ValueError("Current file version is missing")
            filename = file.filename
            sha256 = version.sha256

        payload = self.parser_cache.read(sha256)
        if payload is None:
            raise ValueError("Parsed cache is missing")
        text = str(payload.get("text") or "")
        units = list(payload.get("units") or [])
        return {
            "file_id": file_id,
            "filename": filename,
            "parser": payload.get("parser"),
            "file_type": payload.get("file_type"),
            "title": payload.get("title"),
            "metadata": payload.get("metadata") or {},
            "text": text[:max_chars],
            "text_truncated": len(text) > max_chars,
            "units": units[:max_units],
            "units_truncated": len(units) > max_units,
        }


    def _create_word_document(
        self,
        task_id: str,
        workspace_id: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        sections = arguments.get("sections")
        if not isinstance(sections, list):
            raise ValueError("sections must be a list")
        artifact = self.artifact_service.create_word_document(
            task_id=task_id,
            workspace_id=workspace_id,
            filename=str(arguments.get("filename") or "deskai-output.docx"),
            title=str(arguments.get("title") or ""),
            sections=sections,
        )
        return _artifact_payload(artifact)

    def _create_spreadsheet(
        self,
        task_id: str,
        workspace_id: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        sheets = arguments.get("sheets")
        if not isinstance(sheets, list):
            raise ValueError("sheets must be a list")
        artifact = self.artifact_service.create_spreadsheet(
            task_id=task_id,
            workspace_id=workspace_id,
            filename=str(arguments.get("filename") or "deskai-output.xlsx"),
            sheets=sheets,
        )
        return _artifact_payload(artifact)


    def _calculate_expression(
        self,
        _task_id: str,
        _workspace_id: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        raw_variables = arguments.get("variables") or []
        if not isinstance(raw_variables, list):
            raise ValueError("variables must be a list")
        variables: dict[str, float] = {}
        for item in raw_variables[:50]:
            if not isinstance(item, dict):
                raise ValueError("Each variable must be an object")
            variables[str(item.get("name") or "")] = float(item.get("value"))
        return self.analysis_service.calculate(
            str(arguments.get("expression") or ""),
            variables,
        )

    def _inspect_table(
        self,
        _task_id: str,
        workspace_id: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        return self.analysis_service.inspect_table(
            workspace_id=workspace_id,
            file_id=str(arguments.get("file_id") or ""),
            sheet=_optional_string(arguments.get("sheet")),
            header_row=_bounded_int(arguments.get("header_row"), default=1, minimum=1, maximum=20),
            max_rows=_bounded_int(arguments.get("max_rows"), default=10, minimum=1, maximum=50),
        )

    def _summarize_table(
        self,
        _task_id: str,
        workspace_id: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        columns = arguments.get("columns") or []
        if not isinstance(columns, list):
            raise ValueError("columns must be a list")
        return self.analysis_service.summarize_table(
            workspace_id=workspace_id,
            file_id=str(arguments.get("file_id") or ""),
            sheet=_optional_string(arguments.get("sheet")),
            header_row=_bounded_int(arguments.get("header_row"), default=1, minimum=1, maximum=20),
            columns=[str(item) for item in columns[:20]],
        )

    def _search_web(
        self,
        _task_id: str,
        _workspace_id: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        return self.web_research_service.search(
            query=str(arguments.get("query") or ""),
            max_sources=_bounded_int(
                arguments.get("max_sources"),
                default=6,
                minimum=1,
                maximum=10,
            ),
        )

    def _propose_source_file_edit(
        self,
        task_id: str,
        workspace_id: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        replacements = arguments.get("replacements") or []
        cell_edits = arguments.get("cell_edits") or []
        if not isinstance(replacements, list) or not isinstance(cell_edits, list):
            raise ValueError("replacements and cell_edits must be lists")
        return self.source_edit_service.propose(
            task_id=task_id,
            workspace_id=workspace_id,
            file_id=str(arguments.get("file_id") or ""),
            mode=str(arguments.get("mode") or ""),
            summary=str(arguments.get("summary") or ""),
            replacements=replacements,
            cell_edits=cell_edits,
        )

    def _propose_source_file_edit_batch(
        self,
        task_id: str,
        workspace_id: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        edits = arguments.get("edits") or []
        if not isinstance(edits, list):
            raise ValueError("edits must be a list")
        return self.source_edit_batch_service.propose(
            task_id=task_id,
            workspace_id=workspace_id,
            summary=str(arguments.get("summary") or ""),
            edits=edits,
        )

    def _propose_file_organization(
        self,
        task_id: str,
        workspace_id: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        return self.file_organization_service.propose(
            task_id=task_id,
            workspace_id=workspace_id,
            file_id=str(arguments.get("file_id") or ""),
            operation=str(arguments.get("operation") or ""),
            summary=str(arguments.get("summary") or ""),
            new_name=str(arguments.get("new_name") or ""),
            target_relative_dir=str(arguments.get("target_relative_dir") or ""),
        )

    def _propose_file_organization_batch(
        self,
        task_id: str,
        workspace_id: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        operations = arguments.get("operations") or []
        if not isinstance(operations, list):
            raise ValueError("operations must be a list")
        return self.file_organization_batch_service.propose(
            task_id=task_id,
            workspace_id=workspace_id,
            summary=str(arguments.get("summary") or ""),
            operations=operations,
        )

    def _propose_file_recycle(
        self,
        task_id: str,
        workspace_id: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        return self.file_recycle_service.propose(
            task_id=task_id,
            workspace_id=workspace_id,
            file_id=str(arguments.get("file_id") or ""),
            summary=str(arguments.get("summary") or ""),
        )

    def _propose_file_recycle_batch(
        self,
        task_id: str,
        workspace_id: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        items = arguments.get("items") or []
        if not isinstance(items, list):
            raise ValueError("items must be a list")
        return self.file_recycle_batch_service.propose(
            task_id=task_id,
            workspace_id=workspace_id,
            summary=str(arguments.get("summary") or ""),
            items=items,
        )

    def _aggregate_table(
        self,
        _task_id: str,
        workspace_id: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        return self.analysis_service.aggregate_table(
            workspace_id=workspace_id,
            file_id=str(arguments.get("file_id") or ""),
            sheet=_optional_string(arguments.get("sheet")),
            header_row=_bounded_int(arguments.get("header_row"), default=1, minimum=1, maximum=20),
            group_by=str(arguments.get("group_by") or ""),
            value_column=str(arguments.get("value_column") or ""),
            operation=str(arguments.get("operation") or ""),
            limit=_bounded_int(arguments.get("limit"), default=20, minimum=1, maximum=100),
        )


def _optional_string(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _artifact_payload(artifact) -> dict[str, Any]:
    return {
        "artifact_id": artifact.id,
        "kind": artifact.kind,
        "filename": artifact.filename,
        "mime_type": artifact.mime_type,
        "sha256": artifact.sha256,
        "size": artifact.size,
    }


def _bounded_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(parsed, maximum))


def _compact_json(value: Any, *, max_chars: int) -> str:
    text = json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
    return text[:max_chars]


def _tool_specs() -> list[ToolSpec]:
    return [
        ToolSpec(
            name="search_knowledge",
            description="Search indexed documents inside the active Workspace. Read-only.",
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "minLength": 1},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 8},
                },
                "required": ["query", "limit"],
                "additionalProperties": False,
            },
        ),
        ToolSpec(
            name="search_memory",
            description="Search durable user memories relevant to the active Workspace. Read-only.",
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "minLength": 1},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 8},
                },
                "required": ["query", "limit"],
                "additionalProperties": False,
            },
        ),
        ToolSpec(
            name="list_workspace_files",
            description="List file metadata already authorized in the active Workspace. Does not read file bytes.",
            parameters={
                "type": "object",
                "properties": {
                    "status": {
                        "type": "string",
                        "enum": [
                            "any",
                            "pending",
                            "parsed",
                            "indexed",
                            "failed",
                            "unsupported",
                            "deleted",
                            "revoked",
                        ],
                    },
                    "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                },
                "required": ["status", "limit"],
                "additionalProperties": False,
            },
        ),
        ToolSpec(
            name="read_parsed_document",
            description="Read the existing parsed cache for one file in the active Workspace. Read-only and Workspace-scoped.",
            parameters={
                "type": "object",
                "properties": {
                    "file_id": {"type": "string", "minLength": 1},
                    "max_chars": {"type": "integer", "minimum": 1000, "maximum": 20000},
                    "max_units": {"type": "integer", "minimum": 1, "maximum": 40},
                },
                "required": ["file_id", "max_chars", "max_units"],
                "additionalProperties": False,
            },
        ),
        ToolSpec(
            name="create_word_document",
            description="Create a new DOCX artifact inside DeskAI's private generated/task directory. Never writes to source Workspace folders and never overwrites an existing file.",
            parameters={
                "type": "object",
                "properties": {
                    "filename": {"type": "string", "minLength": 1, "maxLength": 200},
                    "title": {"type": "string", "maxLength": 500},
                    "sections": {
                        "type": "array",
                        "maxItems": 30,
                        "items": {
                            "type": "object",
                            "properties": {
                                "heading": {"type": "string", "maxLength": 500},
                                "body": {"type": "string", "maxLength": 30000},
                            },
                            "required": ["heading", "body"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["filename", "title", "sections"],
                "additionalProperties": False,
            },
        ),
        ToolSpec(
            name="create_spreadsheet",
            description="Create a new XLSX artifact inside DeskAI's private generated/task directory. Cell values are treated as data, formulas are not executed, and existing files are never overwritten.",
            parameters={
                "type": "object",
                "properties": {
                    "filename": {"type": "string", "minLength": 1, "maxLength": 200},
                    "sheets": {
                        "type": "array",
                        "maxItems": 10,
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string", "minLength": 1, "maxLength": 100},
                                "headers": {
                                    "type": "array",
                                    "maxItems": 50,
                                    "items": {"type": "string", "maxLength": 1000},
                                },
                                "rows": {
                                    "type": "array",
                                    "maxItems": 2000,
                                    "items": {
                                        "type": "array",
                                        "maxItems": 50,
                                        "items": {"type": "string", "maxLength": 10000},
                                    },
                                },
                            },
                            "required": ["name", "headers", "rows"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["filename", "sheets"],
                "additionalProperties": False,
            },
        ),
        ToolSpec(
            name="calculate_expression",
            description="Evaluate one bounded numeric expression locally. Supports arithmetic and approved numeric functions only; no imports, attributes, filesystem, network, subprocesses, or arbitrary Python statements.",
            parameters={
                "type": "object",
                "properties": {
                    "expression": {"type": "string", "minLength": 1, "maxLength": 500},
                    "variables": {
                        "type": "array",
                        "maxItems": 50,
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string", "minLength": 1, "maxLength": 64},
                                "value": {"type": "number"},
                            },
                            "required": ["name", "value"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["expression", "variables"],
                "additionalProperties": False,
            },
        ),
        ToolSpec(
            name="inspect_table",
            description="Inspect headers and a bounded row preview from a parsed CSV/XLSX file in the active Workspace. Uses parsed cache only.",
            parameters={
                "type": "object",
                "properties": {
                    "file_id": {"type": "string", "minLength": 1},
                    "sheet": {"type": "string", "maxLength": 255},
                    "header_row": {"type": "integer", "minimum": 1, "maximum": 20},
                    "max_rows": {"type": "integer", "minimum": 1, "maximum": 50},
                },
                "required": ["file_id", "sheet", "header_row", "max_rows"],
                "additionalProperties": False,
            },
        ),
        ToolSpec(
            name="summarize_table",
            description="Compute deterministic column counts and numeric min/max/sum/mean/median from a parsed CSV/XLSX file in the active Workspace.",
            parameters={
                "type": "object",
                "properties": {
                    "file_id": {"type": "string", "minLength": 1},
                    "sheet": {"type": "string", "maxLength": 255},
                    "header_row": {"type": "integer", "minimum": 1, "maximum": 20},
                    "columns": {
                        "type": "array",
                        "maxItems": 20,
                        "items": {"type": "string"},
                    },
                },
                "required": ["file_id", "sheet", "header_row", "columns"],
                "additionalProperties": False,
            },
        ),
        ToolSpec(
            name="search_web",
            description=(
                "Search the public web through the configured OpenAI provider and return "
                "a grounded synthesis with source URLs. Read-only. Does not open arbitrary "
                "URLs, download files, control a browser, or execute webpage instructions."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "minLength": 1, "maxLength": 500},
                    "max_sources": {"type": "integer", "minimum": 1, "maximum": 10},
                },
                "required": ["query", "max_sources"],
                "additionalProperties": False,
            },
        ),
        ToolSpec(
            name="propose_source_file_edit",
            description=(
                "Create a staged edit proposal for an existing TXT/MD/DOCX/XLSX file in "
                "the active Workspace. This tool NEVER overwrites the source file. The "
                "containing Workspace root must already have write_allowed=true, and a "
                "human must confirm the proposal in the desktop UI before any source write."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "file_id": {"type": "string", "minLength": 1},
                    "mode": {
                        "type": "string",
                        "enum": ["text_replace", "docx_replace", "xlsx_cells"],
                    },
                    "summary": {"type": "string", "minLength": 1, "maxLength": 1000},
                    "replacements": {
                        "type": "array",
                        "maxItems": 20,
                        "items": {
                            "type": "object",
                            "properties": {
                                "find": {"type": "string", "minLength": 1, "maxLength": 10000},
                                "replace": {"type": "string", "maxLength": 20000},
                                "replace_all": {"type": "boolean"},
                            },
                            "required": ["find", "replace", "replace_all"],
                            "additionalProperties": False,
                        },
                    },
                    "cell_edits": {
                        "type": "array",
                        "maxItems": 100,
                        "items": {
                            "type": "object",
                            "properties": {
                                "sheet": {"type": "string", "minLength": 1, "maxLength": 255},
                                "cell": {"type": "string", "minLength": 1, "maxLength": 16},
                                "value_type": {
                                    "type": "string",
                                    "enum": ["text", "number", "boolean", "blank"],
                                },
                                "value": {"type": "string", "maxLength": 10000},
                            },
                            "required": ["sheet", "cell", "value_type", "value"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": [
                    "file_id",
                    "mode",
                    "summary",
                    "replacements",
                    "cell_edits",
                ],
                "additionalProperties": False,
            },
        ),
        ToolSpec(
            name="propose_source_file_edit_batch",
            description=(
                "Create one transactional staged proposal spanning 2-10 existing "
                "TXT/MD/DOCX/XLSX files in the active Workspace. This tool NEVER writes "
                "source files. Every member requires write_allowed=true and the whole "
                "batch must be confirmed once in the desktop UI. Confirmation uses "
                "all-or-nothing preflight, backups, SHA checks, and automatic rollback."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "summary": {"type": "string", "minLength": 1, "maxLength": 1000},
                    "edits": {
                        "type": "array",
                        "minItems": 2,
                        "maxItems": 10,
                        "items": {
                            "type": "object",
                            "properties": {
                                "file_id": {"type": "string", "minLength": 1},
                                "mode": {
                                    "type": "string",
                                    "enum": ["text_replace", "docx_replace", "xlsx_cells"],
                                },
                                "summary": {
                                    "type": "string",
                                    "minLength": 1,
                                    "maxLength": 1000,
                                },
                                "replacements": {
                                    "type": "array",
                                    "maxItems": 20,
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "find": {
                                                "type": "string",
                                                "minLength": 1,
                                                "maxLength": 10000,
                                            },
                                            "replace": {
                                                "type": "string",
                                                "maxLength": 20000,
                                            },
                                            "replace_all": {"type": "boolean"},
                                        },
                                        "required": ["find", "replace", "replace_all"],
                                        "additionalProperties": False,
                                    },
                                },
                                "cell_edits": {
                                    "type": "array",
                                    "maxItems": 100,
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "sheet": {
                                                "type": "string",
                                                "minLength": 1,
                                                "maxLength": 255,
                                            },
                                            "cell": {
                                                "type": "string",
                                                "minLength": 1,
                                                "maxLength": 16,
                                            },
                                            "value_type": {
                                                "type": "string",
                                                "enum": ["text", "number", "boolean", "blank"],
                                            },
                                            "value": {
                                                "type": "string",
                                                "maxLength": 10000,
                                            },
                                        },
                                        "required": ["sheet", "cell", "value_type", "value"],
                                        "additionalProperties": False,
                                    },
                                },
                            },
                            "required": [
                                "file_id",
                                "mode",
                                "summary",
                                "replacements",
                                "cell_edits",
                            ],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["summary", "edits"],
                "additionalProperties": False,
            },
        ),
        ToolSpec(
            name="propose_file_organization",
            description=(
                "Stage a rename or move proposal for one existing Workspace file. "
                "This tool NEVER changes the path itself. Rename must preserve the "
                "extension. Move is limited to an existing directory inside the same "
                "authorized writable Workspace root. Targets must not exist, and a "
                "human must confirm the proposal in the desktop UI."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "file_id": {"type": "string", "minLength": 1},
                    "operation": {
                        "type": "string",
                        "enum": ["rename", "move"],
                    },
                    "summary": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 1000,
                    },
                    "new_name": {
                        "type": "string",
                        "maxLength": 240,
                    },
                    "target_relative_dir": {
                        "type": "string",
                        "maxLength": 1000,
                    },
                },
                "required": [
                    "file_id",
                    "operation",
                    "summary",
                    "new_name",
                    "target_relative_dir",
                ],
                "additionalProperties": False,
            },
        ),
        ToolSpec(
            name="propose_file_organization_batch",
            description=(
                "Stage one transactional batch of 2-10 independent file rename/move "
                "operations. This tool NEVER changes paths. Every operation remains "
                "inside the same authorized writable root as its source, preserves "
                "extensions for rename, requires non-existing unique targets, and the "
                "whole batch must be confirmed once in the desktop UI."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "summary": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 1000,
                    },
                    "operations": {
                        "type": "array",
                        "minItems": 2,
                        "maxItems": 10,
                        "items": {
                            "type": "object",
                            "properties": {
                                "file_id": {"type": "string", "minLength": 1},
                                "operation": {
                                    "type": "string",
                                    "enum": ["rename", "move"],
                                },
                                "summary": {
                                    "type": "string",
                                    "minLength": 1,
                                    "maxLength": 1000,
                                },
                                "new_name": {
                                    "type": "string",
                                    "maxLength": 240,
                                },
                                "target_relative_dir": {
                                    "type": "string",
                                    "maxLength": 1000,
                                },
                            },
                            "required": [
                                "file_id",
                                "operation",
                                "summary",
                                "new_name",
                                "target_relative_dir",
                            ],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["summary", "operations"],
                "additionalProperties": False,
            },
        ),
        ToolSpec(
            name="propose_file_recycle",
            description=(
                "Stage a recoverable recycle proposal for one existing Workspace file. "
                "This tool NEVER deletes or moves the source by itself. After explicit "
                "desktop confirmation, DeskAI first creates and SHA-verifies a private "
                "quarantine copy, then removes the Workspace original. The quarantined "
                "copy remains restorable. Permanent deletion is not available."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "file_id": {"type": "string", "minLength": 1},
                    "summary": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 1000,
                    },
                },
                "required": ["file_id", "summary"],
                "additionalProperties": False,
            },
        ),
        ToolSpec(
            name="propose_file_recycle_batch",
            description=(
                "Stage one recoverable recycle transaction for 2-10 existing Workspace "
                "files. This tool NEVER removes files. After one explicit desktop "
                "confirmation, DeskAI must prepare and SHA-verify quarantine copies for "
                "every member before removing any Workspace original. The whole batch "
                "remains restorable and permanent deletion is unavailable."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "summary": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 1000,
                    },
                    "items": {
                        "type": "array",
                        "minItems": 2,
                        "maxItems": 10,
                        "items": {
                            "type": "object",
                            "properties": {
                                "file_id": {"type": "string", "minLength": 1},
                                "summary": {
                                    "type": "string",
                                    "minLength": 1,
                                    "maxLength": 1000,
                                },
                            },
                            "required": ["file_id", "summary"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["summary", "items"],
                "additionalProperties": False,
            },
        ),
        ToolSpec(
            name="aggregate_table",
            description="Group a parsed CSV/XLSX table by one column and compute count/sum/mean/min/max over another column. Read-only and deterministic.",
            parameters={
                "type": "object",
                "properties": {
                    "file_id": {"type": "string", "minLength": 1},
                    "sheet": {"type": "string", "maxLength": 255},
                    "header_row": {"type": "integer", "minimum": 1, "maximum": 20},
                    "group_by": {"type": "string", "minLength": 1},
                    "value_column": {"type": "string", "minLength": 1},
                    "operation": {"type": "string", "enum": ["count", "sum", "mean", "min", "max"]},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                },
                "required": ["file_id", "sheet", "header_row", "group_by", "value_column", "operation", "limit"],
                "additionalProperties": False,
            },
        ),
    ]
