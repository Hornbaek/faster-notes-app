# Faster Notes — companion PWA

Offline-first voice-notes phone app (TanStack Start + React + Tailwind + IndexedDB).
Records anywhere, queues locally, and syncs to the **Faster Notes** transcription
server (`../app.py`, FastAPI + faster-whisper + Ollama) when it's reachable.

## Develop

```bash
npm install
npm run dev          # local dev server (Lovable sandbox config)
```

## Builds — there are two, on purpose

| Command | SPA / prerender | Output | Used for |
| --- | --- | --- | --- |
| `npm run build` | **off** | SSR bundle | Lovable hosting + the Lovable push/build |
| `npm run build:static` | **on** | `dist/client/` | Self-hosting the PWA from FastAPI |

**Why two?** Lovable builds this app for its own SSR/Cloudflare hosting. To serve
it from our local FastAPI server we instead need a plain static client bundle,
which TanStack Start produces in **SPA mode**. But SPA mode prerenders the shell
by crawling a throwaway local server, and that step **crashes inside Lovable's
sandboxed build environment**. So SPA mode is gated behind `SPA_BUILD=1`
(see [vite.config.ts](vite.config.ts)) and only turned on by `build:static`
(via [scripts/build-static.mjs](scripts/build-static.mjs)).

> ⚠️ Never enable `spa` unconditionally in `vite.config.ts` — it breaks the
> Lovable build. Keep it behind `SPA_BUILD`.

## How the local server serves it

`../app.py` chooses a frontend by the port the request arrives on:

- **`https://<ip>:8766/`** (phone) → this PWA, served from `dist/client/`
- **`http://localhost:8765/`** (computer) → the desktop UI in `../static/`

Both share one origin with the API, so the phone trusts **one** TLS cert, the mic
works (HTTPS), and there's no CORS / mixed-content. A subpath like `/app` is *not*
used — the Lovable vite wrapper forces base `/` and ignores a router basepath, so
the PWA lives at root and is selected by port.

### Refresh the PWA on the local server

```bash
npm run build:static          # regenerates dist/client/
# then it's live at https://<ip>:8766/  (run ../start.py)
```

## Connecting a phone

1. `python ../start.py` (serves HTTP :8765 + HTTPS :8766).
2. On the phone open **`https://<ip>:8766/`** and accept the self-signed cert once.
3. Tap **Scan QR code to pair**, scan the **Connect Phone** QR from the desktop app
   (it encodes `{server, port, apiKey, secure}`), then record. Notes queue offline
   and sync when the server is reachable.

## Key integration points

- [src/lib/api.ts](src/lib/api.ts) — `MOCK_API` (false = real server) and the
  scheme-aware base URL. Talks to `/status`, `/upload`, `/job/{id}`, `/result/{id}`.
- [src/lib/sync.ts](src/lib/sync.ts) — upload queue + job polling.
  `POLL_INTERVAL_MS` / `POLL_MAX_ATTEMPTS` (≈120s); raise the latter for long
  meeting recordings on CPU Whisper.
- [src/lib/db.ts](src/lib/db.ts) — IndexedDB stores (recordings, results, pairing).
