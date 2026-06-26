# Compose screen — Lovable spec

This describes the multimodal **Compose** home screen so it can be rebuilt/mirrored
in Lovable. It was implemented locally in this repo (see `src/components/compose/Compose.tsx`,
`src/lib/useAudioRecorder.ts`); this doc is the portable contract. The server already
accepts the new upload shape — **do not change the API**, just match it.

## Goal

Turn the home screen (`/`) from an audio-only recorder into a general **capture**
surface: type text, record audio, and attach/take photos, then **Send** any
combination to the paired server. It must work **before pairing** (notes queue
locally and send once a server is reachable) — so remove the old redirect from `/`
to `/onboarding`.

## Screen layout (mobile-first, `max-w-md`, inside `AppShell`)

1. **Unpaired banner** (only when no server is paired): a tappable note linking to
   `/onboarding` — "Not paired yet — notes are saved here and will send once you
   connect a server."
2. **Text area** (top, grows to fill): placeholder "What's on your mind? Type here,
   record audio, or snap a photo…".
3. **Attachments** (shown when present):
   - A recorded-audio chip: mic icon, an `<audio controls>` preview, duration, and a
     remove (✕) button.
   - An image thumbnail grid (4-up), each removable (✕).
4. **Live recording strip** (while recording/paused): elapsed time, a small live
   waveform (RMS levels from an AnalyserNode), and a Pause/Resume button.
5. **Bottom action row** (three controls):
   - **Add photo** — opens `<input type="file" accept="image/*" capture="environment" multiple>`.
   - **Record / Stop** — toggles `MediaRecorder` (prefers `audio/webm;codecs=opus`).
   - **Send** — primary; enabled only when there's ≥1 of {text, audio, images} and not
     mid-recording.

Styling: existing shadcn/ui + oklch dark-violet palette; `rounded-2xl` cards,
`bg-record` for the mic/record accent, `glow-primary` on Send.

## Behavior

- **Send** creates one queued note in IndexedDB (status `pending`) carrying any of
  `{ text, blob (audio), durationSec, mimeType, images }`, clears the composer, toasts,
  and triggers the existing `drainQueue()`. Offline → it just stays queued.
- Reuse the existing offline queue + poll/result pipeline unchanged.

## Data model (IndexedDB `recordings` store) — additive, no version bump

Make the audio fields optional and add `text` + `images`:

```ts
export interface Recording {
  id: string;
  createdAt: number;
  title: string;
  status: "pending" | "uploading" | "processing" | "done" | "failed";
  text?: string;          // typed text
  blob?: Blob;            // audio (optional now)
  mimeType?: string;
  durationSec?: number;
  images?: Blob[];        // attached photos
  uploadProgress?: number;
  jobId?: string;
  error?: string;
}
```

## Upload contract (`POST {server}/upload`, Bearer token) — UNCHANGED on server

`multipart/form-data` fields:
- `recordingId` (string, required)
- `metadata` (JSON string: `{ title, durationSec }`)
- `file` (the audio blob) — **omit if no audio**
- `text` (string) — omit/empty if none
- `images` — **repeat this same field name once per photo** (FastAPI `list[UploadFile]`)

Returns `{ jobId }`. Then poll `GET /job/{jobId}` → `{status}` until `done`/`failed`,
and `GET /result/{jobId}` → `{ transcript, summary, actionItems, tags, skillId, output }`
(`output.image_count` tells how many photos were processed). The server combines the
typed text + audio transcript and feeds photos to a vision model; an image-only note
is routed to the default skill.

## Other screens (light touches)

- **Queue**: show a modality label instead of always a duration, e.g.
  `🎙 1:23 · 📷 2 · 📝 Text` (guard `durationSec` — it's optional now).
- **Note detail**: render a horizontal strip of the note's local photos (the server
  returns only the text result; the phone keeps the images).

## Gotchas (cost real debugging here)

1. **Snapshot the FileList immediately.** In the file-input `onChange`, capture
   `Array.from(e.target.files)` into a const **before** resetting `e.target.value = ""`
   and before passing into a `setState(prev => …)` updater. `FileList` is live — if you
   call `Array.from` inside the deferred updater it runs after the reset and captures
   nothing.
2. **Service worker caching (self-hosted build):** after `npm run build:static`, the
   SW serves the old shell. Bump/clear it when testing, or you'll see stale code.
