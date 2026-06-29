# Faster Notes — Manual Test Guide

A practical checklist to verify the whole app, not just the new connectors. Work top-down;
the **Quick smoke** (≈10 min) catches most regressions, the **Full pass** exercises everything.

> Automated tests already cover the backend: `python -m pytest` (101 passing). This guide is
> the *human* layer — real audio, a real phone, real destinations.

---

## 0. Run the freshly-built app

The new build is at `dist/FasterNotes/FasterNotes.exe` (PWA + connectors baked in). It uses your
real data dir (`%LOCALAPPDATA%\FasterNotes`), so config, notes and pairing carry over.

1. **Quit the currently running app** (tray icon → Quit, or it'll fight for ports 8765/8766).
2. **Launch the new build by double-clicking it in Explorer** — *not* from a terminal (a
   sandboxed/odd shell can resolve the wrong `%LOCALAPPDATA%` → empty data dir → no tunnel).
3. Tray icon appears → **Open dashboard** → `http://localhost:8765`.
   - ✅ Dashboard loads; sidebar shows **📱 Connect Phone · 🧩 Skills · 🔌 Outputs · 🔀 Flow · ⚙ Settings**.
   - The **🔌 Outputs** button = you're on the new build. (If it's missing, you launched the old install.)

> To make it the permanent install you'd normally run the Inno Setup installer (`iscc installer.iss`)
> — not installed on this machine. Alternatively copy `dist/FasterNotes/*` over
> `C:\Program Files\FasterNotes\` (app closed, admin). For testing, running the dist exe directly is fine.

---

## 1. Quick smoke (≈10 min)

- [ ] **Server health** — dashboard shows Whisper *ready*, Ollama *reachable*, queue 0.
- [ ] **Pair phone** — 🔌… 📱 Connect Phone → scan QR on the phone → phone shows "Server reachable".
- [ ] **Capture a voice note** — record 10–20s on the phone → it uploads → transcript appears, then
      summary + tags. Same note shows in dashboard **History**.
- [ ] **Search** — type a word from the note in the dashboard History search → it filters (FTS).
- [ ] **One output** — 🔌 Outputs → New output → **Markdown vault** → every note → Save. Record another
      note → a `.md` file appears in your notes folder (Skills modal sets the folder; default =
      `%LOCALAPPDATA%\FasterNotes\exports`).
- [ ] **Zero-egress sanity** — with no *other* rules, nothing else leaves the machine.

If all six pass, the core pipeline + new output layer are healthy.

---

## 2. Capture & processing (the core)

- [ ] **Text-only note** — type text, no audio → still processed (summary/tags).
- [ ] **Audio note** — record; verify waveform, pause/resume, review-before-save.
- [ ] **Photo note** — attach/take a photo with no text → vision model describes it (needs a vision
      model like `qwen2.5vl` in Ollama).
- [ ] **Combo** — text + audio + photo in one note → all combined in the result.
- [ ] **Language picker** — set Compose language to **Dansk**/**Svenska**, speak that language →
      transcript isn't mislabeled as Norwegian; summary is in the same language.
- [ ] **Long note** — record/paste a long transcript (>~6000 chars) → still summarizes (map-reduce),
      no truncation, no "DAS DAS DAS"/repetition hallucinations on trailing silence.

## 3. Skills engine (routing & editing)

- [ ] **Auto-routing** — record a meeting-ish note ("we decided…, action: …") → routed to **Meeting**
      skill (decisions/attendees fields); a reflective note → **Journal**; generic → **Quick note**.
- [ ] **🔀 Flow** modal — shows Input → Orchestrator → skill branches; ★ on the default.
- [ ] **Edit a skill** — 🧩 Skills → edit Quick note prompt → **Run test** with sample text → preview
      output (no actions run) → Save → reflected on next note. Reset reverts to the shipped version.
- [ ] **New skill** — create one with a custom JSON field → it appears, routes, renders the field in
      the note detail.
- [ ] **Default fallback** — ★ a different skill → unmatched notes use it.

## 4. Offline & sync resilience

- [ ] **Offline capture** — put phone in airplane mode → record → note sits in **Queue** (pending).
- [ ] **Drain on reconnect** — re-enable network → queue uploads automatically (or on app focus / 60s).
- [ ] **PWA opens offline** — kill Wi-Fi, force-close & reopen the PWA → app shell still loads
      (service worker). New captures queue.
- [ ] **Job recovery** — record a note, then **quit the server while it's transcribing** → relaunch →
      the orphaned upload re-enqueues and finishes; `/job` & `/result` resolve from the archive.
- [ ] **Re-pair** — after any QR/key change, re-scan; an old pairing with a stale key should fail
      cleanly, then work after re-pair.

## 5. Notes browsing

- [ ] **Phone** — Notes tab lists notes; tap → tabs Summary / Script / Actions / Tags. Copy as
      Markdown, export `.md`, play back audio, **re-transcribe** (try a different Whisper model).
- [ ] **Dashboard** — History list, click a note → transcript/summary/tags; search box filters live.

---

## 6. 🔌 Outputs / Connectors (the new layer)

Open **🔌 Outputs**. Test by family; each rule fires when a *new note* is processed (or use the
**Test** button in the rule editor to dry-run / send without waiting for a note).

### 6a. Secrets (privacy)
- [ ] Add a secret (e.g. `slack_webhook_url`) → it's listed by **name only**.
- [ ] Reload the page / reopen modal → the **value is never shown** again. Delete works.

### 6b. Local files (zero-credential — safest)
- [ ] **Markdown vault** → new note → one `.md` file per note in your folder.
- [ ] **Obsidian daily** → multiple notes on the same day → all **appended** to `Daily/YYYY-MM-DD.md`.

### 6c. Automation webhooks
- [ ] **Slack/Discord** — paste a real Incoming Webhook URL as the secret → rule → Test (tick
      "Actually send") → message appears in the channel.
- [ ] **n8n/Zapier/generic** — point at a catch hook (or https://webhook.site) → Test → the full
      note JSON arrives (summary, action_items as an array, tags, fields, transcript).

### 6d. Direct APIs
- [ ] **Todoist** — secret `todoist_token` → Test (Actually send) → a task appears in your Inbox;
      tags become labels. The Test output shows the API's **status + response body**.
- [ ] **Notion** — integration token as `notion_token`, share a DB with it, paste the `database_id`
      in the rule's config field → Test → a page is created (DB title column must be named "Name").
      A wrong token/id surfaces Notion's real error in the Test response.

### 6e. Email & calendar
- [ ] **Email (SMTP)** — fill host/username/from/to + `smtp_password` (Gmail needs an app password,
      `smtp.gmail.com`) → Test (Actually send) → email arrives with summary + transcript.
- [ ] **ICS calendar** — rule → new note → a `.ics` file appears in `…/Calendar`; double-click to
      import the all-day event into your calendar.

### 6f. Routing
- [ ] **Tag rule** — rule with *Send when = tags = work* → a note tagged `work` triggers it; an
      unrelated note does **not**.
- [ ] **Skill rule** — *Send when = skills = meeting* → only meeting-routed notes trigger it.
- [ ] **Global rule** — *every note* → fires on all. **Multiple rules** can fire on one note (plus the
      skill's own actions).

### 6g. Reliability & safety
- [ ] **Delivery log** — every send is listed (sent/failed, target, time).
- [ ] **Retry** — point a webhook rule at a URL that's down → process a note → entry shows **failed** →
      bring the endpoint up → **Retry** (or wait for the auto-retry sweep) → flips to **sent**.
- [ ] **Redaction** — Test a connector with a secret in the URL (dry-run, don't send) → the rendered
      request shows `***`, never the real token.
- [ ] **Disable / delete** a rule → it stops firing.

---

## 7. Remote access (Cloudflare tunnel)

> Currently `cloudflare_enabled` is **off** in your config. Enable to test.

- [ ] Settings → **Remote access** → enable → tunnel connects → `notes.faster-notes.com` reachable.
- [ ] **Pair via remote** — QR defaults to the remote hostname → phone works off your home network
      (try on cellular).
- [ ] **Control-plane lockdown** — from the phone/over the tunnel, hitting `…/api/info` or
      `…/api/connectors` returns **404** (only the bridge endpoints work remotely; secrets stay local).

## 8. Security spot-checks

- [ ] `/api/*` works on **8765 only** (loopback) — a request on 8766 or through the tunnel → 404.
- [ ] Bridge endpoints (`/upload`, `/status`…) require the **Bearer token** — wrong/missing key → 401.
- [ ] Connector **secrets never appear** in `/api/*` responses, the delivery log, or server logs.

---

## Notes
- If summaries are empty/odd: confirm Ollama is running with a model (`ollama list`); a vision model is
  needed for photo notes.
- After any PWA rebuild, **unregister the service worker / reopen** the PWA on the phone to avoid a
  stale shell.
- The data dir is `%LOCALAPPDATA%\FasterNotes` — don't put it inside a live-synced cloud folder
  (SQLite corruption risk); the connector *outputs* are what you point at synced folders.
