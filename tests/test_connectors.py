"""Tests for the output-connectors engine: templating, presets, secrets, the
generic http_request connector, routing rules, write_file modes, the delivery
outbox/retry, and the loopback-only API."""
import asyncio
import json
import os

from fastapi.testclient import TestClient

import app
import connectors
import paths
import store

client = TestClient(app.app, base_url="http://testserver:8765")


def setup_module(_module):
    app.ensure_api_key()


# ── Fake httpx so http_request tests never hit the network ───────────────────

class _FakeResp:
    def __init__(self, status=200, text="{}"):
        self.status_code = status
        self.text = text

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _FakeClient:
    calls: list = []
    status: int = 200
    body_text: str = "{}"

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def request(self, method, url, **kwargs):
        _FakeClient.calls.append({"method": method, "url": url, **kwargs})
        return _FakeResp(_FakeClient.status, _FakeClient.body_text)

    async def post(self, url, **kwargs):
        _FakeClient.calls.append({"method": "POST", "url": url, **kwargs})
        return _FakeResp(_FakeClient.status, _FakeClient.body_text)


def _use_fake_http(monkeypatch, status=200, body_text="{}"):
    _FakeClient.calls = []
    _FakeClient.status = status
    _FakeClient.body_text = body_text
    monkeypatch.setattr(app.httpx, "AsyncClient", _FakeClient)


class _FakeSMTP:
    """Stands in for smtplib.SMTP / SMTP_SSL so email tests never hit a server."""
    last = None

    def __init__(self, host, port, timeout=None, context=None):
        self.host, self.port, self.context = host, port, context
        self.tls = False; self.logged = None; self.sent = []
        _FakeSMTP.last = self

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def ehlo(self):
        pass

    def starttls(self, context=None):
        self.tls = True

    def login(self, u, p):
        self.logged = (u, p)

    def send_message(self, msg):
        self.sent.append(msg)


# ── Template rendering ───────────────────────────────────────────────────────

def test_render_whole_token_keeps_native_type():
    ctx = connectors.build_context(
        {"id": "j1"}, {"tags": ["a", "b"], "action_items": ["x"], "fields": {"k": 1}})
    assert connectors.render("{{tags}}", ctx) == ["a", "b"]          # list preserved
    assert connectors.render("{{action_items}}", ctx) == ["x"]
    assert connectors.render("{{fields}}", ctx) == {"k": 1}          # dict preserved


def test_render_embedded_token_stringifies_and_filters():
    ctx = connectors.build_context(
        {"title": "Hi"}, {"summary": "S", "tags": ["work", "plan"]})
    assert connectors.render("*{{title}}*: {{summary}}", ctx) == "*Hi*: S"
    assert connectors.render("Tags: {{tags | join: \", \"}}", ctx) == "Tags: work, plan"
    assert connectors.render("{{tags | join: \"-\"}}", ctx) == "work-plan"  # embedded via filter


def test_render_recurses_into_dict_and_list():
    ctx = connectors.build_context({"id": "9"}, {"summary": "S"})
    body = {"text": "sum={{summary}}", "items": ["{{id}}"]}
    assert connectors.render(body, ctx) == {"text": "sum=S", "items": ["9"]}


# ── Preset registry + user override ──────────────────────────────────────────

def test_bundled_presets_load():
    by_id = connectors.presets_by_id()
    assert {"markdown-vault", "obsidian-daily", "slack-webhook", "n8n-webhook"} <= set(by_id)
    assert by_id["slack-webhook"]["needs_secrets"] == ["slack_webhook_url"]
    assert by_id["slack-webhook"]["builtin"] is True


def test_user_preset_overrides_bundled_by_id():
    path = os.path.join(paths.CONNECTORS_DIR, "slack-webhook.yaml")
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write("id: slack-webhook\nname: My Slack\nfamily: webhook\n"
                    "connector:\n  type: http_request\n  url: x\n")
        p = connectors.get_preset("slack-webhook")
        assert p["name"] == "My Slack"
        assert p["source"] == "user" and p["overridden"] is True
    finally:
        os.unlink(path)
    assert connectors.get_preset("slack-webhook")["name"] == "Slack (Incoming Webhook)"


