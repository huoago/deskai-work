from __future__ import annotations

from pathlib import Path


class PathAccessError(ValueError):
    pass


def normalize_root(path: str | Path) -> Path:
    root = Path(path).expanduser().resolve(strict=True)
    if not root.is_dir():
        raise PathAccessError(f"Not a directory: {root}")
    return root


def is_within(candidate: str | Path, allowed_root: str | Path) -> bool:
    root = Path(allowed_root).expanduser().resolve(strict=True)
    target = Path(candidate).expanduser().resolve(strict=True)
    try:
        target.relative_to(root)
        return True
    except ValueError:
        return False


def require_within(candidate: str | Path, allowed_root: str | Path) -> Path:
    root = Path(allowed_root).expanduser().resolve(strict=True)
    target = Path(candidate).expanduser().resolve(strict=True)
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise PathAccessError(f"Path is outside allowed root: {target}") from exc
    return target
