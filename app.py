import asyncio
import base64
import os
import json
import logging
import re
import socket
import secrets
import tempfile
import threading
import uuid
import httpx
from datetime import datetime
from contextlib import asynccontextmanager

log = logging.getLogger("faster_notes")

from fastapi import FastAPI, File, UploadFile, HTTPException, Form, Header, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import paths
import store
import skills


def _load_dotenv() -> None:
    """Minimal .env loader (KEY=VALUE per line) so secrets like
    CLOUDFLARE_API_TOKEN can live in a local .env during dev — no python-dotenv
    dependency. Real environment variables win (setdefault). Looks for .env next
    to the source (dev), in the writable data dir (installed app), then the CWD."""
    for d in (os.path.dirname(os.path.abspath(__file__)), paths.DATA_DIR, os.getcwd()):
        path = os.path.join(d, ".env")
        if not os.path.isfile(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                for raw in f:
                    line = raw.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, _, val = line.partition("=")
                    os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))
        except OSError:
            pass
        return  # first .env found wins


_load_dotenv()

# Writable runtime data + read-only assets all resolve through paths.py so the
# app behaves correctly whether run from source or as an installed/frozen app.
NOTES_FILE    = paths.NOTES_FILE
SKILL_FILE    = paths.SKILL_FILE
PROJECTS_FILE = paths.PROJECTS_FILE
CERT_FILE     = paths.CERT_FILE
CONFIG_FILE   = paths.CONFIG_FILE
UPLOAD_DIR    = paths.UPLOADS_DIR
MEDIA_DIR     = paths.MEDIA_DIR
ACTIVITY_FILE = paths.ACTIVITY_FILE
HTTPS_PORT    = 8766
HTTP_PORT     = 8765
OLLAMA_BASE   = "http://localhost:11434"
ACTIVITY_MAX  = 100

WHISPER_MODELS = [
    {"id": "tiny",            "name": "Tiny",            "size": "~39 MB",  "note": "Fastest, rough accuracy"},
    {"id": "base",            "name": "Base",            "size": "~74 MB",  "note": "Fast, decent accuracy"},
    {"id": "small",           "name": "Small",           "size": "~244 MB", "note": "Balanced (default)"},
    {"id": "medium",          "name": "Medium",          "size": "~769 MB", "note": "High accuracy, slower"},
    {"id": "large-v3-turbo",  "name": "Large v3 Turbo",  "size": "~809 MB", "note": "High accuracy + fast"},
    {"id": "large-v3",        "name": "Large v3",        "size": "~1.5 GB", "note": "Best accuracy, slowest"},
    {"id": "Necklace/faster-nb-whisper-large", "name": "NB-Whisper Large (Nordic)",
     "size": "~1.5 GB", "note": "Tuned for Danish / Norwegian / Swedish"},
]

whisper_model = None
mdns_hostname = None  # set by runner.py when mDNS advertising succeeds


def _atomic_write_json(path: str, data) -> None:
    """Write JSON atomically (temp file in the same dir + os.replace) so a crash
    or a concurrent finisher can never leave a half-written/corrupt file."""
    d = os.path.dirname(path) or "."
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".tmp_", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            try:
                os.unlink(tmp)
            except OSError:
                pass
        raise


def read_config() -> dict:
    if not os.path.exists(CONFIG_FILE):
        return {"whisper_model": "small"}
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError as exc:
            # The file exists but is corrupt. Fail loudly instead of returning the
            # default — that path would let ensure_api_key() mint a NEW key and
            # silently break every paired phone. Surfacing the error preserves the
            # on-disk config so the user can fix it.
            raise RuntimeError(
                f"Config file is corrupt: {CONFIG_FILE} ({exc}). "
                "Fix or remove it; the existing api_key is preserved on disk."
            ) from exc


def write_config(cfg: dict) -> None:
    _atomic_write_json(CONFIG_FILE, cfg)


def cf_api_token() -> str | None:
    """The Cloudflare API token used to provision the tunnel — from config
    (dashboard 'Save token') or the CLOUDFLARE_API_TOKEN env var (.env)."""
    return read_config().get("cloudflare_api_token") or os.environ.get("CLOUDFLARE_API_TOKEN")


whisper_loaded_name = None  # which model is currently in memory
_whisper_lock = threading.Lock()


def load_whisper(model_name: str | None = None, force: bool = False):
    """Load (or switch) the Whisper model. Idempotent + thread-safe: two servers
    share one app, so concurrent startups must not reload the model twice."""
    global whisper_model, whisper_loaded_name
    from faster_whisper import WhisperModel
    if model_name is None:
        model_name = read_config().get("whisper_model", "small")
    with _whisper_lock:
        if whisper_model is not None and whisper_loaded_name == model_name and not force:
            return
        log.info("Loading Whisper model (%s)…", model_name)
        # download_root keeps the model cache inside the app's data dir.
        whisper_model = WhisperModel(
            model_name, device="cpu", compute_type="int8", download_root=paths.MODELS_DIR
        )
        whisper_loaded_name = model_name
        log.info("Whisper ready (%s).", model_name)


def ensure_api_key() -> str:
    """Generate a persistent pairing key on first run; reused thereafter."""
    cfg = read_config()
    if not cfg.get("api_key"):
        cfg["api_key"] = secrets.token_urlsafe(18)
        write_config(cfg)
    return cfg["api_key"]


def require_auth(authorization: str | None = Header(default=None)):
    """Bearer-token guard for the phone-PWA bridge endpoints."""
    key = read_config().get("api_key")
    if not key:
        return  # not configured yet — allow
    if authorization != f"Bearer {key}":
        raise HTTPException(status_code=401, detail="Invalid or missing API key")


async def _background_load_whisper():
    try:
        await asyncio.to_thread(load_whisper)
    except Exception as exc:
        log.error("Whisper load failed: %s", exc)


@asynccontextmanager
async def lifespan(app: FastAPI):
    ensure_api_key()
    store.init()  # open the DB + one-time import of any legacy activity.json
    # Load the model in the background so the dashboard is reachable immediately —
    # the first-run download can take a while. The dashboard shows `whisper_ready`.
    asyncio.create_task(_background_load_whisper())
    # Re-enqueue any uploads stranded on disk by a previous crash/restart.
    recover_orphan_jobs()
    yield


app = FastAPI(lifespan=lifespan)

# CORS: the main flow is same-origin (PWA + API both served from :8766), so CORS
# isn't load-bearing there. We still allow the origins a legit deployment can use
# — loopback, private-LAN IPs, fasternotes.local, and *.lovable.app — and
# default-deny everything else, so a random site the phone visits can't read
# bridge responses. (The bridge also requires a Bearer token; this is defense in
# depth.) allow_credentials stays False, so a wildcard would otherwise be wide open.
ALLOWED_ORIGIN_RE = (
    r"^https?://(?:"
    r"localhost|127\.0\.0\.1|\[::1\]|fasternotes\.local|"
    r"10\.\d{1,3}\.\d{1,3}\.\d{1,3}|"
    r"192\.168\.\d{1,3}\.\d{1,3}|"
    r"172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}"
    r")(?::\d+)?$"
    r"|^https://[a-z0-9-]+\.lovable\.app$"
)
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=ALLOWED_ORIGIN_RE,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Headers a reverse proxy / Cloudflare tunnel injects. Their presence means the
# request did NOT arrive directly on the loopback dashboard, so the control plane
# must stay hidden — even if the port somehow looks right.
_PROXY_HEADERS = ("x-forwarded-for", "x-forwarded-host", "x-forwarded-proto",
                  "cf-connecting-ip", "cf-ray")


