"""Skill registry — pluggable LLM post-processing recipes.

A *skill* decides what happens to a transcript: the prompt the LLM runs, the
shape of the output it produces, and the downstream actions taken with the
result. Skills are plain Markdown files with a YAML frontmatter header:

    ---
    id: meeting
    name: Meeting notes
    description: ...
    when_to_use: Use when the note captures a meeting or call with decisions.
    output:
      format: json            # json | markdown
      fields: [{key: summary, type: text}, {key: decisions, type: list}]
    actions:
      - {type: write_file, dir: "{vault}/Meetings"}
    enabled: true
    ---
    <the prompt body>

This module is **pure registry + pure helpers** — it only touches the filesystem
(via paths.py) and never imports app.py, so there's no import cycle. The LLM calls
and action execution live in app.py, which feeds this module's prompt/normalize
helpers.

Layering: bundled defaults ship read-only in ``paths.BUNDLED_SKILLS_DIR``; the
user's own / edited skills live writable in ``paths.SKILLS_DIR`` and override a
bundled skill with the same ``id``. Only the writable copy is ever mutated.
"""
import json
import os
import re
import tempfile

import yaml

import paths

QUICK_NOTE_ID = "quick-note"
WELL_KNOWN_KEYS = ("summary", "action_items", "tags")

# In-code fallback so the pipeline still works if the skills dirs are empty or
# unreadable. Mirrors the app's original hardcoded behaviour exactly.
_FALLBACK_SKILL = {
    "id": QUICK_NOTE_ID,
    "name": "Quick note",
    "description": "General voice note — a short summary, action items and tags.",
    "when_to_use": "The default. Use for any note that doesn't clearly fit a more specific skill.",
    "output": {"format": "json", "fields": [
        {"key": "summary", "type": "text"},
        {"key": "action_items", "type": "list"},
        {"key": "tags", "type": "list"},
    ]},
    "actions": [],
    "enabled": True,
    "prompt": (
        "You are a voice-note assistant. Read the transcript and return a JSON object.\n"
        "Detect the input language (Danish/Swedish/English) and write the summary and "
        "action items in that SAME language.\n\n"
        "Return ONLY valid JSON with exactly these keys:\n"
        '- "summary": a 2-4 sentence summary of the note\n'
        '- "action_items": an array of short actionable task strings (empty array if none)\n'
        '- "tags": an array of 3-5 lowercase keyword tags\n\n'
        "Known projects (for tagging context): {projects}\n"
    ),
}


# ── id / filename helpers ─────────────────────────────────────────────────────

def slugify(text: str) -> str:
    """A filesystem- and url-safe id from a name (lowercase, dash-separated)."""
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").strip().lower()).strip("-")
    return s or "skill"


def _skill_path(skill_id: str) -> str:
    return os.path.join(paths.SKILLS_DIR, f"{slugify(skill_id)}.md")


# ── parse / serialize ─────────────────────────────────────────────────────────

def parse_skill(text: str, fallback_id: str = "") -> dict:
    """Parse a skill file (YAML frontmatter + prompt body) into a dict. A file
    with no frontmatter is treated as a bare prompt with sensible defaults."""
    meta: dict = {}
    body = text
    if text.lstrip().startswith("---"):
        # Split on the first two '---' fences.
        stripped = text.lstrip()
        parts = stripped.split("---", 2)
        if len(parts) == 3:
            try:
                meta = yaml.safe_load(parts[1]) or {}
            except yaml.YAMLError:
                meta = {}
            if not isinstance(meta, dict):
                meta = {}
            body = parts[2]
    output = meta.get("output") or {}
    if not isinstance(output, dict):
        output = {}
    fmt = (output.get("format") or "json").lower()
    if fmt not in ("json", "markdown"):
        fmt = "json"
    actions = meta.get("actions") or []
    if not isinstance(actions, list):
        actions = []
    skill_id = slugify(meta.get("id") or fallback_id or meta.get("name") or "skill")
    return {
        "id": skill_id,
        "name": meta.get("name") or skill_id,
        "description": meta.get("description") or "",
        "when_to_use": meta.get("when_to_use") or "",
        "output": {"format": fmt, "fields": output.get("fields") or []},
        "actions": actions,
        "enabled": meta.get("enabled", True) is not False,
        "prompt": body.strip(),
    }


