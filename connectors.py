"""Output connectors — pluggable destinations a processed note can be piped to.

Faster Notes captures + processes a note locally; *connectors* are the opt-in
"output side" that pushes the tool-agnostic result onward into whatever software
the user already lives in (PKM apps, automation hubs, task apps, email/calendar).

A *connector preset* is a plain YAML file describing one ready-made destination:

    id: slack-webhook
    name: Slack (Incoming Webhook)
    description: Post a note summary to a Slack channel.
    family: webhook                 # local | webhook | api | email  (UI grouping)
    needs_secrets: [slack_webhook_url]
    connector:                      # the action the engine runs (type + config)
      type: http_request
      method: POST
      url: "{{secret.slack_webhook_url}}"
      body_type: json
      body:
        text: "*{{title}}*\n{{summary}}\n\nTags: {{tags | join: \", \"}}"

This module is **pure registry + pure helpers** — it only touches the filesystem
(via paths.py) and never imports app.py, so there's no import cycle. The actual
sending (HTTP, file writes, SMTP) and the delivery outbox live in app.py, which
feeds this module's rendering helpers.

Layering mirrors skills.py: bundled defaults ship read-only in
``paths.BUNDLED_CONNECTORS_DIR``; the user's own / edited presets live writable in
``paths.CONNECTORS_DIR`` and override a bundled preset with the same ``id``.

Privacy: presets reference credentials **by name** (``{{secret.NAME}}``), never by
value — so a preset file is always safe to ship/share. Secret values live only in
the loopback-only config (see app.py) and are injected at send time.
"""
import json
import re

import yaml

import paths
import skills  # reuse slugify / _load_dir-style helpers' conventions

# Action types the engine knows how to run. Kept here so the registry can validate
# presets without importing app.py (which owns the actual implementations).
KNOWN_TYPES = ("http_request", "write_file", "webhook", "append_project", "email", "ics_file")


# ── parse / load (mirrors skills.py layering) ─────────────────────────────────

def parse_connector(text: str, fallback_id: str = "") -> dict:
    """Parse a connector preset YAML file into a normalized dict."""
    try:
        meta = yaml.safe_load(text) or {}
    except yaml.YAMLError:
        meta = {}
    if not isinstance(meta, dict):
        meta = {}
    return normalize_preset(meta, fallback_id)


def normalize_preset(meta: dict, fallback_id: str = "") -> dict:
    cid = skills.slugify(meta.get("id") or fallback_id or meta.get("name") or "connector")
    connector = meta.get("connector")
    if not isinstance(connector, dict):
        connector = {}
    needs = meta.get("needs_secrets") or []
    if not isinstance(needs, list):
        needs = []
    # Non-secret, per-target values the user must fill (e.g. a Notion database id),
    # referenced in the connector as {{config.KEY}}. Each item: {key, label, placeholder}.
    cfg_fields = meta.get("needs_config") or []
    if not isinstance(cfg_fields, list):
        cfg_fields = []
    norm_cfg = []
    for c in cfg_fields:
        if isinstance(c, dict) and c.get("key"):
            norm_cfg.append({"key": str(c["key"]), "label": c.get("label") or str(c["key"]),
                             "placeholder": c.get("placeholder") or ""})
        elif isinstance(c, str):
            norm_cfg.append({"key": c, "label": c, "placeholder": ""})
    return {
        "id": cid,
        "name": meta.get("name") or cid,
        "description": meta.get("description") or "",
        "family": (meta.get("family") or "webhook"),
        "needs_secrets": [str(s) for s in needs],
        "needs_config": norm_cfg,
        "connector": connector,
        "enabled": meta.get("enabled", True) is not False,
    }


def _load_dir(directory: str, source: str) -> dict:
    import os
    out: dict = {}
    try:
        names = os.listdir(directory)
    except OSError:
        return out
    for name in sorted(names):
        if not (name.endswith(".yaml") or name.endswith(".yml")) or name.startswith("."):
            continue
        path = os.path.join(directory, name)
        try:
            with open(path, "r", encoding="utf-8") as f:
                preset = parse_connector(f.read(), fallback_id=os.path.splitext(name)[0])
        except OSError:
            continue
        preset["source"] = source
        out[preset["id"]] = preset
    return out


def load_presets() -> list[dict]:
    """All connector presets, bundled defaults overlaid by the user's writable copies.

    Each carries ``source`` ('bundled'|'user'), ``builtin`` (a bundled default with
    this id exists) and ``overridden`` (a user copy shadows a bundled default)."""
    bundled = _load_dir(paths.BUNDLED_CONNECTORS_DIR, "bundled")
    user = _load_dir(paths.CONNECTORS_DIR, "user")
    merged: dict = dict(bundled)
    for cid, preset in user.items():
        preset["overridden"] = cid in bundled
        merged[cid] = preset
    for cid, preset in merged.items():
        preset["builtin"] = cid in bundled
        preset.setdefault("overridden", False)
    presets = list(merged.values())
    presets.sort(key=lambda p: (p.get("family", ""), p.get("name", "").lower()))
    return presets


def presets_by_id() -> dict:
    return {p["id"]: p for p in load_presets()}


def get_preset(preset_id: str) -> dict | None:
    return presets_by_id().get(preset_id)


# ── template rendering (pure) ─────────────────────────────────────────────────

_TOKEN = re.compile(r"\{\{\s*(.*?)\s*\}\}")
_WHOLE_TOKEN = re.compile(r"^\s*\{\{\s*(.*?)\s*\}\}\s*$")