@app.middleware("http")
async def restrict_control_plane(request: Request, call_next):
    """The /api/* control plane (dashboard) is loopback-only — it serves /api/info
    (which returns the pairing api_key) and flips server settings, so it must never
    be reachable from the LAN or through a tunnel/proxy.

    Fail-closed: allow /api/* ONLY for a direct request to the loopback dashboard
    port (HTTP_PORT 8765, which uvicorn binds to 127.0.0.1) with no proxy headers.
    A previous port-only check (`port == 8766 -> block`) was bypassable behind a
    reverse proxy: the public Host header drops the :8766 so request.url.port was
    None and the block missed — which leaked the api_key over a Cloudflare tunnel.
    Returns 404 (not 403) so the surface isn't even advertised."""
    if request.url.path.startswith("/api/"):
        forwarded = any(h in request.headers for h in _PROXY_HEADERS)
        if forwarded or request.url.port != HTTP_PORT:
            return JSONResponse({"detail": "Not Found"}, status_code=404)
    return await call_next(request)


# ── Notes persistence ────────────────────────────────────────────────────────

def read_notes() -> list:
    if not os.path.exists(NOTES_FILE):
        return []
    with open(NOTES_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def write_notes(notes: list) -> None:
    _atomic_write_json(NOTES_FILE, notes)


# ── Whisper ──────────────────────────────────────────────────────────────────

MIME_TO_EXT = {
    "audio/webm": ".webm",
    "audio/ogg": ".ogg",
    "audio/mp4": ".mp4",
    "audio/mpeg": ".mp3",
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
}

IMAGE_MIME_TO_EXT = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/heic": ".heic",
    "image/gif": ".gif",
}

# Reverse map for serving a saved recording back to the phone with a sane
# Content-Type (so <audio> can play it).
AUDIO_EXT_TO_MIME = {
    ".webm": "audio/webm", ".ogg": "audio/ogg", ".mp4": "audio/mp4",
    ".mp3": "audio/mpeg", ".wav": "audio/wav",
}


@app.post("/api/transcribe")
async def transcribe_audio(audio: UploadFile = File(...), language: str = Form("")):
    content = await audio.read()
    if not content:
        raise HTTPException(status_code=400, detail="Empty audio")

    base_type = (audio.content_type or "audio/webm").split(";")[0].strip()
    suffix = MIME_TO_EXT.get(base_type, ".webm")

    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
            f.write(content)
            tmp_path = f.name

        segments, info = whisper_model.transcribe(
            tmp_path, beam_size=5,
            vad_filter=True, condition_on_previous_text=False,
            language=language or None,
        )
        text = " ".join(seg.text.strip() for seg in segments).strip()
        return {"text": text, "language": info.language}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)


# ── Ollama ───────────────────────────────────────────────────────────────────

@app.get("/api/ollama/models")
async def ollama_models():
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{OLLAMA_BASE}/api/tags")
            r.raise_for_status()
            return r.json()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Ollama unreachable: {exc}")


@app.get("/api/ollama/running")
async def ollama_running():
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{OLLAMA_BASE}/api/ps")
            r.raise_for_status()
            return r.json()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Ollama unreachable: {exc}")


# ── Whisper model management ──────────────────────────────────────────────────

@app.get("/api/whisper/models")
async def get_whisper_models():
    cfg = read_config()
    return {"models": WHISPER_MODELS, "current": cfg.get("whisper_model", "small")}


class WhisperModelIn(BaseModel):
    model: str


@app.post("/api/whisper/model")
async def set_whisper_model(payload: WhisperModelIn):
    valid_ids = {m["id"] for m in WHISPER_MODELS}
    if payload.model not in valid_ids:
        raise HTTPException(status_code=400, detail=f"Unknown model: {payload.model}")
    try:
        await asyncio.to_thread(load_whisper, payload.model)
        cfg = read_config()
        cfg["whisper_model"] = payload.model
        write_config(cfg)
        return {"status": "ok", "model": payload.model}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


def read_projects() -> dict:
    # Prefer the writable copy; fall back to the bundled seed until the first edit
    # writes it out to the data dir (the bundle is read-only in a frozen install).
    path = PROJECTS_FILE if os.path.exists(PROJECTS_FILE) else paths.BUNDLED_PROJECTS_FILE
    if not os.path.exists(path):
        return {"projects": []}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def write_projects(data: dict) -> None:
    _atomic_write_json(PROJECTS_FILE, data)  # always the writable data-dir copy


def build_prompt(text: str) -> str:
    try:
        with open(SKILL_FILE, "r", encoding="utf-8") as f:
            skill = f.read()
    except FileNotFoundError:
        skill = "Clean and summarize the following voice note transcript. Return Markdown only.\n\nTranscript:\n"

    projects = read_projects()
    project_ids = "|".join(p["id"] for p in projects.get("projects", []))
    return skill.replace("{projects}", project_ids) + text


def get_local_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def get_local_ips() -> list[str]:
    """All private LAN IPv4 addresses, default-route one first. On a multi-homed
    PC (e.g. WiFi + Ethernet) the phone may only be able to reach one of them, so
    the dashboard lets the user pick which to advertise in the pairing QR."""
    primary = get_local_ip()
    ips: list[str] = []
    if primary and not primary.startswith("127."):
        ips.append(primary)
    try:
        for ip in socket.gethostbyname_ex(socket.gethostname())[2]:
            if (ip and ip not in ips and not ip.startswith("127.")
                    and not ip.startswith("169.254.")):
                ips.append(ip)
    except Exception:
        pass
    return ips or ["127.0.0.1"]


class AnalyseIn(BaseModel):
    text: str
    model: str
    note_id: str | None = None


@app.post("/api/analyse")
async def analyse(payload: AnalyseIn):
    prompt = build_prompt(payload.text)
    try:
        async with httpx.AsyncClient(timeout=300.0) as client:
            r = await client.post(
                f"{OLLAMA_BASE}/api/generate",
                json={"model": payload.model, "prompt": prompt, "stream": False},
            )
            r.raise_for_status()
            result = r.json()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc))

    analysis = result.get("response", "").strip()

    if payload.note_id:
        notes = read_notes()
        for note in notes:
            if note["id"] == payload.note_id:
                note["analysis"] = analysis
                note["analysis_model"] = payload.model
                note["analysed_at"] = datetime.now().isoformat()
                break
        write_notes(notes)

    return {"analysis": analysis}


# ── Notes CRUD ───────────────────────────────────────────────────────────────

@app.get("/api/notes")
async def get_notes():
    return read_notes()


class NoteIn(BaseModel):
    title: str
    text: str
    analysis: str | None = None
    analysis_model: str | None = None


