"""Encode/decode the Kanban metadata block stashed in a Google Task's notes.

Google Tasks has no concept of columns, tags, priority, or story points -
just a title, plain-text notes, a due date, and completed/needsAction. To
survive a round trip (Kanban -> Google -> Kanban) without losing that
metadata, every synced task's notes field carries the user's description
followed by a small machine-readable block:

    <description>

    -----BEGIN KANBAN-SYNC-----
    board: Primary
    column: Doing
    tags: Health, Work
    priority: NONE
    story_points: 5.0
    kanban_task_id: 76a4a0aa-7be3-4f4f-88fe-a7d42885fb48
    -----END KANBAN-SYNC-----

The block is regenerated from the Kanban side on every export, so editing
it by hand in the Google Tasks app has no lasting effect (and isn't
required for a task to be recognized - see the ``by_google_id`` fallback
lookup in ``state.py``, used when a task's notes lack this block
entirely, e.g. a task created directly in Google Tasks).
"""

from __future__ import annotations

from typing import Optional

BEGIN = "-----BEGIN KANBAN-SYNC-----"
END = "-----END KANBAN-SYNC-----"


def build_notes(
    *,
    description: str,
    board: str,
    column: str,
    tags: list[str],
    priority: str,
    story_points: Optional[float],
    kanban_task_id: str,
) -> str:
    lines = [BEGIN]
    lines.append(f"board: {board}")
    lines.append(f"column: {column}")
    lines.append(f"tags: {', '.join(tags)}")
    lines.append(f"priority: {priority}")
    if story_points is not None:
        lines.append(f"story_points: {story_points}")
    lines.append(f"kanban_task_id: {kanban_task_id}")
    lines.append(END)
    block = "\n".join(lines)
    description = (description or "").strip()
    return f"{description}\n\n{block}" if description else block


def parse_notes(notes: Optional[str]) -> tuple[str, dict]:
    """Split ``notes`` into ``(description, metadata)``.

    ``metadata`` is ``{}`` (with ``tags: []``) when no sync block is
    present - e.g. a task created directly in Google Tasks, or one
    whose notes were hand-edited to remove the block.
    """
    notes = notes or ""
    if BEGIN not in notes or END not in notes:
        return notes.strip(), {"tags": []}
    before, rest = notes.split(BEGIN, 1)
    block, _, _after = rest.partition(END)
    meta: dict = {}
    for line in block.strip().splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        meta[key.strip()] = value.strip()
    if "tags" in meta:
        meta["tags"] = [t.strip() for t in meta["tags"].split(",") if t.strip()]
    else:
        meta["tags"] = []
    return before.strip(), meta
