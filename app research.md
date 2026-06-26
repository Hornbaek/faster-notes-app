# Locally-Hosted Transcription + LLM Note-Taking Apps: Landscape Survey & Architecture Planning Document

## TL;DR
- The architecture (Python/Whisper server + externally-hosted PWA) is validated by a crowded 2026 field led by Meetily, Anarlog, Screenpipe, and WhisperLive — but almost all winners ship as **desktop apps (Tauri/Electron/Rust)**, not externally-hosted PWAs, because of one blocking issue: starting with Chrome 142 (released October 28, 2025), an HTTPS page calling `http://localhost` triggers a "Local Network Access" permission prompt, with no server-side opt-out.
- The single most important near-term decision is **how the PWA reaches the local server**: keep loopback HTTP and accept the LNA prompt + CORS, run the local server over real HTTPS, use a tunnel (cloudflared/ngrok), or repackage as a desktop shell. This decision cascades into every security and stability choice.
- For the transcription/LLM core, the proven stack is **faster-whisper (CTranslate2) + WebSocket streaming with a LocalAgreement/VAD chunking policy + Ollama for summarization + SQLite (optionally SQLCipher) for storage** — exactly the components this developer already has, so the work is integration and hardening, not reinvention.

## Key Findings

1. **The space is mature and converging.** Meetily (Zackriya Solutions; the GitHub repo header reads "Fork 951 · Star 10.3k" in June 2026, MIT) is the reference open-source competitor: Tauri + Next.js frontend, FastAPI backend, Whisper.cpp/Parakeet transcription, Ollama summarization, SQLite + vector DB. Anarlog, Char (fastrepl), Notes4Me, ownscribe, Squirrel Notes, and Screenpipe (YC S26; GitHub reports ~16.8k stars) round out a clear pattern: **local transcription + pluggable LLM + local storage + Markdown/Obsidian export.**
2. **Nobody else is doing externally-hosted-PWA + local-server.** This is the developer's genuine differentiator and biggest technical risk. The whole field ships native desktop apps precisely to avoid the browser sandbox constraints (mixed content, Local Network Access, background-audio limits) that this architecture runs straight into.
3. **WebSocket streaming with a LocalAgreement-n / VAD policy is the settled real-time pattern.** The reference implementations are ufal/whisper_streaming, collabora/WhisperLive, and WhisperLiveKit. Raw 30-second Whisper chunking does not work for low latency.
4. **The Chrome 142 Local Network Access change is the defining constraint** and must drive the roadmap.
5. **Security model is unusual:** because the PWA origin is external (Lovable) but the server is local, this is effectively a cross-origin, public→loopback scenario — the exact case browsers are now locking down. CORS origin allow-listing + a shared secret/token + localhost binding are mandatory.
6. **Local-first sync (for the team roadmap) is a solved problem** with PocketBase, CouchDB/PouchDB, or CRDT libraries (Yjs/Automerge), but should be deferred until single-user is rock-solid.

## Details

### 1. Existing Projects & Tools

**Full meeting-notes apps (closest competitors):**
- **Meetily / meeting-minutes (Zackriya-Solutions)** — the benchmark. The GitHub repo header reads "Fork 951 · Star 10.3k" (June 2026), MIT. Stack: Tauri + Next.js (frontend), **FastAPI (backend)**, Whisper.cpp + Parakeet (transcription, with speaker diarization and "4x faster" live transcription), Ollama (local LLM) plus optional Claude/Groq/OpenRouter/OpenAI, SQLite + ChromaDB (semantic search). Notable: they explicitly credit borrowing code from Whisper.cpp and Screenpipe. Community Edition free; Pro $10/user/month; Enterprise custom. 252,000+ downloads claimed on their site (self-reported).
- **Anarlog** — MIT, macOS-first (Windows/Linux later). Stores notes as plain Markdown on disk (Obsidian-compatible); built-in notepad merges manual notes with transcript via template; bring-your-own LLM (Ollama/LM Studio/OpenAI/Anthropic).
- **Char (fastrepl)** — open-source, macOS, `brew install --cask`. Captures system audio with no bot; 9 STT providers including two local (Parakeet V3, Whisper Small via Cactus); AI chat can edit notes in place; exports Markdown/PDF/JSON/VTT/Org.
- **Notes4Me (andyj)** — MIT, macOS, Electron. Pipeline is literally: sox captures system audio via BlackHole → whisper.cpp (~5x speed) → Ollama (llama3.2) → structured Markdown notes. Good architectural reference for crash recovery (it documents `pkill` cleanup and retention cleanup).
- **ownscribe (paberr)** — local-first CLI; **WhisperX** transcription + pyannote diarization + built-in Phi-4-mini summarization (or Ollama/LM Studio); "Ask your meetings" two-stage LLM Q&A; silence auto-stop; `warmup` command to pre-download models.
- **Squirrel Notes (christie304)** — the most architecturally similar: a **localhost Flask web app** (`http://localhost:5000`) that records, transcribes with Whisper, summarizes with Ollama, and writes Markdown to an Obsidian vault. Demonstrates the polling-status pattern (JS hits `/status/<job_id>`).
- **meeting-transcriber (okamyuji)** — faster-whisper + Kotoba-Whisper + Ollama + RAG (knowledge base), fully offline on CPU with int8 quantization. Good reference for RAG over notes.

