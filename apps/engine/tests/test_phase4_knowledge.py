from __future__ import annotations

from pathlib import Path

import fitz
from openpyxl import Workbook
from sqlalchemy import select

from app.database.models import Chunk, File, FileVersion


def _workspace(client) -> str:
    response = client.post("/workspaces", json={"name": "Knowledge Test"})
    assert response.status_code == 201
    return response.json()["id"]


def _ingest(client, workspace_id: str, source: Path) -> None:
    added = client.post(
        f"/workspaces/{workspace_id}/roots",
        json={"path": str(source), "scan_now": True, "watch_enabled": False},
    )
    assert added.status_code == 201
    queued = added.json()["scan"]["queued"]
    assert queued > 0

    parsed = client.post("/parser/process", params={"limit": 100})
    assert parsed.status_code == 200
    assert parsed.json()["processed"] == queued

    indexed = client.post("/knowledge/process", params={"limit": 100})
    assert indexed.status_code == 200
    assert indexed.json()["processed"] == queued


def _search(client, workspace_id: str, query: str, limit: int = 8) -> list[dict]:
    response = client.post(
        "/search",
        json={"workspace_id": workspace_id, "query": query, "limit": limit},
    )
    assert response.status_code == 200
    return response.json()["results"]


def test_phase4_indexes_chinese_and_engineering_codes(client, tmp_path: Path):
    workspace_id = _workspace(client)
    source = tmp_path / "text"
    source.mkdir()
    (source / "324-meter-plan.md").write_text(
        "# 324地块水表计划\n"
        "水表统计总数为4447个，其中使用年限大于5年的有1802个，"
        "无水表连接154个，异常水表4个。\n"
        "按更换规则共需处理1960个水表。",
        encoding="utf-8",
    )
    (source / "rrp04.txt").write_text(
        "RRP-04 reservoir hydrostatic pressure test record.",
        encoding="utf-8",
    )

    _ingest(client, workspace_id, source)

    meter_hits = _search(client, workspace_id, "水表统计总数")
    assert meter_hits
    assert meter_hits[0]["filename"] == "324-meter-plan.md"
    assert "4447" in meter_hits[0]["content"]
    assert meter_hits[0]["citation_label"].startswith("324-meter-plan.md")

    code_hits = _search(client, workspace_id, "RRP-04")
    assert code_hits
    assert code_hits[0]["filename"] == "rrp04.txt"
    assert "RRP-04" in code_hits[0]["content"]


def test_phase4_pdf_search_preserves_page_citation(client, tmp_path: Path):
    workspace_id = _workspace(client)
    source = tmp_path / "pdf"
    source.mkdir()
    path = source / "hot-tapping.pdf"

    document = fitz.open()
    page1 = document.new_page()
    page1.insert_text((72, 72), "General project introduction")
    page2 = document.new_page()
    page2.insert_text((72, 72), "DN1500 hot tapping operation under pressure")
    document.save(path)
    document.close()

    _ingest(client, workspace_id, source)
    hits = _search(client, workspace_id, "DN1500")
    assert hits
    assert hits[0]["filename"] == "hot-tapping.pdf"
    assert hits[0]["locator"]["page_start"] == 2
    assert hits[0]["locator"]["page_end"] == 2
    assert hits[0]["citation_label"] == "hot-tapping.pdf · p.2"


def test_phase4_xlsx_search_preserves_sheet_and_rows(client, tmp_path: Path):
    workspace_id = _workspace(client)
    source = tmp_path / "xlsx"
    source.mkdir()
    path = source / "meter-summary.xlsx"

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "324"
    sheet.append(["Category", "Count"])
    sheet.append(["Total meters", 4447])
    sheet.append([">5 years", 1802])
    sheet.append(["No meter", 154])
    workbook.save(path)
    workbook.close()

    _ingest(client, workspace_id, source)
    hits = _search(client, workspace_id, "4447")
    assert hits
    hit = hits[0]
    assert hit["filename"] == "meter-summary.xlsx"
    assert hit["locator"]["sheet_name"] == "324"
    assert hit["locator"]["cell_range"] == "rows 1-4"
    assert "324" in hit["citation_label"]
    assert "rows 1-4" in hit["citation_label"]


def test_phase4_reindex_removes_old_version_from_retrieval(client, tmp_path: Path):
    workspace_id = _workspace(client)
    source = tmp_path / "version"
    source.mkdir()
    path = source / "decision.txt"
    path.write_text(
        "Legacy decision marker ALPHA-ONLY: install 100 meters.",
        encoding="utf-8",
    )

    _ingest(client, workspace_id, source)
    assert _search(client, workspace_id, "ALPHA-ONLY")

    path.write_text(
        "Current decision marker BETA-CURRENT: install 1960 meters.",
        encoding="utf-8",
    )
    scan = client.post(f"/workspaces/{workspace_id}/scan").json()
    assert scan["summary"]["queued"] == 1
    assert client.post("/parser/process", params={"limit": 10}).json()["processed"] == 1
    assert client.post("/knowledge/process", params={"limit": 10}).json()["processed"] == 1

    assert _search(client, workspace_id, "ALPHA-ONLY") == []
    current = _search(client, workspace_id, "BETA-CURRENT")
    assert current
    assert "1960" in current[0]["content"]

    with client.app.state.database.session() as session:
        file = session.scalar(select(File).where(File.filename == "decision.txt"))
        assert file is not None
        versions = session.scalars(select(FileVersion).where(FileVersion.file_id == file.id)).all()
        chunks = session.scalars(select(Chunk).where(Chunk.file_id == file.id)).all()
        assert len(versions) == 2
        assert sum(1 for item in versions if item.active) == 1
        assert sum(1 for item in chunks if item.active) >= 1
        assert all(
            chunk.file_version_id == file.current_version_id
            for chunk in chunks
            if chunk.active
        )


def test_phase4_workspace_boundary_blocks_cross_project_hits(client, tmp_path: Path):
    workspace_a = _workspace(client)
    workspace_b_response = client.post("/workspaces", json={"name": "Other Workspace"})
    workspace_b = workspace_b_response.json()["id"]

    source_a = tmp_path / "a"
    source_b = tmp_path / "b"
    source_a.mkdir()
    source_b.mkdir()
    (source_a / "secret.txt").write_text("PROJECT-A-ONLY marker 778899", encoding="utf-8")
    (source_b / "public.txt").write_text("PROJECT-B content", encoding="utf-8")

    _ingest(client, workspace_a, source_a)
    _ingest(client, workspace_b, source_b)

    assert _search(client, workspace_a, "778899")
    assert _search(client, workspace_b, "778899") == []


def test_phase4_status_reports_indexed_files_and_chunks(client, tmp_path: Path):
    workspace_id = _workspace(client)
    source = tmp_path / "status"
    source.mkdir()
    (source / "one.md").write_text("# One\nA searchable knowledge item.", encoding="utf-8")

    _ingest(client, workspace_id, source)
    response = client.get("/knowledge/status", params={"workspace_id": workspace_id})
    assert response.status_code == 200
    status = response.json()
    assert status["indexed_files"] == 1
    assert status["parsed_files"] == 0
    assert status["index_failed_files"] == 0
    assert status["active_chunks"] >= 1
    assert status["embedding_provider"] == "local-hash-384-v1"
