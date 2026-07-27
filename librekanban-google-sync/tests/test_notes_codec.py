from librekanban_sync.notes_codec import build_notes, parse_notes


def test_round_trip_with_all_fields():
    notes = build_notes(
        description="Pick up the health check.",
        board="Primary",
        column="Doing",
        tags=["Health", "Work"],
        priority="NONE",
        story_points=5.0,
        kanban_task_id="76a4a0aa-7be3-4f4f-88fe-a7d42885fb48",
    )
    description, meta = parse_notes(notes)
    assert description == "Pick up the health check."
    assert meta["board"] == "Primary"
    assert meta["column"] == "Doing"
    assert meta["tags"] == ["Health", "Work"]
    assert meta["priority"] == "NONE"
    assert meta["story_points"] == "5.0"
    assert meta["kanban_task_id"] == "76a4a0aa-7be3-4f4f-88fe-a7d42885fb48"


def test_round_trip_no_description_no_story_points():
    notes = build_notes(
        description="",
        board="Primary",
        column="To Do",
        tags=[],
        priority="NONE",
        story_points=None,
        kanban_task_id="abc",
    )
    description, meta = parse_notes(notes)
    assert description == ""
    assert meta["tags"] == []
    assert "story_points" not in meta
    assert meta["kanban_task_id"] == "abc"


def test_plain_notes_with_no_sync_block():
    description, meta = parse_notes("just some notes I typed in Google Tasks")
    assert description == "just some notes I typed in Google Tasks"
    assert meta == {"tags": []}


def test_empty_notes():
    description, meta = parse_notes(None)
    assert description == ""
    assert meta == {"tags": []}
