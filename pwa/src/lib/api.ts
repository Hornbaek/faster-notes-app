import { getPairing } from "./db";

export type JobStatus = "queued" | "transcribing" | "summarizing" | "done" | "failed";

// Mock mode: simulates the FastAPI server end-to-end so the UI is clickable.
// Set to false to talk to the real paired server.
export const MOCK_API = false;

export interface ApiResult {
  transcript: string;
  summary: string;
  actionItems: string[];
  tags: string[];
  // Present on the real server: "summarizing" = transcript ready, summary still
  // coming; "done" = complete. hasAudio = a recording is saved server-side.
  status?: JobStatus;
  hasAudio?: boolean;
}

async function baseUrl() {
  const p = await getPairing();
  if (!p) throw new Error("No paired server");
  // Default to https — phones require a secure context for the mic, and an
  // https PWA can only reach an https API (no mixed content).
  const scheme = p.secure === false ? "http" : "https";
  return { url: `${scheme}://${p.server}:${p.port}`, apiKey: p.apiKey };
}

function authHeaders(apiKey: string) {
  return { Authorization: `Bearer ${apiKey}` };
}

// ---------- Mock state ----------
const mockJobs = new Map<string, { startedAt: number; status: JobStatus }>();

function mockTranscriptFor(seed: string): ApiResult {
  const variants = [
    {
      transcript:
        "Okay so the main idea is to ship a quick prototype this week. We need to confirm the data shape with the API team, set up the basic queue, and run a first end-to-end test on Friday. Don't forget to ping Maya about the design review.",
      summary:
        "Plan to ship a prototype this week, align on data shape, build the queue, and run end-to-end testing on Friday.",
      actionItems: [
        "Confirm API data shape with the backend team",
        "Set up the upload queue",
        "Run end-to-end test on Friday",
        "Ping Maya about the design review",
      ],
      tags: ["work", "prototype", "planning"],
    },
    {
      transcript:
        "Grocery list for tomorrow: oat milk, sourdough, two avocados, a lemon, parmesan, and some basil. Also remember to grab dog food and pick up the dry cleaning before six.",
      summary: "Grocery and errands list for tomorrow.",
      actionItems: [
        "Buy: oat milk, sourdough, 2 avocados, lemon, parmesan, basil",
        "Buy dog food",
        "Pick up dry cleaning before 6pm",
      ],
      tags: ["personal", "errands"],
    },
    {
      transcript:
        "Idea: an offline-first voice notes app that syncs to a private server on the home network. Records anywhere, queues uploads, and once you're home it transcribes and summarizes locally. No cloud accounts, just a paired server.",
      summary:
        "Concept for a private, offline-first voice notes PWA that syncs to a self-hosted transcription server.",
      actionItems: [
        "Sketch the pairing flow",
        "Define the queue states",
        "Spec the API contract",
      ],
      tags: ["idea", "product"],
    },
  ];
  let h = 0;
  for (let i = 0; i < seed.length; i++) h = (h * 31 + seed.charCodeAt(i)) >>> 0;
  return variants[h % variants.length];
}

// ---------- API ----------
export async function getStatus(): Promise<boolean> {
  if (MOCK_API) {
    const p = await getPairing().catch(() => undefined);
    if (!p) return false;
    // Pretend the home server is reachable ~90% of the time
    await new Promise((r) => setTimeout(r, 200));
    return true;
  }
  try {
    const { url, apiKey } = await baseUrl();
    const res = await fetch(`${url}/status`, {
      headers: authHeaders(apiKey),
      signal: AbortSignal.timeout(4000),
    });
    return res.ok;
  } catch {
    return false;
  }
}

/** Test the paired server and return a human-readable reason on failure. */
export async function testServer(): Promise<{ ok: boolean; url?: string; error?: string }> {
  let url: string | undefined;
  try {
    const base = await baseUrl();
    url = base.url;
    const res = await fetch(`${url}/status`, {
      headers: authHeaders(base.apiKey),
      signal: AbortSignal.timeout(6000),
    });
    if (res.ok) return { ok: true, url };
    if (res.status === 401) return { ok: false, url, error: "Wrong API key — re-scan the QR code." };
    return { ok: false, url, error: `Server responded with ${res.status}.` };
  } catch (e) {
    // A failed fetch here is almost always: wrong address, not on the same
    // Wi-Fi, the certificate wasn't accepted, or the server isn't running.
    const reason = e instanceof DOMException && e.name === "TimeoutError"
      ? "timed out"
      : "couldn't connect";
    if (!url) return { ok: false, error: "No server paired." };
    const p = await getPairing().catch(() => undefined);
    const host = p?.server ?? "";
    const PRIVATE = /^(?:localhost|(?:[a-z0-9-]+\.)*local|10(?:\.\d{1,3}){3}|192\.168(?:\.\d{1,3}){2}|172\.(?:1[6-9]|2\d|3[01])(?:\.\d{1,3}){2}|127(?:\.\d{1,3}){3}|169\.254(?:\.\d{1,3}){2})$/i;
    const isRemote = !!host && !PRIVATE.test(host);
    const error = isRemote
      ? `Can't reach ${url} (${reason}). If using remote access, check the server is running at home and the Cloudflare tunnel is active.`
      : `Can't reach ${url} (${reason}). Open it in your browser first and accept the certificate, and make sure your phone is on the same Wi-Fi.`;
    return { ok: false, url, error };
  }
}

