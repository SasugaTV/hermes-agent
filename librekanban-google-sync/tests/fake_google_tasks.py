"""An in-memory stand-in for GoogleTasksClient, used by the sync tests.

Implements the same methods with the same signatures/return shapes as
the real client so ``sync.py``'s core logic can be exercised without
network access or real credentials.
"""

from __future__ import annotations

import itertools
from typing import Optional


class FakeGoogleTasksClient:
    def __init__(self) -> None:
        self._id_counter = itertools.count(1)
        self.tasklists: dict[str, str] = {}  # title -> id
        self.tasks: dict[str, dict[str, dict]] = {}  # tasklist_id -> {task_id: task}

    def _next_id(self, prefix: str) -> str:
        return f"{prefix}{next(self._id_counter)}"

    def ensure_tasklist(self, title: str) -> str:
        if title in self.tasklists:
            return self.tasklists[title]
        tid = self._next_id("tasklist")
        self.tasklists[title] = tid
        self.tasks[tid] = {}
        return tid

    def list_tasks(self, tasklist_id: str) -> list[dict]:
        return list(self.tasks[tasklist_id].values())

    def get_task(self, tasklist_id: str, task_id: str) -> Optional[dict]:
        return self.tasks.get(tasklist_id, {}).get(task_id)

    def insert_task(self, tasklist_id: str, body: dict) -> dict:
        tid = self._next_id("task")
        task = {"id": tid, "status": "needsAction", **body}
        self.tasks[tasklist_id][tid] = task
        return dict(task)

    def patch_task(self, tasklist_id: str, task_id: str, body: dict) -> dict:
        task = self.tasks[tasklist_id][task_id]
        task.update(body)
        return dict(task)

    def delete_task(self, tasklist_id: str, task_id: str) -> None:
        self.tasks[tasklist_id].pop(task_id, None)