**Always-on / memory tools:**
- **Screenpipe (YC S26; GitHub reports ~16.8k stars, MIT)** — records screen + audio 24/7, local SQLite, Whisper Large-V3-Turbo transcription, OCR, **full REST API on localhost:3030**, MCP server, Ollama integration. Per Screenpipe's own README/FAQ, it uses ~5–10 GB per month of storage and "Typical CPU usage is 5–10% on modern hardware." Strong reference for the local REST-API-to-client pattern and privacy-scrubbing.

**Transcription engines / building blocks:**
- **faster-whisper (SYSTRAN)** — CTranslate2 reimplementation. Per the project README: "This implementation is up to 4 times faster than openai/whisper for the same accuracy while using less memory. The efficiency can be further improved with 8-bit quantization on both CPU and GPU." The de-facto backend.
- **WhisperX (m-bain)** — word-level timestamps (±50 ms via wav2vec2 forced alignment), pyannote diarization, up to 60–70× real-time batched inference. BSD-4-Clause. The engine for "who said what."
- **WhisperLive (collabora)**, **WhisperLiveKit**, **ufal/whisper_streaming**, **WhisperFlow** — real-time streaming servers (details in §2).
- **Other notable:** Buzz (offline desktop), speaches (OpenAI-compatible faster-whisper server with streaming/live transcription), hwdsl2/docker-whisper-live (self-hosted WhisperLive Docker with WebSocket + OpenAI-compatible REST).

### 2. Technical Architecture Patterns

**Server/client split.** The dominant pattern is exactly the developer's: a Python server (FastAPI most common, Flask for simpler apps) exposing (a) a WebSocket endpoint for live audio + partial transcripts and (b) REST endpoints for file upload, job status, and note CRUD. Meetily uses FastAPI; Squirrel Notes uses Flask with status polling; Screenpipe exposes a localhost:3030 REST API + SDK.

