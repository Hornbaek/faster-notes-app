import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { motion, AnimatePresence } from "framer-motion";
import { RefreshCw, UploadCloud, Inbox, Trash2 } from "lucide-react";
import { formatDistanceToNow } from "date-fns";
import { AppShell } from "@/components/AppShell";
import { Button } from "@/components/ui/button";
import { useRecordings, useSync } from "@/lib/hooks";
import { drainQueue, checkServer } from "@/lib/sync";
import { updateRecording, deleteRecording, type Recording } from "@/lib/db";

export const Route = createFileRoute("/queue")({
  head: () => ({
    meta: [
      { title: "Queue — Faster Notes" },
      { name: "description", content: "Recordings waiting to upload and transcribe." },
    ],
  }),
  component: Queue,
});

const STATUS_STYLES: Record<Recording["status"], string> = {
  pending: "bg-muted text-muted-foreground",
  uploading: "bg-primary/15 text-primary",
  processing: "bg-warning/15 text-warning",
  done: "bg-success/15 text-success",
  failed: "bg-destructive/15 text-destructive",
};

function fmtDur(s: number) {
  const m = Math.floor(s / 60);
  const ss = Math.floor(s % 60).toString().padStart(2, "0");
  return `${m}:${ss}`;
}

function modalityLabel(r: Recording) {
  const parts: string[] = [];
  if (r.blob && typeof r.durationSec === "number") parts.push(`🎙 ${fmtDur(r.durationSec)}`);
  else if (r.blob) parts.push("🎙 Audio");
  if (r.images && r.images.length) parts.push(`📷 ${r.images.length}`);
  if (r.text && r.text.trim()) parts.push("📝 Text");
  return parts.length ? parts.join(" · ") : "Note";
}

function Queue() {
  const recs = useRecordings();
  const sync = useSync();
  const nav = useNavigate();

  async function refresh() {
    await checkServer();
    drainQueue();
  }

  return (
    <AppShell>
      <div className="px-5 pt-4">
        <div className="flex items-center justify-between">
          <h1 className="text-2xl font-semibold tracking-tight">Queue</h1>
          <Button variant="ghost" size="icon" onClick={refresh} aria-label="Refresh">
            <RefreshCw className={`h-4 w-4 ${sync.syncing ? "animate-spin" : ""}`} />
          </Button>
        </div>
        <p className="mt-1 text-sm text-muted-foreground">
          {recs.length === 0
            ? "Nothing waiting."
            : `${recs.length} item${recs.length === 1 ? "" : "s"}`}
        </p>
      </div>

      <div className="flex-1 px-5 pb-6 pt-4">
        {recs.length === 0 ? (
          <EmptyState />
        ) : (
          <ul className="space-y-3">
            <AnimatePresence initial={false}>
              {recs.map((r) => (
                <motion.li
                  key={r.id}
                  layout
                  initial={{ opacity: 0, y: 8 }}
                  animate={{ opacity: 1, y: 0 }}
                  exit={{ opacity: 0, x: -20 }}
                  className="rounded-2xl border border-border bg-card/70 p-4"
                >
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <p className="truncate font-medium">{r.title}</p>
                      <p className="mt-0.5 text-xs text-muted-foreground">
                        {modalityLabel(r)} ·{" "}
                        {formatDistanceToNow(r.createdAt, { addSuffix: true })}
                      </p>
                    </div>
                    <span
                      className={`shrink-0 rounded-full px-2.5 py-1 text-[11px] font-medium uppercase tracking-wide ${STATUS_STYLES[r.status]}`}
                    >
                      {r.status}
                    </span>
                  </div>

                  {r.status === "uploading" && (
                    <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-muted">
                      <motion.div
                        className="h-full rounded-full bg-primary"
                        animate={{ width: `${r.uploadProgress ?? 0}%` }}
                        transition={{ duration: 0.2 }}
                      />
                    </div>
                  )}

                  {r.error && (
                    <p className="mt-2 text-xs text-destructive/90">{r.error}</p>
                  )}

                  <div className="mt-3 flex gap-2">
                    {r.status === "done" ? (
                      <Button
                        size="sm"
                        variant="secondary"
                        className="rounded-xl"
                        onClick={() => nav({ to: "/notes/$id", params: { id: r.id } })}
                      >
                        Open note
                      </Button>
                    ) : (
                      <>
                        <Button
                          size="sm"
                          variant="secondary"
                          className="rounded-xl"
                          onClick={async () => {
                            await updateRecording(r.id, { status: "pending", error: undefined });
                            drainQueue();
                          }}
                        >
                          <UploadCloud className="mr-1.5 h-3.5 w-3.5" />
                          {r.status === "failed" ? "Retry" : "Upload now"}
                        </Button>
                      </>
                    )}
                    <Button
                      size="sm"
                      variant="ghost"
                      className="ml-auto rounded-xl text-muted-foreground"
                      onClick={() => deleteRecording(r.id)}
                    >
                      <Trash2 className="h-4 w-4" />
                    </Button>
                  </div>
                </motion.li>
              ))}
            </AnimatePresence>
          </ul>
        )}
      </div>
    </AppShell>
  );
}

function EmptyState() {
  return (
    <div className="grid h-full place-items-center py-20 text-center">
      <div>
        <div className="mx-auto grid h-14 w-14 place-items-center rounded-2xl bg-accent/50">
          <Inbox className="h-6 w-6 text-muted-foreground" />
        </div>
        <p className="mt-4 font-medium">Queue is empty</p>
        <p className="mt-1 text-sm text-muted-foreground">
          Recordings appear here while they upload.
        </p>
      </div>
    </div>
  );
}