@app.post("/api/notes")
async def create_note(payload: NoteIn):
    notes = read_notes()
    note = {
        "id": str(int(datetime.now().timestamp() * 1000)),
        "title": payload.title or f"Note — {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "text": payload.text,
        "analysis": payload.analysis,
        "analysis_model": payload.analysis_model,
        "created_at": datetime.now().isoformat(),
    }
    notes.insert(0, note)
    write_notes(notes)
    return note


@app.delete("/api/notes/{note_id}")
async def delete_note(note_id: str):
    notes = read_notes()
    updated = [n for n in notes if n["id"] != note_id]
    if len(updated) == len(notes):
        raise HTTPException(status_code=404, detail="Note not found")
    write_notes(updated)
    return {"status": "deleted"}


# ── Projects ─────────────────────────────────────────────────────────────────

@app.get("/api/projects")
async def get_projects():
    return read_projects()


# ── Server info (local IP for QR pairing) ────────────────────────────────────

@app.get("/api/info")
async def server_info():
    ip = get_local_ip()
    secure = os.path.exists(CERT_FILE)
    port = HTTPS_PORT if secure else HTTP_PORT
    scheme = "https" if secure else "http"
    api_key = ensure_api_key()
    cfg = read_config()
    # Public Cloudflare hostname (port 443), advertised for pairing when enabled so
    # the phone can pair to a stable internet address that works away from home.
    tunnel = None
    if cfg.get("cloudflare_enabled") and cfg.get("cloudflare_hostname"):
        tunnel = {"hostname": cfg["cloudflare_hostname"]}
    return {
        "ip": ip,
        "ips": get_local_ips(),       # all candidate addresses (multi-homed PCs)
        "hostname": mdns_hostname,    # stable mDNS name (survives IP changes) or null
        "tunnel": tunnel,             # {hostname} when remote access is on, else null
        "port": port,
        "scheme": scheme,
        "url": f"{scheme}://{ip}:{port}",
        "apiKey": api_key,
        "http_port": HTTP_PORT,
        "https_port": HTTPS_PORT,
        "https_available": secure,
        # Default (recommended) payload the phone PWA's QR scanner expects.
        "pairing": {"server": ip, "port": port, "apiKey": api_key, "secure": secure},
    }


# ── Phone PWA bridge (job queue: /status, /upload, /job, /result) ─────────────
#
# The Lovable PWA talks to these unprefixed endpoints with a Bearer token.
# Upload kicks off an async job: transcribe (Whisper) -> summarize (Ollama JSON).

JOBS: dict[str, dict] = {}

# Serialize transcription: faster-whisper's CTranslate2 model is one shared
# instance and isn't safe to call from several threads at once. Uploads still
# return {jobId} immediately and queue up behind this semaphore. One event loop
# is shared by both uvicorn servers, so a single module-level semaphore is enough.
_transcribe_sem = asyncio.Semaphore(1)


def read_activity() -> list:
    """Newest activity entries for the dashboard, backed by SQLite (store.py).
    Bounded for the list view; full history stays in the DB and is searchable."""
    return store.list_activity(limit=ACTIVITY_MAX)


def log_activity(entry: dict) -> None:
    """Persist a finished job to the SQLite activity store (atomic + searchable)."""
    store.add_activity(entry)


def transcribe_path(path: str, language: str | None = None) -> tuple[str, str]:
    # vad_filter strips silence (kills end-of-clip "subtitle credit" / "..." loop
    # hallucinations); condition_on_previous_text=False stops repetition cascades.
    # language: a forced code ("da"/"sv"/"en") when the user picked one; None lets
    # Whisper auto-detect (which often mislabels Danish/Swedish as Norwegian).
    segments, info = whisper_model.transcribe(
        path, beam_size=5,
        vad_filter=True, condition_on_previous_text=False,
        language=language or None,
    )
    text = " ".join(seg.text.strip() for seg in segments).strip()
    return text, info.language


async def pick_analysis_model() -> str | None:
    """Use the configured model, else the first model Ollama has installed."""
    cfg = read_config()
    if cfg.get("analysis_model"):
        return cfg["analysis_model"]
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{OLLAMA_BASE}/api/tags")
            r.raise_for_status()
            models = r.json().get("models", [])
            if models:
                return models[0]["name"]
    except Exception:
        pass
    return None


async def pick_orchestration_model() -> str | None:
    """Model that runs the skill router (classify_skill). A separate, usually
    smaller/faster model can be set for this cheap routing step; when unset it
    falls back to the analysis model."""
    cfg = read_config()
    if cfg.get("orchestration_model"):
        return cfg["orchestration_model"]
    return await pick_analysis_model()


def _project_ids() -> str:
    projects = read_projects()
    return ", ".join(p["id"] for p in projects.get("projects", [])) or "none"


# Long transcripts overflow a local model's context window and yield shallow
# summaries. Above MAP_REDUCE_CHARS we summarize chunk-by-chunk (map), then
# summarize the combined partials (reduce). Short notes keep the fast single shot.
MAP_REDUCE_CHARS = 6000
CHUNK_CHARS = 4000


def _chunk_text(text: str, size: int = CHUNK_CHARS) -> list[str]:
    """Split text into <=size pieces, breaking on whitespace so words stay whole."""
    chunks: list[str] = []
    i, n = 0, len(text)
    while i < n:
        end = min(i + size, n)
        if end < n:
            sp = text.rfind(" ", i, end)
            if sp > i:
                end = sp
        piece = text[i:end].strip()
        if piece:
            chunks.append(piece)
        i = end
    return chunks


async def _ollama_generate(model: str, prompt: str, fmt: str | None = None,
                           images: list[str] | None = None,
                           timeout: float = 300.0) -> str:
    payload: dict = {"model": model, "prompt": prompt, "stream": False}
    if fmt:
        payload["format"] = fmt
    if images:
        # Ollama /api/generate accepts base64-encoded images for vision models
        # (e.g. qwen2.5vl). A non-vision model simply ignores them.
        payload["images"] = images
    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.post(f"{OLLAMA_BASE}/api/generate", json=payload)
        r.raise_for_status()
        return r.json().get("response", "")


async def _map_reduce_transcript(model: str, transcript: str) -> str:
    """Map step: summarize each chunk to plain text. Returns the concatenated
    partial summaries, which the caller then reduces into the final JSON."""
    chunks = _chunk_text(transcript)
    partials: list[str] = []
    for idx, chunk in enumerate(chunks, 1):
        prompt = (
            "Summarize this part of a longer voice note in 2-4 sentences. "
            "Preserve any tasks, decisions, names and numbers. "
            "Write in the SAME language as the input. Output plain text only.\n\n"
            f"Part {idx} of {len(chunks)}:\n{chunk}\n"
        )
        partials.append((await _ollama_generate(model, prompt)).strip())
    return "\n".join(p for p in partials if p)


CLASSIFY_HEAD_CHARS = 1500  # classify on the start of the note to keep it cheap