# ── Secret injection + redaction ─────────────────────────────────────────────

def test_secret_injected_and_redacted():
    secret = "https://hooks.slack.com/services/T0/SECRETTOKEN"
    conn = connectors.get_preset("slack-webhook")["connector"]
    rendered = connectors.render_connector(
        conn, {"title": "T"}, {"summary": "S", "tags": []},
        {"slack_webhook_url": secret})
    assert rendered["url"] == secret                       # injected for the real send
    safe = connectors.redact(rendered, {"slack_webhook_url": secret})
    assert secret not in json.dumps(safe) and "***" in safe["url"]


# ── Generic http_request connector ───────────────────────────────────────────

def test_http_request_builds_call(monkeypatch):
    _use_fake_http(monkeypatch)
    monkeypatch.setattr(app, "read_config",
                        lambda: {"connector_secrets": {"n8n_webhook_url": "https://hook.test/abc"}})
    conn = connectors.get_preset("n8n-webhook")["connector"]
    job = {"id": "j5", "title": "Note", "transcript": "raw"}
    result = {"summary": "did it", "action_items": ["a"], "tags": ["t"], "fields": {"x": 1},
              "skill_id": "quick-note"}
    asyncio.run(app._action_http_request(conn, job, result))
    assert len(_FakeClient.calls) == 1
    call = _FakeClient.calls[0]
    assert call["method"] == "POST" and call["url"] == "https://hook.test/abc"
    body = call["json"]
    assert body["summary"] == "did it"
    assert body["action_items"] == ["a"]      # whole-token kept the list
    assert body["fields"] == {"x": 1}         # whole-token kept the dict


def test_http_request_missing_url_raises(monkeypatch):
    _use_fake_http(monkeypatch)
    monkeypatch.setattr(app, "read_config", lambda: {})
    try:
        asyncio.run(app._action_http_request({"type": "http_request"}, {"id": "j"}, {}))
        assert False, "expected ValueError"
    except ValueError:
        pass


# ── Routing rules ────────────────────────────────────────────────────────────

def test_rule_matches_all_tag_skill():
    assert connectors.rule_matches({"all": True}, [], None) is True
    assert connectors.rule_matches({"tags": ["Work"]}, ["work"], None) is True   # case-insensitive
    assert connectors.rule_matches({"skills": ["meeting"]}, [], "meeting") is True
    assert connectors.rule_matches({"tags": ["x"]}, ["y"], "meeting") is False
    assert connectors.rule_matches({}, ["anything"], "meeting") is False         # zero-egress default


def test_run_actions_fires_matching_global_rule(monkeypatch):
    _use_fake_http(monkeypatch)
    cfg = {
        "connector_secrets": {"n8n_webhook_url": "https://hook.test/route"},
        "outputs": [{
            "id": "r1", "name": "Work to n8n", "enabled": True,
            "match": {"tags": ["work"]},
            "connector": connectors.get_preset("n8n-webhook")["connector"],
        }],
    }
    monkeypatch.setattr(app, "read_config", lambda: cfg)
    skill = {"id": "quick-note", "actions": []}     # skill itself has no actions
    job = {"id": "jr", "title": "T", "transcript": ""}
    result = {"summary": "s", "action_items": [], "tags": ["work"], "fields": {}, "skill_id": "quick-note"}
    asyncio.run(app.run_actions(skill, job, result))
    assert len(_FakeClient.calls) == 1                       # the routed rule fired
    assert _FakeClient.calls[0]["url"] == "https://hook.test/route"
    assert any(d["status"] == "sent" for d in store.list_deliveries(limit=5))


