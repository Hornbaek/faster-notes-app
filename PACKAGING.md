# Packaging Faster Notes as a Windows app

The server runs as a **system-tray app** (`tray.py`): a tray icon owns the UI while
two servers run in-process — the dashboard on `127.0.0.1:8765` (loopback only) and
the phone PWA + bridge on `0.0.0.0:8766` (TLS). All writable data lives under
`%LOCALAPPDATA%\FasterNotes\` (config, cert, history, uploads, logs, models).

## Prerequisites
- **Python 3.11+** with `pip install -r requirements.txt` and `pip install pyinstaller`
- **Node + npm** (to build the PWA once)
- **Inno Setup 6** (for the installer) — https://jrsoftware.org/isdl.php
- **Ollama** installed and running, with at least one model pulled
  (`ollama pull qwen2.5`) — this stays a separate prerequisite; it is not bundled.

## Build steps
```bash
# 1. Build the phone PWA into faster-notes/dist/client (bundled into the exe)
cd faster-notes && npm install && npm run build:static && cd ..

# 2. Freeze the tray app into dist/FasterNotes/
pyinstaller FasterNotes.spec

# 3. Make the installer -> dist/FasterNotesSetup.exe
iscc installer.iss
```

## What the installer does
- Installs to `C:\Program Files\FasterNotes` (one UAC prompt).
- Start-Menu shortcut (+ optional desktop shortcut).
- Optional **autostart at login** (HKCU Run).
- Adds a **Windows Firewall** inbound rule for TCP **8766** (the phone port).
  Port 8765 is loopback-only, so it needs no rule.
- Uninstaller removes the shortcuts, autostart entry, and firewall rule.
  (It does **not** delete `%LOCALAPPDATA%\FasterNotes` — your history/config stay.)

## Running it
- Launch **Faster Notes** → a tray icon appears (it may take a few seconds on first
  run while the Whisper model downloads; the dashboard shows "loading…").
- Tray menu: **Open dashboard**, **Pair a phone** (opens the QR), **Quit**.
- On the phone, open `https://<this-pc-ip>:8766/`, accept the certificate once, and
  scan the QR.

## First-run / runtime notes
- The chosen Whisper model downloads to `%LOCALAPPDATA%\FasterNotes\models` on first
  use (needs internet once; up to ~1.5 GB for large-v3).
- If Ollama isn't running, transcription still works but summaries fall back to the
  raw transcript — start Ollama and pick a model in **Settings**.
- Logs: `%LOCALAPPDATA%\FasterNotes\logs\server.log` (rotating).
- The self-signed cert auto-regenerates when your LAN IP changes.

## Always-on remote access (cold boot / wake-on-LAN)
For "turn the PC on (or wake it) → connect from my phone anywhere":
- **Pairing key is stable.** `api_key` is generated once and reused (in `config.json`),
  so a paired phone keeps working across restarts. If a phone shows 401 after these
  changes, it's holding an *old* key — just re-pair from the QR once.
- **Pair to the Cloudflare hostname** (`notes.faster-notes.com`), which is stable
  regardless of the PC's IP. The QR now defaults to it when remote access is enabled.
- **Auto-start:** the installer adds login-autostart (HKCU Run); on launch the app
  auto-resumes the tunnel from config (`cloudflare_enabled` + `cloudflare_tunnel_token`).
- **For a true cold boot with no one at the keyboard, enable Windows auto-login** so the
  PC logs in on boot and the app starts:
  - `netplwiz` → untick "Users must enter a user name and password…" → enter the
    password once (or run `control userpasswords2`). On some builds this option is
    hidden until you disable Windows Hello sign-in
    (Settings → Accounts → Sign-in options → "require Windows Hello sign-in" = Off).
  - Then: power on / wake-on-LAN → auto-login → tray app starts → tunnel comes up →
    phone reconnects to `notes.faster-notes.com` automatically.
  - Wake-from-**sleep** needs nothing extra — the session and app stay running.

## Dev (no packaging)
`python start.py` runs the same two servers from source (no tray). Point data
somewhere else with `set FASTER_NOTES_DATA=...` before launching.

## Known risks
- The native ML deps (`ctranslate2`, `onnxruntime`, `av`) are the fiddly part of the
  PyInstaller build; if the exe fails with a missing-DLL/module error, add the
  offending package to the `collect_all(...)` loop in `FasterNotes.spec`.
- The exe is unsigned → a one-time Windows SmartScreen/antivirus warning
  ("More info" → "Run anyway").
