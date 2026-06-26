"""Tests for server-side recording persistence, /media playback, re-transcribe,
and the incremental (transcript-first, summary-second) result flow."""
import asyncio
import os

from fastapi.testclient import TestClient

import app

client = TestClient(app.app, base_url="http://testserver:8765")


def setup_module(_module):
    app.ensure_api_key()


def _auth():
    return {"Authorization": f"Bearer {app.read_config().get('api_key')}"}


def _base_job(jid):
    now = "2026-06-24T00:00:00"
    return {
        "id": jid, "title": "t", "duration_sec": None, "status": "queued",
        "created_at": now, "updated_at": now, "language": None, "transcript": "",
        "summary": "", "tags": [], "model": None, "result": None, "error": None,
        "text": "", "image_paths": [],
    }


async def _fake_ra(text, images=None):
    skill = {"id": "quick-note", "actions": []}
    return skill, {"skill_id": "quick-note", "format": "json", "summary": "final summary",
                   "action_items": ["a"], "tags": ["t"], "fields": {}, "body": "", "model": "m"}


async def _noop(*_a, **_k):
    return None


def test_audio_persisted_and_servable(monkeypatch, tmp_path):
    monkeypatch.setattr(app, "load_whisper", lambda *a, **k: None)
    monkeypatch.setattr(app, "transcribe_path", lambda p, language=None: ("hello world", "en"))
    monkeypatch.setattr(app, "run_analysis", _fake_ra)
    audio = tmp_path / "a.webm"
    audio.write_bytes(b"RIFFfakeaudio")
    jid = "job_persist"
    app.JOBS[jid] = _base_job(jid)
    asyncio.run(app.process_job(jid, str(audio)))

    job = app.JOBS[jid]
    assert job["status"] == "done"
    # The recording moved out of the scratch UPLOAD_DIR into permanent MEDIA_DIR.
    assert not audio.exists()
    af = job["audio_file"]
    assert af and os.path.exists(os.path.join(app.MEDIA_DIR, af))
    # The archive row records it, and the result advertises a playable recording.
    assert app.store.get_activity(jid)["audio_file"] == af
    assert job["result"]["hasAudio"] is True and job["result"]["status"] == "done"
    # /media streams the original bytes back.
    r = client.get(f"/media/{jid}", headers=_auth())
    assert r.status_code == 200 and r.content == b"RIFFfakeaudio"


def test_media_404_without_recording():
    r = client.get("/media/no_such_job_zzz", headers=_auth())
    assert r.status_code == 404


def test_transcript_archived_before_summary(monkeypatch, tmp_path):
    """The transcript is persisted (status 'summarizing') before the LLM runs."""
    monkeypatch.setattr(app, "load_whisper", lambda *a, **k: None)
    monkeypatch.setattr(app, "transcribe_path", lambda p, language=None: ("early transcript", "en"))
    seen = {}

    async def ra(text, images=None):
        row = app.store.get_activity("job_inc")
        seen["status"] = row["status"]
        seen["transcript"] = row["transcript"]
        seen["summary"] = row["summary"]
        return await _fake_ra(text, images)

    monkeypatch.setattr(app, "run_analysis", ra)
    audio = tmp_path / "c.webm"
    audio.write_bytes(b"x")
    app.JOBS["job_inc"] = _base_job("job_inc")
    asyncio.run(app.process_job("job_inc", str(audio)))

    # At analysis time the note already held the transcript, with no summary yet.
    assert seen["status"] == "summarizing"
    assert seen["transcript"] == "early transcript"
    assert not seen["summary"]
    # And the final write fills the summary in.
    final = app.store.get_activity("job_inc")
    assert final["status"] == "done" and final["summary"] == "final summary"


def test_partial_result_endpoint_reports_summarizing(monkeypatch):
    """/result returns the transcript with status 'summarizing' before the summary."""
    jid = "job_partial"
    app.JOBS[jid] = {
        **_base_job(jid), "status": "summarizing",
        "result": app._partial_result("a transcript", "en", 0, "job_partial.webm"),
    }
    r = client.get(f"/result/{jid}", headers=_auth())
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "summarizing"
    assert body["transcript"] == "a transcript"
    assert body["summary"] == "" and body["hasAudio"] is True


def test_retranscribe_reprocesses_saved_audio(monkeypatch, tmp_path):
    monkeypatch.setattr(app, "load_whisper", lambda *a, **k: None)
    monkeypatch.setattr(app, "transcribe_path", lambda p, language=None: ("first pass", "en"))
    monkeypatch.setattr(app, "run_analysis", _fake_ra)
    audio = tmp_path / "b.webm"
    audio.write_bytes(b"audiobytes")
    jid = "job_retx"
    app.JOBS[jid] = _base_job(jid)
    asyncio.run(app.process_job(jid, str(audio)))
    af = app.JOBS[jid]["audio_file"]
    assert af

    # Re-transcribe should reuse the persisted recording — no upload needed.
    monkeypatch.setattr(app, "process_job", _noop)
    r = client.post(f"/retranscribe/{jid}", headers=_auth())
    assert r.status_code == 200 and r.json()["jobId"] == jid
    job = app.JOBS[jid]
    assert job["status"] == "queued"
    assert job["audio_file"] == af
    assert job["text"] == "" and job["image_paths"] == []
    assert os.path.exists(os.path.join(app.MEDIA_DIR, os.path.basename(af)))


def test_retranscribe_requires_recording():
    app.store.add_activity({"id": "txtnote", "title": "t", "status": "done",
                            "transcript": "typed", "summary": "s", "audio_file": None})
    r = client.post("/retranscribe/txtnote", headers=_auth())
    assert r.status_code == 400


def test_retranscribe_unknown_note():
    r = client.post("/retranscribe/does_not_exist_zzz", headers=_auth())
    assert r.status_code == 404
