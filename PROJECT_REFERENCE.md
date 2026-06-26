# Faster Notes — Project Reference

A locally-hosted voice-notes system: a **FastAPI server** that runs on your PC and a **phone PWA**
that records audio anywhere, queues it offline, and uploads it to the server when you're back on the
home network. The server transcribes with **faster-whisper** and summarizes with a local **Ollama**
model. Nothing leaves your machine.

> This document is a future-reference snapshot of how the project is built and why. For the original
> landscape/market research see [`app research.md`](app%20research.md); for packaging detail see
> [`PACKAGING.md`](PACKAGING.md).

---

## Table of contents
1. [Concept & data flow](#1-concept--data-flow)
2. [Architecture](#2-architecture)
3. [Repository layout](#3-repository-layout)
4. [Backend modules](#4-backend-modules)
5. [Storage & data locations](#5-storage--data-locations)
6. [API reference](#6-api-reference)
7. [The phone PWA](#7-the-phone-pwa)
8. [The desktop dashboard](#8-the-desktop-dashboard)
9. [Pairing & connectivity model](#9-pairing--connectivity-model)
10. [Security model](#10-security-model)
11. [Transcription & LLM pipeline](#11-transcription--llm-pipeline)
12. [Packaging & distribution](#12-packaging--distribution)
13. [Build & run](#13-build--run)
14. [Testing](#14-testing)
15. [Configuration](#15-configuration)
16. [Known constraints & gotchas](#16-known-constraints--gotchas)
17. [Deferred / future roadmap](#17-deferred--future-roadmap)
18. [Hardening changelog (this session)](#18-hardening-changelog)

---

## 1. Concept & data flow

```
 ┌─────────────┐   record (offline)    ┌──────────────────┐   when home / on Wi-Fi
 │  Phone PWA  │ ───────────────────►  │  IndexedDB queue │ ───────────────┐
 │ (browser)   │                       │ (recordings)     │                │  upload (multipart + Bearer token)
 └─────────────┘                       └──────────────────┘                ▼
                                                                  ┌───────────────────────┐
                                                                  │  FastAPI server (PC)  │
                                                                  │  ① Whisper transcribe │
                                                                  │  ② Ollama summarize   │
                                                                  │  ③ persist to SQLite  │
                                                                  └───────────────────────┘
                                                                           │  poll /job → /result
                                                                           ▼
                                                              transcript + summary + action items + tags
                                                              saved back into the PWA (IndexedDB) and
                                                              logged to the desktop dashboard
```

The phone is the only thing that records. The desktop UI is a **server dashboard**, not a recorder.

---

## 2. Architecture

**One FastAPI app, served on two ports in one process** (`runner.py` runs both via `asyncio.gather`):

| Port | Bind | TLS | Serves | Audience |
|------|------|-----|--------|----------|
| **8765** | `127.0.0.1` (loopback) | no | Desktop **dashboard** + `/api/*` control plane | The PC only |
| **8766** | `0.0.0.0` (LAN) | **yes** (self-signed) | Companion **PWA** + bridge endpoints | The phone |

The request handler `serve_frontend` (`app.py`) is **port-routed**: a request on 8766 gets the built PWA
(`faster-notes/dist/client`); anything else gets the desktop UI (`static/`). Serving the PWA and the API
from the **same HTTPS origin** (`https://<ip>:8766`) is the key design decision — it means:

- the phone trusts **one** certificate,
- the microphone works (secure context),
- there's **no CORS, no mixed-content, and no Chrome "Local Network Access" prompt** to fight.

This is why the project does **not** use a tunnel (cloudflared/ngrok) or a desktop-shell wrapper — the
same-origin trick solves the public→loopback browser problem with zero third-party egress.

---

## 3. Repository layout

```
Faster Notes/
├── app.py                 # FastAPI app: routes, job queue, Whisper+Ollama pipeline, frontend serving
├── runner.py              # In-process dual-uvicorn runner: TLS cert, mDNS, IP-watcher, file logging
├── paths.py               # Single source of truth for every filesystem path (data vs. bundled assets)
├── store.py               # SQLite + FTS5 activity store (search, migration from legacy JSON)
├── tray.py                # Windows system-tray entry point (pystray); runs runner.run() on a thread
├── start.py               # Thin dev shim → runner.run()
├── requirements.txt       # Runtime deps (pyinstaller is build-only, commented)
├── FasterNotes.spec       # PyInstaller one-folder build spec
├── installer.iss          # Inno Setup installer script
├── conftest.py            # pytest bootstrap (isolates test data under a temp dir)
├── tests/                 # pytest smoke/store/summarize suites
├── static/index.html      # Desktop dashboard (single-file pure-JS app, oklch dark-violet theme)
├── SKILL_token_optimized_v2.md  # LLM prompt (desktop Markdown-output path)
├── projects.json          # Known project ids (tagging context for the LLM)
└── faster-notes/          # The phone PWA (TanStack Start + React 19 + Tailwind 4 + shadcn/ui)
    ├── src/
    │   ├── routes/        # __root, index (record), queue, notes, notes.$id, settings, onboarding
    │   ├── components/recorder/Recorder.tsx   # MediaRecorder capture + waveform
    │   └── lib/           # api.ts, sync.ts, db.ts (IndexedDB)
    ├── public/            # manifest.webmanifest, icons/, sw.js (service worker)
    ├── scripts/build-static.mjs   # SPA self-host build (SPA_BUILD=1, VITE_SELF_HOSTED=1)
    └── dist/client/       # Built PWA served by FastAPI on :8766
```

---

## 4. Backend modules

### `app.py`
The FastAPI application. Responsibilities:
- **Whisper**: `load_whisper()` (idempotent, thread-safe, keep-warm), `transcribe_path()`.
- **Ollama**: `_ollama_generate()`, `analyse_json()` (+ `_map_reduce_transcript()` for long notes),
  `pick_analysis_model()`.
- **Job queue**: in-memory `JOBS` dict, `process_job()` (transcribe → summarize → archive),
  `recover_orphan_jobs()` (startup re-enqueue), `_archive_job()` / `_find_archived()`.
- **Concurrency/durability**: `_transcribe_sem = asyncio.Semaphore(1)` serializes Whisper;
  `_atomic_write_json()` for all JSON writes.
- **Security middleware**: `restrict_control_plane` (404s `/api/*` on the LAN port); `require_auth`
  (Bearer token on bridge endpoints); CORS via `ALLOWED_ORIGIN_RE`.
- **Frontend serving**: `serve_frontend` (port-routed, path-traversal-guarded `_safe_file`,
  JS/manifest MIME pinning via `_media_type_for`).

### `runner.py`
Runs both uvicorn servers in one event loop. Also: rotating file logging (`%LOCALAPPDATA%\FasterNotes\logs`),
self-signed cert generation/refresh (`ensure_cert`, regenerates when the LAN IP changes), **mDNS**
advertising of `fasternotes.local` (`start_mdns`/`update_mdns` via zeroconf), and a daemon **IP-watcher**
thread that re-points mDNS when the network changes (work ↔ home).

### `paths.py`
Splits **writable runtime data** (`%LOCALAPPDATA%\FasterNotes`, override with `FASTER_NOTES_DATA`) from
**read-only bundled assets** (resolved from `sys._MEIPASS` when frozen, else the repo root). Creates the
`models/`, `uploads/`, `logs/` subdirs on import.

### `store.py`
SQLite-backed activity history with **FTS5** full-text search (title/summary/transcript/tags). One lazily-
created connection (WAL, single `RLock`), external-content FTS table kept in sync by triggers, safe
prefix-AND `MATCH` query construction (LIKE fallback if FTS5 is unavailable), and a one-time import of any
legacy `activity.json` (renamed to `activity.json.imported` afterward).

---

## 5. Storage & data locations

All **writable** data lives under `%LOCALAPPDATA%\FasterNotes\` (override with the `FASTER_NOTES_DATA`
env var — used for dev/tests):

| Path | Contents |
|------|----------|
| `config.json` | `whisper_model`, `api_key`, `analysis_model`, `cert_ip` |
| `activity.db` | **SQLite** activity history (+ FTS5 index) — the live note store |
| `activity.json.imported` | Backup of the pre-SQLite history after one-time migration |
| `notes.json` | **Legacy** notes CRUD store (unused by dashboard/PWA) |
| `cert.pem` / `key.pem` | Self-signed TLS cert for :8766 |
| `uploads/` | Audio files awaiting/undergoing transcription (deleted on success) |
| `logs/server.log` | Rotating log (1 MB × 3) |
| `models/` | Downloaded Whisper models (`download_root`) |

**Read-only bundled assets** resolve from `paths.RESOURCE_DIR`: `static/`, `faster-notes/dist/client`,
`SKILL_token_optimized_v2.md`, `projects.json`.

---

## 6. API reference

### Control plane — `/api/*` (loopback :8765 only; **404 on :8766**)
| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/info` | Pairing payload: IPs, hostname, ports, **apiKey**, scheme |
| GET | `/api/activity` | Dashboard feed: `{active, history, server}` |
| GET | `/api/search?q=` | **FTS search** over past notes → `{results}` |
| GET/POST | `/api/settings/analysis-model` | Get/set the Ollama summarization model (null = auto) |
| GET | `/api/whisper/models` · POST `/api/whisper/model` | List / switch Whisper model (reloads) |
| GET | `/api/ollama/models` · `/api/ollama/running` | Proxy Ollama `/api/tags` · `/api/ps` |
| GET | `/api/projects` | Known project ids |
| — | `/api/transcribe`, `/api/analyse`, `/api/notes` (CRUD) | **Legacy**, unused by current UIs |

### Bridge — phone PWA (reachable on :8766; **Bearer token required**)
| Method | Path | Purpose |
|--------|------|---------|
| GET | `/status` | Health/reachability check |
| POST | `/upload` | multipart `file` + `recordingId` + `metadata` → `{jobId}` |
| GET | `/job/{id}` | `{status}` (queued/transcribing/summarizing/done/failed); falls back to archive after restart |
| GET | `/result/{id}` | `{transcript, summary, actionItems, tags}`; falls back to archive after restart |

Auth: `Authorization: Bearer <api_key>`. The key is auto-generated into `config.json` on first run.

---

## 7. The phone PWA

**Stack:** TanStack Start + React 19 + Tailwind 4 (oklch dark-violet) + shadcn/ui + `idb`. Mobile-first
with Record / Queue / Notes / Settings tabs.

- **Capture** (`Recorder.tsx`): `MediaRecorder` (webm/opus, falls back to platform default e.g. mp4/aac on
  iOS), live waveform via `AnalyserNode`, pause/resume, review-before-save.
- **Offline queue** (`db.ts`): IndexedDB is the source of truth — object stores `recordings`, `pairing`,
  `results`. Recordings are saved locally first, regardless of connectivity.
- **Sync** (`sync.ts`): `drainQueue()` uploads pending recordings sequentially, polls each job
  (`POLL_INTERVAL_MS=3000`, `POLL_MAX_ATTEMPTS=400` ≈ 20 min — CPU Whisper is slower than real-time), then
  saves the result. Triggered on `online`/`visibilitychange` and a 60 s interval.
- **API client** (`api.ts`): `MOCK_API=false` (set true for a UI-only demo); scheme-aware `baseUrl`
  (`secure` flag → https); `testServer()` returns human-readable failure reasons.
- **Service worker** (`public/sw.js`): cache-first app shell + stale-while-revalidate static assets so the
  PWA **opens offline**; API/bridge GETs and all non-GET requests are never cached. Registered in
  `__root.tsx`, **gated on `import.meta.env.VITE_SELF_HOSTED === "1"`** (so it's active only for the
  self-hosted build, not the Lovable SSR build).

### Two build modes (important)
| Command | Mode | Output | Use |
|---------|------|--------|-----|
| `npm run build` | Lovable SSR (SPA **off**) | — | Lovable hosting; **never** enable SPA here (prerender crawls a local server and crashes the sandbox) |
| `npm run build:static` | SPA on (`SPA_BUILD=1`, `VITE_SELF_HOSTED=1`) | `faster-notes/dist/client/` | **Self-hosting from FastAPI** |

node/npm only (no bun). The Lovable wrapper forces base `/` and ignores router basepath, so the PWA is
served at root and selected by **port**, not a subpath.

---

## 8. The desktop dashboard

`static/index.html` — a single-file pure-JS app (no build step). Polls `/api/activity` every 3 s to show:
- **Processing now** (active jobs with stage), **History** (clickable → transcript/summary/tags detail),
- **Server status** (Whisper ready, Ollama reachable, queue), **Connect Phone** QR, **Settings**
  (Whisper + Ollama model pickers),
- **History search box** (`#history-search`) — debounced calls to `/api/search`; the 3 s poll skips
  re-rendering history while a search query is active so results aren't clobbered.

---

## 9. Pairing & connectivity model

- **QR pairing**: the dashboard "Connect Phone" QR encodes `{server, port, apiKey, secure}` from
  `/api/info`. **Must be dark-on-light** (`colorDark:'#0b0b12'`) or phone scanners can't read it.
- **Address picker**: on a multi-homed PC (Wi-Fi + Ethernet), `/api/info` returns `ips: [...]` and the
  dashboard lets you pick which interface to advertise.
- **Stable hostname**: `fasternotes.local` via mDNS survives IP changes (laptop work↔home) because the cert
  SAN already includes it and the IP-watcher re-points the name. Resolves natively on iOS/macOS/Windows;
  Android browsers are flaky → IP picker is the fallback.
- **Tray deep-link**: `/?pair=1` auto-opens the QR (used by the tray "Pair a phone" menu).
- The dashboard is **loopback-only (8765)** — never advertise it for pairing; the phone must use **8766**.

---

## 10. Security model

- **Same-origin serving** removes CORS/mixed-content/LNA as attack surface for the main flow.
- **Control plane is loopback-only**: `restrict_control_plane` middleware returns 404 for any `/api/*`
  request arriving on the LAN port (8766), so a device on the same Wi-Fi cannot read `/api/info` (which
  contains the `api_key`) or change server settings.
- **Bridge auth**: every bridge endpoint requires `Authorization: Bearer <api_key>` (`require_auth`).
- **CORS** is restricted by `ALLOWED_ORIGIN_RE` to loopback / private-LAN / `fasternotes.local` /
  `*.lovable.app` (no wildcard).
- **Bind discipline**: dashboard binds `127.0.0.1`; the firewall rule (installer) opens **only** 8766.
- **TLS**: self-signed cert, 10-year validity, SAN = `localhost`, `fasternotes.local`, `127.0.0.1`, LAN IP.
- **Path-traversal guard**: `_safe_file` confines static serving to the asset roots.
- **At rest**: SQLite/JSON are **plaintext** (personal-use decision; SQLCipher is a deferred option).

---

## 11. Transcription & LLM pipeline

### Whisper (faster-whisper / CTranslate2)
- CPU, `compute_type="int8"`, `beam_size=5`.
- `vad_filter=True` (strips silence) + `condition_on_previous_text=False` — these two fixed the Danish
  **hallucination** bugs (fake subtitle credits like "Scandinavian Text Service 2018" and "DAS DAS DAS"/
  "… …" repetition loops on trailing silence). These were never truncation issues.
- Model loaded **once** at startup (keep-warm), switchable at runtime (`POST /api/whisper/model`).
- `WHISPER_MODELS`: tiny / base / small (default) / medium / large-v3-turbo / large-v3 / **NB-Whisper
  Large** (`Necklace/faster-nb-whisper-large`, tuned for Danish/Norwegian/Swedish).

### Ollama summarization
- REST `http://localhost:11434/api/generate`. Ollama is an **external prerequisite** (not bundled);
  models download on first use.
- **Bridge path** (PWA): `analyse_json()` returns `{summary, action_items, tags}` JSON. For transcripts
  longer than `MAP_REDUCE_CHARS` (6000), `_map_reduce_transcript()` summarizes `CHUNK_CHARS` (4000)
  word-boundary chunks, then reduces — keeping each call inside the local model's context window. Malformed
  model JSON degrades to empty fields instead of failing the job.
- **Desktop path**: `/api/analyse` uses the richer Markdown prompt in `SKILL_token_optimized_v2.md`
  (multi-finding output, `{projects}` placeholder filled from `projects.json`).

### Job lifecycle
`queued → transcribing → summarizing → done | failed`. If summarization fails, the transcript is still
returned (partial-result fallback). Audio is persisted to `uploads/` before processing and deleted on
completion; orphans are re-enqueued on startup.

---

## 12. Packaging & distribution

- **`FasterNotes.spec`** (PyInstaller, one-folder). `collect_all()` for the native ML stack
  (`faster_whisper`, `ctranslate2`, `onnxruntime`, `av`, `tokenizers`, `huggingface_hub`, `zeroconf`); PyAV
  bundles FFmpeg (no separate install). Bundles `static/`, **`faster-notes/dist/client`**,
  `SKILL_token_optimized_v2.md`, `projects.json` as data. Entry point is `tray.py`, windowed (no console).
  → `dist/FasterNotes/FasterNotes.exe`.
- **`installer.iss`** (Inno Setup): installs to Program Files (admin), HKCU Run autostart, firewall rule
  for 8766 only, uninstaller. → run `iscc installer.iss`.
- **`tray.py`**: pystray + Pillow tray icon; menu = Open dashboard / Pair a phone / Quit. Runs
  `runner.run()` on a daemon thread.

> **The exe is a frozen snapshot.** Both the Python backend **and** the PWA build are baked in at
> `pyinstaller` time. Rebuilding the PWA (`npm run build:static`) does **not** update a running exe —
> you must rebuild the exe (and re-run the installer to update the installed copy).

---

## 13. Build & run

### Dev (from source)
```bash
# Dashboard only (loopback), fastest for backend iteration:
python -m uvicorn app:app --port 8765
# Full app exactly like the exe (both ports + TLS + mDNS):
python start.py
```
First run downloads the Whisper model (`small` ≈ 244 MB) from HuggingFace.

### Rebuild the packaged app
```bash
cd faster-notes && npm run build:static && cd ..   # 1. build the PWA (with service worker)
pyinstaller --noconfirm FasterNotes.spec           # 2. re-bundle backend + PWA → dist/FasterNotes
iscc installer.iss                                  # 3. (optional) rebuild installer, then reinstall
```
Order matters — the PWA must be built **before** PyInstaller so the fresh `dist/client` is bundled. Quit
any running instance first (it locks the exe and holds 8765/8766).

### Local verification without touching real data
Point the app at a throwaway data dir and use a free port:
```bash
FASTER_NOTES_DATA="/tmp/fn" python -m uvicorn app:app --port 8799
```

---

## 14. Testing

`python -m pytest` from the repo root (**18 tests**). `conftest.py` sets `FASTER_NOTES_DATA` to a temp dir
so tests never touch real data; Whisper/Ollama are stubbed (fast, offline).

| File | Covers |
|------|--------|
| `tests/test_smoke.py` | Control-plane port guard, bridge auth, CORS allow/deny, path traversal, JS MIME, SW served on 8766, upload→job→result pipeline + restart/archive fallback |
| `tests/test_store.py` | SQLite ordering, JSON-column round-trip, FTS search (+ prefix, punctuation, REPLACE re-index), legacy-JSON migration |
| `tests/test_summarize.py` | Chunking, single-shot vs. map-reduce path selection, bad-JSON fallback |

---

## 15. Configuration

`config.json` (in the data dir):

| Key | Meaning |
|-----|---------|
| `whisper_model` | Active model id (default `small`) |
| `api_key` | Bearer token for the bridge (auto-generated, persistent) |
| `analysis_model` | Ollama summarization model; `null` = auto-pick first installed |
| `cert_ip` | LAN IP the TLS cert was issued for (triggers regen on change) |

Env: `FASTER_NOTES_DATA` overrides the writable data directory.

---

## 16. Known constraints & gotchas

- **iOS PWA**: no reliable background audio capture (iOS suspends backgrounded PWAs), 7-day cache expiry,
  no Background Sync. Capture must happen with the app foregrounded. Design for foreground capture.
- **Two lifespans**: both uvicorn servers share one `app`, so lifespan startup runs **twice** — startup
  work must be idempotent (`load_whisper` lock; `recover_orphan_jobs` run-once flag).
- **Windows `.js` MIME**: `mimetypes` can return `text/plain` for `.js`, which breaks ES modules **and**
  service-worker registration — `_media_type_for` pins `text/javascript`.
- **Whisper is slower than real-time on CPU** — long clips take minutes; the phone's 20-min poll budget
  accounts for this. The server keeps working even if the phone gives up polling.
- **Never enable SPA mode unconditionally** in `vite.config.ts` — it breaks the Lovable push.
- **Self-signed cert**: the phone must accept it once (open `https://<ip>:8766` in the browser first).

---

## 17. Deferred / future roadmap

Intentionally **not** built (with the trigger to revisit):
- **Streaming transcription (WebSocket/LocalAgreement)** — *rejected*: contradicts the offline-first
  record-then-upload design. Only for a future live same-network dictation mode.
- **Speaker diarization (WhisperX/pyannote)** — needs a GPU; irrelevant for single-speaker notes. Revisit
  for multi-speaker meeting capture.
- **At-rest encryption (SQLCipher)** — add if transcripts become sensitive.
- **Multi-user / team sync (accounts + RBAC, PocketBase/CouchDB, CRDTs)** — only after single-user is
  rock-solid.

Small optional housekeeping:
- Legacy `notes.json` / `/api/notes` could be removed or migrated to SQLite.
- `MAP_REDUCE_CHARS` (6000) threshold may want tuning after real long-meeting tests.
- Activity history is now unbounded in SQLite — a retention/prune policy is a future nicety.

---

## 18. Hardening changelog

Work completed in the 2026-06 review/hardening pass (see the plan at
`~/.claude/plans/c-dev-faster-notes-app-research-md-take-fluttering-wreath.md`):

**P0 — correctness & security**
- **P0.1** Offline **service worker** (`faster-notes/public/sw.js`) so the PWA loads away from the server.
- **P0.2** **LAN control-plane lockdown** — `/api/*` 404s on :8766, closing the `/api/info` key-leak.
- **P0.3** **Job recovery** — re-enqueue orphaned uploads on startup; `/job` & `/result` fall back to the
  archive after a restart (archive now stores `action_items`).
- **P0.4** **Atomic JSON writes** + **single-worker** transcription semaphore; jobs wait for the model to
  finish loading instead of crashing on a `None` model.

**P1 — features & robustness**
- **P1.1** **SQLite + FTS5** activity store (`store.py`) with `/api/search` and a dashboard search box;
  one-time migration from `activity.json`.
- **P1.2** **Map-reduce summarization** for long transcripts; hardened JSON parsing.
- **P1.3** **CORS** narrowed off the wildcard to an allow-list regex.
- **P1.4** **18-test pytest suite** (smoke / store / summarize).

Also: pinned JS/manifest MIME types in `serve_frontend` for reliable SW + module loading on Windows.