def test_run_actions_no_rules_no_egress(monkeypatch):
    _use_fake_http(monkeypatch)
    monkeypatch.setattr(app, "read_config", lambda: {})
    skill = {"id": "quick-note", "actions": []}
    asyncio.run(app.run_actions(skill, {"id": "jx"}, {"tags": ["work"], "summary": ""}))
    assert _FakeClient.calls == []                            # nothing configured -> nothing sent


# ── write_file: append + templated filename ──────────────────────────────────

def test_write_file_append_daily(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "read_config", lambda: {"vault_dir": str(tmp_path)})
    action = {"type": "write_file", "dir": "{vault}/Daily", "filename": "{{date}}.md",
              "mode": "append", "template": "## {{title}}\n{{summary}}"}
    for i in (1, 2):
        job = {"id": f"j{i}", "title": f"Note {i}", "created_at": "2026-06-28T09:00:00"}
        result = {"summary": f"sum {i}", "skill_id": "x", "tags": [], "action_items": [], "fields": {}}
        app._action_write_file(action, job, result)
    daily = tmp_path / "Daily" / "2026-06-28.md"
    text = daily.read_text(encoding="utf-8")
    assert "## Note 1" in text and "## Note 2" in text       # both appended to one file


def test_write_file_default_behaviour_unchanged(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "read_config", lambda: {"vault_dir": str(tmp_path)})
    action = {"type": "write_file", "dir": "{vault}/J"}
    job = {"id": "jd", "title": "My Day", "created_at": "2026-06-28T10:00:00", "transcript": "raw"}
    result = {"skill_id": "journal", "format": "markdown", "summary": "Good day.",
              "body": "Good day.", "action_items": [], "tags": [], "fields": {}}
    app._action_write_file(action, job, result)
    files = list((tmp_path / "J").glob("*.md"))
    assert len(files) == 1 and "# My Day" in files[0].read_text(encoding="utf-8")


# ── Delivery outbox + retry ──────────────────────────────────────────────────

def test_failed_delivery_then_retry_succeeds(monkeypatch):
    monkeypatch.setattr(app, "read_config",
                        lambda: {"connector_secrets": {"n8n_webhook_url": "https://hook.test/retry"}})
    conn = connectors.get_preset("n8n-webhook")["connector"]
    job = {"id": "jretry", "title": "T", "transcript": ""}
    result = {"summary": "s", "action_items": [], "tags": [], "fields": {}, "skill_id": "quick-note"}
    # First attempt fails (destination "down").
    _use_fake_http(monkeypatch, status=500)
    asyncio.run(app._run_one_action("test", conn, job, result))
    failed = [d for d in store.list_deliveries(status="failed") if d["job_id"] == "jretry"]
    assert failed, "a failed delivery should be recorded"
    did = failed[0]["id"]
    # Destination recovers; retry the same delivery.
    _use_fake_http(monkeypatch, status=200)
    asyncio.run(app.retry_delivery(did))
    assert store.get_delivery(did)["status"] == "sent"
    assert store.get_delivery(did)["attempts"] == 2


# ── API (loopback control plane) ─────────────────────────────────────────────

def test_api_list_connectors():
    body = client.get("/api/connectors").json()
    ids = {c["id"] for c in body["connectors"]}
    assert "slack-webhook" in ids
    assert "secrets" in body


def test_api_secrets_never_return_values():
    r = client.post("/api/connectors/secrets", json={"name": "demo_token", "value": "supersecret"})
    assert r.status_code == 200 and "demo_token" in r.json()["names"]
    listed = client.get("/api/connectors/secrets").json()
    assert "demo_token" in listed["names"]
    assert "supersecret" not in json.dumps(listed)          # value is never echoed
    # cleanup
    assert "demo_token" not in client.delete("/api/connectors/secrets/demo_token").json()["names"]


