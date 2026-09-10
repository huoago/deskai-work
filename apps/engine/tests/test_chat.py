from __future__ import annotations

import json


def test_chat_stream_creates_persistent_conversation(client):
    workspace = client.post("/workspaces", json={"name": "Chat Test"}).json()

    with client.stream(
        "POST",
        "/chat/stream",
        json={"workspace_id": workspace["id"], "message": "检查 324 的资料"},
    ) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        body = "".join(response.iter_text())

    assert "event: meta" in body
    assert "event: delta" in body
    assert "event: done" in body

    meta_block = next(block for block in body.split("\n\n") if block.startswith("event: meta"))
    data_line = next(line for line in meta_block.splitlines() if line.startswith("data: "))
    meta = json.loads(data_line.removeprefix("data: "))

    conversations = client.get(
        "/conversations",
        params={"workspace_id": workspace["id"]},
    ).json()
    assert len(conversations) == 1
    assert conversations[0]["id"] == meta["conversation_id"]

    messages = client.get(
        f"/conversations/{meta['conversation_id']}/messages"
    ).json()
    assert [item["role"] for item in messages] == ["user", "assistant"]
    assert messages[0]["content"] == "检查 324 的资料"
    assert "Phase 1" in messages[1]["content"]


def test_chat_rejects_workspace_conversation_mismatch(client):
    first = client.post("/workspaces", json={"name": "One"}).json()
    second = client.post("/workspaces", json={"name": "Two"}).json()
    conversation = client.post(
        "/conversations",
        json={"workspace_id": first["id"], "title": "One conversation"},
    ).json()

    response = client.post(
        "/chat/stream",
        json={
            "workspace_id": second["id"],
            "conversation_id": conversation["id"],
            "message": "should fail",
        },
    )
    assert response.status_code == 409