export interface UploadOpts {
  recordingId: string;
  blob?: Blob;
  text?: string;
  images?: Blob[];
  metadata: Record<string, unknown>;
  onProgress?: (pct: number) => void;
  signal?: AbortSignal;
}

export async function upload(opts: UploadOpts): Promise<{ jobId: string }> {
  if (MOCK_API) {
    for (let p = 0; p <= 100; p += 10) {
      await new Promise((r) => setTimeout(r, 90));
      opts.onProgress?.(p);
    }
    const jobId = `job_${opts.recordingId}`;
    mockJobs.set(jobId, { startedAt: Date.now(), status: "queued" });
    return { jobId };
  }
  const { url, apiKey } = await baseUrl();
  const fd = new FormData();
  fd.append("recordingId", opts.recordingId);
  fd.append("metadata", JSON.stringify(opts.metadata));
  if (opts.blob) fd.append("file", opts.blob, `${opts.recordingId}.webm`);
  if (opts.text) fd.append("text", opts.text);
  if (opts.images && opts.images.length) {
    opts.images.forEach((img, i) => {
      const ext = (img.type.split("/")[1] || "jpg").split(";")[0];
      fd.append("images", img, `${opts.recordingId}-${i}.${ext}`);
    });
  }
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", `${url}/upload`);
    xhr.setRequestHeader("Authorization", `Bearer ${apiKey}`);
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) opts.onProgress?.((e.loaded / e.total) * 100);
    };
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        try {
          resolve(JSON.parse(xhr.responseText));
        } catch (e) {
          reject(e);
        }
      } else reject(new Error(`Upload failed: ${xhr.status}`));
    };
    xhr.onerror = () => reject(new Error("Network error"));
    opts.signal?.addEventListener("abort", () => xhr.abort());
    xhr.send(fd);
  });
}

export async function getJob(jobId: string): Promise<{ status: JobStatus }> {
  if (MOCK_API) {
    const j = mockJobs.get(jobId);
    if (!j) return { status: "failed" };
    const elapsed = Date.now() - j.startedAt;
    if (elapsed < 1200) j.status = "queued";
    else if (elapsed < 3000) j.status = "transcribing";
    else if (elapsed < 4500) j.status = "summarizing";
    else j.status = "done";
    return { status: j.status };
  }
  const { url, apiKey } = await baseUrl();
  const res = await fetch(`${url}/job/${jobId}`, { headers: authHeaders(apiKey) });
  if (!res.ok) throw new Error(`Job poll failed: ${res.status}`);
  return res.json();
}

export async function getResultApi(jobId: string): Promise<ApiResult> {
  if (MOCK_API) return { ...mockTranscriptFor(jobId), status: "done" };
  const { url, apiKey } = await baseUrl();
  const res = await fetch(`${url}/result/${jobId}`, { headers: authHeaders(apiKey) });
  if (!res.ok) throw new Error(`Result fetch failed: ${res.status}`);
  return res.json();
}

/** Re-run transcription on the note's saved recording (with the server's current
 *  Whisper model), then regenerate the summary. Returns the (same) job id to poll. */
export async function retranscribe(jobId: string): Promise<{ jobId: string; status: JobStatus }> {
  if (MOCK_API) {
    mockJobs.set(jobId, { startedAt: Date.now(), status: "queued" });
    return { jobId, status: "queued" };
  }
  const { url, apiKey } = await baseUrl();
  const res = await fetch(`${url}/retranscribe/${jobId}`, {
    method: "POST",
    headers: authHeaders(apiKey),
  });
  if (!res.ok) {
    let detail = `Re-transcribe failed (${res.status}).`;
    try {
      const j = await res.json();
      if (j?.detail) detail = j.detail;
    } catch {
      /* keep the status-code message */
    }
    throw new Error(detail);
  }
  return res.json();
}

/** Fetch the saved recording (auth-guarded) as an object URL for an <audio> tag.
 *  Caller is responsible for URL.revokeObjectURL when done. */
export async function getMediaUrl(jobId: string): Promise<string> {
  const { url, apiKey } = await baseUrl();
  const res = await fetch(`${url}/media/${jobId}`, { headers: authHeaders(apiKey) });
  if (!res.ok) throw new Error(`Couldn't load recording (${res.status}).`);
  return URL.createObjectURL(await res.blob());
}
