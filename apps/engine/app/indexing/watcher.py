from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select

from app.database.models import Setting, WorkspaceRoot
from app.database.session import Database
from app.indexing.scanner import ScanStats, scan_root

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class WatcherSnapshot:
    running: bool
    watched_roots: int
    cycles: int
    last_cycle_at: str | None
    last_error: str | None
    last_summary: dict[str, int]

    def as_dict(self) -> dict:
        return {
            "running": self.running,
            "watched_roots": self.watched_roots,
            "cycles": self.cycles,
            "last_cycle_at": self.last_cycle_at,
            "last_error": self.last_error,
            "last_summary": self.last_summary,
        }


class WorkspaceWatcher:
    """Cross-platform polling watcher for authorized workspace roots.

    Phase 2 deliberately watches metadata and file hashes only. It does not parse
    document contents; parsing is a Phase 3 responsibility.
    """

    def __init__(self, database: Database, *, interval_seconds: float = 2.0) -> None:
        self.database = database
        self.interval_seconds = max(interval_seconds, 0.5)
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._cycles = 0
        self._watched_roots = 0
        self._last_cycle_at: str | None = None
        self._last_error: str | None = None
        self._last_summary = ScanStats().as_dict()

    def start(self) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._run,
                name="deskai-workspace-watcher",
                daemon=True,
            )
            self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        self._wake.set()
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=timeout)

    def wake(self) -> None:
        self._wake.set()

    def snapshot(self) -> WatcherSnapshot:
        thread = self._thread
        with self._lock:
            return WatcherSnapshot(
                running=bool(thread and thread.is_alive() and not self._stop.is_set()),
                watched_roots=self._watched_roots,
                cycles=self._cycles,
                last_cycle_at=self._last_cycle_at,
                last_error=self._last_error,
                last_summary=dict(self._last_summary),
            )

    def run_once(self) -> dict[str, int]:
        summary = ScanStats()
        watched_roots = 0
        try:
            with self.database.session() as session:
                auto_index = True
                setting = session.get(Setting, "auto_index")
                if setting is not None:
                    auto_index = bool(setting.value_json)
                roots = session.scalars(
                    select(WorkspaceRoot).where(
                        WorkspaceRoot.read_allowed.is_(True),
                        WorkspaceRoot.watch_enabled.is_(True),
                    )
                ).all()
                watched_roots = len(roots)
                for root in roots:
                    stats = scan_root(
                        session,
                        root,
                        queue_changes=auto_index,
                        metadata_shortcut=True,
                    )
                    summary.merge(stats)
            error = None
        except Exception as exc:  # watcher must not crash the desktop process
            logger.exception("Workspace watcher cycle failed")
            error = f"{type(exc).__name__}: {exc}"

        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            self._cycles += 1
            self._watched_roots = watched_roots
            self._last_cycle_at = now
            self._last_error = error
            self._last_summary = summary.as_dict()
        return summary.as_dict()

    def _run(self) -> None:
        while not self._stop.is_set():
            self.run_once()
            self._wake.wait(self.interval_seconds)
            self._wake.clear()
