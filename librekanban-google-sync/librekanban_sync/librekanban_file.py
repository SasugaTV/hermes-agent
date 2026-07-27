"""Read/write Libre Kanban export and backup files.

Libre Kanban (com.buenhijogames.mikanban) exports two shapes of JSON:

  * ``.librekanbanbackup`` - a full backup: ``{"boards": [<board-entry>, ...]}``
  * ``.mikanban``          - a single board: just a ``<board-entry>`` at the
    top level

A ``<board-entry>`` looks like::

    {
        "exportTimestamp": 1785119398342,
        "board": {"id": ..., "name": ..., "description": ..., "color": ...,
                   "position": ..., "createdAt": ..., "updatedAt": ...,
                   "sourceId": ...},
        "columns": [{"id": ..., "boardId": ..., "name": ..., "color": ...,
                      "position": ...}, ...],
        "tasks": [{"id": ..., "boardId": ..., "columnId": ..., "title": ...,
                    "description": ..., "position": ..., "color": ...,
                    "priority": "NONE", "dueDate": <epoch ms or null>,
                    "createdAt": <epoch ms>, "history": [...],
                    "storyPoints": <float or absent>}, ...],
        "tags": [{"id": ..., "boardId": ..., "name": ..., "color": ...}, ...]
    }

Every field is stored as a plain dict rather than reconstructed into a
narrower dataclass, and never dropped on save. The schema above was
reverse-engineered from a single real export, so fields this module
doesn't know about (e.g. swimlanes, a task-to-tag link) may exist on
other boards - keeping the raw dict intact means round-tripping through
this tool can't lose them even though this code doesn't understand them.
"""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Optional


class LibreKanbanFile:
    def __init__(self, raw: dict, is_backup: bool):
        self.raw = raw
        self.is_backup = is_backup

    @classmethod
    def load(cls, path: str) -> "LibreKanbanFile":
        raw = json.loads(Path(path).read_text())
        is_backup = "boards" in raw
        return cls(raw, is_backup)

    def save(self, path: str) -> None:
        Path(path).write_text(json.dumps(self.raw, indent=4))

    def board_entries(self) -> list[dict]:
        if self.is_backup:
            return self.raw["boards"]
        return [self.raw]

    def find_board(
        self, name: Optional[str] = None, board_id: Optional[str] = None
    ) -> dict:
        entries = self.board_entries()
        if board_id:
            for e in entries:
                if e["board"]["id"] == board_id:
                    return e
            raise ValueError(f"no board with id {board_id!r}")
        if name:
            matches = [e for e in entries if e["board"]["name"] == name]
            if not matches:
                raise ValueError(f"no board named {name!r}")
            if len(matches) > 1:
                raise ValueError(
                    f"multiple boards named {name!r}; set board_id in config instead"
                )
            return matches[0]
        if len(entries) == 1:
            return entries[0]
        raise ValueError(
            "file contains multiple boards; set board_name or board_id in config.json"
        )


def get_column(board_entry: dict, column_id: Optional[str]) -> Optional[dict]:
    if column_id is None:
        return None
    for c in board_entry["columns"]:
        if c["id"] == column_id:
            return c
    return None


def get_column_by_name(board_entry: dict, name: str) -> Optional[dict]:
    for c in board_entry["columns"]:
        if c["name"] == name:
            return c
    return None


def get_tag(board_entry: dict, tag_id: str) -> Optional[dict]:
    for t in board_entry.get("tags", []):
        if t["id"] == tag_id:
            return t
    return None


def get_tag_by_name(board_entry: dict, name: str) -> Optional[dict]:
    for t in board_entry.get("tags", []):
        if t["name"] == name:
            return t
    return None


def task_tag_names(board_entry: dict, task: dict) -> list[str]:
    """Best-effort tag lookup.

    The export used to build this tool had no tagged tasks, so the
    field name Libre Kanban uses to link a task to its tags is
    unconfirmed. This checks a couple of plausible shapes
    (``tagIds``: list of tag ids, ``tags``: list of ids or names) and
    falls back to no tags rather than guessing wrong. If tags aren't
    round-tripping for you, check what key your export actually uses
    and adjust this function.
    """
    raw_ids = task.get("tagIds")
    if raw_ids:
        tags = [get_tag(board_entry, tid) for tid in raw_ids]
        return [t["name"] for t in tags if t]
    raw_tags = task.get("tags")
    if raw_tags:
        out = []
        for t in raw_tags:
            if isinstance(t, str):
                tag = get_tag(board_entry, t) or get_tag_by_name(board_entry, t)
                out.append(tag["name"] if tag else t)
            elif isinstance(t, dict) and "name" in t:
                out.append(t["name"])
        return out
    return []


def new_task_id() -> str:
    return str(uuid.uuid4())


def now_millis() -> int:
    return int(time.time() * 1000)


def add_task(
    board_entry: dict,
    *,
    column_id: str,
    title: str,
    description: str = "",
    due_millis: Optional[int] = None,
    task_id: Optional[str] = None,
) -> dict:
    tid = task_id or new_task_id()
    col = get_column(board_entry, column_id)
    max_pos = max(
        (t["position"] for t in board_entry["tasks"] if t["columnId"] == column_id),
        default=-1,
    )
    task = {
        "id": tid,
        "boardId": board_entry["board"]["id"],
        "columnId": column_id,
        "title": title,
        "description": description,
        "position": max_pos + 1,
        "color": col["color"] if col else 0,
        "priority": "NONE",
        "dueDate": due_millis,
        "createdAt": now_millis(),
        "history": [
            {
                "id": str(uuid.uuid4()),
                "taskId": tid,
                "changeType": "created",
                "fieldName": None,
                "oldValue": None,
                "newValue": None,
                "createdAt": now_millis(),
            }
        ],
    }
    board_entry["tasks"].append(task)
    return task


def find_task(board_entry: dict, task_id: str) -> Optional[dict]:
    for t in board_entry["tasks"]:
        if t["id"] == task_id:
            return t
    return None


def remove_task(board_entry: dict, task_id: str) -> bool:
    tasks = board_entry["tasks"]
    for i, t in enumerate(tasks):
        if t["id"] == task_id:
            del tasks[i]
            return True
    return False


def set_task_column(board_entry: dict, task: dict, column_id: str) -> None:
    if task["columnId"] == column_id:
        return
    old_col = get_column(board_entry, task["columnId"])
    new_col = get_column(board_entry, column_id)
    task.setdefault("history", []).append(
        {
            "id": str(uuid.uuid4()),
            "taskId": task["id"],
            "changeType": "moved",
            "fieldName": "column",
            "oldValue": old_col["name"] if old_col else None,
            "newValue": new_col["name"] if new_col else None,
            "createdAt": now_millis(),
        }
    )
    task["columnId"] = column_id
