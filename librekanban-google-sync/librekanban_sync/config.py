from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

VALID_DELETE_ACTIONS = ("leave", "delete", "complete")


@dataclass
class Config:
    librekanban_export_path: str
    google_credentials_path: str = "credentials.json"
    google_token_path: str = "token.json"
    state_path: str = "sync_state.json"
    board_name: Optional[str] = None
    board_id: Optional[str] = None
    google_tasklist_title: str = "Kanban"
    done_column_names: list[str] = field(
        default_factory=lambda: ["Finished", "Done", "Completed"]
    )
    reopened_column_name: str = "To Do"
    new_task_column_name: str = "To Do"
    on_kanban_delete: str = "leave"  # leave | delete | complete
    on_google_delete: str = "leave"  # leave | delete

    def __post_init__(self) -> None:
        if self.on_kanban_delete not in VALID_DELETE_ACTIONS:
            raise ValueError(
                f"on_kanban_delete must be one of {VALID_DELETE_ACTIONS}, "
                f"got {self.on_kanban_delete!r}"
            )
        if self.on_google_delete not in ("leave", "delete"):
            raise ValueError(
                f"on_google_delete must be 'leave' or 'delete', got {self.on_google_delete!r}"
            )

    @classmethod
    def load(cls, path: str) -> "Config":
        data = json.loads(Path(path).read_text())
        known = {f.name for f in cls.__dataclass_fields__.values()}
        unknown = set(data) - known
        if unknown:
            raise ValueError(f"unknown config key(s) in {path}: {sorted(unknown)}")
        return cls(**data)