def test_api_outputs_crud():
    rule = {"name": "All to vault", "connector": {"type": "write_file", "dir": "{vault}"},
            "match": {"all": True}}
    created = client.post("/api/outputs", json=rule).json()["output"]
    rid = created["id"]
    assert any(o["id"] == rid for o in client.get("/api/outputs").json()["outputs"])
    assert client.delete(f"/api/outputs/{rid}").json()["status"] == "ok"
    assert not any(o["id"] == rid for o in client.get("/api/outputs").json()["outputs"])


def test_api_output_rejects_connector_without_type():
    assert client.post("/api/outputs", json={"name": "bad", "connector": {}}).status_code == 400


def test_api_connector_test_dryrun_redacts(monkeypatch):
    client.post("/api/connectors/secrets",
                json={"name": "slack_webhook_url", "value": "https://hooks.slack.com/SECRET"})
    try:
        conn = connectors.get_preset("slack-webhook")["connector"]
        r = client.post("/api/connectors/test",
                        json={"connector": conn, "text": "hello", "send": False})
        assert r.status_code == 200
        out = r.json()
        assert out["sent"] is False
        assert "SECRET" not in json.dumps(out)              # redacted in the dry-run echo
        assert "***" in out["rendered"]["url"]
    finally:
        client.delete("/api/connectors/secrets/slack_webhook_url")


def test_api_connectors_blocked_on_lan_port():
    c8766 = TestClient(app.app, base_url="https://testserver:8766")
    assert c8766.get("/api/connectors").status_code == 404
    assert c8766.get("/api/connectors/secrets").status_code == 404


# ── Phase 2: direct-API presets (config + secret), richer Test ───────────────

def test_notion_preset_needs_config_parsed():
    p = connectors.get_preset("notion-page")
    assert p["family"] == "api"
    assert p["needs_secrets"] == ["notion_token"]
    assert p["needs_config"][0]["key"] == "database_id"
    assert p["needs_config"][0]["label"]              # has a human label


def test_notion_connector_renders_config_and_secret():
    conn = {**connectors.get_preset("notion-page")["connector"], "config": {"database_id": "DB123"}}
    rendered = connectors.render_connector(
        conn, {"title": "Hello"}, {"summary": "A summary"}, {"notion_token": "tok_abc"})
    assert rendered["url"] == "https://api.notion.com/v1/pages"
    assert rendered["headers"]["Authorization"] == "Bearer tok_abc"
    assert rendered["headers"]["Notion-Version"] == "2022-06-28"
    assert rendered["body"]["parent"]["database_id"] == "DB123"
    assert rendered["body"]["properties"]["Name"]["title"][0]["text"]["content"] == "Hello"
    assert rendered["body"]["children"][0]["paragraph"]["rich_text"][0]["text"]["content"] == "A summary"


def test_todoist_connector_renders_tags_as_labels():
    conn = connectors.get_preset("todoist-task")["connector"]
    rendered = connectors.render_connector(
        conn, {"title": "Do X"}, {"summary": "desc", "tags": ["work", "errand"]}, {"todoist_token": "tdt"})
    assert rendered["url"].endswith("/rest/v2/tasks")
    assert rendered["headers"]["Authorization"] == "Bearer tdt"
    assert rendered["body"]["content"] == "Do X"
    assert rendered["body"]["labels"] == ["work", "errand"]      # whole-token kept the list


def test_api_connectors_exposes_needs_config():
    body = client.get("/api/connectors").json()
    notion = next(c for c in body["connectors"] if c["id"] == "notion-page")
    assert notion["needs_config"][0]["key"] == "database_id"


def test_api_connector_test_send_returns_response(monkeypatch):
    client.post("/api/connectors/secrets", json={"name": "todoist_token", "value": "tdt"})
    try:
        _use_fake_http(monkeypatch, status=200, body_text='{"id":"99","content":"Do X"}')
        conn = connectors.get_preset("todoist-task")["connector"]
        r = client.post("/api/connectors/test", json={"connector": conn, "text": "Do X", "send": True})
        assert r.status_code == 200
        d = r.json()
        assert d["sent"] is True and d["ok"] is True and d["status_code"] == 200
        assert "Do X" in d["response"]                          # destination response surfaced
        assert "tdt" not in json.dumps(d["rendered"])           # token still redacted in the echo
    finally:
        client.delete("/api/connectors/secrets/todoist_token")