def serialize_skill(skill: dict) -> str:
    """Render a skill dict back to a Markdown file (frontmatter + prompt body)."""
    meta = {
        "id": skill["id"],
        "name": skill.get("name") or skill["id"],
        "description": skill.get("description") or "",
        "when_to_use": skill.get("when_to_use") or "",
        "output": {
            "format": (skill.get("output") or {}).get("format", "json"),
            "fields": (skill.get("output") or {}).get("fields", []),
        },
        "actions": skill.get("actions") or [],
        "enabled": bool(skill.get("enabled", True)),
    }
    front = yaml.safe_dump(meta, sort_keys=False, allow_unicode=True, default_flow_style=False)
    return f"---\n{front}---\n\n{(skill.get('prompt') or '').strip()}\n"


# ── load / merge ──────────────────────────────────────────────────────────────

def _load_dir(directory: str, source: str) -> dict:
    out: dict = {}
    try:
        names = os.listdir(directory)
    except OSError:
        return out
    for name in sorted(names):
        if not name.endswith(".md") or name.startswith("."):
            continue
        path = os.path.join(directory, name)
        try:
            with open(path, "r", encoding="utf-8") as f:
                skill = parse_skill(f.read(), fallback_id=os.path.splitext(name)[0])
        except OSError:
            continue
        skill["source"] = source
        out[skill["id"]] = skill
    return out


def load_skills() -> list[dict]:
    """All skills, bundled defaults overlaid by the user's writable copies.

    Each returned skill carries ``source`` ('bundled' or 'user'), ``builtin``
    (a bundled default with this id exists) and ``overridden`` (a user copy
    shadows a bundled default). Sorted with the default quick-note first, then
    by name."""
    bundled = _load_dir(paths.BUNDLED_SKILLS_DIR, "bundled")
    user = _load_dir(paths.SKILLS_DIR, "user")
    merged: dict = dict(bundled)
    for sid, skill in user.items():
        skill["overridden"] = sid in bundled
        merged[sid] = skill
    for sid, skill in merged.items():
        skill["builtin"] = sid in bundled
        skill.setdefault("overridden", False)
    if QUICK_NOTE_ID not in merged:
        fallback = dict(_FALLBACK_SKILL, source="builtin", builtin=True, overridden=False)
        merged[QUICK_NOTE_ID] = fallback
    skills = list(merged.values())
    skills.sort(key=lambda s: (s["id"] != QUICK_NOTE_ID, s.get("name", "").lower()))
    return skills


def skills_by_id() -> dict:
    return {s["id"]: s for s in load_skills()}


def get_skill(skill_id: str) -> dict | None:
    return skills_by_id().get(skill_id)


def default_skill(default_id: str | None = None) -> dict:
    """The configured default skill, falling back to quick-note / first enabled."""
    by_id = skills_by_id()
    if default_id and default_id in by_id:
        return by_id[default_id]
    if QUICK_NOTE_ID in by_id:
        return by_id[QUICK_NOTE_ID]
    enabled = [s for s in by_id.values() if s.get("enabled", True)]
    return enabled[0] if enabled else dict(_FALLBACK_SKILL)


# ── CRUD (writes only ever land in the writable SKILLS_DIR) ───────────────────

def save_skill(skill: dict) -> dict:
    """Create or update a user skill. Returns the normalized, reloaded skill."""
    skill = parse_skill(serialize_skill({
        "id": slugify(skill.get("id") or skill.get("name") or "skill"),
        "name": skill.get("name") or "",
        "description": skill.get("description") or "",
        "when_to_use": skill.get("when_to_use") or "",
        "output": skill.get("output") or {},
        "actions": skill.get("actions") or [],
        "enabled": skill.get("enabled", True),
        "prompt": skill.get("prompt") or "",
    }))
    text = serialize_skill(skill)
    path = _skill_path(skill["id"])
    os.makedirs(paths.SKILLS_DIR, exist_ok=True)
    # Atomic write (temp file + os.replace) so a crash can't truncate the file.
    fd, tmp = tempfile.mkstemp(dir=paths.SKILLS_DIR, prefix=".tmp_", suffix=".md")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            try:
                os.unlink(tmp)
            except OSError:
                pass
        raise
    return get_skill(skill["id"]) or skill


def delete_skill(skill_id: str) -> bool:
    """Delete the user's copy. A bundled default with the same id reverts to the
    shipped version; a purely user-made skill disappears. Returns True if a file
    was removed."""
    path = _skill_path(skill_id)
    if os.path.isfile(path):
        try:
            os.unlink(path)
            return True
        except OSError:
            return False
    return False


# ── prompt construction + output normalization (pure) ─────────────────────────

