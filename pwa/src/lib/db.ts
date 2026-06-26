import { openDB, type DBSchema, type IDBPDatabase } from "idb";

export type RecordingStatus =
  | "pending"
  | "uploading"
  | "processing"
  | "done"
  | "failed";

export interface Recording {
  id: string;
  createdAt: number;
  title: string;
  status: RecordingStatus;
  text?: string;
  blob?: Blob;
  mimeType?: string;
  durationSec?: number;
  images?: Blob[];
  language?: string;    // forced transcription language ("sv"|"da"|"en"); undefined = auto
  uploadProgress?: number;
  jobId?: string;
  error?: string;
}

export interface Pairing {
  id: "current";
  server: string;
  port: number;
  apiKey: string;
  secure?: boolean;
  pairedAt: number;
}

export interface NoteResult {
  recordingId: string;
  jobId: string;
  transcript: string;
  summary: string;
  actionItems: string[];
  tags: string[];
  completedAt: number;
  hasAudio?: boolean;        // a recording is saved server-side (enables re-transcribe + playback)
  summaryPending?: boolean;  // transcript is in, summary still being generated
}

interface FasterDB extends DBSchema {
  recordings: { key: string; value: Recording; indexes: { byCreatedAt: number } };
  pairing: { key: string; value: Pairing };
  results: { key: string; value: NoteResult };
}

let dbp: Promise<IDBPDatabase<FasterDB>> | null = null;
function getDB() {
  if (!dbp) {
    dbp = openDB<FasterDB>("faster-notes", 1, {
      upgrade(db) {
        const rec = db.createObjectStore("recordings", { keyPath: "id" });
        rec.createIndex("byCreatedAt", "createdAt");
        db.createObjectStore("pairing", { keyPath: "id" });
        db.createObjectStore("results", { keyPath: "recordingId" });
      },
    });
  }
  return dbp;
}

export async function addRecording(r: Recording) {
  const db = await getDB();
  await db.put("recordings", r);
  emit();
}
export async function updateRecording(id: string, patch: Partial<Recording>) {
  const db = await getDB();
  const existing = await db.get("recordings", id);
  if (!existing) return;
  await db.put("recordings", { ...existing, ...patch });
  emit();
}
export async function deleteRecording(id: string) {
  const db = await getDB();
  await db.delete("recordings", id);
  await db.delete("results", id);
  emit();
}
export async function listRecordings(): Promise<Recording[]> {
  const db = await getDB();
  const all = await db.getAll("recordings");
  return all.sort((a, b) => b.createdAt - a.createdAt);
}
export async function getRecording(id: string) {
  const db = await getDB();
  return db.get("recordings", id);
}

export async function getPairing(): Promise<Pairing | undefined> {
  const db = await getDB();
  return db.get("pairing", "current");
}
export async function setPairing(p: Omit<Pairing, "id" | "pairedAt">) {
  const db = await getDB();
  await db.put("pairing", { id: "current", pairedAt: Date.now(), ...p });
  emit();
}
export async function clearPairing() {
  const db = await getDB();
  await db.delete("pairing", "current");
  emit();
}

export async function saveResult(r: NoteResult) {
  const db = await getDB();
  await db.put("results", r);
  emit();
}
export async function listResults(): Promise<NoteResult[]> {
  const db = await getDB();
  const all = await db.getAll("results");
  return all.sort((a, b) => b.completedAt - a.completedAt);
}
export async function getResult(recordingId: string) {
  const db = await getDB();
  return db.get("results", recordingId);
}

export async function clearNotesData() {
  const db = await getDB();
  await db.clear("recordings");
  await db.clear("results");
  emit();
}

export async function clearAllData() {
  const db = await getDB();
  await db.clear("recordings");
  await db.clear("results");
  await db.clear("pairing");
  emit();
}

export async function storageEstimate() {
  if (typeof navigator !== "undefined" && navigator.storage?.estimate) {
    return navigator.storage.estimate();
  }
  return { usage: 0, quota: 0 } as StorageEstimate;
}

// Tiny event bus for reactive updates
type Listener = () => void;
const listeners = new Set<Listener>();
export function subscribe(fn: Listener) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}
function emit() {
  listeners.forEach((l) => l());
}
export function notify() {
  emit();
}
