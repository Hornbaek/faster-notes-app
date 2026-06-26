"""Tests for the SQLite activity store (store.py): ordering, FTS search,
JSON-column round-trip, and one-time migration from a legacy activity.json.

Each test points the store at a fresh DB under tmp_path and resets the module
singleton so tests are isolated from each other and from the shared session DB.
"""
import json
import os

import store


def _fresh(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB_FILE", str(tmp_path / "activity.db"))
    monkeypatch.setattr(store, "LEGACY_JSON", str(tmp_path / "activity.json"))
    monkeypatch.setattr(store, "_conn", None)
    monkeypatch.setattr(store, "_fts", True)


def _entry(i, **kw):
    e = {
        "id": f"job_{i}", "title": f"Note {i}", "status": "done",
        "created_at": f"2026-01-0{i}T00:00:00",
        "completed_at": f"2026-01-0{i}T00:01:00",
        "duration_sec": 10, "language": "en", "model": "m",
        "summary": "", "transcript": "", "action_items": [], "tags": [],
        "error": None,
    }
    e.update(kw)
    return e


def test_add_get_and_ordering(tmp_path, monkeypatch):
    _fresh(tmp_path, monkeypatch)
    store.add_activity(_entry(1))
    store.add_activity(_entry(3))
    store.add_activity(_entry(2))
    assert [e["id"] for e in store.list_activity()] == ["job_3", "job_2", "job_1"]
    got = store.get_activity("job_2")
    assert got and got["title"] == "Note 2"
    assert store.get_activity("missing") is None


def test_json_columns_roundtrip(tmp_path, monkeypatch):
    _fresh(tmp_path, monkeypatch)
    store.add_activity(_entry(1, action_items=["do x", "do y"], tags=["work", "idea"]))
    e = store.get_activity("job_1")
    assert e["action_items"] == ["do x", "do y"]
    assert e["tags"] == ["work", "idea"]


def test_fts_search(tmp_path, monkeypatch):
    _fresh(tmp_path, monkeypatch)
    store.add_activity(_entry(1, transcript="quarterly budget review with finance"))
    store.add_activity(_entry(2, transcript="grocery list oat milk avocado"))
    assert [e["id"] for e in store.search_activity("budget")] == ["job_1"]
    assert store.search_activity("avoc")[0]["id"] == "job_2"   # prefix match
    assert store.search_activity("") == []                      # empty query
    assert store.search_activity("!!!") == []                   # punctuation only, no crash


def test_update_keeps_fts_in_sync(tmp_path, monkeypatch):
    _fresh(tmp_path, monkeypatch)
    store.add_activity(_entry(1, transcript="placeholder"))
    store.add_activity(_entry(1, transcript="revised mentions zephyr"))  # same id → REPLACE
    assert [e["id"] for e in store.search_activity("zephyr")] == ["job_1"]
    assert store.search_activity("placeholder") == []  # old text de-indexed


def test_legacy_json_migration(tmp_path, monkeypatch):
    _fresh(tmp_path, monkeypatch)
    with open(store.LEGACY_JSON, "w", encoding="utf-8") as f:
        json.dump([_entry(1), _entry(2)], f)
    store.init()
    assert {e["id"] for e in store.list_activity()} == {"job_1", "job_2"}
    # File renamed aside so a later restart won't re-import.
    assert not os.path.exists(store.LEGACY_JSON)
    assert os.path.exists(store.LEGACY_JSON + ".imported")
