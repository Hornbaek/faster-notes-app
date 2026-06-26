import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { useEffect, useState } from "react";
import { ArrowLeft, Copy, Download, Trash2, RefreshCw, Volume2, Loader2 } from "lucide-react";
import { format } from "date-fns";
import { AppShell } from "@/components/AppShell";
import { Button } from "@/components/ui/button";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import {
  getResult,
  getRecording,
  deleteRecording,
  subscribe,
  type NoteResult,
  type Recording,
} from "@/lib/db";
import { retranscribeNote } from "@/lib/sync";
import { getMediaUrl } from "@/lib/api";
import { toast } from "sonner";

export const Route = createFileRoute("/notes/$id")({
  head: () => ({
    meta: [
      { title: "Note — Faster Notes" },
      { name: "description", content: "Transcript, summary, action items, and tags." },
    ],
  }),
  component: NoteDetail,
});

function NoteDetail() {
  const { id } = Route.useParams();
  const nav = useNavigate();
  const [result, setResult] = useState<NoteResult | null>(null);
  const [rec, setRec] = useState<Recording | null>(null);
  const [imageUrls, setImageUrls] = useState<string[]>([]);
  const [audioUrl, setAudioUrl] = useState<string | null>(null);
  const [loadingAudio, setLoadingAudio] = useState(false);
  const [retx, setRetx] = useState(false);

  // Reactive: re-read the note whenever the local DB changes, so the transcript
  // (then summary) appears as the server's two-pass update lands.
  useEffect(() => {
    let alive = true;
    const reload = async () => {
      const [r, re] = await Promise.all([getResult(id), getRecording(id)]);
      if (!alive) return;
      setResult(r ?? null);
      setRec(re ?? null);
    };
    reload();
    const unsub = subscribe(reload);
    return () => {
      alive = false;
      unsub();
    };
  }, [id]);

  // Build object URLs for any attached images once per note.
  useEffect(() => {
    if (!rec?.images?.length) return;
    const urls = rec.images.map((b) => URL.createObjectURL(b));
    setImageUrls(urls);
    return () => urls.forEach((u) => URL.revokeObjectURL(u));
  }, [rec?.id, rec?.images?.length]);

  // Revoke the recording's object URL on unmount.
  useEffect(() => {
    return () => {
      if (audioUrl) URL.revokeObjectURL(audioUrl);
    };
  }, [audioUrl]);

  async function loadAudio() {
    if (!result?.jobId || audioUrl || loadingAudio) return;
    setLoadingAudio(true);
    try {
      setAudioUrl(await getMediaUrl(result.jobId));
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Couldn't load the recording.");
    } finally {
      setLoadingAudio(false);
    }
  }

  async function onRetranscribe() {
    if (retx) return;
    setRetx(true);
    const t = toast.loading("Re-transcribing with the current model…");
    try {
      await retranscribeNote(id);
      toast.success("Re-transcribed.", { id: t });
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Re-transcribe failed.", { id: t });
    } finally {
      setRetx(false);
    }
  }

  function asMarkdown() {
    if (!result || !rec) return "";
    return [
      `# ${rec.title}`,
      `_${format(result.completedAt, "PPpp")}_`,
      "",
      "## Summary",
      result.summary,
      "",
      "## Action items",
      ...result.actionItems.map((a) => `- [ ] ${a}`),
      "",
      "## Tags",
      result.tags.map((t) => `#${t}`).join(" "),
      "",
      "## Transcript",
      result.transcript,
    ].join("\n");
  }

  async function copyMd() {
    await navigator.clipboard.writeText(asMarkdown());
    toast.success("Copied as Markdown.");
  }
  function downloadMd() {
    const blob = new Blob([asMarkdown()], { type: "text/markdown" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${rec?.title ?? "note"}.md`;
    a.click();
    URL.revokeObjectURL(url);
  }

  if (!result || !rec) {
    return (
      <AppShell>
        <div className="px-5 pt-4">
          <div className="h-6 w-32 animate-pulse rounded bg-muted" />
          <div className="mt-3 space-y-2">
            <div className="h-4 w-full animate-pulse rounded bg-muted" />
            <div className="h-4 w-5/6 animate-pulse rounded bg-muted" />
            <div className="h-4 w-2/3 animate-pulse rounded bg-muted" />
          </div>
        </div>
      </AppShell>
    );
  }

  return (
    <AppShell>
      <div className="px-5 pt-3">
        <Link
          to="/notes"
          className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
        >
          <ArrowLeft className="h-4 w-4" /> Notes
        </Link>
        <h1 className="mt-2 text-2xl font-semibold leading-tight tracking-tight">
          {rec.title}
        </h1>
        <p className="mt-1 text-xs text-muted-foreground">
          {format(result.completedAt, "PPp")}
        </p>

        <div className="mt-3 flex flex-wrap gap-2">
          <Button size="sm" variant="secondary" className="rounded-xl" onClick={copyMd}>
            <Copy className="mr-1.5 h-3.5 w-3.5" /> Copy MD
          </Button>
          <Button size="sm" variant="secondary" className="rounded-xl" onClick={downloadMd}>
            <Download className="mr-1.5 h-3.5 w-3.5" /> Export
          </Button>
          {result.hasAudio && (
            <>
              <Button
                size="sm"
                variant="secondary"
                className="rounded-xl"
                onClick={onRetranscribe}
                disabled={retx}
              >
                {retx ? (
                  <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
                ) : (
                  <RefreshCw className="mr-1.5 h-3.5 w-3.5" />
                )}
                Re-transcribe
              </Button>
              {!audioUrl && (
                <Button
                  size="sm"
                  variant="secondary"
                  className="rounded-xl"
                  onClick={loadAudio}
                  disabled={loadingAudio}
                >
                  {loadingAudio ? (
                    <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
                  ) : (
                    <Volume2 className="mr-1.5 h-3.5 w-3.5" />
                  )}
                  Play
                </Button>
              )}
            </>
          )}
          <Button
            size="sm"
            variant="ghost"
            className="ml-auto rounded-xl text-muted-foreground"
            onClick={async () => {
              await deleteRecording(id);
              toast.success("Note deleted.");
              nav({ to: "/notes" });
            }}
          >
            <Trash2 className="h-4 w-4" />
          </Button>
        </div>

        {audioUrl && (
          <audio src={audioUrl} controls autoPlay className="mt-3 w-full" />
        )}
      </div>

      {imageUrls.length > 0 && (
        <div className="mt-4 flex gap-2 overflow-x-auto px-5 pb-1">
          {imageUrls.map((u, i) => (
            <a
              key={i}
              href={u}
              target="_blank"
              rel="noreferrer"
              className="h-24 w-24 shrink-0 overflow-hidden rounded-xl border border-border bg-muted"
            >
              <img src={u} alt="" className="h-full w-full object-cover" />
            </a>
          ))}
        </div>
      )}

      <div className="flex-1 px-5 pb-6 pt-4">
        <Tabs defaultValue="summary" className="w-full">
          <TabsList className="grid w-full grid-cols-4 rounded-2xl bg-muted/60 p-1">
            <TabsTrigger value="summary" className="rounded-xl">Summary</TabsTrigger>
            <TabsTrigger value="transcript" className="rounded-xl">Script</TabsTrigger>
            <TabsTrigger value="actions" className="rounded-xl">Actions</TabsTrigger>
            <TabsTrigger value="tags" className="rounded-xl">Tags</TabsTrigger>
          </TabsList>
          <TabsContent value="summary" className="mt-4">
            <div className="rounded-2xl border border-border bg-card/60 p-4 text-[15px] leading-relaxed">
              {result.summary ? (
                result.summary
              ) : result.summaryPending ? (
                <span className="flex items-center gap-2 text-muted-foreground">
                  <Loader2 className="h-4 w-4 animate-spin" />
                  Transcript ready — generating summary…
                </span>
              ) : (
                <span className="text-muted-foreground">No summary.</span>
              )}
            </div>
          </TabsContent>
          <TabsContent value="transcript" className="mt-4">
            <div className="whitespace-pre-wrap rounded-2xl border border-border bg-card/60 p-4 text-[15px] leading-relaxed">
              {result.transcript}
            </div>
          </TabsContent>
          <TabsContent value="actions" className="mt-4">
            <ul className="space-y-2">
              {result.actionItems.map((a, i) => (
                <li
                  key={i}
                  className="rounded-xl border border-border bg-card/60 p-3 text-sm"
                >
                  <span className="mr-2 text-primary">▸</span>
                  {a}
                </li>
              ))}
              {result.actionItems.length === 0 && (
                <p className="text-sm text-muted-foreground">No action items.</p>
              )}
            </ul>
          </TabsContent>
          <TabsContent value="tags" className="mt-4">
            <div className="flex flex-wrap gap-2">
              {result.tags.map((t) => (
                <span
                  key={t}
                  className="rounded-full bg-accent/60 px-3 py-1 text-sm text-accent-foreground"
                >
                  #{t}
                </span>
              ))}
              {result.tags.length === 0 && (
                <p className="text-sm text-muted-foreground">No tags.</p>
              )}
            </div>
          </TabsContent>
        </Tabs>
      </div>
    </AppShell>
  );
}
