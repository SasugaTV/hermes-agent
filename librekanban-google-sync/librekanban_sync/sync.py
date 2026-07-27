"""Core sync logic between a Libre Kanban board and a Google Tasks list.

Two one-directional operations, meant to be run manually and separately
(see README.md for the workflow):

  export_to_google   Kanban export file  -> Google Tasks
  import_from_google  Google Tasks -> a new Kanban import file

Neither is a live/automatic sync - each is a single push or pull you run
by hand, in whichever order fits how you actually work.
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Optional

from . import librekanban_file as lkf
from .config import Config
from .google_tasks import GoogleTasksClient, due_to_millis, millis_to_due
from .notes_codec import build_notes, parse_notes
from .state import SyncState

log = logging.getLogger(__name__)


def _fingerprint(payload: dict) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str).encode()
    ).hexdigest()


def _kanban_fields(board_entry: dict, task: dict) -> dict:
    col = lkf.get_column(board_entry, task["columnId"])
    return {
        "title": task["title"],
        "description": task.get("description") or "",
        "due_millis": task.get("dueDate"),
        "column": col["name"] if col else None,
        "tags": sorted(lkf.task_tag_names(board_entry, task)),
        "priority": task.get("priority", "NONE"),
        "story_points": task.get("storyPoints"),
    }


def _google_fields(gt: dict) -> dict:
    return {
        "title": gt.get("title"),
        "notes": gt.get("notes"),
        "due": gt.get("due"),
        "status": gt.get("status"),
    }


def _is_done_column(board_entry: dict, column_id: Optional[str], done_names: list[str]) -> bool:
    col = lkf.get_column(board_entry, column_id)
    if not col:
        return False
    lowered = {n.strip().lower() for n in done_names}
    return col["name"].strip().lower() in lowered


def _google_body_from_kanban(board_entry: dict, task: dict, cfg: Config) -> dict:
    fields = _kanban_fields(board_entry, task)
    notes = build_notes(
        description=fields["description"],
        board=board_entry["board"]["name"],
        column=fields["column"] or "",
        tags=fields["tags"],
        priority=fields["priority"],
        story_points=fields["story_points"],
        kanban_task_id=task["id"],
    )
    body: dict = {
        "title": task["title"],
        "notes": notes,
        "status": (
            "completed"
            if _is_done_column(board_entry, task["columnId"], cfg.done_column_names)
            else "needsAction"
        ),
    }
    if fields["due_millis"] is not None:
        body["due"] = millis_to_due(fields["due_millis"])
    return body


def export_to_google(cfg: Config, client: GoogleTasksClient, state: SyncState) -> dict:
    """Push the current Kanban export up to Google Tasks.

    Returns ``{"created": n, "updated": n, "unchanged": n, "orphaned": [kanban_id, ...]}``.
    ``orphaned`` lists Kanban tasks that used to be synced but have since
    been deleted from the board; what happens to their Google Task is
    controlled by ``cfg.on_kanban_delete``.
    """
    file = lkf.LibreKanbanFile.load(cfg.librekanban_export_path)
    board_entry = file.find_board(name=cfg.board_name, board_id=cfg.board_id)

    if not state.tasklist_id:
        state.tasklist_id = client.ensure_tasklist(cfg.google_tasklist_title)
    tasklist_id = state.tasklist_id

    summary: dict = {"created": 0, "updated": 0, "unchanged": 0, "orphaned": []}
    seen_ids = set()

    for task in board_entry["tasks"]:
        seen_ids.add(task["id"])
        current_hash = _fingerprint(_kanban_fields(board_entry, task))
        entry = state.get(task["id"])
        body = _google_body_from_kanban(board_entry, task, cfg)

        if entry is None:
            created = client.insert_task(tasklist_id, body)
            state.set(
                task["id"],
                google_task_id=created["id"],
                kanban_hash=current_hash,
                google_hash=_fingerprint(_google_fields(created)),
            )
            summary["created"] += 1
            continue

        if entry["kanban_hash"] == current_hash:
            summary["unchanged"] += 1
            continue

        updated = client.patch_task(tasklist_id, entry["google_task_id"], body)
        state.set(
            task["id"],
            google_task_id=entry["google_task_id"],
            kanban_hash=current_hash,
            google_hash=_fingerprint(_google_fields(updated)),
        )
        summary["updated"] += 1

    for kanban_id, entry in list(state.items()):
        if kanban_id in seen_ids:
            continue
        action = cfg.on_kanban_delete
        if action == "delete":
            try:
                client.delete_task(tasklist_id, entry["google_task_id"])
            except Exception as exc:
                log.warning("failed to delete google task %s: %s", entry["google_task_id"], exc)
            state.drop(kanban_id)
        elif action == "complete":
            try:
                client.patch_task(tasklist_id, entry["google_task_id"], {"status": "completed"})
            except Exception as exc:
                log.warning("failed to complete google task %s: %s", entry["google_task_id"], exc)
            state.drop(kanban_id)
        else:
            summary["orphaned"].append(kanban_id)

    return summary


def import_from_google(
    cfg: Config, client: GoogleTasksClient, state: SyncState, output_path: str
) -> dict:
    """Pull Google Tasks changes into a fresh Libre Kanban import file.

    Loads ``cfg.librekanban_export_path`` as the base board, applies
    Google-side changes on top of it, and writes the result to
    ``output_path`` - which you then manually import into Libre Kanban.
    Requires ``export_to_google`` to have run at least once (so the
    target Google Tasks list is known).
    """
    file = lkf.LibreKanbanFile.load(cfg.librekanban_export_path)
    board_entry = file.find_board(name=cfg.board_name, board_id=cfg.board_id)

    if not state.tasklist_id:
        raise RuntimeError(
            "no Google Tasks list is known yet; run export-to-google at least once first"
        )
    tasklist_id = state.tasklist_id

    done_col = None
    for name in cfg.done_column_names:
        done_col = lkf.get_column_by_name(board_entry, name)
        if done_col:
            break
    if done_col is None:
        log.warning(
            "no column on the board matches done_column_names=%s; completed "
            "Google Tasks won't move columns",
            cfg.done_column_names,
        )
    reopened_col = lkf.get_column_by_name(board_entry, cfg.reopened_column_name)
    new_col = lkf.get_column_by_name(board_entry, cfg.new_task_column_name)

    google_tasks = client.list_tasks(tasklist_id)
    summary = {"created": 0, "updated": 0, "unchanged": 0, "removed": 0}
    seen_google_ids = set()

    for gt in google_tasks:
        seen_google_ids.add(gt["id"])
        description, meta = parse_notes(gt.get("notes", ""))
        kanban_id = meta.get("kanban_task_id") or state.by_google_id(gt["id"])
        google_hash = _fingerprint(_google_fields(gt))

        if kanban_id is None:
            if new_col is None:
                log.warning(
                    "no column named %r; skipping new google task %r",
                    cfg.new_task_column_name,
                    gt.get("title"),
                )
                continue
            task = lkf.add_task(
                board_entry,
                column_id=new_col["id"],
                title=gt.get("title") or "(untitled)",
                description=description,
                due_millis=due_to_millis(gt["due"]) if gt.get("due") else None,
            )
            if gt.get("status") == "completed" and done_col:
                lkf.set_task_column(board_entry, task, done_col["id"])
            state.set(
                task["id"],
                google_task_id=gt["id"],
                kanban_hash=_fingerprint(_kanban_fields(board_entry, task)),
                google_hash=google_hash,
            )
            summary["created"] += 1
            continue

        entry = state.get(kanban_id)
        if entry is not None and entry.get("google_hash") == google_hash:
            summary["unchanged"] += 1
            continue

        task = lkf.find_task(board_entry, kanban_id)
        if task is None:
            # Kanban task was removed locally but Google still has it; the
            # next export-to-google run's orphan handling decides its fate.
            continue

        was_done = _is_done_column(board_entry, task["columnId"], cfg.done_column_names)
        task["title"] = gt.get("title") or task["title"]
        task["description"] = description
        task["dueDate"] = due_to_millis(gt["due"]) if gt.get("due") else None

        target_col = None
        if gt.get("status") == "completed" and not was_done:
            target_col = done_col
        elif gt.get("status") != "completed" and was_done:
            target_col = reopened_col
        if target_col:
            lkf.set_task_column(board_entry, task, target_col["id"])

        state.set(
            kanban_id,
            google_task_id=gt["id"],
            kanban_hash=_fingerprint(_kanban_fields(board_entry, task)),
            google_hash=google_hash,
        )
        summary["updated"] += 1

    for kanban_id, entry in list(state.items()):
        if entry["google_task_id"] in seen_google_ids:
            continue
        if cfg.on_google_delete == "delete":
            lkf.remove_task(board_entry, kanban_id)
            state.drop(kanban_id)
            summary["removed"] += 1
        else:
            # "leave": keep the kanban task, just drop the stale mapping so
            # the next export-to-google run recreates a fresh Google Task.
            state.drop(kanban_id)

    file.save(output_path)
    return summary
