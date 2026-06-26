"""Tests for the Cloudflare Tunnel client, endpoints, and connector lifecycle.

All Cloudflare HTTP + the cloudflared subprocess are mocked — no network, no binary.
"""
import asyncio
import json

from fastapi.testclient import TestClient

import app
import cftunnel
import runner

client = TestClient(app.app, base_url="http://testserver:8765")  # loopback control plane


def setup_module(_module):
    app.ensure_api_key()


def _coro(value):
    async def _c():
        return value
    return _c()


# ── _cf envelope unwrapping (fake httpx) ─────────────────────────────────────

class _Resp:
    def __init__(self, payload):
        self._p = payload
        self.text = json.dumps(payload)

    def json(self):
        return self._p


class _Client:
    def __init__(self, payload):
        self._p = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def request(self, method, url, headers=None, json=None, params=None):
        return _Resp(self._p)


def test_cf_unwraps_success(monkeypatch):
    monkeypatch.setattr(cftunnel.httpx, "AsyncClient",
                        lambda *a, **k: _Client({"success": True, "result": {"x": 1}}))
    assert asyncio.run(cftunnel._cf("GET", "/x", "tok")) == {"x": 1}


def test_cf_raises_on_failure(monkeypatch):
    monkeypatch.setattr(cftunnel.httpx, "AsyncClient",
                        lambda *a, **k: _Client({"success": False, "errors": [{"message": "nope"}]}))
    try:
        asyncio.run(cftunnel._cf("GET", "/x", "tok"))
        assert False, "should have raised"
    except cftunnel.CloudflareError as e:
        assert "nope" in str(e)


# ── higher-level client (monkeypatched _cf) ──────────────────────────────────

def test_ensure_dns_creates_then_updates(monkeypatch):
    calls = []
    state = {"existing": []}

    async def fake_cf(method, path, token, json_body=None, params=None):
        calls.append((method, path))
        if method == "GET" and "/dns_records" in path:
            return state["existing"]
        return {"id": "rec1"}

    monkeypatch.setattr(cftunnel, "_cf", fake_cf)
    # No existing record → POST create.
    asyncio.run(cftunnel.ensure_dns("tok", "z1", "notes.a.com", "tun1"))
    assert calls[-1][0] == "POST" and "/dns_records" in calls[-1][1]
    # Existing record → PUT update by id.
    state["existing"] = [{"id": "rec1"}]
    calls.clear()
    asyncio.run(cftunnel.ensure_dns("tok", "z1", "notes.a.com", "tun1"))
    assert calls[-1][0] == "PUT" and calls[-1][1].endswith("/dns_records/rec1")


def test_ensure_tunnel_create_vs_reuse(monkeypatch):
    async def fake_cf(method, path, token, json_body=None, params=None):
        if path.endswith("/token"):
            return "connector-tok"
        if method == "POST" and path.endswith("/cfd_tunnel"):
            assert json_body["config_src"] == "cloudflare"
            return {"id": "new-tun"}
        if method == "GET":  # confirm an existing tunnel
            return {"id": "old-tun"}
        return None

    monkeypatch.setattr(cftunnel, "_cf", fake_cf)
    tid, tok = asyncio.run(cftunnel.ensure_tunnel("t", "acct", "fasternotes"))
    assert tid == "new-tun" and tok == "connector-tok"           # created
    tid2, _ = asyncio.run(cftunnel.ensure_tunnel("t", "acct", "fasternotes", existing_id="old-tun"))
    assert tid2 == "old-tun"                                      # reused


def test_provision_orchestrates(monkeypatch):
    monkeypatch.setattr(cftunnel, "ensure_tunnel", lambda *a, **k: _coro(("tun1", "ctok")))
    seen = {}

    async def fake_ingress(token, acct, tid, host):
        seen["ingress"] = (tid, host)

    async def fake_dns(token, zone, host, tid):
        seen["dns"] = (zone, host, tid)

    monkeypatch.setattr(cftunnel, "configure_ingress", fake_ingress)
    monkeypatch.setattr(cftunnel, "ensure_dns", fake_dns)
    out = asyncio.run(cftunnel.provision("t", "acct", "z1", "notes.a.com"))
    assert out == {"tunnel_id": "tun1", "tunnel_token": "ctok", "hostname": "notes.a.com"}
    assert seen["ingress"] == ("tun1", "notes.a.com")
    assert seen["dns"] == ("z1", "notes.a.com", "tun1")


