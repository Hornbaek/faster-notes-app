"""Tests for multimodal capture: text/image/audio uploads + the pipeline."""
import asyncio
import base64
import os

from fastapi.testclient import TestClient

import app

client = TestClient(app.app, base_url="http://testserver:8765")

# A valid 1x1 PNG.
PNG_1x1 = base64.b64decode(
    b"iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)


def setup_module(_module):
    app.ensure_api_key()


def _auth():
    return {"Authorization": f"Bearer {app.read_config().get('api_key')}"}


def _coro(value):
    async def _c():
        return value
    return _c()


async def _noop(*_a, **_k):
    return None


def _base_job(jid):
    now = "2026-06-20T00:00:00"
    return {
        "id": jid, "title": "t", "duration_sec": None, "status": "queued",
        "created_at": now, "updated_at": now, "language": None, "transcript": "",
        "summary": "", "tags": [], "model": None, "result": None, "error": None,
        "text": "", "image_paths": [],
    }


# ── /upload input handling (process_job stubbed so nothing runs in background) ─

def test_upload_rejects_empty(monkeypatch):
    monkeypatch.setattr(app, "process_job", _noop)
    r = client.post("/upload", headers=_auth(), data={"recordingId": "e1", "metadata": "{}"})
    assert r.status_code == 400


def test_upload_accepts_text_only(monkeypatch):
    monkeypatch.setattr(app, "process_job", _noop)
    r = client.post("/upload", headers=_auth(),
                    data={"recordingId": "t1", "text": "a typed note"})
    assert r.status_code == 200
    jid = r.json()["jobId"]
    assert app.JOBS[jid]["text"] == "a typed note"
    assert app.JOBS[jid]["image_paths"] == []


def test_upload_accepts_image_only(monkeypatch):
    monkeypatch.setattr(app, "process_job", _noop)
    r = client.post("/upload", headers=_auth(), data={"recordingId": "i1"},
                    files=[("images", ("a.png", PNG_1x1, "image/png"))])
    assert r.status_code == 200
    jid = r.json()["jobId"]
    paths = app.JOBS[jid]["image_paths"]
    assert len(paths) == 1 and os.path.exists(paths[0])
    os.unlink(paths[0])  # tidy up (process_job, which would clean it, was stubbed)


def test_upload_accepts_combined(monkeypatch):
    monkeypatch.setattr(app, "process_job", _noop)
    r = client.post(
        "/upload", headers=_auth(),
        data={"recordingId": "c1", "text": "see attached"},
        files=[("file", ("a.webm", b"fakeaudio", "audio/webm")),
               ("images", ("a.png", PNG_1x1, "image/png")),
               ("images", ("b.png", PNG_1x1, "image/png"))],
    )
    assert r.status_code == 200
    jid = r.json()["jobId"]
    assert app.JOBS[jid]["text"] == "see attached"
    assert len(app.JOBS[jid]["image_paths"]) == 2
    for p in app.JOBS[jid]["image_paths"]:
        os.unlink(p)


# ── process_job: skips transcription without audio ───────────────────────────

def test_process_job_text_only_skips_transcription(monkeypatch):
    def boom(*_a, **_k):
        raise AssertionError("transcribe_path must not be called for a text-only note")

    monkeypatch.setattr(app, "transcribe_path", boom)

    async def fake_run_analysis(text, images=None):
        assert text == "just text" and not images
        skill = {"id": "quick-note", "actions": []}
        return skill, {"skill_id": "quick-note", "format": "json", "summary": "S",
                       "action_items": [], "tags": [], "fields": {}, "body": "", "model": "m"}

    monkeypatch.setattr(app, "run_analysis", fake_run_analysis)
    jid = "job_txt_only"
    app.JOBS[jid] = {**_base_job(jid), "text": "just text"}
    asyncio.run(app.process_job(jid, None))
    job = app.JOBS[jid]
    assert job["status"] == "done"
    assert job["transcript"] == "just text"
    assert job["result"]["summary"] == "S"


def test_process_job_passes_images_and_combines(monkeypatch, tmp_path):
    captured = {}

    async def fake_gen(model, prompt, fmt=None, images=None, timeout=300.0):
        captured["images"] = images
        return '{"summary":"from image","action_items":[],"tags":[]}'

    monkeypatch.setattr(app, "_ollama_generate", fake_gen)
    monkeypatch.setattr(app, "pick_analysis_model", lambda: _coro("vision"))
    monkeypatch.setattr(app, "pick_orchestration_model", lambda: _coro("vision"))

    imgp = tmp_path / "x.png"
    imgp.write_bytes(PNG_1x1)
    jid = "job_img"
    app.JOBS[jid] = {**_base_job(jid), "text": "look", "image_paths": [str(imgp)]}
    asyncio.run(app.process_job(jid, None))
    job = app.JOBS[jid]
    assert job["status"] == "done"
    assert job["transcript"] == "look"
    # The skill run received base64 image data, and the count is recorded.
    assert captured["images"] and isinstance(captured["images"][0], str)
    assert job["output"]["image_count"] == 1
    assert job["result"]["summary"] == "from image"


# ── routing + multimodal plumbing ────────────────────────────────────────────

def test_classify_empty_text_returns_default_without_router_call(monkeypatch):
    calls = {"n": 0}

    async def fake_gen(*_a, **_k):
        calls["n"] += 1
        return '{"skill_id":"meeting"}'

    monkeypatch.setattr(app, "_ollama_generate", fake_gen)
    chosen = asyncio.run(app.classify_skill("", "m"))  # image-only note → no text
    assert chosen["id"] == "quick-note"  # the default/fallback
    assert calls["n"] == 0               # no router LLM call was made


def test_ollama_generate_sends_images(monkeypatch):
    captured = {}

    class FakeResp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"response": "ok"}

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json=None):
            captured["payload"] = json
            return FakeResp()

    monkeypatch.setattr(app.httpx, "AsyncClient", FakeClient)
    out = asyncio.run(app._ollama_generate("m", "hi", images=["BASE64DATA"]))
    assert out == "ok"
    assert captured["payload"]["images"] == ["BASE64DATA"]
    # And no images key when none are passed.
    asyncio.run(app._ollama_generate("m", "hi"))
    assert "images" not in captured["payload"]


