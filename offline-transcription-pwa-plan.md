# Offline Voice Notes Transcription System (Option B - QR Pairing)

## Goal

Build a mobile-first PWA that can:

1. Record audio while offline.
2. Store recordings locally on the phone.
3. Pair once with a home transcription server using a QR code.
4. Automatically upload recordings when the laptop is available.
5. Transcribe audio using Faster-Whisper.
6. Process transcripts with a local LLM.
7. Return summaries, action items, and structured data back to the phone.

---

# High-Level Architecture

```text
Phone PWA
    │
    ├── Record audio (Opus)
    ├── Store locally (IndexedDB)
    ├── Upload queue
    └── QR-paired server information

                ↓

Local Network

                ↓

Laptop Server
    ├── FastAPI
    ├── Authentication
    ├── Faster-Whisper
    ├── Ollama
    ├── File storage
    └── Processing queue
```

---

# Phase 1 - PWA Foundation

## Technology Stack

### Frontend

- React
- TypeScript
- Vite
- PWA support
- IndexedDB

### Recording

Use:

```javascript
MediaRecorder
```

Format:

```javascript
audio/webm;codecs=opus
```

Benefits:

- Small files
- Excellent quality
- Supported by Faster-Whisper
- Ideal for mobile recording

---

## Main Screens

### Record

Features:

- Start recording
- Pause recording
- Stop recording
- Recording timer
- Audio playback

### Queue

Displays:

- Pending uploads
- Uploading
- Processing
- Completed
- Failed

### Transcript Viewer

Displays:

- Original transcript
- Summary
- Action items
- Tags

### Settings

Displays:

- Paired server
- Connection status
- Re-pair button

---

# Phase 2 - Local Storage

## IndexedDB Structure

### recordings

```json
{
  "id": "uuid",
  "createdAt": "timestamp",
  "duration": 120,
  "filename": "note.webm",
  "status": "pending",
  "blob": "..."
}
```

### pairing

```json
{
  "server": "192.168.1.123",
  "port": 8000,
  "apiKey": "secret"
}
```

### results

```json
{
  "recordingId": "uuid",
  "transcript": "...",
  "summary": "...",
  "actionItems": []
}
```

---

# Phase 3 - QR Pairing

## Laptop Startup

FastAPI starts.

Server generates:

```json
{
  "server": "192.168.1.123",
  "port": 8000,
  "apiKey": "generated-secret"
}
```

Convert JSON to QR code.

Display:

- Web dashboard
- Local browser page
- Console output (optional)

---

## Phone Pairing

User opens:

```text
Settings → Pair Server
```

Phone scans QR.

Phone stores:

- Server IP
- Port
- API key

Pairing is complete.

No account required.

No cloud required.

---

# Phase 4 - Upload Service

## Connection Check

Every few minutes:

```text
GET /status
```

If successful:

```text
Server Available
```

Trigger upload queue.

---

## Upload Endpoint

```text
POST /upload
```

Payload:

- Recording file
- Recording ID
- Metadata

Server returns:

```json
{
  "jobId": "123"
}
```

---

# Phase 5 - Transcription Server

## FastAPI Components

### Endpoints

```text
GET  /status
POST /upload
GET  /job/{id}
GET  /result/{id}
```

### Services

```text
Upload Service
Queue Service
Whisper Service
LLM Service
Storage Service
```

---

## Faster-Whisper

Suggested model:

```text
medium
```

Upgrade later:

```text
large-v3
```

Pipeline:

```text
Opus Audio
      ↓
Faster-Whisper
      ↓
Raw Transcript
```

---

# Phase 6 - LLM Processing

## Ollama

Suggested models:

### Lightweight

- Qwen3 4B

### Higher Quality

- Qwen3 8B

---

## Prompt Workflow

Input:

```text
Transcript
```

Generate:

```json
{
  "summary": "",
  "action_items": [],
  "tags": []
}
```

Example use cases:

- Meeting notes
- Voice memos
- Ideas
- Project planning
- To-do extraction

---

# Phase 7 - Sync Results Back

PWA polls:

```text
GET /job/{id}
```

When completed:

```text
GET /result/{id}
```

Store locally.

Update UI.

Show notification:

```text
Transcript Ready
```

---

# Phase 8 - Security

## API Key

Generated during pairing.

Stored:

- On laptop
- In PWA

Required for:

```text
/upload
/result
/job
```

---

## Optional Future Improvements

- QR re-pairing
- API key rotation
- Device approval list
- HTTPS on LAN
- User accounts

---

# Future Enhancements

## Automatic Network Detection

When connected to home WiFi:

```text
Auto-upload pending recordings
```

---

## Search

Local transcript search:

```text
Find all notes mentioning:
- Customer X
- Project Y
- Budget
```

---

## Export

Export:

- Markdown
- PDF
- JSON
- CSV

---

## Multi-Device Support

Pair:

- Phone
- Tablet
- Secondary phone

With one server.

---

# MVP Milestone

The first working version should support:

1. Record Opus audio.
2. Store recordings offline.
3. Pair using QR code.
4. Upload to FastAPI server.
5. Transcribe using Faster-Whisper.
6. Summarize using Ollama.
7. View results on the phone.

This MVP can be built without any cloud services and should run entirely on the local network.