def build_context(job: dict, result: dict, secrets: dict | None = None,
                  config: dict | None = None) -> dict:
    """The variables available to a connector template, from a (job, result) pair.
    Mirrors the payload shape app._action_webhook already assembles. ``config`` holds
    the per-target values the user filled in (e.g. a Notion database id)."""
    return {
        "id": job.get("id"),
        "title": job.get("title") or "Untitled note",
        "transcript": job.get("transcript") or "",
        "language": job.get("language") or "",
        "date": (job.get("created_at") or "")[:10],
        "created_at": job.get("created_at") or "",
        "skill_id": result.get("skill_id"),
        "summary": result.get("summary") or "",
        "action_items": result.get("action_items") or [],
        "tags": result.get("tags") or [],
        "body": result.get("body") or "",
        "fields": result.get("fields") or {},
        "secret": dict(secrets or {}),
        "config": dict(config or {}),
    }


def _resolve_path(path: str, ctx: dict):
    cur = ctx
    for part in path.split("."):
        part = part.strip()
        if isinstance(cur, dict):
            cur = cur.get(part)
        else:
            return None
        if cur is None:
            return None
    return cur


def _apply_filter(spec: str, value):
    spec = spec.strip()
    name, _, arg = spec.partition(":")
    name = name.strip()
    arg = arg.strip().strip('"').strip("'")
    if name == "join":
        sep = arg if arg else ", "
        if isinstance(value, (list, tuple)):
            return sep.join(str(v) for v in value)
        return "" if value is None else str(value)
    if name == "upper":
        return str(value or "").upper()
    if name == "lower":
        return str(value or "").lower()
    return value


def _eval(expr: str, ctx: dict):
    parts = expr.split("|")
    value = _resolve_path(parts[0].strip(), ctx)
    for f in parts[1:]:
        value = _apply_filter(f, value)
    return value


def _stringify(value) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        return ", ".join(str(v) for v in value)
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def render(value, ctx: dict):
    """Recursively render a template structure (str / dict / list) against ctx.

    A string that is *exactly* one ``{{ expr }}`` keeps the resolved value's native
    type (a list stays a list — handy for JSON bodies like ``"{{action_items}}"``).
    A token embedded in a larger string is stringified (lists join with ", ")."""
    if isinstance(value, str):
        whole = _WHOLE_TOKEN.match(value)
        if whole:
            return _eval(whole.group(1), ctx)
        return _TOKEN.sub(lambda m: _stringify(_eval(m.group(1), ctx)), value)
    if isinstance(value, dict):
        return {k: render(v, ctx) for k, v in value.items()}
    if isinstance(value, list):
        return [render(v, ctx) for v in value]
    return value


def render_connector(connector: dict, job: dict, result: dict, secrets: dict | None = None) -> dict:
    """Render a connector action spec (type + config) into a concrete request. The
    connector's own ``config`` map (user-filled per-target values) is exposed to the
    template as ``{{config.KEY}}``."""
    ctx = build_context(job, result, secrets, connector.get("config"))
    rendered = render(connector, ctx)
    if not isinstance(rendered, dict):
        rendered = {}
    return rendered


# ── secret redaction (so values never reach logs / API responses) ─────────────

def redact(obj, secrets: dict | None):
    """Replace any secret VALUE found in obj with '***' (deep). Used before a
    rendered request is logged or echoed to the dashboard."""
    values = [str(v) for v in (secrets or {}).values() if v]
    if not values:
        return obj
    def _scrub(x):
        if isinstance(x, str):
            for v in values:
                if v:
                    x = x.replace(v, "***")
            return x
        if isinstance(x, dict):
            return {k: _scrub(v) for k, v in x.items()}
        if isinstance(x, list):
            return [_scrub(v) for v in x]
        return x
    return _scrub(obj)


# ── routing match (pure) ──────────────────────────────────────────────────────

def rule_matches(match: dict, tags: list, skill_id: str | None) -> bool:
    """Does an output rule's ``match`` apply to a note?

    Zero-egress by default: a rule only fires when it explicitly matches —
    ``{all: true}``, an overlapping tag, or a matching skill. An empty/ambiguous
    match never fires."""
    if not isinstance(match, dict):
        return False
    if match.get("all"):
        return True
    want_tags = [str(t).lower() for t in (match.get("tags") or [])]
    have_tags = [str(t).lower() for t in (tags or [])]
    if want_tags and any(t in have_tags for t in want_tags):
        return True
    want_skills = [str(s) for s in (match.get("skills") or [])]
    if want_skills and skill_id in want_skills:
        return True
    return False


def describe_target(connector: dict) -> str:
    """A short, secret-free label for the delivery log (the destination host/path)."""
    typ = (connector or {}).get("type") or "?"
    if typ in ("http_request", "webhook"):
        url = str(connector.get("url") or "")
        # strip a rendered secret URL down to scheme+host so the log stays useful
        m = re.match(r"(https?://[^/]+)", url)
        return f"{typ} {m.group(1) if m else url[:40]}"
    if typ == "write_file":
        return f"write_file {connector.get('dir') or '{vault}'}"
    if typ == "email":
        return f"email → {connector.get('to') or ''}"
    if typ == "ics_file":
        return f"ics_file {connector.get('dir') or '{vault}/Calendar'}"
    if typ == "append_project":
        return f"append_project {connector.get('project') or ''}"
    return typ