# ── spoken-language selection ────────────────────────────────────────────────

class _FakeSeg:
    text = "hej"


class _FakeInfo:
    language = "da"


def test_transcribe_path_forces_language(monkeypatch):
    captured = {}

    class FakeModel:
        def transcribe(self, path, **kw):
            captured.update(kw)
            return ([_FakeSeg()], _FakeInfo())

    monkeypatch.setattr(app, "whisper_model", FakeModel())
    text, lang = app.transcribe_path("x.webm", "da")
    assert captured["language"] == "da"          # forced, not auto-detected
    assert text == "hej" and lang == "da"
    app.transcribe_path("x.webm", None)
    assert captured["language"] is None          # Auto → no forced language


def test_upload_carries_language(monkeypatch):
    monkeypatch.setattr(app, "process_job", _noop)
    r = client.post("/upload", headers=_auth(),
                    data={"recordingId": "lang1", "text": "hej", "metadata": '{"language":"sv"}'})
    assert r.status_code == 200
    assert app.JOBS[r.json()["jobId"]]["lang_choice"] == "sv"


def test_process_job_forces_chosen_language(monkeypatch, tmp_path):
    captured = {}
    monkeypatch.setattr(app, "load_whisper", lambda *a, **k: None)

    def fake_tp(path, language=None):
        captured["language"] = language
        return ("hej", language or "auto")

    monkeypatch.setattr(app, "transcribe_path", fake_tp)

    async def fake_ra(text, images=None):
        skill = {"id": "quick-note", "actions": []}
        return skill, {"skill_id": "quick-note", "format": "json", "summary": "s",
                       "action_items": [], "tags": [], "fields": {}, "body": "", "model": "m"}

    monkeypatch.setattr(app, "run_analysis", fake_ra)
    audio = tmp_path / "a.webm"
    audio.write_bytes(b"x")
    jid = "job_lang"
    app.JOBS[jid] = {**_base_job(jid), "lang_choice": "da"}
    asyncio.run(app.process_job(jid, str(audio)))
    assert captured["language"] == "da"
    assert app.JOBS[jid]["result"]["language"] == "da"


def test_read_config_raises_on_corrupt(tmp_path, monkeypatch):
    bad = tmp_path / "config.json"
    bad.write_text("{ not json", encoding="utf-8")
    monkeypatch.setattr(app, "CONFIG_FILE", str(bad))
    import pytest
    with pytest.raises(RuntimeError):
        app.read_config()
