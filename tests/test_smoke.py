"""Smoke tests for the server's security boundary and job pipeline.

Run: `python -m pytest` from the repo root. Whisper/Ollama are stubbed so the
suite is fast and offline (no model download, no Ollama needed).
"""
import asyncio
import os

from fastapi.testclient import TestClient

import app

# Loopback-style client (no explicit port → control plane allowed) and a
# LAN-port client (8766 → control plane must be blocked, bridge must work).
# Dashboard client on the loopback port (8765); control plane is allowed here.
client = TestClient(app.app, base_url="http://testserver:8765")
client8766 = TestClient(app.app, base_url="https://testserver:8766")


def _key() -> str:
    return app.read_config().get("api_key")


def setup_module(_module):
    app.ensure_api_key()


# ── P0.2: /api/* control plane is loopback-only ──────────────────────────────

def test_api_info_ok_on_loopback():
    r = client.get("/api/info")
    assert r.status_code == 200
    assert "apiKey" in r.json()


def test_control_plane_blocked_on_lan_port():
    # The pairing key must NOT be readable from the LAN-facing TLS port.
    assert client8766.get("/api/info").status_code == 404
    assert client8766.get("/api/activity").status_code == 404


def test_control_plane_blocked_when_proxied():
    # Behind a reverse proxy / Cloudflare tunnel the request can reach the loopback
    # port but carries forwarding headers — the control plane must stay hidden
    # (this is the api_key leak the tunnel test surfaced).
    for hdr in ("cf-connecting-ip", "x-forwarded-for", "cf-ray", "x-forwarded-host"):
        assert client.get("/api/info", headers={hdr: "203.0.113.7"}).status_code == 404, hdr
    # …but the token-guarded bridge (not under /api/) still works through a proxy.
    ok = client.get("/status", headers={"Authorization": f"Bearer {_key()}",
                                        "cf-connecting-ip": "203.0.113.7"})
    assert ok.status_code == 200


# ── Bridge auth + LAN reachability ───────────────────────────────────────────

def test_status_requires_token():
    assert client.get("/status").status_code == 401
    ok = client.get("/status", headers={"Authorization": f"Bearer {_key()}"})
    assert ok.status_code == 200


def test_bridge_reachable_on_lan_port():
    # /status is not under /api/, so the phone can still reach it on 8766.
    r = client8766.get("/status", headers={"Authorization": f"Bearer {_key()}"})
    assert r.status_code == 200


# ── Path-traversal guard (serve_frontend) ────────────────────────────────────

def test_safe_file_rejects_traversal():
    assert app._safe_file(app.STATIC_DIR, app.STATIC_DIR_ABS, "../../app.py") is None
    assert app._safe_file(app.STATIC_DIR, app.STATIC_DIR_ABS, "") is None


def test_js_mime_is_pinned():
    # A service worker / ES module won't load if Windows' mimetypes returns
    # text/plain for .js, so serve_frontend must force a JS MIME.
    assert app._media_type_for("/x/sw.js") == "text/javascript"
    assert app._media_type_for("/x/app.mjs") == "text/javascript"
    assert app._media_type_for("/x/manifest.webmanifest") == "application/manifest+json"
    assert app._media_type_for("/x/index.html") is None


def test_cors_allows_local_denies_public():
    # An allowed (loopback) origin is echoed back; a random public site is not.
    ok = client.get("/api/info", headers={"Origin": "http://localhost:5173"})
    assert ok.headers.get("access-control-allow-origin") == "http://localhost:5173"
    evil = client.get("/api/info", headers={"Origin": "https://evil.example.com"})
    assert "access-control-allow-origin" not in evil.headers


def test_sw_served_with_js_mime_on_phone_port():
    # End-to-end: the built service worker must be reachable on the phone port
    # with a JS content-type (browsers reject text/plain for SW scripts).
    if not app.PWA_AVAILABLE:
        import pytest
        pytest.skip("PWA not built (run `npm run build:static`)")
    r = client8766.get("/sw.js")
    assert r.status_code == 200
    assert "javascript" in r.headers["content-type"]


def test_pwa_served_over_tunnel_host_header():
    # A Cloudflare tunnel / reverse proxy forwards a Host header WITHOUT :8766, so
    # request.url.port is None — but the request still arrives on the 8766 socket
    # (scope server port). The PWA (manifest/SW), not the desktop dashboard, must be
    # served, otherwise the phone can't install the app or register the SW.
    if not app.PWA_AVAILABLE:
        import pytest
        pytest.skip("PWA not built (run `npm run build:static`)")
    r = client8766.get("/manifest.webmanifest", headers={"host": "notes.faster-notes.com"})
    assert r.status_code == 200
    assert "manifest+json" in r.headers["content-type"]  # not text/html (desktop fallback)
    assert "start_url" in r.text


# ── Job pipeline + restart/archive fallback (P0.3 / P0.4) ────────────────────

def test_pipeline_archives_and_result_survives_restart(monkeypatch):
    monkeypatch.setattr(app, "load_whisper", lambda *a, **k: None)
    monkeypatch.setattr(app, "transcribe_path", lambda _p, _lang=None: ("hello world", "en"))

    async def fake_run_analysis(_t, images=None):
        skill = {"id": "quick-note", "actions": []}
        result = {
            "skill_id": "quick-note", "format": "json", "summary": "a summary",
            "action_items": ["do x"], "tags": ["tag1"], "fields": {}, "body": "",
            "model": "fakemodel",
        }
        return skill, result

    monkeypatch.setattr(app, "run_analysis", fake_run_analysis)

    os.makedirs(app.UPLOAD_DIR, exist_ok=True)
    jid = "job_test_abc"
    path = os.path.join(app.UPLOAD_DIR, jid + ".webm")
    with open(path, "wb") as f:
        f.write(b"fake-audio")
    now = "2026-01-01T00:00:00"
    app.JOBS[jid] = {
        "id": jid, "title": "t", "duration_sec": 1, "status": "queued",
        "created_at": now, "updated_at": now, "language": None,
        "transcript": "", "summary": "", "tags": [], "model": None,
        "result": None, "error": None,
    }

    asyncio.run(app.process_job(jid, path))

    job = app.JOBS[jid]
    assert job["status"] == "done"
    assert job["result"]["transcript"] == "hello world"
    assert job["result"]["actionItems"] == ["do x"]
    assert not os.path.exists(path)  # uploaded file cleaned up after success

    archived = app._find_archived(jid)
    assert archived is not None and archived["status"] == "done"
    assert archived["action_items"] == ["do x"]

    # Simulate a restart: the live job is gone, /result must serve from archive.
    app.JOBS.pop(jid)
    r = client.get(f"/result/{jid}", headers={"Authorization": f"Bearer {_key()}"})
    assert r.status_code == 200
    body = r.json()
    assert body["transcript"] == "hello world"
    assert body["actionItems"] == ["do x"]
