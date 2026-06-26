import {
  listRecordings,
  updateRecording,
  saveResult,
  getRecording,
  getPairing,
  notify,
} from "./db";
import { getStatus, upload, getJob, getResultApi, retranscribe } from "./api";

type Listener = (s: SyncState) => void;
export interface SyncState {
  online: boolean;
  serverReachable: boolean;
  lastChecked: number | null;
  syncing: boolean;
}

// Job poll cadence. CPU Whisper is often slower than real-time, so a 2-min clip
// can take several minutes to transcribe + summarize. Total wait ≈ INTERVAL ×
// MAX = 3s × 400 = 20 min, which comfortably covers long recordings. The server
// keeps working regardless; this is just how long the phone waits for the result.
const POLL_INTERVAL_MS = 3000;
const POLL_MAX_ATTEMPTS = 400;

const state: SyncState = {
  online: typeof navigator !== "undefined" ? navigator.onLine : true,
  serverReachable: false,
  lastChecked: null,
  syncing: false,
};
const listeners = new Set<Listener>();

export function subscribeSync(fn: Listener) {
  listeners.add(fn);
  fn(state);
  return () => listeners.delete(fn);
}
function emit() {
  listeners.forEach((l) => l({ ...state }));
}
export function getSyncState() {
  return { ...state };
}

export async function checkServer() {
  state.online = navigator.onLine;
  if (!state.online) {
    state.serverReachable = false;
  } else {
    state.serverReachable = await getStatus().catch(() => false);
  }
  state.lastChecked = Date.now();
  emit();
  return state.serverReachable;
}

let draining = false;
export async function drainQueue() {
  if (draining) return;
  const pairing = await getPairing();
  if (!pairing) return;
  if (!(await checkServer())) return;
  draining = true;
  state.syncing = true;
  emit();
  try {
    const all = await listRecordings();
    const pending = all.filter(
      (r) => r.status === "pending" || r.status === "failed" || r.status === "uploading"
    );
    for (const rec of pending) {
      try {
        await updateRecording(rec.id, { status: "uploading", uploadProgress: 0, error: undefined });
        const { jobId } = await upload({
          recordingId: rec.id,
          blob: rec.blob,
          text: rec.text,
          images: rec.images,
          metadata: { title: rec.title, durationSec: rec.durationSec, language: rec.language },
          onProgress: (pct) => {
            updateRecording(rec.id, { uploadProgress: pct });
          },
        });
        await updateRecording(rec.id, { status: "processing", jobId, uploadProgress: 100 });
        // Poll: saves the transcript the moment it's ready, then the summary.
        await pollNote(rec.id, jobId);
        await updateRecording(rec.id, { status: "done" });
        toast("Summary ready.");
      } catch (e) {
        await updateRecording(rec.id, {
          status: "failed",
          error: e instanceof Error ? e.message : String(e),
        });
      }
    }
  } finally {
    draining = false;
    state.syncing = false;
    emit();
    notify();
  }
}

/**
 * Poll a job to completion, saving the note in two passes: the transcript as soon
 * as it's available (status "summarizing"), then the full summary when done. Used
 * by both the upload queue and the re-transcribe flow. Throws on failure/timeout.
 */
export async function pollNote(recordingId: string, jobId: string): Promise<void> {
  let savedTranscript = false;
  let done = false;
  for (let i = 0; i < POLL_MAX_ATTEMPTS && !done; i++) {
    await new Promise((r) => setTimeout(r, POLL_INTERVAL_MS));
    const j = await getJob(jobId);
    if (j.status === "failed") throw new Error("The server failed to process this recording.");
    // Transcript is ready before the (slower) summary — save it right away so the
    // note shows the text while the LLM keeps working.
    if (!savedTranscript && (j.status === "summarizing" || j.status === "done")) {
      const partial = await getResultApi(jobId).catch(() => null);
      if (partial?.transcript) {
        await saveResult({
          recordingId,
          jobId,
          transcript: partial.transcript,
          summary: partial.summary ?? "",
          actionItems: partial.actionItems ?? [],
          tags: partial.tags ?? [],
          hasAudio: partial.hasAudio,
          summaryPending: j.status !== "done",
          completedAt: Date.now(),
        });
        savedTranscript = true;
        if (j.status !== "done") toast("Transcript ready — summarizing…");
      }
    }
    if (j.status === "done") done = true;
  }
  if (!done) throw new Error("Still processing after a long time — the server may be overloaded. Tap retry to keep waiting.");
  const result = await getResultApi(jobId);
  await saveResult({
    recordingId,
    jobId,
    transcript: result.transcript,
    summary: result.summary,
    actionItems: result.actionItems,
    tags: result.tags,
    hasAudio: result.hasAudio,
    summaryPending: false,
    completedAt: Date.now(),
  });
}

/**
 * Re-transcribe an existing note from its server-saved recording (e.g. after
 * switching Whisper models), updating it in place. Reuses the same job id.
 */
export async function retranscribeNote(recordingId: string): Promise<void> {
  const rec = await getRecording(recordingId);
  if (!rec?.jobId) throw new Error("This note has no recording to re-transcribe.");
  const { jobId } = await retranscribe(rec.jobId);
  await updateRecording(recordingId, { status: "processing", error: undefined });
  try {
    await pollNote(recordingId, jobId);
    await updateRecording(recordingId, { status: "done" });
    toast("Re-transcribed.");
  } catch (e) {
    await updateRecording(recordingId, {
      status: "failed",
      error: e instanceof Error ? e.message : String(e),
    });
    throw e;
  }
}

let started = false;
let pollTimer: number | null = null;
export function startSync() {
  if (started || typeof window === "undefined") return;
  started = true;
  const tick = () => {
    drainQueue();
  };
  window.addEventListener("online", () => {
    state.online = true;
    emit();
    tick();
  });
  window.addEventListener("offline", () => {
    state.online = false;
    state.serverReachable = false;
    emit();
  });
  window.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") tick();
  });
  pollTimer = window.setInterval(tick, 60_000);
  tick();
}
export function stopSync() {
  if (pollTimer) clearInterval(pollTimer);
  started = false;
}

// Tiny toast bus (consumed by sonner in shell)
type ToastFn = (msg: string) => void;
let toastFn: ToastFn = () => {};
export function setToast(fn: ToastFn) {
  toastFn = fn;
}
function toast(msg: string) {
  toastFn(msg);
}
