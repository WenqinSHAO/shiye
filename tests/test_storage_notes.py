import pytest

from storage import LocalStore, NoteConflictError


def make_store(tmp_path):
    db_path = tmp_path / "shiye.db"
    return LocalStore(db_path=db_path, data_dir=tmp_path, embedder=None)


def test_save_and_list_note(tmp_path):
    store = make_store(tmp_path)
    note = store.save_note("Title line\n\nbody text")
    assert note
    listing = store.list_notes()
    assert listing
    assert listing[0]["id"] == note["id"]
    fetched = store.get_note(note["id"])
    assert fetched["content"].startswith("Title line")
    assert fetched["updated_at"]


def test_update_note_metadata(tmp_path):
    store = make_store(tmp_path)
    note = store.save_note("first version", title="First")
    updated = store.save_note("second version", title="Second title", note_id=note["id"])
    assert updated["id"] == note["id"]
    assert updated["title"] == "Second title"
    assert updated["content"] == "second version"
    assert updated["updated_at"] >= note["updated_at"]


def test_image_references_are_captured(tmp_path):
    store = make_store(tmp_path)
    note = store.save_note("![img](/assets/img/test.png)", title="Img note")
    assert "/assets/img/test.png" in note["images"]
    again = store.get_note(note["id"])
    assert "/assets/img/test.png" in again["images"]


def test_save_note_conflict(tmp_path):
    store = make_store(tmp_path)
    original = store.save_note("first", title="Conflict")
    assert original["updated_at"]
    # Update once to move the server version forward
    latest = store.save_note("second", title="Conflict", note_id=original["id"])
    assert latest["updated_at"] != original["updated_at"]
    with pytest.raises(NoteConflictError):
        store.save_note(
            "stale overwrite",
            title="Conflict",
            note_id=original["id"],
            expected_updated_at=original["updated_at"],
        )