async def classify_skill(transcript: str, model: str) -> dict:
    """Pick the skill whose `when_to_use` best matches the note. Skips the extra
    LLM call when there's nothing to choose, and always falls back to the default
    skill on any failure — so 'auto-classify' can never strand a note."""
    cfg = read_config()
    default = skills.default_skill(cfg.get("default_skill_id"))
    if not (transcript or "").strip():
        # Nothing to route on (e.g. an image-only note) — use the default skill.
        return default
    enabled = [s for s in skills.load_skills() if s.get("enabled", True)]
    if len(enabled) <= 1:
        return enabled[0] if enabled else default
    catalog = "\n".join(
        f'- "{s["id"]}": {s.get("when_to_use") or s.get("description") or s["name"]}'
        for s in enabled
    )
    prompt = (
        "You are a router. Read the note and choose the single best-matching skill.\n"
        "Return ONLY JSON: {\"skill_id\": \"<one id from the list>\"}.\n\n"
        f"Skills:\n{catalog}\n\n"
        f"Note (start):\n{transcript[:CLASSIFY_HEAD_CHARS]}\n"
    )
    try:
        resp = await _ollama_generate(model, prompt, fmt="json", timeout=60.0)
        data = json.loads(resp) if resp else {}
        chosen = skills.get_skill(str(data.get("skill_id", "")).strip())
        if chosen and chosen.get("enabled", True):
            log.info("Skill router picked '%s'", chosen["id"])
            return chosen
    except Exception as exc:
        log.warning("Skill classification failed (%s) — using default '%s'", exc, default["id"])
    return default


async def run_skill(skill: dict, transcript: str, model: str,
                    images: list[str] | None = None) -> dict:
    """Run one skill over a note (text and/or images) and return the normalized
    result {skill_id, format, summary, action_items, tags, fields, body}. Long
    text is condensed via map-reduce first (images ride only on the final call).
    Never raises on a bad model response — normalize_output degrades to empty."""
    source = transcript
    if len(transcript) > MAP_REDUCE_CHARS:
        log.info("Long transcript (%d chars) — map-reduce condense", len(transcript))
        source = await _map_reduce_transcript(model, transcript)
    prompt = skills.build_skill_prompt(skill, source, _project_ids(), has_images=bool(images))
    fmt = "json" if (skill.get("output") or {}).get("format") == "json" else None
    resp = await _ollama_generate(model, prompt, fmt=fmt, images=images)
    return skills.normalize_output(skill, resp)


async def run_analysis(transcript: str, images: list[str] | None = None) -> tuple[dict, dict]:
    """Auto-select a skill (via the orchestration/router model, on the text) and
    run it on the note's text + images (via the analysis model). Returns
    (skill, result); result carries the analysis model under 'model'."""
    model = await pick_analysis_model()
    if not model:
        raise RuntimeError("No Ollama model available for summarization")
    router_model = await pick_orchestration_model() or model
    skill = await classify_skill(transcript, router_model)
    result = await run_skill(skill, transcript, model, images=images)
    result["model"] = model
    return skill, result


# ── Skill actions (best-effort post-processing of the result) ─────────────────

def _vault_root() -> str:
    """Where {vault} resolves to for write_file actions. Configurable; defaults to
    an 'exports' folder in the data dir so it always works out of the box."""
    return read_config().get("vault_dir") or os.path.join(paths.DATA_DIR, "exports")


_UNSAFE_FILENAME = re.compile(r'[<>:"/\\|?*\n\r\t]+')


def _safe_filename(name: str) -> str:
    name = _UNSAFE_FILENAME.sub(" ", name or "").strip() or "note"
    return name[:80]


def _action_write_file(action: dict, job: dict, result: dict) -> None:
    directory = (action.get("dir") or "{vault}").replace("{vault}", _vault_root())
    os.makedirs(directory, exist_ok=True)
    stamp = (job.get("created_at") or datetime.now().isoformat())[:10]
    fname = f"{stamp} {_safe_filename(job.get('title') or 'note')}.md"
    with open(os.path.join(directory, fname), "w", encoding="utf-8") as f:
        f.write(skills.render_markdown_note(job, result))


async def _action_webhook(action: dict, job: dict, result: dict) -> None:
    url = action.get("url")
    if not url:
        raise ValueError("webhook action missing 'url'")
    payload = {
        "id": job.get("id"), "title": job.get("title"),
        "skill_id": result.get("skill_id"), "summary": result.get("summary"),
        "action_items": result.get("action_items"), "tags": result.get("tags"),
        "fields": result.get("fields"), "body": result.get("body"),
        "transcript": job.get("transcript"),
    }
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.post(url, json=payload)
        r.raise_for_status()


def _action_append_project(action: dict, job: dict, result: dict) -> None:
    pid = action.get("project")
    if not pid:
        raise ValueError("append_project action missing 'project'")
    data = read_projects()
    for proj in data.get("projects", []):
        if proj.get("id") == pid:
            proj.setdefault("notes", []).append({
                "id": job.get("id"), "title": job.get("title"),
                "summary": result.get("summary"), "created_at": job.get("created_at"),
            })
            write_projects(data)
            return
    raise ValueError(f"unknown project '{pid}'")


async def run_actions(skill: dict, job: dict, result: dict) -> None:
    """Execute a skill's downstream actions. Best-effort: each action is isolated
    so one failure (bad webhook, read-only path) never fails the note; errors are
    logged and recorded on the job for the dashboard."""
    for action in skill.get("actions") or []:
        typ = (action.get("type") or "").strip()
        try:
            if typ == "write_file":
                await asyncio.to_thread(_action_write_file, action, job, result)
            elif typ == "webhook":
                await _action_webhook(action, job, result)
            elif typ == "append_project":
                await asyncio.to_thread(_action_append_project, action, job, result)
            else:
                log.warning("[job %s] unknown action type '%s'", job.get("id"), typ)
                continue
            log.info("[job %s] action '%s' done", job.get("id"), typ)
        except Exception as exc:
            log.warning("[job %s] action '%s' failed: %s", job.get("id"), typ, exc)
            job.setdefault("action_errors", []).append(f"{typ}: {exc}")


def _read_images_b64(image_paths: list[str]) -> list[str]:
    out: list[str] = []
    for p in image_paths:
        try:
            with open(p, "rb") as f:
                out.append(base64.b64encode(f.read()).decode("ascii"))
        except OSError:
            pass
    return out


def _persist_audio(job_id: str, src_path: str | None) -> str | None:
    """Move a processed recording into permanent MEDIA_DIR storage so the note can
    be re-transcribed later (e.g. after switching Whisper models). Returns the stored
    filename (relative to MEDIA_DIR), or None. Idempotent for files already in
    MEDIA_DIR — re-transcribe reads straight from there, so the move is a no-op."""
    if not src_path or not os.path.exists(src_path):
        return None
    os.makedirs(MEDIA_DIR, exist_ok=True)
    if os.path.dirname(os.path.abspath(src_path)) == os.path.abspath(MEDIA_DIR):
        return os.path.basename(src_path)
    ext = os.path.splitext(src_path)[1] or ".webm"
    dest_name = job_id + ext
    try:
        os.replace(src_path, os.path.join(MEDIA_DIR, dest_name))
        return dest_name
    except OSError as exc:
        log.warning("[job %s] could not persist recording: %s", job_id, exc)
        return None


def _partial_result(combined: str, lang: str | None, images_n: int,
                    audio_file: str | None) -> dict:
    """The /result payload right after transcription — transcript present, summary
    still pending. The phone renders this immediately, then polls for the rest."""
    return {
        "transcript": combined,
        "summary": "",
        "actionItems": [],
        "tags": [],
        "skillId": None,
        "output": {"format": "json", "fields": {}, "body": "", "image_count": images_n},
        "language": lang,
        "status": "summarizing",
        "hasAudio": bool(audio_file),
    }


