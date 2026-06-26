# Build "Faster Notes" — an offline-first voice-notes PWA

You are an expert React + TypeScript engineer and product designer. Build a
polished, installable, **mobile-first Progressive Web App** for capturing voice
notes anywhere — even with no internet — that later sync to a self-hosted
transcription server.

## Product in one sentence
Record voice notes offline on your phone; when you're back on your home network
they auto-upload to a private server that transcribes them (Whisper) and
summarizes them (a local LLM), then the results sync back to your phone.

## The core loop
Record (offline OK) → store locally → auto-upload when the paired server is
reachable → server transcribes + summarizes → results poll back → view on phone.

## ⚠️ Scope (important)
This project is the **phone-side PWA only**. The transcription + LLM server
already exists as a self-hosted FastAPI service on the local network. Build the
PWA as an **HTTP client** of that server. **Do NOT run Whisper or any LLM in the
browser.** Everything ML happens server-side; the PWA records, queues, uploads,
polls, and displays.

## Tech stack
- React + TypeScript + Vite
- Tailwind CSS + shadcn/ui components + lucide-react icons
- Framer Motion for transitions and micro-interactions
- **PWA**: web manifest + service worker, installable, offline app shell
- **IndexedDB** (via `idb`) for recordings, results, and pairing config
- `MediaRecorder` capturing `audio/webm;codecs=opus`
- QR scanning for pairing (e.g. `html5-qrcode`)

## App structure — bottom tab navigation
Four tabs, thumb-reachable: **Record · Queue · Notes · Settings**

### 1. Record (home / hero screen)
- A large, central, tactile **record button** as the focal point of the app.
- While recording: live animated **waveform** + a `tabular-nums` **timer**,
  pulsing record indicator, **Pause / Resume**, and **Stop**.
- After stopping: inline **audio playback** of the clip with a Save / Discard
  choice and an optional title field. Saving enqueues it for upload.
- Recording must work fully **offline** — never block on network.

### 2. Queue
- A list of cards, newest first, each showing status as a colored pill:
  **Pending · Uploading (with progress) · Processing · Done · Failed**.
- Failed items get a **Retry** button; a manual **"Upload now"** action.
- Pull-to-refresh; live connection state at the top (server reachable or not).

### 3. Notes (transcript viewer)
- List of completed notes: title + short summary preview + date.
- **Search** across transcripts and summaries.
- Detail view with tabs: **Transcript · Summary · Action items · Tags**.
- Export / copy a note as **Markdown**.

### 4. Settings
- **Paired server** card: IP/host, live connection dot (polls status), and a
  **Re-pair** button that opens the QR scanner.
- Clear local data, storage usage, model info, and an About section.

## First-run pairing flow
If no server is paired, show a friendly onboarding screen with a single
**"Scan QR code"** CTA → request camera permission → scan → store config →
success animation → land on Record. No accounts, no cloud, no login.

## Data model (IndexedDB)
```ts
recordings: { id, createdAt, durationSec, title, mimeType, blob, status }
//   status: 'pending' | 'uploading' | 'processing' | 'done' | 'failed'
pairing:    { server, port, apiKey }          // a single record
results:    { recordingId, jobId, transcript, summary, actionItems[], tags[], completedAt }
```

## Server API contract (the PWA talks to this)
QR payload is JSON: `{ "server": "192.168.1.123", "port": 8000, "apiKey": "..." }`.
Send `Authorization: Bearer <apiKey>` on every request. Base URL =
`http://{server}:{port}`.

- `GET  /status` → `{ ok: true }` (health + auth check; used for connectivity polling)
- `POST /upload` (multipart: `file`, `recordingId`, `metadata`) → `{ jobId }`
- `GET  /job/{jobId}` → `{ status: 'queued'|'transcribing'|'summarizing'|'done'|'failed' }`
- `GET  /result/{jobId}` → `{ transcript, summary, actionItems: [], tags: [] }`

(Endpoint paths may be adjusted later to match the real FastAPI server — keep
the API layer in one isolated module so it's easy to swap.)

## Upload & sync behavior
- Poll `GET /status` every few minutes (and on app focus / regaining network).
- When the server is reachable, drain the queue: upload pending recordings, then
  poll `/job/{id}` until `done`, then fetch `/result/{id}` and store it locally.
- Optimistic UI: status pills update immediately; everything is resumable and
  survives app restarts (all state in IndexedDB).
- On completion, surface a subtle in-app notification: **"Transcript ready."**

## Design direction (treat this as a priority, not an afterthought)
- **Dark-first**, modern, calm. Deep near-black background, one confident accent
  color (a refined violet/indigo), high contrast, generous whitespace.
- Soft, rounded cards (`rounded-2xl`), gentle shadows, layered depth — no flat
  boxy gray UI.
- The **record button is the signature moment**: large, satisfying, with a
  press/scale animation and an expressive live waveform.
- Smooth page/tab transitions, skeleton loaders, and thoughtful **empty states**
  (e.g. an inviting "No notes yet — tap record to start" rather than a blank
  screen).
- Feels like a native app: bottom nav, large tap targets, no desktop-y density.
- **Accessible**: strong contrast, focus states, respects
  `prefers-reduced-motion`, and supports both light and dark themes.

## MVP scope for this first version
1. Record Opus audio with pause/resume, timer, waveform, and playback.
2. Store recordings offline in IndexedDB and survive reloads.
3. QR pairing onboarding + Settings re-pair.
4. Upload queue with status, retry, and auto-sync when the server is reachable.
5. Notes list + detail (transcript / summary / actions / tags) with search.
6. Installable PWA with an offline app shell and an offline banner.

Mock the server responses where needed so the full UI and flows are clickable
end-to-end without a live backend, but keep all network calls behind a single
`api` module so real endpoints drop in cleanly.
