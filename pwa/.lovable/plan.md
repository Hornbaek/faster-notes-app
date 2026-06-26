# Faster Notes — Cloud, Getting Started, Settings split

## 1. Enable Lovable Cloud + auth

- Turn on Lovable Cloud for this project.
- Add a `/auth` page with two tabs: **Email + password** (sign up / sign in) and **Continue with Google** (via the Lovable Google broker, then enable Google in Supabase Auth).
- Wrap signed-in pages with the integration-managed `_authenticated` layout. The onboarding/auth pages stay public.
- Show the signed-in user's email + sign-out button in Settings.

## 2. Per-user cloud sync (notes + pairing + queue)

Three Postgres tables, all RLS-scoped to `auth.uid()`:

- `pairings` — one row per user (`server`, `port`, `api_key`, `secure`). The local QR validation we just added still runs before insert.
- `recordings` — queue mirror (`id`, `status`, `duration_sec`, `title`, `created_at`, `job_id`, `error`, `audio_path` nullable).
- `note_results` — finished notes (`recording_id`, `transcript`, `summary`, `action_items`, `tags`, `completed_at`).
- Storage bucket `recordings/` (private, RLS by user folder) for the audio blobs.

Sync strategy (offline-first stays intact):

- IndexedDB remains the source of truth on the device for in-flight recordings.
- After upload to the paired desktop server succeeds and a note result comes back, push `recordings` row + audio file + `note_results` row to Cloud.
- On app start (signed in), pull rows the device doesn't have yet — newer-wins by `updated_at`.
- Pairing row is read on sign-in and written to IndexedDB so existing code paths keep working.

Security note: the desktop server API key sits in a user-owned row protected by RLS — same trust level as the device. We'll flag this in security memory.

## 3. Homepage = Getting Started

New flow on `/`:

```text
                    ┌─ not signed in ──► /auth
signed in + …
  ├─ no pairing ──► Getting Started page
  │                 Step 1: Download desktop server (button → installer file)
  │                 Step 2: Run it on your computer
  │                 Step 3: "Pair with QR code" → opens existing QrScanner
  └─ paired ──────► Recorder (current `/` UI)
```

- You'll upload the installer file in chat; I'll drop it under `public/downloads/` and wire the Download button to it. The button shows file size + version.
- Getting Started is reachable any time from Settings ("Show setup guide").

## 4. Settings — split destructive actions

Replace the single "Clear local data" button with three clearly separated rows, each with its own confirm dialog:

1. **Reset pairing** — removes pairing locally + in Cloud, returns to Getting Started.
2. **Clear notes** — deletes recordings + results locally + in Cloud (audio files too).
3. **Sign out** — ends the session (kept separate from data deletion).

Also surface storage usage (already computed) above these rows.

---

## Technical notes

- Cloud DB writes go through `createServerFn` with `requireSupabaseAuth` (one `*.functions.ts` module per entity). Storage uploads use the browser client with the user's session.
- `src/lib/sync.ts` gets a second phase: after the desktop server returns a result, push to Cloud; on app boot, pull deltas into IndexedDB and emit() so the UI updates.
- Auth gate: move `index.tsx`, `queue.tsx`, `notes.*`, `settings.tsx` under `src/routes/_authenticated/`. `/auth` and `/onboarding` stay public. `/` redirect logic becomes: no pairing → Getting Started view; paired → Recorder.
- Add `installer` asset: `public/downloads/faster-notes-server-<version>.<ext>` once you upload it.
- Update security memory: pairing row now stored server-side per user (RLS-protected), not just on device.

## Out of scope (ask if you want any of these)

- Multi-device live sync of in-flight recordings (we sync after the result is final).
- Sharing notes between users.
- Versioned/edit history for notes.
- Auto-update channel for the desktop installer.