async def process_job(job_id: str, audio_path: str | None = None) -> None:
    """Process a captured note: optional audio (transcribed), optional typed text,
    and optional images — combined and routed through the skill pipeline. The
    transcript is published (and archived) as soon as transcription finishes, then
    the slower LLM summary updates the note a second time — so the phone shows a
    result quickly instead of waiting for the whole pipeline."""
    job = JOBS[job_id]
    typed_text = (job.get("text") or "").strip()
    image_paths = job.get("image_paths") or []
    audio_file = job.get("audio_file")  # preset on re-transcribe
    try:
        transcript, lang = "", None
        if audio_path:
            job["status"] = "transcribing"
            job["updated_at"] = datetime.now().isoformat()
            async with _transcribe_sem:
                # Idempotent: returns immediately if already loaded, and blocks the
                # job (rather than crashing on a None model) if an upload lands while
                # the first-run model download is still in flight.
                await asyncio.to_thread(load_whisper)
                # Force the user-chosen language (None = auto-detect).
                transcript, lang = await asyncio.to_thread(
                    transcribe_path, audio_path, job.get("lang_choice"))
            # Keep the recording (moved out of the scratch UPLOAD_DIR) so the note
            # can be re-transcribed later with a different model.
            audio_file = _persist_audio(job_id, audio_path) or audio_file
            job["audio_file"] = audio_file
        # The note's text = what was typed + what was transcribed.
        combined = "\n\n".join(x for x in (typed_text, transcript.strip()) if x).strip()
        images_b64 = await asyncio.to_thread(_read_images_b64, image_paths)
        job["language"] = lang
        job["transcript"] = combined

        # ── Stage 1: transcript is ready — publish + archive it before the LLM step
        job["status"] = "summarizing"
        job["summary"] = ""
        job["tags"] = []
        job["model"] = None
        job["skill_id"] = None
        job["output"] = {"format": "json", "fields": {}, "body": "",
                         "image_count": len(images_b64)}
        job["result"] = _partial_result(combined, lang, len(images_b64), audio_file)
        job["updated_at"] = datetime.now().isoformat()
        _archive_job(job)  # intermediate persist (status 'summarizing', transcript only)

        # ── Stage 2: summarize / run the skill
        skill = None
        try:
            skill, result = await run_analysis(combined, images=images_b64 or None)
        except Exception as exc:
            # Transcription/typing succeeded but the LLM step failed — still return
            # whatever text we have so the note isn't lost.
            log.warning("[job %s] analysis failed: %s", job_id, exc)
            result = {
                "skill_id": None, "format": "json",
                "summary": combined[:280] or ("(image note)" if images_b64 else ""),
                "action_items": [], "tags": [], "fields": {}, "body": "", "model": None,
            }
        result["image_count"] = len(images_b64)
        job["result"] = {
            "transcript": combined,
            "summary": result["summary"],
            "actionItems": result["action_items"],
            "tags": result["tags"],
            "skillId": result.get("skill_id"),
            "output": _output_payload(result),
            "language": lang,
            "status": "done",
            "hasAudio": bool(audio_file),
        }
        job["summary"] = result["summary"]
        job["tags"] = result["tags"]
        job["model"] = result.get("model")
        job["skill_id"] = result.get("skill_id")
        job["output"] = _output_payload(result)
        job["status"] = "done"
        job["updated_at"] = datetime.now().isoformat()
        _archive_job(job)  # final persist (status 'done', summary filled in)
        # Downstream actions run after the note is safely archived, and never
        # fail the job (errors are logged + recorded on the job).
        if skill:
            await run_actions(skill, job, result)
    except Exception as exc:
        # On a transcription failure the audio is still in UPLOAD_DIR — persist it
        # so the note keeps a recording to re-transcribe (and doesn't get re-run by
        # orphan recovery on every restart).
        if audio_path and not job.get("audio_file"):
            job["audio_file"] = _persist_audio(job_id, audio_path)
        job["status"] = "failed"
        job["error"] = str(exc)
        job["updated_at"] = datetime.now().isoformat()
        log.error("[job %s] failed: %s", job_id, exc)
        _archive_job(job)
    finally:
        # Images aren't persisted server-side; only the audio recording is kept
        # (for re-transcribe). Clean up the per-job image scratch files.
        for p in image_paths:
            if p and os.path.exists(p):
                try:
                    os.unlink(p)
                except OSError:
                    pass


def _output_payload(result: dict) -> dict:
    """The skill's structured output, kept alongside the flat summary/tags so the
    dashboard can render custom fields (Meeting decisions, Journal prose, …)."""
    return {
        "format": result.get("format", "json"),
        "fields": result.get("fields") or {},
        "body": result.get("body") or "",
        "image_count": result.get("image_count", 0),
    }


def _archive_job(job: dict) -> None:
    """Persist a finished job to the activity history for the dashboard. Includes
    action_items + output_json so /result can still be served from the archive
    after a restart (the live in-memory JOBS map is gone by then)."""
    log_activity({
        "id": job["id"],
        "title": job.get("title", ""),
        "status": job["status"],
        "created_at": job.get("created_at"),
        "completed_at": job.get("updated_at"),
        "duration_sec": job.get("duration_sec"),
        "language": job.get("language"),
        "model": job.get("model"),
        "summary": job.get("summary", ""),
        "transcript": job.get("transcript", ""),
        "action_items": (job.get("result") or {}).get("actionItems", []),
        "tags": job.get("tags", []),
        "error": job.get("error"),
        "skill_id": job.get("skill_id"),
        "output_json": json.dumps(job.get("output") or {}, ensure_ascii=False),
        "audio_file": job.get("audio_file"),
    })


def _find_archived(job_id: str) -> dict | None:
    """Look up a finished job in the persistent store (used as a fallback for
    /job and /result when the id is no longer in the in-memory JOBS map).
    Indexed by primary key — no full scan."""
    return store.get_activity(job_id)


_recovery_done = False


def recover_orphan_jobs() -> None:
    """Re-enqueue audio files left in UPLOADS_DIR by a crash/restart so their
    recordings aren't silently lost. process_job deletes the file once it
    finishes, so anything still on disk had no completed job. Runs once — both
    uvicorn servers share this app and each fires lifespan startup, but the body
    is await-free so the flag check-and-set is atomic on the single event loop."""
    global _recovery_done
    if _recovery_done:
        return
    _recovery_done = True
    try:
        files = os.listdir(UPLOAD_DIR)
    except OSError:
        return
    for fn in files:
        path = os.path.join(UPLOAD_DIR, fn)
        if not os.path.isfile(path) or fn.startswith(".tmp_"):
            continue
        if "__img" in fn:
            # Orphaned image from a crashed multimodal job — its text/audio context
            # is gone, so it can't be reconstructed. Drop it rather than transcribe.
            try:
                os.unlink(path)
            except OSError:
                pass
            continue
        job_id, _ext = os.path.splitext(fn)
        if job_id in JOBS:
            continue
        now = datetime.now().isoformat()
        JOBS[job_id] = {
            "id": job_id, "title": "Recovered note", "duration_sec": None,
            "status": "queued", "created_at": now, "updated_at": now,
            "language": None, "transcript": "", "summary": "", "tags": [],
            "model": None, "result": None, "error": None,
        }
        asyncio.create_task(process_job(job_id, path))
        log.info("Recovered orphaned upload -> job %s", job_id)


