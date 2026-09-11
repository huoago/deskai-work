from __future__ import annotations

import ast
import math
import statistics
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select

from app.database.models import File, FileVersion
from app.database.session import Database

ALLOWED_FUNCTIONS = {
    "abs": abs,
    "round": round,
    "min": min,
    "max": max,
    "sqrt": math.sqrt,
    "log": math.log,
    "log10": math.log10,
    "exp": math.exp,
    "floor": math.floor,
    "ceil": math.ceil,
}

ALLOWED_NODES = (
    ast.Expression,
    ast.Constant,
    ast.Name,
    ast.Load,
    ast.BinOp,
    ast.UnaryOp,
    ast.Add,
    ast.Sub,
    ast.Mult,
    ast.Div,
    ast.FloorDiv,
    ast.Mod,
    ast.Pow,
    ast.USub,
    ast.UAdd,
    ast.Call,
    ast.Tuple,
    ast.List,
)


@dataclass(slots=True)
class TableData:
    filename: str
    sheet: str | None
    headers: list[str]
    rows: list[list[str]]


class DataAnalysisService:
    def __init__(self, database: Database, parser_cache) -> None:
        self.database = database
        self.parser_cache = parser_cache

    def calculate(self, expression: str, variables: dict[str, float] | None = None) -> dict[str, Any]:
        expression = expression.strip()
        if not expression or len(expression) > 500:
            raise ValueError("Expression must contain 1-500 characters")
        safe_variables = {
            str(key): float(value)
            for key, value in (variables or {}).items()
            if _valid_identifier(str(key))
        }
        tree = ast.parse(expression, mode="eval")
        nodes = list(ast.walk(tree))
        if len(nodes) > 100:
            raise ValueError("Expression is too complex")
        for node in nodes:
            if not isinstance(node, ALLOWED_NODES):
                raise ValueError(f"Unsupported expression element: {type(node).__name__}")
            if isinstance(node, ast.Name) and node.id not in safe_variables and node.id not in ALLOWED_FUNCTIONS:
                raise ValueError(f"Unknown name: {node.id}")
            if isinstance(node, ast.Call):
                if not isinstance(node.func, ast.Name) or node.func.id not in ALLOWED_FUNCTIONS:
                    raise ValueError("Only approved numeric functions may be called")
                if node.keywords:
                    raise ValueError("Keyword arguments are not supported")
            if isinstance(node, ast.Constant):
                if not isinstance(node.value, (int, float)):
                    raise ValueError("Only numeric constants are allowed")
                if abs(float(node.value)) > 1e100:
                    raise ValueError("Numeric constant is too large")
            if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Pow):
                if not isinstance(node.right, ast.Constant) or not isinstance(
                    node.right.value, (int, float)
                ):
                    raise ValueError("Exponent must be a numeric constant")
                if abs(float(node.right.value)) > 100:
                    raise ValueError("Exponent is too large")

        value = eval(
            compile(tree, "<deskai-safe-expression>", "eval"),
            {"__builtins__": {}},
            {**ALLOWED_FUNCTIONS, **safe_variables},
        )
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("Expression must return one numeric value")
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError("Expression returned a non-finite number")
        return {"expression": expression, "variables": safe_variables, "result": value}

    def inspect_table(
        self,
        *,
        workspace_id: str,
        file_id: str,
        sheet: str | None,
        header_row: int,
        max_rows: int,
    ) -> dict[str, Any]:
        table = self._load_table(
            workspace_id=workspace_id,
            file_id=file_id,
            sheet=sheet,
            header_row=header_row,
        )
        rows = table.rows[: max(1, min(max_rows, 50))]
        return {
            "file_id": file_id,
            "filename": table.filename,
            "sheet": table.sheet,
            "headers": table.headers,
            "row_count": len(table.rows),
            "preview_rows": rows,
            "preview_truncated": len(table.rows) > len(rows),
        }

    def summarize_table(
        self,
        *,
        workspace_id: str,
        file_id: str,
        sheet: str | None,
        header_row: int,
        columns: list[str],
    ) -> dict[str, Any]:
        table = self._load_table(
            workspace_id=workspace_id,
            file_id=file_id,
            sheet=sheet,
            header_row=header_row,
        )
        requested = columns[:20] if columns else table.headers[:20]
        result: list[dict[str, Any]] = []
        for column in requested:
            index = _column_index(table.headers, column)
            values = [row[index] if index < len(row) else "" for row in table.rows]
            nonempty = [value for value in values if str(value).strip()]
            numbers = [_to_number(value) for value in nonempty]
            numeric = [value for value in numbers if value is not None]
            item: dict[str, Any] = {
                "column": table.headers[index],
                "nonempty_count": len(nonempty),
                "empty_count": len(values) - len(nonempty),
                "unique_count": len(set(nonempty)),
                "numeric_count": len(numeric),
            }
            if numeric:
                item.update(
                    {
                        "min": min(numeric),
                        "max": max(numeric),
                        "sum": sum(numeric),
                        "mean": statistics.fmean(numeric),
                        "median": statistics.median(numeric),
                    }
                )
            result.append(item)
        return {
            "file_id": file_id,
            "filename": table.filename,
            "sheet": table.sheet,
            "row_count": len(table.rows),
            "columns": result,
        }

    def aggregate_table(
        self,
        *,
        workspace_id: str,
        file_id: str,
        sheet: str | None,
        header_row: int,
        group_by: str,
        value_column: str,
        operation: str,
        limit: int,
    ) -> dict[str, Any]:
        table = self._load_table(
            workspace_id=workspace_id,
            file_id=file_id,
            sheet=sheet,
            header_row=header_row,
        )
        group_index = _column_index(table.headers, group_by)
        value_index = _column_index(table.headers, value_column)
        groups: dict[str, list[float]] = {}
        raw_counts: dict[str, int] = {}

        for row in table.rows:
            group = (row[group_index] if group_index < len(row) else "").strip() or "(blank)"
            raw_counts[group] = raw_counts.get(group, 0) + 1
            value = _to_number(row[value_index] if value_index < len(row) else "")
            if value is not None:
                groups.setdefault(group, []).append(value)

        rows: list[dict[str, Any]] = []
        for group, count in raw_counts.items():
            values = groups.get(group, [])
            if operation == "count":
                aggregate: int | float = count
            elif not values:
                aggregate = 0.0
            elif operation == "sum":
                aggregate = sum(values)
            elif operation == "mean":
                aggregate = statistics.fmean(values)
            elif operation == "min":
                aggregate = min(values)
            elif operation == "max":
                aggregate = max(values)
            else:
                raise ValueError("Unsupported aggregation operation")
            rows.append(
                {
                    "group": group,
                    "value": aggregate,
                    "row_count": count,
                    "numeric_count": len(values),
                }
            )

        rows.sort(key=lambda item: (-float(item["value"]), item["group"]))
        capped = rows[: max(1, min(limit, 100))]
        return {
            "file_id": file_id,
            "filename": table.filename,
            "sheet": table.sheet,
            "group_by": table.headers[group_index],
            "value_column": table.headers[value_index],
            "operation": operation,
            "groups": capped,
            "truncated": len(rows) > len(capped),
        }

    def _load_table(
        self,
        *,
        workspace_id: str,
        file_id: str,
        sheet: str | None,
        header_row: int,
    ) -> TableData:
        if header_row < 1 or header_row > 20:
            raise ValueError("header_row must be between 1 and 20")

        with self.database.session() as session:
            file = session.get(File, file_id)
            if file is None or file.workspace_id != workspace_id:
                raise ValueError("File is not available in the active Workspace")
            if file.status not in {"parsed", "indexed"} or not file.current_version_id:
                raise ValueError("File has not completed parsing")
            if file.extension.lower() not in {".csv", ".xlsx"}:
                raise ValueError("Data analysis currently supports CSV and XLSX only")
            version = session.get(FileVersion, file.current_version_id)
            if version is None:
                raise ValueError("Current file version is missing")
            filename = file.filename
            extension = file.extension.lower()
            sha256 = version.sha256

        payload = self.parser_cache.read(sha256)
        if payload is None:
            raise ValueError("Parsed cache is missing")

        lines: list[str] = []
        chosen_sheet: str | None = None
        units = list(payload.get("units") or [])
        if extension == ".csv":
            for unit in units:
                if unit.get("kind") == "csv_rows":
                    lines.extend(str(unit.get("text") or "").splitlines())
        else:
            sheet_units = [unit for unit in units if unit.get("kind") == "sheet"]
            available = [str((unit.get("locator") or {}).get("sheet") or "") for unit in sheet_units]
            if not sheet_units:
                raise ValueError("Workbook contains no parsed sheet data")
            chosen_sheet = sheet or available[0]
            selected = next(
                (
                    unit
                    for unit in sheet_units
                    if str((unit.get("locator") or {}).get("sheet") or "") == chosen_sheet
                ),
                None,
            )
            if selected is None:
                raise ValueError(f"Sheet not found. Available sheets: {', '.join(available)}")
            lines = str(selected.get("text") or "").splitlines()

        raw_rows = [[cell.strip() for cell in line.split("\t")] for line in lines if line.strip()]
        if len(raw_rows) < header_row:
            raise ValueError("Table does not contain the requested header row")
        headers = _dedupe_headers(raw_rows[header_row - 1])
        rows = raw_rows[header_row:]
        if len(rows) > 20000:
            rows = rows[:20000]
        width = len(headers)
        normalized = [(row + [""] * width)[:width] for row in rows]
        return TableData(
            filename=filename,
            sheet=chosen_sheet,
            headers=headers,
            rows=normalized,
        )


def _dedupe_headers(values: list[str]) -> list[str]:
    headers: list[str] = []
    counts: dict[str, int] = {}
    for index, raw in enumerate(values, start=1):
        base = raw.strip() or f"Column {index}"
        counts[base] = counts.get(base, 0) + 1
        headers.append(base if counts[base] == 1 else f"{base} ({counts[base]})")
    return headers


def _column_index(headers: list[str], requested: str) -> int:
    target = requested.strip().casefold()
    for index, header in enumerate(headers):
        if header.casefold() == target:
            return index
    raise ValueError(f"Column not found: {requested}. Available: {', '.join(headers[:50])}")


def _to_number(value: Any) -> float | None:
    text = str(value).strip().replace(",", "")
    if not text:
        return None
    try:
        number = float(text)
    except ValueError:
        return None
    return number if math.isfinite(number) else None


def _valid_identifier(value: str) -> bool:
    return value.isidentifier() and not value.startswith("_")
