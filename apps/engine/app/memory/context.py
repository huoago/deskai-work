from __future__ import annotations

import json
from typing import Any


def build_memory_context(memories: list[dict[str, Any]], max_chars: int = 6000) -> str:
    if not memories:
        return ""

    lines: list[str] = []
    remaining = max_chars
    for item in memories:
        value = json.dumps(item.get("value"), ensure_ascii=False, sort_keys=True)
        line = (
            f"- ({item.get('type', 'memory')}) "
            f"{item.get('subject', '')} | {item.get('predicate', '')} = {value}"
        )
        if len(line) > remaining:
            break
        lines.append(line)
        remaining -= len(line)

    if not lines:
        return ""
    return (
        "\n\n<memory_context>\n"
        "These are durable memories previously provided or confirmed by the user. "
        "Use them only when relevant. The current user message always overrides a conflicting memory. "
        "Do not cite these as local-file sources.\n"
        + "\n".join(lines)
        + "\n</memory_context>"
    )