@app.get("/status")
async def bridge_status(_=Depends(require_auth)):
    return {"ok": True}


@app.post("/upload")
async def bridge_upload(
    _=Depends(require_auth),
    file: UploadFile | None = File(None),     # audio (optional)
    recordingId: str = Form(...),
    metadata: str = Form("{}"),
    text: str = Form(""),                     # typed text (optional)
    images: list[UploadFile] = File(default=[]),  # one repeated `images` field
):
    """Accept a captured note: any combination of audio, typed text and images."""
    text = (text or "").strip()
    audio_bytes = await file.read() if file is not None else b""
    image_blobs: list[tuple[str, bytes]] = []
    for img in images or []:
        data = await img.read()
        if data:
            image_blobs.append(((img.content_type or "").split(";")[0].strip(), data))
    if not audio_bytes and not text and not image_blobs:
        raise HTTPException(status_code=400, detail="Empty note — needs text, audio, or an image")

    os.makedirs(UPLOAD_DIR, exist_ok=True)
    job_id = f"job_{recordingId}_{uuid.uuid4().hex[:6]}"

    audio_path = None
    if audio_bytes:
        base_type = (file.content_type or "audio/webm").split(";")[0].strip()
        suffix = MIME_TO_EXT.get(base_type, ".webm")
        audio_path = os.path.join(UPLOAD_DIR, job_id + suffix)
        with open(audio_path, "wb") as f:
            f.write(audio_bytes)

    image_paths: list[str] = []
    for i, (ctype, data) in enumerate(image_blobs):
        ext = IMAGE_MIME_TO_EXT.get(ctype, ".jpg")
        p = os.path.join(UPLOAD_DIR, f"{job_id}__img{i}{ext}")
        with open(p, "wb") as f:
            f.write(data)
        image_paths.append(p)

    try:
        meta = json.loads(metadata) if metadata else {}
    except json.JSONDecodeError:
        meta = {}
    now = datetime.now().isoformat()
    JOBS[job_id] = {
        "id": job_id,
        "title": meta.get("title") or "Untitled note",
        "duration_sec": meta.get("durationSec"),
        "status": "queued",
        "created_at": now,
        "updated_at": now,
        "language": None,
        "transcript": "",
        "summary": "",
        "tags": [],
        "model": None,
        "result": None,
        "error": None,
        "text": text,
        "image_paths": image_paths,
        "lang_choice": meta.get("language") or None,  # forced Whisper language, None=auto
    }
    asyncio.create_task(process_job(job_id, audio_path))
    return {"jobId": job_id}


@app.get("/job/{job_id}")
async def bridge_job(job_id: str, _=Depends(require_auth)):
    job = JOBS.get(job_id)
    if job:
        return {"status": job["status"]}
    # Server may have restarted since the phone uploaded — resolve from archive
    # so the phone gets a terminal status instead of a 404 → spurious failure.
    archived = _find_archived(job_id)
    if archived:
        return {"status": archived.get("status", "done")}
    raise HTTPException(status_code=404, detail="Unknown job")


@app.get("/result/{job_id}")
async def bridge_result(job_id: str, _=Depends(require_auth)):
    # Return as soon as the transcript exists (status 'summarizing'), so the phone
    # can show it before the slower summary lands; the 'status' field tells the
    # client whether to keep polling for the summary.
    job = JOBS.get(job_id)
    if job and job.get("result"):
        return job["result"]
    archived = _find_archived(job_id)
    if archived and archived.get("status") in ("done", "summarizing"):
        try:
            output = json.loads(archived.get("output_json") or "{}")
        except (ValueError, TypeError):
            output = {}
        return {
            "transcript": archived.get("transcript", ""),
            "summary": archived.get("summary", ""),
            "actionItems": archived.get("action_items", []),
            "tags": archived.get("tags", []),
            "skillId": archived.get("skill_id"),
            "output": output,
            "language": archived.get("language"),
            "status": archived.get("status"),
            "hasAudio": bool(archived.get("audio_file")),
        }
    raise HTTPException(status_code=404, detail="Result not ready")


@app.get("/media/{job_id}")
async def bridge_media(job_id: str, _=Depends(require_auth)):
    """Stream the saved recording for a note so the phone can play it back."""
    entry = JOBS.get(job_id) or _find_archived(job_id)
    audio_file = (entry or {}).get("audio_file")
    if not audio_file:
        raise HTTPException(status_code=404, detail="No saved recording")
    path = os.path.join(MEDIA_DIR, os.path.basename(audio_file))
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Recording file missing")
    ext = os.path.splitext(path)[1].lower()
    return FileResponse(path, media_type=AUDIO_EXT_TO_MIME.get(ext, "application/octet-stream"))


