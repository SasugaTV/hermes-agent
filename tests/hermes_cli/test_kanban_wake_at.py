"""Tests for the ``scheduled`` lane's wake timer (``tasks.wake_at``).

Before ``wake_at`` the lane was a one-way door: ``schedule_task`` parked a card
with no record of when it was due, and nothing in the dispatcher ever looked at
``scheduled`` again, so only a human (or a cron calling ``unblock``) could get a
card out. These cover the timer itself, the untimed path that must keep the old
behaviour, and the legacy-DB migration.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import pytest

from hermes_cli import kanban_db as kb
from hermes_cli import kanban_db_connect as kbc
from hermes_cli import kanban_db_dispatch as kbd


@pytest.fixture
def kanban_home(tmp_path, monkeypatch):
    """Isolated HERMES_HOME with an empty kanban DB."""
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    kb.init_db()
    return home


def _wake_at(conn: sqlite3.Connection, task_id: str):
    return conn.execute("SELECT wake_at FROM tasks WHERE id = ?", (task_id,)).fetchone()[0]


# ---------------------------------------------------------------------------
# The timer
# ---------------------------------------------------------------------------


def test_scheduled_task_wakes_once_its_time_arrives(kanban_home):
    now = int(time.time())
    with kbc.connect() as conn:
        t = kb.create_task(conn, title="recheck the deploy", assignee="ops")
        assert kb.schedule_task(conn, t, reason="check back in an hour", wake_at=now + 3600) is True
        assert kb.get_task(conn, t).status == "scheduled"
        assert _wake_at(conn, t) == now + 3600

        # Not yet due: the sweep must leave it parked.
        assert kb.wake_due_scheduled_tasks(conn, now=now) == []
        assert kb.get_task(conn, t).status == "scheduled"

        # One second past due is enough.
        assert kb.wake_due_scheduled_tasks(conn, now=now + 3601) == [t]
        assert kb.get_task(conn, t).status == "ready"


def test_wake_clears_the_timer_so_it_cannot_refire(kanban_home):
    now = int(time.time())
    with kbc.connect() as conn:
        t = kb.create_task(conn, title="one shot", assignee="ops")
        kb.schedule_task(conn, t, wake_at=now + 10)
        kb.wake_due_scheduled_tasks(conn, now=now + 11)
        assert _wake_at(conn, t) is None
        # Re-parking with no timer must not inherit the consumed one.
        kb.schedule_task(conn, t, reason="indefinite")
        assert _wake_at(conn, t) is None
        assert kb.wake_due_scheduled_tasks(conn, now=now + 10**7) == []


def test_manual_unblock_consumes_a_pending_timer(kanban_home):
    """A human who unblocks early must not leave a timer that fires later."""
    now = int(time.time())
    with kbc.connect() as conn:
        t = kb.create_task(conn, title="impatient operator", assignee="ops")
        kb.schedule_task(conn, t, wake_at=now + 3600)
        assert kb.unblock_task(conn, t) is True
        assert _wake_at(conn, t) is None


def test_wake_emits_an_event_naming_the_timer(kanban_home):
    now = int(time.time())
    with kbc.connect() as conn:
        t = kb.create_task(conn, title="audit trail", assignee="ops")
        kb.schedule_task(conn, t, reason="tomorrow", wake_at=now + 5)
        scheduled = [e for e in kb.list_events(conn, t) if e.kind == "scheduled"]
        assert scheduled[-1].payload == {"reason": "tomorrow", "wake_at": now + 5}

        kb.wake_due_scheduled_tasks(conn, now=now + 6)
        assert any(e.kind == "woke" for e in kb.list_events(conn, t))


def test_due_tasks_wake_in_time_order_under_the_limit(kanban_home):
    now = int(time.time())
    with kbc.connect() as conn:
        late = kb.create_task(conn, title="late", assignee="ops")
        early = kb.create_task(conn, title="early", assignee="ops")
        kb.schedule_task(conn, late, wake_at=now - 10)
        kb.schedule_task(conn, early, wake_at=now - 100)
        assert kb.wake_due_scheduled_tasks(conn, now=now, limit=1) == [early]
        assert kb.get_task(conn, late).status == "scheduled"


# ---------------------------------------------------------------------------
# The untimed path keeps its old behaviour
# ---------------------------------------------------------------------------


def test_untimed_park_is_never_woken_by_the_sweep(kanban_home):
    with kbc.connect() as conn:
        t = kb.create_task(conn, title="waiting on a human", assignee="ops")
        assert kb.schedule_task(conn, t, reason="until someone says so") is True
        assert _wake_at(conn, t) is None
        assert kb.wake_due_scheduled_tasks(conn, now=int(time.time()) + 10**7) == []
        assert kb.get_task(conn, t).status == "scheduled"


def test_untimed_scheduled_event_payload_is_unchanged(kanban_home):
    """Existing consumers parse this payload; an untimed park must not grow a key."""
    with kbc.connect() as conn:
        t = kb.create_task(conn, title="shape check", assignee="ops")
        kb.schedule_task(conn, t, reason="run next week")
        events = [e for e in kb.list_events(conn, t) if e.kind == "scheduled"]
        assert events[-1].payload == {"reason": "run next week"}


# ---------------------------------------------------------------------------
# Dispatcher integration
# ---------------------------------------------------------------------------


def test_dispatch_tick_wakes_due_tasks_and_reports_them(kanban_home):
    now = int(time.time())
    with kbc.connect() as conn:
        t = kb.create_task(conn, title="woken by the tick", assignee="ops")
        kb.schedule_task(conn, t, wake_at=now - 1)

        result = kbd.dispatch_once(conn, dry_run=True, spawn_fn=lambda *a, **k: None)
        assert t in result.woke
        assert kb.get_task(conn, t).status in {"ready", "todo"}


def test_dispatch_tick_leaves_untimed_parks_alone(kanban_home):
    with kbc.connect() as conn:
        t = kb.create_task(conn, title="still parked", assignee="ops")
        kb.schedule_task(conn, t, reason="no timer")

        result = kbd.dispatch_once(conn, dry_run=True, spawn_fn=lambda *a, **k: None)
        assert result.woke == []
        assert kb.get_task(conn, t).status == "scheduled"


# ---------------------------------------------------------------------------
# Migration
# ---------------------------------------------------------------------------


def test_legacy_board_without_wake_at_is_migrated(kanban_home, tmp_path):
    """A board created before the column must gain it on the next open."""
    db_path = tmp_path / "legacy" / "kanban.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with kbc.connect(db_path) as conn:
        t = kb.create_task(conn, title="pre-existing", assignee="ops")
        # The partial index references the column, so it goes first — exactly
        # the order a pre-``wake_at`` board is already in.
        conn.execute("DROP INDEX IF EXISTS idx_tasks_wake_at")
        conn.execute("ALTER TABLE tasks DROP COLUMN wake_at")
        conn.commit()
        assert "wake_at" not in {r["name"] for r in conn.execute("PRAGMA table_info(tasks)")}

    # ``_INITIALIZED_PATHS`` caches "already migrated" per process, so clearing
    # it is what a restart of the gateway/dispatcher looks like to this DB —
    # the first ``connect`` in the new process runs the ALTER TABLE pass.
    kbc._INITIALIZED_PATHS.discard(str(db_path.resolve()))

    with kbc.connect(db_path) as conn:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(tasks)")}
        assert "wake_at" in cols
        assert _wake_at(conn, t) is None
        # And the migrated board can use the timer.
        now = int(time.time())
        assert kb.schedule_task(conn, t, wake_at=now - 1) is True
        assert kb.wake_due_scheduled_tasks(conn, now=now) == [t]
