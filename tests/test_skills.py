"""Tests for the skills engine: registry, normalization, routing, actions, API."""
import asyncio
import json
import os

from fastapi.testclient import TestClient

import app
import paths
import skills
import store

client = TestClient(app.app, base_url="http://testserver:8765")


def _key() -> str:
    return app.read_config().get("api_key")


def setup_module(_module):
    app.ensure_api_key()


# ── Registry: load + user override by id ─────────────────────────────────────

def test_bundled_skills_load():
    by_id = skills.skills_by_id()
    assert "quick-note" in by_id
    assert by_id["quick-note"]["builtin"] is True
    assert {"meeting", "journal", "curator"} <= set(by_id)


def test_user_skill_overrides_bundled_by_id():
    try:
        skills.save_skill({
            "id": "quick-note", "name": "My quick note",
            "prompt": "custom prompt", "output": {"format": "json"},
        })
        s = skills.get_skill("quick-note")
        assert s["name"] == "My quick note"
        assert s["source"] == "user"
        assert s["builtin"] is True and s["overridden"] is True
    finally:
        skills.delete_skill("quick-note")
    # Reverts to the shipped version after the user copy is deleted.
    assert skills.get_skill("quick-note")["name"] == "Quick note"


def test_save_and_delete_user_skill():
    saved = skills.save_skill({"name": "Test Recipe!", "prompt": "do a thing"})
    assert saved["id"] == "test-recipe"
    assert skills.get_skill("test-recipe") is not None
    assert skills.delete_skill("test-recipe") is True
    assert skills.get_skill("test-recipe") is None


# ── normalize_output ─────────────────────────────────────────────────────────

def test_normalize_json_maps_well_known_and_keeps_custom_fields():
    skill = {"id": "meeting", "output": {"format": "json"}}
    raw = json.dumps({
        "summary": "we met", "decisions": ["ship it"], "attendees": ["Sam"],
        "action_items": ["email Sam"], "tags": ["#Work", "plan"],
    })
    out = skills.normalize_output(skill, raw)
    assert out["summary"] == "we met"
    assert out["action_items"] == ["email Sam"]
    assert out["tags"] == ["Work", "plan"]                # leading '#' stripped
    assert out["fields"]["decisions"] == ["ship it"]      # custom field preserved
    assert out["fields"]["attendees"] == ["Sam"]


def test_normalize_markdown_is_body_with_preview_summary():
    skill = {"id": "journal", "output": {"format": "markdown"}}
    out = skills.normalize_output(skill, "  Today I built a skills engine.  ")
    assert out["body"] == "Today I built a skills engine."
    assert out["summary"] == "Today I built a skills engine."
    assert out["action_items"] == [] and out["fields"] == {}


def test_normalize_bad_json_degrades():
    skill = {"id": "quick-note", "output": {"format": "json"}}
    out = skills.normalize_output(skill, "sorry, not json")
    assert out["summary"] == "" and out["action_items"] == [] and out["tags"] == []


# ── classify_skill routing + fallback ────────────────────────────────────────

def test_classify_returns_chosen_skill(monkeypatch):
    async def fake_gen(model, prompt, fmt=None, images=None, timeout=300.0):
        return '{"skill_id":"meeting"}'

    monkeypatch.setattr(app, "_ollama_generate", fake_gen)
    chosen = asyncio.run(app.classify_skill("we had a standup", "m"))
    assert chosen["id"] == "meeting"


def test_classify_unknown_id_falls_back_to_default(monkeypatch):
    async def fake_gen(model, prompt, fmt=None, images=None, timeout=300.0):
        return '{"skill_id":"does-not-exist"}'

    monkeypatch.setattr(app, "_ollama_generate", fake_gen)
    chosen = asyncio.run(app.classify_skill("whatever", "m"))
    assert chosen["id"] == "quick-note"   # configured/implicit default


def test_classify_error_falls_back(monkeypatch):
    async def boom(*a, **k):
        raise RuntimeError("ollama down")

    monkeypatch.setattr(app, "_ollama_generate", boom)
    chosen = asyncio.run(app.classify_skill("whatever", "m"))
    assert chosen["id"] == "quick-note"


# ── write_file action ────────────────────────────────────────────────────────

def test_write_file_action_writes_markdown(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "read_config", lambda: {"vault_dir": str(tmp_path)})
    skill = {"id": "journal", "actions": [{"type": "write_file", "dir": "{vault}/Journal"}]}
    job = {"id": "job_x", "title": "My Day", "created_at": "2026-06-19T10:00:00",
           "transcript": "raw words", "language": "en"}
    result = {"skill_id": "journal", "format": "markdown", "summary": "A good day.",
              "body": "A good day.", "action_items": [], "tags": [], "fields": {}}
    asyncio.run(app.run_actions(skill, job, result))
    out_dir = tmp_path / "Journal"
    files = list(out_dir.glob("*.md"))
    assert len(files) == 1
    text = files[0].read_text(encoding="utf-8")
    assert "# My Day" in text and "A good day." in text
    assert "job" not in job.get("action_errors", [])  # no errors recorded