@app.post("/retranscribe/{job_id}")
async def bridge_retranscribe(job_id: str, _=Depends(require_auth)):
    """Re-run transcription on the saved recording with the CURRENT Whisper model,
    then regenerate the summary — updating the existing note in place (same id).
    Note: only the audio is persisted, so any original typed text / images that
    accompanied the first capture are not part of the re-transcribe."""
    entry = _find_archived(job_id) or JOBS.get(job_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Unknown note")
    audio_file = entry.get("audio_file")
    if not audio_file:
        raise HTTPException(status_code=400, detail="No saved recording for this note")
    path = os.path.join(MEDIA_DIR, os.path.basename(audio_file))
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Recording file missing")
    live = JOBS.get(job_id)
    if live and live.get("status") in ACTIVE_STATUSES:
        return {"jobId": job_id, "status": live["status"]}  # already running
    now = datetime.now().isoformat()
    JOBS[job_id] = {
        "id": job_id,
        "title": entry.get("title") or "Untitled note",
        "duration_sec": entry.get("duration_sec"),
        "status": "queued",
        "created_at": entry.get("created_at") or now,
        "updated_at": now,
        "language": None,
        "transcript": "",
        "summary": "",
        "tags": [],
        "model": None,
        "result": None,
        "error": None,
        "text": "",
        "image_paths": [],
        "lang_choice": entry.get("language") or None,
        "audio_file": audio_file,
    }
    asyncio.create_task(process_job(job_id, path))
    return {"jobId": job_id, "status": "queued"}


# ── Server dashboard ─────────────────────────────────────────────────────────

ACTIVE_STATUSES = {"queued", "transcribing", "summarizing"}


async def ollama_reachable() -> bool:
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            r = await client.get(f"{OLLAMA_BASE}/api/tags")
            return r.status_code == 200
    except Exception:
        return False


@app.get("/api/activity")
async def get_activity():
    """Live activity feed for the desktop server dashboard."""
    active = [
        {
            "id": j["id"], "title": j.get("title"), "status": j["status"],
            "created_at": j.get("created_at"), "updated_at": j.get("updated_at"),
            "duration_sec": j.get("duration_sec"), "language": j.get("language"),
        }
        for j in JOBS.values() if j["status"] in ACTIVE_STATUSES
    ]
    active.sort(key=lambda j: j.get("created_at") or "", reverse=True)
    # A job mid-pipeline now also has an intermediate archive row (so its transcript
    # survives a restart); drop those from history while it's still live so the
    # dashboard doesn't show it twice (once under "Processing now", once under history).
    active_ids = {j["id"] for j in active}
    history = [h for h in read_activity() if h.get("id") not in active_ids]
    cfg = read_config()
    return {
        "active": active,
        "history": history,
        "server": {
            "whisper_model": cfg.get("whisper_model", "small"),
            "whisper_ready": whisper_model is not None,
            "analysis_model": cfg.get("analysis_model"),  # None = auto-pick
            "orchestration_model": cfg.get("orchestration_model"),  # None = use analysis model
            "ollama_reachable": await ollama_reachable(),
            "active_count": len(active),
            "default_skill_id": skills.default_skill(cfg.get("default_skill_id"))["id"],
            "vault_dir": cfg.get("vault_dir"),
            "cloudflare": _cloudflare_status(),
        },
    }


@app.get("/api/search")
async def search_activity(q: str = ""):
    """Full-text search across past notes (title/summary/transcript/tags).
    Loopback-only like the rest of /api/* (blocked on the LAN port). Returns the
    same entry shape as /api/activity history so the dashboard reuses its
    renderer."""
    return {"results": store.search_activity(q)}


class AnalysisModelIn(BaseModel):
    model: str | None = None


@app.post("/api/settings/analysis-model")
async def set_analysis_model(payload: AnalysisModelIn):
    cfg = read_config()
    cfg["analysis_model"] = payload.model or None
    write_config(cfg)
    return {"status": "ok", "analysis_model": cfg["analysis_model"]}


@app.post("/api/settings/orchestration-model")
async def set_orchestration_model(payload: AnalysisModelIn):
    """The model that runs the skill router. null = use the analysis model."""
    cfg = read_config()
    cfg["orchestration_model"] = payload.model or None
    write_config(cfg)
    return {"status": "ok", "orchestration_model": cfg["orchestration_model"]}


# ── Skills (pluggable LLM post-processing) ───────────────────────────────────
#
# Loopback-only like the rest of /api/* (the LAN-port guard blocks these on 8766).

class SkillIn(BaseModel):
    id: str | None = None
    name: str
    description: str | None = ""
    when_to_use: str | None = ""
    output: dict | None = None
    actions: list | None = None
    enabled: bool = True
    prompt: str | None = ""


class SkillTestIn(BaseModel):
    text: str
    skill: SkillIn | None = None   # an unsaved skill (live editor preview), or…
    skill_id: str | None = None    # …an existing skill by id


class DefaultSkillIn(BaseModel):
    skill_id: str | None = None


class VaultDirIn(BaseModel):
    vault_dir: str | None = None


def _skill_summary(s: dict) -> dict:
    return {
        "id": s["id"], "name": s.get("name"), "description": s.get("description"),
        "when_to_use": s.get("when_to_use"), "enabled": s.get("enabled", True),
        "format": (s.get("output") or {}).get("format", "json"),
        "actions": [a.get("type") for a in (s.get("actions") or [])],
        "builtin": s.get("builtin", False), "overridden": s.get("overridden", False),
    }


@app.get("/api/skills")
async def list_skills():
    cfg = read_config()
    return {
        "skills": [_skill_summary(s) for s in skills.load_skills()],
        "default_skill_id": skills.default_skill(cfg.get("default_skill_id"))["id"],
    }


@app.post("/api/skills/test")
async def test_skill(payload: SkillTestIn):
    """Dry-run a skill against pasted text. Runs NO actions — just the prompt +
    normalize step — so the editor can preview output before saving."""
    if payload.skill is not None:
        skill = skills.parse_skill(skills.serialize_skill(payload.skill.model_dump()))
    elif payload.skill_id:
        skill = skills.get_skill(payload.skill_id)
        if not skill:
            raise HTTPException(status_code=404, detail="Unknown skill")
    else:
        raise HTTPException(status_code=400, detail="Provide a skill or skill_id")
    model = await pick_analysis_model()
    if not model:
        raise HTTPException(status_code=503, detail="No Ollama model available")
    try:
        result = await run_skill(skill, payload.text, model)
    except Exception as exc:
        # Some httpx timeout exceptions stringify to '' — fall back to the class
        # name so the dashboard shows something actionable instead of a bare 503.
        raise HTTPException(status_code=503, detail=str(exc) or type(exc).__name__)
    result["model"] = model
    return result


@app.get("/api/skills/{skill_id}")
async def get_one_skill(skill_id: str):
    skill = skills.get_skill(skill_id)
    if not skill:
        raise HTTPException(status_code=404, detail="Unknown skill")
    return skill


@app.post("/api/skills")
async def upsert_skill(payload: SkillIn):
    if not (payload.name or "").strip():
        raise HTTPException(status_code=400, detail="Skill needs a name")
    saved = skills.save_skill(payload.model_dump())
    return {"status": "ok", "skill": saved}


@app.delete("/api/skills/{skill_id}")
async def remove_skill(skill_id: str):
    removed = skills.delete_skill(skill_id)
    # If a bundled default still exists under this id, it reverts to the shipped
    # version; report whether anything user-authored was actually removed.
    return {"status": "ok", "removed": removed, "reverted": skills.get_skill(skill_id) is not None}


@app.get("/api/settings/default-skill")
async def get_default_skill():
    cfg = read_config()
    return {"default_skill_id": skills.default_skill(cfg.get("default_skill_id"))["id"]}


@app.post("/api/settings/default-skill")
async def set_default_skill(payload: DefaultSkillIn):
    if payload.skill_id and not skills.get_skill(payload.skill_id):
        raise HTTPException(status_code=400, detail="Unknown skill")
    cfg = read_config()
    cfg["default_skill_id"] = payload.skill_id or None
    write_config(cfg)
    return {"status": "ok", "default_skill_id": cfg["default_skill_id"]}


@app.post("/api/settings/vault-dir")
async def set_vault_dir(payload: VaultDirIn):
    cfg = read_config()
    cfg["vault_dir"] = (payload.vault_dir or "").strip() or None
    write_config(cfg)
    return {"status": "ok", "vault_dir": cfg["vault_dir"]}


# ── Remote access (Cloudflare Tunnel) — loopback-only control plane ──────────
#
# The phone bridge becomes reachable from anywhere via a Cloudflare Tunnel. These
# endpoints provision the tunnel through the Cloudflare API (server-side) and toggle
# the cloudflared connector. The CF API token + tunnel token live only in config and
# are NEVER returned to a client.

import cftunnel  # noqa: E402


class CloudflareTokenIn(BaseModel):
    api_token: str


class CloudflareEnableIn(BaseModel):
    zone_id: str
    hostname: str


def _cloudflare_status() -> dict:
    """Public (no-secret) view of the remote-access state for the dashboard."""
    cfg = read_config()
    import runner  # lazy: avoids an import cycle (runner imports app)
    return {
        "configured": bool(cf_api_token()),  # config OR .env / env var
        "enabled": bool(cfg.get("cloudflare_enabled")),
        "hostname": cfg.get("cloudflare_hostname"),
        "connected": runner.cloudflared_running(),
    }


@app.post("/api/settings/cloudflare")
async def set_cloudflare_token(payload: CloudflareTokenIn):
    """Save + validate the Cloudflare API token."""
    token = (payload.api_token or "").strip()
    if not token:
        raise HTTPException(status_code=400, detail="API token required")
    try:
        await cftunnel.verify_token(token)
    except cftunnel.CloudflareError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    cfg = read_config()
    cfg["cloudflare_api_token"] = token
    write_config(cfg)
    return {"status": "ok"}


@app.get("/api/cloudflare/zones")
async def cloudflare_zones():
    token = cf_api_token()
    if not token:
        raise HTTPException(status_code=400, detail="No Cloudflare token (set it in the dashboard or CLOUDFLARE_API_TOKEN)")
    try:
        return {"zones": await cftunnel.list_zones(token)}
    except cftunnel.CloudflareError as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@app.post("/api/cloudflare/enable")
async def cloudflare_enable(payload: CloudflareEnableIn):
    """Provision the tunnel (create/reuse + ingress + DNS) and start the connector."""
    cfg = read_config()
    token = cf_api_token()
    if not token:
        raise HTTPException(status_code=400, detail="No Cloudflare token (set it in the dashboard or CLOUDFLARE_API_TOKEN)")
    hostname = (payload.hostname or "").strip().lower()
    if not hostname:
        raise HTTPException(status_code=400, detail="Hostname required")
    try:
        # The account id comes from the zone itself (a Tunnel/DNS token can't list
        # /accounts, but it can read the zone, which carries account.id).
        account_id = await cftunnel.account_for_zone(token, payload.zone_id)
        prov = await cftunnel.provision(
            token, account_id, payload.zone_id, hostname,
            existing_id=cfg.get("cloudflare_tunnel_id"),
        )
    except cftunnel.CloudflareError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    cfg.update({
        "cloudflare_account_id": account_id,
        "cloudflare_zone_id": payload.zone_id,
        "cloudflare_tunnel_id": prov["tunnel_id"],
        "cloudflare_tunnel_token": prov["tunnel_token"],
        "cloudflare_hostname": prov["hostname"],
        "cloudflare_enabled": True,
    })
    write_config(cfg)
    import runner
    try:
        await asyncio.to_thread(runner.start_cloudflared, prov["tunnel_token"])
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Tunnel provisioned but connector failed: {exc}")
    return {"status": "ok", "hostname": hostname, "url": f"https://{hostname}"}


@app.post("/api/cloudflare/disable")
async def cloudflare_disable():
    cfg = read_config()
    cfg["cloudflare_enabled"] = False
    write_config(cfg)
    import runner
    runner.stop_cloudflared()
    return {"status": "ok"}


# ── Frontend serving (port-routed, same-origin) ──────────────────────────────
#
# Both frontends live at their own root and are chosen by the port the request
# arrived on:
#   • HTTPS_PORT (phone)  -> the companion PWA  (pwa/dist/client)
#   • otherwise (desktop) -> the computer UI    (static/)
# Serving the PWA on the same https origin as the API means the phone trusts ONE
# certificate, the mic works, and there's no CORS / mixed-content to juggle.
# If the PWA isn't built, every port falls back to the desktop UI.

from fastapi.responses import FileResponse  # noqa: E402

STATIC_DIR = paths.STATIC_DIR
STATIC_DIR_ABS = os.path.abspath(STATIC_DIR)
DESKTOP_INDEX = os.path.join(STATIC_DIR, "index.html")
PWA_DIR = paths.PWA_DIR
PWA_DIR_ABS = os.path.abspath(PWA_DIR)
PWA_SHELL = os.path.join(PWA_DIR, "_shell.html")
PWA_AVAILABLE = os.path.isdir(PWA_DIR) and os.path.isfile(PWA_SHELL)

if PWA_AVAILABLE:
    log.info("[pwa] Companion PWA will be served on port %s (from %s)", HTTPS_PORT, PWA_DIR)
else:
    log.warning("[pwa] Companion PWA not built — phone port falls back to desktop UI "
                "(run `npm run build:static` in faster-notes/ to enable)")


def _safe_file(root: str, root_abs: str, rel: str) -> str | None:
    """Resolve rel under root, guarding against path traversal."""
    if not rel:
        return None
    cand = os.path.abspath(os.path.join(root, rel))
    if cand.startswith(root_abs + os.sep) and os.path.isfile(cand):
        return cand
    return None


def _media_type_for(path: str) -> str | None:
    """Force MIME types the browser is strict about. On Windows, mimetypes reads
    the registry and can return text/plain for .js — which breaks ES-module
    loading AND service-worker registration. Pin them explicitly."""
    if path.endswith(".webmanifest"):
        return "application/manifest+json"
    if path.endswith((".js", ".mjs")):
        return "text/javascript"
    return None


def _arrival_port(request: Request) -> int | None:
    """The local port the request actually arrived on, from the ASGI scope's bound
    server address. Unlike request.url.port (which Starlette derives from the Host
    header), this isn't spoofed by a reverse proxy / Cloudflare tunnel: the tunnel
    forwards to localhost:8766, so a public request still arrives on the phone port
    and must get the PWA — not the desktop dashboard."""
    server = request.scope.get("server")
    if server and len(server) == 2 and server[1]:
        return server[1]
    return request.url.port


# Tell a CDN (Cloudflare) explicitly NOT to store these. Plain Cache-Control isn't
# enough: Cloudflare caches .js/.png by default and rewrites their Browser Cache TTL
# (we saw it force max-age=14400 on /sw.js), so a stale service worker / manifest
# could be served from the edge — breaking SW registration AND app updates. The
# CDN-Cache-Control + Cloudflare-CDN-Cache-Control headers take precedence over
# Cloudflare's cache settings and keep these files dynamic.
_NO_STORE = {
    "Cache-Control": "no-cache, no-store, must-revalidate",
    "CDN-Cache-Control": "no-store",
    "Cloudflare-CDN-Cache-Control": "no-store",
}


def _cache_headers(path: str) -> dict:
    """Cache policy for served frontend files. The service worker, web manifest,
    icons and the HTML shell must never be served stale from a CDN. Content-hashed
    build assets (/assets/...) carry their hash in the filename, so they're safe to
    cache forever."""
    if f"{os.sep}assets{os.sep}" in path:
        return {"Cache-Control": "public, max-age=31536000, immutable"}
    return dict(_NO_STORE)


@app.get("/")
@app.get("/{path:path}")
async def serve_frontend(request: Request, path: str = ""):
    on_phone_port = _arrival_port(request) == HTTPS_PORT
    if PWA_AVAILABLE and on_phone_port:
        root, root_abs, shell = PWA_DIR, PWA_DIR_ABS, PWA_SHELL
    else:
        root, root_abs, shell = STATIC_DIR, STATIC_DIR_ABS, DESKTOP_INDEX
    f = _safe_file(root, root_abs, path)
    if f:
        return FileResponse(f, media_type=_media_type_for(f), headers=_cache_headers(f))
    return FileResponse(shell, media_type="text/html", headers=dict(_NO_STORE))
