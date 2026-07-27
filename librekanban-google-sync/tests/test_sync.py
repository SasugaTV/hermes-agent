import json
import shutil
from pathlib import Path

import pytest

from librekanban_sync import librekanban_file as lkf
from librekanban_sync.config import Config
from librekanban_sync.state import SyncState
from librekanban_sync.sync import export_to_google, import_from_google
from tests.fake_google_tasks import FakeGoogleTasksClient

FIXTURE = Path(__file__).parent / "fixtures" / "sample_backup.json"


@pytest.fixture
def board_file(tmp_path: Path) -> Path:
    dest = tmp_path / "board.json"
    shutil.copy(FIXTURE, dest)
    return dest


@pytest.fixture
def cfg(board_file: Path, tmp_path: Path) -> Config:
    return Config(
        librekanban_export_path=str(board_file),
        board_name="Primary",
        state_path=str(tmp_path / "state.json"),
        google_tasklist_title="Kanban: Primary",
    )


@pytest.fixture
def client() -> FakeGoogleTasksClient:
    return FakeGoogleTasksClient()


@pytest.fixture
def state(cfg: Config) -> SyncState:
    return SyncState.load(cfg.state_path)


def test_export_creates_one_google_task_per_card(cfg, client, state):
    summary = export_to_google(cfg, client, state)
    assert summary == {"created": 1, "updated": 0, "unchanged": 0, "orphaned": []}
    tasklist_id = state.tasklist_id
    tasks = client.list_tasks(tasklist_id)
    assert len(tasks) == 1
    assert tasks[0]["title"] == "Finish health check"
    # The sample task sits in "Doing", not a done column -> needsAction.
    assert tasks[0]["status"] == "needsAction"
    assert "kanban_task_id" in tasks[0]["notes"]


def test_export_second_run_is_unchanged(cfg, client, state):
    export_to_google(cfg, client, state)
    summary = export_to_google(cfg, client, state)
    assert summary == {"created": 0, "updated": 0, "unchanged": 1, "orphaned": []}


def test_export_updates_when_kanban_card_changes(cfg, client, state, board_file):
    export_to_google(cfg, client, state)

    file = lkf.LibreKanbanFile.load(str(board_file))
    entry = file.find_board(name="Primary")
    entry["tasks"][0]["title"] = "Finish health check (updated)"
    file.save(str(board_file))

    summary = export_to_google(cfg, client, state)
    assert summary == {"created": 0, "updated": 1, "orphaned": [], "unchanged": 0}
    tasks = client.list_tasks(state.tasklist_id)
    assert tasks[0]["title"] == "Finish health check (updated)"


def test_export_moves_task_to_done_column_marks_completed(cfg, client, state, board_file):
    file = lkf.LibreKanbanFile.load(str(board_file))
    entry = file.find_board(name="Primary")
    finished = lkf.get_column_by_name(entry, "Finished")
    lkf.set_task_column(entry, entry["tasks"][0], finished["id"])
    file.save(str(board_file))

    export_to_google(cfg, client, state)
    tasks = client.list_tasks(state.tasklist_id)
    assert tasks[0]["status"] == "completed"


def test_export_orphaned_card_leaves_google_task_by_default(cfg, client, state, board_file):
    export_to_google(cfg, client, state)

    file = lkf.LibreKanbanFile.load(str(board_file))
    entry = file.find_board(name="Primary")
    lkf.remove_task(entry, entry["tasks"][0]["id"])
    file.save(str(board_file))

    summary = export_to_google(cfg, client, state)
    assert summary["orphaned"] != []
    assert len(client.list_tasks(state.tasklist_id)) == 1  # still there


def test_export_orphaned_card_deletes_when_configured(board_file, client, tmp_path):
    cfg = Config(
        librekanban_export_path=str(board_file),
        board_name="Primary",
        state_path=str(tmp_path / "state.json"),
        on_kanban_delete="delete",
    )
    state = SyncState.load(cfg.state_path)
    export_to_google(cfg, client, state)

    file = lkf.LibreKanbanFile.load(str(board_file))
    entry = file.find_board(name="Primary")
    lkf.remove_task(entry, entry["tasks"][0]["id"])
    file.save(str(board_file))

    summary = export_to_google(cfg, client, state)
    assert summary["orphaned"] == []
    assert len(client.list_tasks(state.tasklist_id)) == 0


def test_import_completed_google_task_moves_card_to_done_column(cfg, client, state, board_file):
    export_to_google(cfg, client, state)
    tasklist_id = state.tasklist_id
    gtask = client.list_tasks(tasklist_id)[0]
    client.patch_task(tasklist_id, gtask["id"], {"status": "completed"})

    output = board_file.parent / "synced.json"
    summary = import_from_google(cfg, client, state, str(output))
    assert summary["updated"] == 1

    result = lkf.LibreKanbanFile.load(str(output))
    entry = result.find_board(name="Primary")
    task = entry["tasks"][0]
    col = lkf.get_column(entry, task["columnId"])
    assert col["name"] == "Finished"


def test_import_new_google_task_creates_new_card(cfg, client, state, board_file):
    export_to_google(cfg, client, state)
    tasklist_id = state.tasklist_id
    client.insert_task(tasklist_id, {"title": "Buy milk", "notes": "from the grocery run"})

    output = board_file.parent / "synced.json"
    summary = import_from_google(cfg, client, state, str(output))
    assert summary["created"] == 1

    result = lkf.LibreKanbanFile.load(str(output))
    entry = result.find_board(name="Primary")
    titles = [t["title"] for t in entry["tasks"]]
    assert "Buy milk" in titles
    new_task = next(t for t in entry["tasks"] if t["title"] == "Buy milk")
    col = lkf.get_column(entry, new_task["columnId"])
    assert col["name"] == "To Do"


def test_import_second_run_with_no_changes_is_unchanged(cfg, client, state, board_file):
    export_to_google(cfg, client, state)
    output = board_file.parent / "synced.json"
    import_from_google(cfg, client, state, str(output))

    # Re-run against the same (now updated) board file with no further changes.
    shutil.copy(output, board_file)
    summary = import_from_google(cfg, client, state, str(output))
    assert summary == {"created": 0, "updated": 0, "unchanged": 1, "removed": 0}


def test_import_removes_card_when_google_task_deleted_and_configured(board_file, client, tmp_path):
    cfg = Config(
        librekanban_export_path=str(board_file),
        board_name="Primary",
        state_path=str(tmp_path / "state.json"),
        on_google_delete="delete",
    )
    state = SyncState.load(cfg.state_path)
    export_to_google(cfg, client, state)
    tasklist_id = state.tasklist_id
    gtask_id = client.list_tasks(tasklist_id)[0]["id"]
    client.delete_task(tasklist_id, gtask_id)

    output = board_file.parent / "synced.json"
    summary = import_from_google(cfg, client, state, str(output))
    assert summary["removed"] == 1

    result = lkf.LibreKanbanFile.load(str(output))
    entry = result.find_board(name="Primary")
    assert entry["tasks"] == []


def test_import_requires_prior_export(cfg, client, state, board_file):
    output = board_file.parent / "synced.json"
    with pytest.raises(RuntimeError):
        import_from_google(cfg, client, state, str(output))