def test_append_project_writes_to_data_dir():
    # read_projects falls back to the bundled seed until the first write…
    seeded = app.read_projects()
    assert seeded["projects"], "expected bundled seed projects"
    pid = seeded["projects"][0]["id"]
    skill = {"id": "x", "actions": [{"type": "append_project", "project": pid}]}
    job = {"id": "job_pp", "title": "Note", "created_at": "2026-06-19T10:00:00"}
    result = {"summary": "did a thing", "skill_id": "x"}
    try:
        asyncio.run(app.run_actions(skill, job, result))
        # …and now the writable copy exists (not the read-only bundle) with the note.
        assert os.path.exists(paths.PROJECTS_FILE)
        proj = next(p for p in app.read_projects()["projects"] if p["id"] == pid)
        assert any(n.get("id") == "job_pp" for n in proj.get("notes", []))
        assert not job.get("action_errors")
    finally:
        if os.path.exists(paths.PROJECTS_FILE):
            os.unlink(paths.PROJECTS_FILE)


def test_action_failure_does_not_raise(monkeypatch):
    skill = {"id": "x", "actions": [{"type": "webhook"}]}  # missing url
    job = {"id": "job_y"}
    result = {"skill_id": "x", "summary": "", "action_items": [], "tags": [],
              "fields": {}, "body": ""}
    asyncio.run(app.run_actions(skill, job, result))  # must not raise
    assert job["action_errors"]  # error recorded instead


# ── store.py migration round-trips output_json + skill_id ────────────────────

def test_store_persists_skill_id_and_output_json():
    entry = {
        "id": "job_store_test", "title": "t", "status": "done",
        "summary": "s", "transcript": "hi", "action_items": ["a"], "tags": ["t"],
        "skill_id": "meeting",
        "output_json": json.dumps({"format": "json", "fields": {"decisions": ["d"]}, "body": ""}),
    }
    store.add_activity(entry)
    back = store.get_activity("job_store_test")
    assert back["skill_id"] == "meeting"
    parsed = json.loads(back["output_json"])
    assert parsed["fields"]["decisions"] == ["d"]


# ── API: list / get / upsert / delete / default-skill ────────────────────────

def test_api_list_skills():
    r = client.get("/api/skills")
    assert r.status_code == 200
    body = r.json()
    ids = {s["id"] for s in body["skills"]}
    assert "quick-note" in ids
    assert body["default_skill_id"]


def test_api_skill_crud_and_default():
    # create
    r = client.post("/api/skills", json={"name": "Api Skill", "prompt": "hello",
                                         "output": {"format": "markdown"}})
    assert r.status_code == 200
    sid = r.json()["skill"]["id"]
    assert sid == "api-skill"
    # get
    assert client.get(f"/api/skills/{sid}").json()["name"] == "Api Skill"
    # set as default
    r = client.post("/api/settings/default-skill", json={"skill_id": sid})
    assert r.json()["default_skill_id"] == sid
    assert client.get("/api/skills").json()["default_skill_id"] == sid
    # delete + default falls back
    assert client.delete(f"/api/skills/{sid}").json()["removed"] is True
    assert client.get(f"/api/skills/{sid}").status_code == 404
    assert client.get("/api/skills").json()["default_skill_id"] == "quick-note"


def test_api_default_skill_rejects_unknown():
    assert client.post("/api/settings/default-skill",
                       json={"skill_id": "nope"}).status_code == 400


# ── Orchestration (router) model ─────────────────────────────────────────────

def test_orchestration_model_falls_back_to_analysis(monkeypatch):
    monkeypatch.setattr(app, "read_config", lambda: {})                       # unset
    monkeypatch.setattr(app, "pick_analysis_model", lambda: _coro("analysis-m"))
    assert asyncio.run(app.pick_orchestration_model()) == "analysis-m"
    monkeypatch.setattr(app, "read_config", lambda: {"orchestration_model": "router-m"})
    assert asyncio.run(app.pick_orchestration_model()) == "router-m"


def test_api_set_orchestration_model():
    r = client.post("/api/settings/orchestration-model", json={"model": "phi3"})
    assert r.status_code == 200 and r.json()["orchestration_model"] == "phi3"
    assert app.read_config().get("orchestration_model") == "phi3"
    r = client.post("/api/settings/orchestration-model", json={"model": None})
    assert r.json()["orchestration_model"] is None  # clears back to auto


def test_api_skills_blocked_on_lan_port():
    c8766 = TestClient(app.app, base_url="https://testserver:8766")
    assert c8766.get("/api/skills").status_code == 404


# ── API: test/dry-run runs the skill, no actions ─────────────────────────────

def test_api_skill_test_runs_without_actions(monkeypatch):
    async def fake_gen(model, prompt, fmt=None, images=None, timeout=300.0):
        return '{"summary":"dry run","action_items":[],"tags":["x"]}'

    monkeypatch.setattr(app, "_ollama_generate", fake_gen)
    monkeypatch.setattr(app, "pick_analysis_model", lambda: _coro("m"))

    r = client.post("/api/skills/test", json={"text": "hello", "skill_id": "quick-note"})
    assert r.status_code == 200
    assert r.json()["summary"] == "dry run"


def _coro(value):
    async def _c():
        return value
    return _c()
