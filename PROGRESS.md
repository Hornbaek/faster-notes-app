# Faster Notes — Project Progress & State

Single source of truth for the project's current state. Last updated 2026-06-23.
(Companion docs: [PACKAGING.md](PACKAGING.md) for build/install; plan files under
`~/.claude/plans/`; detailed change log in Claude's memory `project_faster_notes.md`.)

## What it is

A **local-first voice/notes system**: a Windows tray app (FastAPI server) that
transcribes audio with **faster-whisper**, post-processes with a local **Ollama** LLM,
and stores results. A companion **phone PWA** (in `faster-notes/`, built with Lovable —
TanStack Start + React 19 + Tailwind) captures notes and syncs to the server. All
processing is on your own machine; no cloud accounts required for the core.

## Architecture / key files (parent repo = the server)

- **`app.py`** — FastAPI app: `/api/*` control plane (dashboard, loopback-only) +
  token-guarded phone **bridge** (`/status`, `/upload`, `/job`, `/result`). Config,
  Whisper, Ollama, skills pipeline, Cloudflare endpoints, `.env` loader.
- **`runner.py`** — runs BOTH servers in-process: `127.0.0.1:8765` (dashboard) +
  `0.0.0.0:8766` (phone PWA + bridge, self-signed TLS). mDNS (`fasternotes.local`),
  IP-watcher, and the **cloudflared** subprocess lifecycle live here.
- **`tray.py`** — system-tray entry (`runner.run()` on a daemon thread). `--server-only`
  runs headless.
- **`paths.py`** — all paths. Writable data in `%LOCALAPPDATA%\FasterNotes\`
  (config.json, activity.db, cert, models/, uploads/, skills/, bin/cloudflared.exe).
  Read-only bundled assets in `RESOURCE_DIR`. `FASTER_NOTES_DATA` env overrides the
  data dir (dev/tests).
- **`store.py`** — SQLite + FTS5 activity history (`activity.db`).
- **`skills.py`** — pluggable LLM "skills" registry (pure module).
- **`cftunnel.py`** — Cloudflare Tunnel client (download cloudflared + provision via API).
- **`gsheets.py`** — Google Sheets client, **parked/inert** (see Deferred).
- **`static/index.html`** — desktop dashboard (single-file, pure JS).
- **`faster-notes/`** — the phone PWA (its own git repo; Lovable source of truth).
- **`FasterNotes.spec`** / **`installer.iss`** — PyInstaller one-folder build + Inno
  Setup installer (Program Files, login-autostart, firewall rule for 8766).

## Features delivered (all shipped + tested; 62 pytest passing)

1. **Base app** — phone records → `/upload` → Whisper transcribe → Ollama summarize →
   results in dashboard + phone. Offline IndexedDB queue, QR/mDNS pairing, same-origin
   port-routed serving (PWA on 8766, dashboard on 8765).
2. **Skills engine** — each note is auto-routed by an **orchestrator** (a cheap LLM
   "router" call) to the best **skill** (an editable Markdown+frontmatter recipe owning
   prompt + output shape + actions). Bundled skills: `quick-note` (default/fallback),
   `meeting`, `journal`, `curator`. Skills are files in `skills/` (bundled defaults +
   writable user overrides by id), editable in the dashboard **🧩 Skills** modal.
   Actions: `write_file` (to a `{vault}` folder), `webhook`, `append_project`.
   Separate **analysis model** (runs the skill) vs **orchestration model** (routes),
   both pickable in Settings.
3. **Multimodal capture** — the PWA home is a **Compose** screen: type text, record
   audio, attach/take photos — any combination in one note. Server combines typed text +
   transcript and feeds photos to a **vision model** (qwen2.5vl) via Ollama's `images`.
   Desktop **🔀 Flow** modal visualizes input → orchestrator → skill branches.
4. **Remote access (Cloudflare Tunnel) — LIVE** at **`notes.faster-notes.com`**.
   The app provisions a named tunnel via the Cloudflare API (`cftunnel.py`) and runs
   `cloudflared` as a managed subprocess; auto-resumes on startup from config. Domain
   `faster-notes.com` is registered at Cloudflare. Configured in Settings → **Remote
   access**. Control plane stays private (see Security).
5. **QR / key / language fixes (latest)** —
   - Pairing QR **defaults to the remote hostname** (`notes.faster-notes.com:443`,
     real cert) when the tunnel is on; LAN/mDNS chips still selectable.
   - **api_key is stable** across restarts (generated once, reused); `read_config`
     hardened to never silently lose it.
   - **Spoken-language picker** on Compose (Auto / Svenska / Dansk / English, persisted)
     → forces Whisper's language so it stops mislabeling Danish/Swedish as Norwegian.

## Run / build / test

- **Dev (from source):** `python start.py` (dual server, no tray). Tests:
  `python -m pytest` (62 passing). PWA dev build: `cd faster-notes && npm run build:static`.
- **Package the app:** `pyinstaller FasterNotes.spec` → `dist/FasterNotes/FasterNotes.exe`
  (**close the running app first** — it locks `dist/`). Installer: `iscc installer.iss`.
- **⚠️ Local dashboard testing must use port 8765** — `/api/*` is now allowed ONLY on
  8765 (the hardening); an arbitrary port returns 404 for `/api`.
- Prereqs: Python 3.14, Ollama running with a model (vision model like `qwen2.5vl` for
  images), Node/npm for the PWA. No new heavy Python deps (httpx + cryptography reused).

## Current live config (this machine)

- `config.json`: `whisper_model=large-v3-turbo`, stable `api_key`, `cloudflare_enabled=true`,
  `cloudflare_hostname=notes.faster-notes.com` (+ tunnel id/token/zone/account).
- **`.env`** (parent root, KEEP PRIVATE, not committed/bundled): `CLOUDFLARE_API_TOKEN=…`
  (Cloudflare Tunnel:Edit + DNS:Edit). The runtime connector uses the stored
  `cloudflare_tunnel_token`; the API token is only needed to re-provision.

## Security model

- Phone bridge is **Bearer-token** guarded (`api_key`). cloudflared is **outbound-only**
  (no inbound ports).
- **Control-plane lockdown is proxy-safe (fail-closed):** `/api/*` is served ONLY on the
  loopback dashboard port 8765 with no forwarding headers; blocked on 8766 and through
  any proxy/tunnel. (A prior port-only check leaked `/api/info`'s api_key over the
  tunnel — fixed + verified.)
- Tunnel terminates TLS at Cloudflare's edge (real cert) → routes to `https://localhost:8766`.

## Gotchas (learned the hard way)

- **Dashboard `/api` only works on port 8765** (hardening) — test the dashboard there.
- **Service worker caches the PWA shell** — after a `build:static`, clear/unregister the
  SW (or reopen the app) on phone + browser, or you see stale code.
- **Re-pair the phone after the QR/key changes** — old pairings hold a stale key/address.
- **Cold boot / wake-on-LAN:** enable Windows **auto-login** (`netplwiz`) so the app
  starts without an interactive login; then the tunnel auto-resumes (see PACKAGING.md).
- **Cloudflare account id:** a Tunnel+DNS token can't list `/accounts`; read account id
  from the **zone** object (`account_for_zone`).
- **Lovable merges:** the PWA is a separate git repo; resolve future Lovable pulls toward
  their version and reconcile (`LOVABLE_COMPOSE_SPEC.md` documents the compose contract).
- **Live FileList:** snapshot `Array.from(input.files)` before resetting the input.
- **SQLite in a cloud-synced folder corrupts** — don't put the data dir in Drive directly.

## Deferred / next phase (documented, NOT built)

- **Google Sheets mirror** — `gsheets.py` + `paths.GSHEET_CRED_FILE` exist but are inert;
  would append each note to a Sheet (service-account, server-side). Then a two-way
  **Sheets/Drive relay** (phone writes to a Sheet from anywhere → server polls/processes/
  writes back; media via Drive) — needs Lovable Cloud + Google login on the phone.
- **Server as system-of-record** — durable per-note media storage under the data dir +
  a generic exporter (Drive/Dropbox/S3) for backup; a safe scheduled SQLite snapshot
  (`VACUUM INTO`) into a synced folder (avoids the live-DB-corruption footgun).
- **Cloudflare Access** — identity gate in front of the hostname (needs Access
  service-token headers in the PWA).
- **Two-way edit** of notes from the dashboard/phone; richer per-skill action UI.