def build_skill_prompt(skill: dict, source_text: str, project_ids: str = "none",
                       has_images: bool = False) -> str:
    """Compose the final prompt: the skill body (with {projects} substituted),
    then the transcript. The body carries all the skill-specific instructions.
    When images are attached, tell the model how to treat them — especially for
    an image-only note, where the image IS the content."""
    body = (skill.get("prompt") or "").replace("{projects}", project_ids)
    parts = [body.strip()]
    if has_images:
        if source_text.strip():
            parts.append("One or more images are attached to this note — use them as additional context.")
        else:
            parts.append("This note has no text; its content is the attached image(s). "
                         "Describe and analyze the image(s) as the note.")
    parts.append(f"Transcript:\n{source_text}\n")
    return "\n\n".join(parts)


def _coerce_list(value) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        items = value
    else:
        items = [value]
    return [str(v).strip() for v in items if str(v).strip()]


def normalize_output(skill: dict, raw_response: str) -> dict:
    """Turn a raw model response into the canonical result shape the rest of the
    app stores and serves:
        {skill_id, format, summary, action_items, tags, fields, body}
    - json skills: parse JSON; well-known keys map to columns, the full object is
      kept in ``fields``. Non-JSON degrades to empty fields (never raises).
    - markdown skills: the whole response is the ``body``; summary is a preview."""
    fmt = (skill.get("output") or {}).get("format", "json")
    result = {
        "skill_id": skill["id"],
        "format": fmt,
        "summary": "",
        "action_items": [],
        "tags": [],
        "fields": {},
        "body": "",
    }
    raw = raw_response or ""
    if fmt == "markdown":
        body = raw.strip()
        result["body"] = body
        result["summary"] = _preview(body)
        return result
    # json
    try:
        data = json.loads(raw)
        if not isinstance(data, dict):
            data = {}
    except (ValueError, TypeError):
        data = {}
    result["fields"] = data
    result["summary"] = str(data.get("summary") or "").strip()
    actions = data.get("action_items")
    if actions is None:
        actions = data.get("actions")
    result["action_items"] = _coerce_list(actions)
    result["tags"] = [t.lstrip("#").strip() for t in _coerce_list(data.get("tags"))]
    # If a json skill omitted a summary, derive one so the dashboard list isn't blank.
    if not result["summary"]:
        result["summary"] = _preview(_fields_preview(data))
    return result


def _preview(text: str, limit: int = 280) -> str:
    text = (text or "").strip()
    return text[:limit].strip()


def _fields_preview(data: dict) -> str:
    """A readable one-liner from arbitrary json fields (used when there's no
    explicit summary), e.g. 'decisions: a, b · attendees: x'."""
    parts = []
    for k, v in data.items():
        if k in ("summary",):
            continue
        if isinstance(v, list):
            v = ", ".join(str(x) for x in v)
        v = str(v).strip()
        if v:
            parts.append(f"{k}: {v}")
    return " · ".join(parts)


# ── action template rendering (pure) ──────────────────────────────────────────

def render_markdown_note(job: dict, result: dict) -> str:
    """A standard Markdown rendering of a processed note, used by the write_file
    action. Includes the custom skill output (body or fields) plus the transcript."""
    title = job.get("title") or "Untitled note"
    created = job.get("created_at") or ""
    lines = [f"# {title}", ""]
    meta = []
    if created:
        meta.append(f"- **Date:** {created}")
    if result.get("skill_id"):
        meta.append(f"- **Skill:** {result['skill_id']}")
    if job.get("language"):
        meta.append(f"- **Language:** {job['language']}")
    if meta:
        lines += meta + [""]
    if result.get("summary"):
        lines += ["## Summary", "", result["summary"], ""]
    if result.get("body"):
        lines += [result["body"], ""]
    fields = result.get("fields") or {}
    for key, value in fields.items():
        if key in ("summary",):
            continue
        lines.append(f"## {key.replace('_', ' ').title()}")
        lines.append("")
        if isinstance(value, list):
            lines += [f"- {item}" for item in value] or ["_(none)_"]
        else:
            lines.append(str(value))
        lines.append("")
    if result.get("action_items"):
        lines += ["## Action items", ""]
        lines += [f"- [ ] {a}" for a in result["action_items"]]
        lines.append("")
    if result.get("tags"):
        lines += ["**Tags:** " + ", ".join(f"#{t}" for t in result["tags"]), ""]
    transcript = job.get("transcript") or ""
    if transcript:
        lines += ["---", "", "## Transcript", "", transcript, ""]
    return "\n".join(lines)
