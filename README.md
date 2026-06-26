# Faster Notes

Local-first AI voice notes for Windows. Record on your phone, transcribe with
**Whisper** and summarise with **Ollama** — everything runs on your own PC. No cloud
accounts, no subscription.

This repo contains both halves of the product:

- **Server** (`app.py`, `runner.py`, …) — a FastAPI app that serves a desktop dashboard
  (loopback) and a phone bridge + PWA (LAN/TLS), runs faster-whisper for transcription,
  and an Ollama-backed "skills" pipeline for summaries/action-items/tags. Packaged as a
  Windows tray app.
- **PWA** (`pwa/`) — the companion phone web app (TanStack Start + React + Tailwind).
  Built to `pwa/dist/client` and bundled into the server executable.

> The marketing site lives in a separate repo; the Windows installer is published as a
> GitHub release.

## Run (dev)

```bash
# server
pip install -r requirements.txt
python start.py            # dashboard http://localhost:8765, phone https://<ip>:8766

# phone PWA (built output is served by the server)
cd pwa && npm install && npm run build:static
```

First run downloads the Whisper model. Ollama is optional (transcription works without
it). Override the data directory with the `FASTER_NOTES_DATA` env var.

## Build the Windows installer

```bash
cd pwa && npm run build:static        # build the PWA first
pyinstaller FasterNotes.spec          # -> dist/FasterNotes/
iscc installer.iss                    # -> dist/FasterNotesSetup.exe
```

See `PACKAGING.md` for details and `PROGRESS.md` for the current state of the project.

## License

Free and open source.
