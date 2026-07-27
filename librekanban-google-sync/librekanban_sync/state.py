"""Local persistence for the Kanban-task <-> Google-Task mapping.

Kept as a plain JSON file next to the script (path set by
``config.state_path``). This is the only place that remembers which
Google Task corresponds to which Kanban task, and the hash of each side
last seen, so a sync run can tell "changed since last time" from
"unchanged" without re-pushing/re-pulling everything.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional


class SyncState:
    def __init__(self, raw: dict):
        self.raw = raw

    @classmethod
    def load(cls, path: str) -> "SyncState":
        p = Path(path)
        if p.exists():
            return cls(json.loads(p.read_text()))
        return cls({"tasklist_id": None, "tasks": {}})

    def save(self, path: str) -> None:
        Path(path).write_text(json.dumps(self.raw, indent=2))

    @property
    def tasklist_id(self) -> Optional[str]:
        return self.raw.get("tasklist_id")

    @tasklist_id.setter
    def tasklist_id(self, value: str) -> None:
        self.raw["tasklist_id"] = value

    def get(self, kanban_task_id: str) -> Optional[dict]:
        return self.raw["tasks"].get(kanban_task_id)

    def set(
        self, kanban_task_id: str, *, google_task_id: str, kanban_hash: str, google_hash: str
    ) -> None:
        self.raw["tasks"][kanban_task_id] = {
            "google_task_id": google_task_id,
            "kanban_hash": kanban_hash,
            "google_hash": google_hash,
        }

    def drop(self, kanban_task_id: str) -> None:
        self.raw["tasks"].pop(kanban_task_id, None)

    def by_google_id(self, google_task_id: str) -> Optional[str]:
        for kid, entry in self.raw["tasks"].items():
            if entry["google_task_id"] == google_task_id:
                return kid
        return None

    def items(self) -> list[tuple[str, dict]]:
        return list(self.raw["tasks"].items())
