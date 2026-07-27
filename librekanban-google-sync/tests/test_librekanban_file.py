from pathlib import Path

from librekanban_sync import librekanban_file as lkf

FIXTURE = Path(__file__).parent / "fixtures" / "sample_backup.json"


def test_load_detects_backup_format():
    file = lkf.LibreKanbanFile.load(str(FIXTURE))
    assert file.is_backup is True
    assert len(file.board_entries()) == 1


def test_find_board_by_name():
    file = lkf.LibreKanbanFile.load(str(FIXTURE))
    entry = file.find_board(name="Primary")
    assert entry["board"]["name"] == "Primary"
    assert len(entry["tasks"]) == 1


def test_find_board_missing_name_raises():
    file = lkf.LibreKanbanFile.load(str(FIXTURE))
    try:
        file.find_board(name="Nope")
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_get_column_by_name():
    file = lkf.LibreKanbanFile.load(str(FIXTURE))
    entry = file.find_board(name="Primary")
    col = lkf.get_column_by_name(entry, "Doing")
    assert col is not None
    assert col["name"] == "Doing"
    assert lkf.get_column_by_name(entry, "Nonexistent") is None


def test_task_tag_names_defaults_to_empty_when_untagged():
    file = lkf.LibreKanbanFile.load(str(FIXTURE))
    entry = file.find_board(name="Primary")
    task = entry["tasks"][0]
    assert lkf.task_tag_names(entry, task) == []


def test_task_tag_names_resolves_tag_ids():
    file = lkf.LibreKanbanFile.load(str(FIXTURE))
    entry = file.find_board(name="Primary")
    task = dict(entry["tasks"][0])
    health_tag_id = next(t["id"] for t in entry["tags"] if t["name"] == "Health")
    task["tagIds"] = [health_tag_id]
    assert lkf.task_tag_names(entry, task) == ["Health"]


def test_add_task_appends_and_defaults_priority():
    file = lkf.LibreKanbanFile.load(str(FIXTURE))
    entry = file.find_board(name="Primary")
    todo = lkf.get_column_by_name(entry, "To Do")
    task = lkf.add_task(entry, column_id=todo["id"], title="New card", description="desc")
    assert task in entry["tasks"]
    assert task["columnId"] == todo["id"]
    assert task["priority"] == "NONE"
    assert task["history"][0]["changeType"] == "created"


def test_remove_task():
    file = lkf.LibreKanbanFile.load(str(FIXTURE))
    entry = file.find_board(name="Primary")
    task_id = entry["tasks"][0]["id"]
    assert lkf.remove_task(entry, task_id) is True
    assert lkf.find_task(entry, task_id) is None
    assert lkf.remove_task(entry, task_id) is False


def test_set_task_column_records_history():
    file = lkf.LibreKanbanFile.load(str(FIXTURE))
    entry = file.find_board(name="Primary")
    task = entry["tasks"][0]
    finished = lkf.get_column_by_name(entry, "Finished")
    before_history_len = len(task["history"])
    lkf.set_task_column(entry, task, finished["id"])
    assert task["columnId"] == finished["id"]
    assert len(task["history"]) == before_history_len + 1
    assert task["history"][-1]["changeType"] == "moved"


def test_save_round_trip_preserves_unknown_fields(tmp_path):
    file = lkf.LibreKanbanFile.load(str(FIXTURE))
    out = tmp_path / "out.json"
    file.save(str(out))
    reloaded = lkf.LibreKanbanFile.load(str(out))
    assert reloaded.raw == file.raw
    # sourceId is a field this module never touches; confirm it survives.
    assert (
        reloaded.board_entries()[0]["board"]["sourceId"]
        == file.board_entries()[0]["board"]["sourceId"]
    )
