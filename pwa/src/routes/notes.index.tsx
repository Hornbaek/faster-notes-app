import { createFileRoute, Link } from "@tanstack/react-router";
import { useMemo, useState } from "react";
import { motion } from "framer-motion";
import { Search, NotebookText } from "lucide-react";
import { formatDistanceToNow } from "date-fns";
import { AppShell } from "@/components/AppShell";
import { Input } from "@/components/ui/input";
import { useRecordings, useResults } from "@/lib/hooks";

export const Route = createFileRoute("/notes/")({
  head: () => ({
    meta: [
      { title: "Notes — Faster Notes" },
      { name: "description", content: "Your transcribed voice notes." },
    ],
  }),
  component: Notes,
});

function Notes() {
  const results = useResults();
  const recs = useRecordings();
  const [q, setQ] = useState("");

  const items = useMemo(() => {
    const byId = new Map(recs.map((r) => [r.id, r]));
    return results
      .map((r) => ({ result: r, rec: byId.get(r.recordingId) }))
      .filter(({ result, rec }) => {
        if (!q.trim()) return true;
        const hay = `${rec?.title ?? ""} ${result.transcript} ${result.summary} ${result.tags.join(" ")}`.toLowerCase();
        return hay.includes(q.toLowerCase());
      });
  }, [results, recs, q]);

  return (
    <AppShell>
      <div className="px-5 pt-4">
        <h1 className="text-2xl font-semibold tracking-tight">Notes</h1>
        <div className="relative mt-3">
          <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Search transcripts, summaries, tags…"
            className="rounded-2xl pl-9"
          />
        </div>
      </div>

      <div className="flex-1 px-5 pb-6 pt-4">
        {items.length === 0 ? (
          <div className="grid h-full place-items-center py-20 text-center">
            <div>
              <div className="mx-auto grid h-14 w-14 place-items-center rounded-2xl bg-accent/50">
                <NotebookText className="h-6 w-6 text-muted-foreground" />
              </div>
              <p className="mt-4 font-medium">No notes yet</p>
              <p className="mt-1 text-sm text-muted-foreground">
                Tap record to capture your first one.
              </p>
            </div>
          </div>
        ) : (
          <ul className="space-y-3">
            {items.map(({ result, rec }, i) => (
              <motion.li
                key={result.recordingId}
                initial={{ opacity: 0, y: 6 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ delay: i * 0.02 }}
              >
                <Link
                  to="/notes/$id"
                  params={{ id: result.recordingId }}
                  className="block rounded-2xl border border-border bg-card/70 p-4 transition-colors hover:bg-card"
                >
                  <div className="flex items-start justify-between gap-3">
                    <p className="truncate font-medium">{rec?.title ?? "Untitled note"}</p>
                    <span className="shrink-0 text-xs text-muted-foreground">
                      {formatDistanceToNow(result.completedAt, { addSuffix: true })}
                    </span>
                  </div>
                  <p className="mt-1 line-clamp-2 text-sm text-muted-foreground">
                    {result.summary}
                  </p>
                  {result.tags.length > 0 && (
                    <div className="mt-2 flex flex-wrap gap-1.5">
                      {result.tags.slice(0, 3).map((t) => (
                        <span
                          key={t}
                          className="rounded-full bg-accent/60 px-2 py-0.5 text-[11px] text-accent-foreground"
                        >
                          #{t}
                        </span>
                      ))}
                    </div>
                  )}
                </Link>
              </motion.li>
            ))}
          </ul>
        )}
      </div>
    </AppShell>
  );
}
