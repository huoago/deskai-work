from __future__ import annotations


def test_workspace_round_trip(client):
    created = client.post(
        "/workspaces",
        json={"name": "利马管网项目", "description": "Phase 0 persistence smoke test"},
    )
    assert created.status_code == 201
    workspace = created.json()
    assert workspace["name"] == "利马管网项目"
    assert workspace["archived"] is False

    listed = client.get("/workspaces")
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()] == [workspace["id"]]