# ── endpoints ────────────────────────────────────────────────────────────────

def test_save_token_validates(monkeypatch):
    monkeypatch.setattr(cftunnel, "verify_token", lambda token: _coro({"status": "active"}))
    r = client.post("/api/settings/cloudflare", json={"api_token": "cf-token"})
    assert r.status_code == 200
    assert app.read_config()["cloudflare_api_token"] == "cf-token"


def test_save_token_rejects_bad(monkeypatch):
    async def boom(token):
        raise cftunnel.CloudflareError("invalid token")
    monkeypatch.setattr(cftunnel, "verify_token", boom)
    r = client.post("/api/settings/cloudflare", json={"api_token": "bad"})
    assert r.status_code == 400


def test_enable_provisions_and_starts(monkeypatch):
    # Token must be saved first.
    monkeypatch.setattr(cftunnel, "verify_token", lambda token: _coro({"status": "active"}))
    client.post("/api/settings/cloudflare", json={"api_token": "cf"})

    # Account id is resolved from the zone (not /accounts).
    monkeypatch.setattr(cftunnel, "account_for_zone", lambda token, zone_id: _coro("acct-1"))

    async def fake_provision(token, acct, zone, host, name="fasternotes", existing_id=None):
        assert acct == "acct-1"
        return {"tunnel_id": "tunX", "tunnel_token": "tokX", "hostname": host}
    monkeypatch.setattr(cftunnel, "provision", fake_provision)
    started = {}
    monkeypatch.setattr(runner, "start_cloudflared", lambda token: started.update(token=token))

    r = client.post("/api/cloudflare/enable", json={"zone_id": "z1", "hostname": "notes.a.com"})
    assert r.status_code == 200 and r.json()["hostname"] == "notes.a.com"
    cfg = app.read_config()
    assert cfg["cloudflare_enabled"] is True
    assert cfg["cloudflare_tunnel_id"] == "tunX" and cfg["cloudflare_hostname"] == "notes.a.com"
    assert started["token"] == "tokX"
    # /api/info now advertises the tunnel for pairing.
    info = client.get("/api/info").json()
    assert info["tunnel"] == {"hostname": "notes.a.com"}


def test_disable_stops(monkeypatch):
    stopped = {}
    monkeypatch.setattr(runner, "stop_cloudflared", lambda: stopped.update(done=True))
    r = client.post("/api/cloudflare/disable")
    assert r.status_code == 200
    assert app.read_config()["cloudflare_enabled"] is False
    assert stopped.get("done")


def test_cloudflare_endpoints_blocked_on_lan_port():
    c8766 = TestClient(app.app, base_url="https://testserver:8766")
    assert c8766.post("/api/cloudflare/enable", json={"zone_id": "z", "hostname": "h"}).status_code == 404
    assert c8766.get("/api/cloudflare/zones").status_code == 404


# ── connector subprocess lifecycle (mocked Popen) ────────────────────────────

class _FakeProc:
    def __init__(self):
        self._alive = True
        self.pid = 4321

    def poll(self):
        return None if self._alive else 0

    def terminate(self):
        self._alive = False

    def wait(self, timeout=None):
        return 0

    def kill(self):
        self._alive = False


def test_start_stop_cloudflared(monkeypatch):
    monkeypatch.setattr(cftunnel, "ensure_cloudflared", lambda: "cloudflared.exe")
    monkeypatch.setattr(runner.subprocess, "Popen", lambda *a, **k: _FakeProc())
    runner.start_cloudflared("connector-token")
    assert runner.cloudflared_running() is True
    runner.stop_cloudflared()
    assert runner.cloudflared_running() is False