# ── Phase 3: email (SMTP) + calendar (.ics) ──────────────────────────────────

def test_email_preset_and_render():
    p = connectors.get_preset("email-smtp")
    assert p["family"] == "email" and p["needs_secrets"] == ["smtp_password"]
    assert {c["key"] for c in p["needs_config"]} == {"smtp_host", "smtp_username", "from_addr", "to_addr"}


def test_email_action_starttls(monkeypatch):
    monkeypatch.setattr(app, "read_config", lambda: {"connector_secrets": {"smtp_password": "pw"}})
    monkeypatch.setattr(app.smtplib, "SMTP", _FakeSMTP)
    conn = {**connectors.get_preset("email-smtp")["connector"],
            "config": {"smtp_host": "smtp.example.com", "smtp_username": "u@example.com",
                       "from_addr": "u@example.com", "to_addr": "dest@example.com"}}
    job = {"id": "je", "title": "Hello world", "transcript": "raw", "created_at": "2026-07-01T09:00:00"}
    result = {"summary": "a summary", "tags": ["x"], "action_items": [], "fields": {}, "skill_id": "q"}
    app._action_email(conn, job, result)
    s = _FakeSMTP.last
    assert s.host == "smtp.example.com" and s.port == 587 and s.tls is True
    assert s.logged == ("u@example.com", "pw")               # password injected from secret
    msg = s.sent[0]
    assert msg["To"] == "dest@example.com" and msg["From"] == "u@example.com"
    assert msg["Subject"] == "Note: Hello world"
    assert "a summary" in msg.get_content()


def test_email_missing_host_raises(monkeypatch):
    monkeypatch.setattr(app, "read_config", lambda: {})
    try:
        app._action_email({"type": "email", "to": "a@x", "from": "b@x"}, {"id": "j"}, {"summary": ""})
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_ics_file_writes_escaped_event(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "read_config", lambda: {"vault_dir": str(tmp_path)})
    conn = connectors.get_preset("ics-export")["connector"]
    job = {"id": "jics", "title": "Doctor, appt; today", "created_at": "2026-07-01T09:00:00"}
    result = {"summary": "Bring the X-ray\nand papers", "tags": [], "action_items": [], "fields": {}, "skill_id": "x"}
    app._action_ics_file(conn, job, result)
    files = list((tmp_path / "Calendar").glob("*.ics"))
    assert len(files) == 1
    t = files[0].read_text(encoding="utf-8")
    assert "BEGIN:VEVENT" in t and "END:VCALENDAR" in t
    assert "DTSTART;VALUE=DATE:20260701" in t
    assert "SUMMARY:Doctor\\, appt\\; today" in t            # commas/semicolons escaped
    assert "DESCRIPTION:Bring the X-ray\\nand papers" in t   # newline escaped


def test_api_connectors_has_email_and_calendar():
    ids = {c["id"] for c in client.get("/api/connectors").json()["connectors"]}
    assert {"email-smtp", "ics-export"} <= ids


def test_api_connector_test_send_surfaces_destination_error(monkeypatch):
    client.post("/api/connectors/secrets", json={"name": "todoist_token", "value": "tdt"})
    try:
        _use_fake_http(monkeypatch, status=401, body_text='{"error":"unauthorized"}')
        conn = connectors.get_preset("todoist-task")["connector"]
        d = client.post("/api/connectors/test", json={"connector": conn, "send": True}).json()
        assert d["sent"] is True and d["ok"] is False and d["status_code"] == 401
        assert "unauthorized" in d["response"]
    finally:
        client.delete("/api/connectors/secrets/todoist_token")