**Transport choice:**
- **WebSocket** is the consensus for real-time streaming audio in / transcripts out — a single persistent bidirectional channel avoids per-request HTTP overhead. Used by WhisperLive, WhisperLiveKit, Baseten's reference implementation, and the ScienceIO/E-Sensia FastAPI examples (browser captures mic via MediaRecorder in webm/opus, streams chunks to `/asr`, server decodes with FFmpeg).
- **REST** for non-streaming: file upload, summarization jobs, note retrieval. SSE (`stream=true`) is an alternative for streaming transcription segments without a full WebSocket.
- **gRPC** appears only in heavy multi-GPU serving stacks (e.g., Simplismart's input layer); overkill here and poorly supported from browsers.

**Audio chunking for low latency.** Vanilla Whisper expects ≤30s chunks containing full sentences; naive fixed windows split words. The proven approaches:
- **LocalAgreement-n policy (ufal/whisper_streaming):** process new chunks, emit only text confirmed by 2 consecutive iterations, scroll the buffer on confirmed sentence boundaries. Achieves ~3.3s latency on long-form speech. (Note: the project notes that as of 2025 WhisperStreaming is being superseded by SimulStreaming.)
- **VAD-gated chunking:** Silero/WebRTC VAD segments speech, removing silence. Per the ufal paper, VAD off gives lower latency (0.23s+ faster) on fluent speech, but VAD on gives 2–3% better WER on speech with pauses — so an adaptive VAD policy is ideal.
- **Overlap + stitch:** 2–3s overlap between chunks, strip the first/last ~0.5s of each chunk's output and concatenate stable middles (Baseten/Spheron pattern). Finalize on VAD silence >~600ms.
- **Buffer thresholds:** simple servers buffer ~5s before transcribing (balance of latency vs. accuracy); streaming partials at ~1s intervals.

**Speaker diarization:**
- **pyannote.audio 3.1** (pure PyTorch, 16kHz mono in, speaker segments out) is the standard; runs on a modest GPU (RTX 3060/4060, 6–8GB VRAM). Requires accepting HF license + token.
- **WhisperX** integrates pyannote after transcription; **WhisperLive** offers online cosine-similarity clustering of pyannote embeddings for real-time speaker ID.
- NVIDIA **Sortformer** is a newer, heavier alternative (needs much more VRAM, e.g. A6000-class).
- Diarization generally requires full-audio analysis and is **not supported in pure streaming mode** (hwdsl2/docker-whisper-live silently skips it when streaming) — run it as a post-pass on the finalized recording.

**LLM post-processing pipeline.** The settled pattern is **map-reduce / hierarchical summarization**:
1. Chunk the transcript topically (not just by fixed token count — topic segmentation beats linear segmentation).
2. Summarize each chunk; then summarize the summaries (rolling/hierarchical).
3. Separate prompts for distinct outputs: summary, action items (who/what/when/status), decisions, open questions, technical notes. The Action-Item-Driven Summarization paper (arXiv 2312.17581) uses a "neighborhood" of ~3 utterances before/after a detected action item for context.
4. Two-model patterns (extractive then abstractive rewrite to third person) are used in production meeting-recap systems (arXiv 2307.15793).
- **Local LLM integration:** Ollama REST API (`/api/generate`) is the most common (used by Meetily, Notes4Me, Squirrel Notes, ownscribe); alternatives are llama-cpp-python and HF transformers. Recommended local models for summarization: Qwen 2.5 (7B/14B), Llama 3.2, Mistral, Gemma 3, Phi-4-mini. A 7B model is usually sufficient for meeting summaries.
- **Long transcriptions:** chunk + rolling summary; for retrieval across many notes, RAG with a vector DB (ChromaDB in Meetily; embedding cache in meeting-transcriber).

### 3. Security

**This is the developer's hardest problem because the PWA is external (Lovable) and the server is local — a cross-origin, public→loopback scenario that browsers are actively locking down.**

**The Chrome 142 Local Network Access (LNA) change is the defining constraint.** Per the Chrome Developers blog ("Local Network Access," published June 9, 2025) and confirmed by Beyond Identity's admin notice ("Starting with the release of Chrome version 142 on October 28, 2025, Chrome and all Chromium-based browsers will introduce a new Local Network Access (LNA) permission prompt"): a request from a public origin (the Lovable-hosted HTTPS PWA) to a loopback/local destination (the Python server on 127.0.0.1) now requires the user to grant a one-time permission ("Look for and connect to any device on your local network"). Critically:
- **There is no server-side or site-side way to opt out** — a Chromium engineer confirmed it is always a user choice.
- The permission can only be requested **from a secure (HTTPS) context**.
- Mixed-content is **exempted** for clearly-local targets if the request uses a private-IP literal, a `.local` domain, or the `fetch(..., { targetAddressSpace: "local" })` option.
- **WebSockets and WebTransport were initially NOT gated by LNA** when it shipped in Chrome 142, but per the Chrome 142 release notes / Steele O'Brien analysis (Oct 2025), "The feature shipped in Chrome 142 on desktop platforms and expanded to include WebSocket and WebTransport connections in Chrome 147." This means the streaming `ws://localhost` path is also now gated in current Chrome — plan accordingly.

**Mixed-content baseline (pre-LNA, still relevant for Firefox/Safari):** loopback addresses are "potentially trustworthy" per the W3C Secure Contexts spec, so historically HTTPS→`http://127.0.0.1` and `http://localhost` were exempt from mixed-content blocking — Chrome since v53, Firefox literal IP since v55 (bug 903966) and the `localhost` name since v84, WebSocket loopback from secure origins since Firefox bug 1376309. Safari tracks this in WebKit bug 171934.

**Practical connectivity solutions (ranked for this architecture):**
1. **Tunnel (cloudflared / ngrok):** `cloudflared tunnel --url http://localhost:8000` or `ngrok http 8000` exposes the local server at a public HTTPS URL, converting the public→loopback request into a normal public HTTPS request — sidestepping both mixed content AND the Chrome LNA prompt. Trade-off: traffic egresses through a third party; requires the tunnel process running; must add auth.
2. **Run the local server over real HTTPS + accept the LNA prompt.** Serve uvicorn with `--ssl-keyfile/--ssl-certfile`. For end users, the cleanest "real cert" approach is the Spotify/Dropbox/GitHub/Discord pattern — a public DNS name with an A-record to 127.0.0.1 plus a publicly-trusted cert bundled in the app (`*.spotilocal.com`, `www.dropboxlocalhost.com`, `ghconduit.com`, `discordapp.io`). **Let's Encrypt explicitly warns against this** (bundling a private key in a distributed binary risks cert revocation and enables MitM), but it remains widely used in production.
3. **mkcert / self-signed CA + trust import** — `mkcert -install` then `mkcert localhost 127.0.0.1 ::1`. Great for the developer's own machine and small teams, but the root CA must be trusted on every user device, so it doesn't scale to arbitrary users.
4. **Repackage as a desktop app (Tauri/Electron)** — eliminates the browser sandbox, mixed-content, and LNA entirely. This is precisely why Meetily, Anarlog, Char, and Notes4Me are all native desktop apps.

**Authentication between PWA and local server:**
- **CORS origin allow-listing is mandatory** — FastAPI `CORSMiddleware` with `allow_origins=["https://<your-app>.lovable.app"]` (never `*` when credentials are involved; the browser rejects `*` + credentials). Middleware must be added before routes; FastAPI auto-handles the OPTIONS preflight that fires for POST-with-JSON or an Authorization header.
- **Bind the server to 127.0.0.1 only** (not 0.0.0.0) so it isn't reachable from the network.
- **Shared secret / token:** since there's a trust boundary, require a token (API key or JWT) on every request. FastAPI's `OAuth2PasswordBearer` + python-jose/PyJWT is the standard; for a single-user local tool a long random API key (`openssl rand -hex 32`) in the `Authorization` header is simpler and sufficient. Pair a short-lived access token with a refresh token if you add accounts.
- **Origin validation** beyond CORS: validate the `Origin` header server-side (don't blindly reflect it) and use `Vary: Origin` if caching.

**Data-at-rest encryption:** SQLite is plaintext by default. Use **SQLCipher** (AES-256, transparent full-DB encryption including WAL/journal, ~5–15% overhead) via the `sqlcipher3` Python driver (`PRAGMA key=...`). Store the key in an OS secret store / env var, never hard-coded. Harden with `chmod 600` on DB files, run the server as a non-root user, `PRAGMA secure_delete=ON`, `journal_mode=WAL`, and disable extension loading.

**Multi-user/team security (future):** move to per-user accounts with hashed passwords (Argon2 via pwdlib), JWT with scopes/roles for RBAC, and HTTPS-only. Consider a Business Associate Agreement path if targeting healthcare (HIPAA), which several competitors advertise.

### 4. Stability & Reliability

- **Whisper model loading: keep-warm, not on-demand.** Load the model once at server startup and reuse it across requests — reloading per request (or per WebSocket connection) is the most common latency/throughput bug. WhisperLive defaults to a new model per client connection but offers a single-model mode (`--no_single_model` to opt out); for a single-user tool, load once globally. Provide a `warmup` step (ownscribe does this) to pre-download/initialize models.
- **Queue-based audio processing** so audio is never lost when the server is busy: write incoming audio to a durable buffer/queue (disk or in-memory ring buffer), process asynchronously, and decouple capture from transcription (Baseten's Chains pattern scales chunking and transcription independently). A simple Python worker pulling from a directory works for batch.
- **Crash recovery / graceful degradation:** persist raw audio to disk before transcription (Notes4Me saves WAV first, then transcribes, then summarizes — each stage's output is on disk so a crash mid-summarization doesn't lose the transcript). Document process-cleanup for stuck child processes (sox/whisper/ollama).
- **Process management:** **systemd** is the first-choice service manager on Linux (dependency ordering, OS hardening, auto-restart, start-on-boot); **supervisor** is a good alternative for managing several non-daemonized child processes with a unified CLI; **PM2** if the stack is Node-adjacent. Set `autorestart=true`, tune `startsecs` above your model-load time to avoid false-failure restart loops, and bind any control interface to 127.0.0.1.
- **Streaming error handling:** catch `WebSocketDisconnect`, release resources, and reset buffers on silence/finalization. Set autoscaling/connection limits (`--max_clients`, `--max_connection_time`) even locally to bound resource use.
- **Offline-first PWA + reconnect (client side):** three layers — service worker (cache-first for shell, network-first for API), IndexedDB as the local source of truth, and a sync queue. Queue writes to IndexedDB first; replay via the Background Sync API when the server reconnects. **Caveat:** Background Sync is Chromium-only (not Safari/Firefox as of early 2026), so always implement an immediate-retry fallback with exponential backoff.

### 5. UX/UI Patterns

- **Capture modes:** push-to-talk (hold-to-dictate, à la local-whisper's Right-Cmd hold), always-on (Screenpipe), and auto-detect silence (ownscribe stops after sustained silence; local-whisper after 3s < -40dB). Offer all three; default to explicit start/stop for meetings and push-to-talk for quick notes.
- **Live transcription feedback:** the proven pattern is the **two-tier partial/final display** — show unconfirmed buffer text in grey/light ("aperçu"), promote to normal text once Whisper finalizes the segment (ScienceIO/WhisperLiveKit). Add a recording indicator (pulsing dot + elapsed timer).
- **Note organization:** Markdown files on disk (Anarlog, Notes4Me, Squirrel Notes) for portability/Obsidian compatibility; tagging, folders, timeline/DVR views (Screenpipe), and full-text + semantic search (SQLite FTS5 for keyword, ChromaDB for concepts).
- **LLM-assisted UX:** automatic post-meeting summaries, inline action-item extraction with checkboxes/stars (arXiv 2307.15793 design), AI chat to query transcripts, and click-through from a summary item back to the source transcript utterances (±3 utterances of context).
- **PWA specifics:** installable manifest, offline shell, push notifications (iOS 16.4+ only for installed PWAs, not in EU under DMA), and a custom install prompt.
- **Mobile audio-capture constraints (critical for a PWA):**
  - iOS Safari supports `MediaRecorder` and mic capture, but historically required enabling experimental features and has had bugs in standalone/installed PWA mode (e.g., 44-byte WAV uploads).
  - **No reliable background audio capture on iOS** — iOS suspends PWAs in the background, 7-day cache expiry, no Background Sync, no background tasks. Capture must happen while the app is foregrounded and open.
  - Web Audio API limitations: `MediaRecorder` doesn't expose raw PCM; `AudioWorklet` is the path for raw audio but is more complex. Plan to capture webm/opus via MediaRecorder and decode server-side with FFmpeg (the standard pattern).
  - Media Session API now works on iOS for lock-screen controls, which helps for playback but not background capture.

### 6. Scaling to Teams (Future Roadmap)

- **Evolution path:** personal local tool → multi-user auth (accounts, RBAC) → shared note spaces → sync. Meetily's own tiering (Community local-only → Pro → Enterprise with centralized self-hosted storage, admin dashboard, audit logging) is a good template.
- **Deployment for teams:** local-network deployment behind a reverse proxy (Nginx/Caddy) with HTTPS, VPN access for remote members, or a self-hosted server on the team's infrastructure (Azure/AWS/GCP/on-prem). Caddy is attractive for automatic local TLS.
- **Sync architecture options:**
  - **PocketBase** — single-binary Go backend (collections, real-time WebSocket subscriptions, built-in JWT/OAuth auth); easiest self-hosted sync server, Python SDK available.
  - **CouchDB + PouchDB** — proven offline-first replication; PouchDB in the browser/IndexedDB syncs bidirectionally to CouchDB. Conflict model is deterministic revision-tree (picks a winner; you resolve manually) — detects but doesn't auto-resolve.
  - **Supabase (self-hosted)** or **ElectricSQL/PowerSync** over Postgres for SQL-based sync.
- **Conflict resolution:** for notes, **last-write-wins (timestamp)** handles ~80% of cases and is simplest; field-level merge for structured records; **CRDTs (Yjs, Automerge)** for true concurrent collaborative editing (commutative merges, no central coordination). Start with LWW, adopt CRDTs only when real-time co-editing is a requirement.

## Recommendations

**Stage 0 — Resolve the connectivity problem first (this is the gating decision).**
- Test the current architecture in Chrome 142+ today. If the PWA fetches `http://localhost`, you will hit the Local Network Access prompt; and because LNA expanded to WebSocket/WebTransport in Chrome 147, the streaming path is now gated too — so you cannot rely on WebSocket as an unguarded escape hatch.
- **Recommended primary path:** ship a **cloudflared (or ngrok) tunnel** that gives the local server a public HTTPS URL; the PWA then makes ordinary same-scheme HTTPS calls (REST and WebSocket), bypassing both mixed content and LNA. Add a token so the tunnel URL isn't open. This is the fastest unblock with the least user friction.
- **Strongly consider** offering a **desktop-wrapper option (Tauri preferred — it's what Meetily uses, lighter than Electron)** for users who want zero browser friction. This hedges against ongoing LNA tightening and matches what every successful competitor ships. Threshold to prioritize this: if the LNA permission prompt causes measurable drop-off among Chrome users, build the desktop shell.

**Stage 1 — Harden the single-user core (you already have the pieces).**
- Swap to **faster-whisper** (CTranslate2) if not already, for up to 4× throughput at equal accuracy; load the model once at startup (keep-warm).
- Implement **WebSocket streaming with a LocalAgreement-2 + adaptive-VAD policy** (reuse ufal/whisper_streaming or WhisperLiveKit rather than writing from scratch); render partial (grey) vs. final (normal) text.
- Add **CORS allow-listing** (your exact Lovable origin only), **127.0.0.1 binding**, and a **shared token** on every request.
- Persist raw audio to disk before transcription; run the server under **systemd** with `autorestart` and a generous `startsecs`.
- Encrypt storage with **SQLCipher** if transcripts are sensitive; `chmod 600`, non-root user.

**Stage 2 — LLM note quality.**
- Build a **map-reduce summarization pipeline** with topic-segmented chunking and separate prompts for summary / action items / decisions / open questions, via the **Ollama REST API** (Qwen 2.5 14B or Llama 3.2 as defaults).
- Add **RAG over past notes** (ChromaDB) for "ask your meetings."
- Add **diarization as a post-pass** with WhisperX/pyannote 3.1 (not in the streaming loop).

**Stage 3 — Offline-first PWA polish.**
- IndexedDB as source of truth + service-worker sync queue + Background Sync (with retry fallback for Safari/Firefox).
- Capture webm/opus via MediaRecorder, decode server-side with FFmpeg; set expectations that **background/locked-screen capture won't work on iOS** — design for foreground capture.

**Stage 4 — Team scaling (only after single-user is solid).**
- Add accounts (Argon2 + JWT/RBAC).
- Introduce a sync server — **PocketBase** for fastest path, or **CouchDB/PouchDB** if you want battle-tested offline replication.
- Start conflict resolution with **last-write-wins**; adopt **Yjs/Automerge CRDTs** only if real-time co-editing becomes a requirement.
- Deploy behind **Caddy/Nginx + HTTPS**, VPN for remote access.

## Caveats
- **Browser landscape is shifting against this architecture.** Chrome 142's LNA shipped Oct 28, 2025, and expanded to WebSocket/WebTransport in Chrome 147; Firefox and Brave are implementing similar local-network controls. Some of Chrome's roadmap language is forward-looking — re-validate connectivity each Chrome release.
- **iOS PWA limits are real and unlikely to change soon:** no background audio capture, 7-day cache expiry, no Background Sync, EU loses installed-PWA features under the DMA. A PWA-only mobile story for long meetings is risky.
- **Vendor/marketing claims** (download counts, "#1" positioning) come from the projects' own sites/blogs and should be treated as self-reported; GitHub star counts cited here are from the repos' headers in June 2026 and drift over time.
- **Diarization needs a GPU** for reasonable speed and an HF token/license acceptance; it adds latency and can't run in the pure streaming path.
- The **embedded-public-cert-to-127.0.0.1 trick** works in production today but is explicitly discouraged by Let's Encrypt and carries revocation/MitM risk — use tunnels or a desktop shell instead where possible.
- Several how-to sources (Medium, DEV) are tutorial-quality; the architectural facts here are corroborated by primary sources (MDN, Chrome Developers blog, official project READMEs, FastAPI docs, arXiv papers).