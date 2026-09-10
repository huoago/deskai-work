from __future__ import annotations

from pathlib import Path

import pytest

from app.security.paths import PathAccessError, is_within, require_within


def test_path_relationship_uses_filesystem_semantics(tmp_path: Path):
    root = tmp_path / "allowed"
    root.mkdir()
    inside = root / "child.txt"
    inside.write_text("ok", encoding="utf-8")
    sibling = tmp_path / "allowed-not-really"
    sibling.mkdir()
    outside = sibling / "child.txt"
    outside.write_text("no", encoding="utf-8")

    assert is_within(inside, root)
    assert not is_within(outside, root)
    assert require_within(inside, root) == inside.resolve()
    with pytest.raises(PathAccessError):
        require_within(outside, root)
