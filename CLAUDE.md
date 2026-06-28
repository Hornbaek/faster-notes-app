# Faster Notes — server + phone PWA

Local-first AI voice notes for Windows: a **FastAPI server** (faster-whisper transcription
+ Ollama "skills" summarization, packaged as a Windows tray app) plus the companion phone
**PWA** in `pwa/`, which is built and **bundled into the server `.exe`**.

Read **`PROGRESS.md`** first for current state, then **`PROJECT_REFERENCE.md`** (deep
reference) and **`PACKAGING.md`** (build/install). This file is the quick orientation plus
the rules that aren't obvious from the code.

## This is a 3-repo product
- **this repo** — `Hornbaek/faster-notes-app` (public): server (Python at the root) + PWA (`pwa/`).
- `Hornbaek/faster-notes-landing` (private): marketing site → Cloudflare Pages → faster-notes.com.
- `Hornbaek/faster-notes-releases` (public): hosts the downloadable `FasterNotesSetup.exe`.

## Commands
```bash
pip install -r requirements.txt
python start.py                                   # run server: dashboard :8765, phone :8766
python -m pytest                                  # tests (70+)
cd pwa && npm install && npm run build:static     # build the PWA -> pwa/dist/client
pyinstaller FasterNotes.spec && iscc installer.iss  # build the Windows installer (Windows only)
```

## Conventions & gotchas — read before changing things
- **Secrets are in `.env`** (Cloudflare API tokens) and it is **gitignored — NEVER commit it.**
  Same for `config.json`, `cert.pem`, `key.pem`. This repo is **public**.
- **Online/remote sync is intentionally "coming soon", not shipped.** The dashboard's
  "🔒 Remote sync — coming soon" placeholder and the landing's coming-soon wording are
  deliberate — do **not** "fix" them into claiming it works. (The owner's own Cloudflare
  tunnel still auto-resumes from on-disk config; managed multi-user sync is a future phase.)
- **The PWA (`pwa/`) was generated in Lovable; that link is cut** — edit it locally here.
  Build modes matter: `npm run build:static` is the self-hosted build (sets
  `VITE_SELF_HOSTED=1` → registers the service worker, needed for installability). Plain
  `npm run build` is the old Lovable SSR build — don't ship that one.
- **Data dir:** writable runtime data lives in `%LOCALAPPDATA%\FasterNotes\`; override with
  the `FASTER_NOTES_DATA` env var. The server must run as the **normal logged-in user** — a
  sandboxed/elevated launch resolves a different `%LOCALAPPDATA%` → empty dir → no tunnel.
- **Cloud (online) sessions can't run the server or build the Windows `.exe`** (that needs
  Windows + the local machine). You *can* edit/test/build the PWA, edit server code, run
  pytest, and commit. Shipping a new installer is a local Windows step.
