import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useEffect, useState } from "react";
import { QrCode, Trash2, HardDrive, Info, Cpu, BookOpen, Link2Off } from "lucide-react";
import { AppShell } from "@/components/AppShell";
import { Button } from "@/components/ui/button";
import { QrScanner } from "@/components/QrScanner";
import { usePairing, useSync } from "@/lib/hooks";
import { clearNotesData, clearPairing, setPairing, storageEstimate } from "@/lib/db";
import { checkServer } from "@/lib/sync";
import { testServer } from "@/lib/api";
import { toast } from "sonner";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog";

export const Route = createFileRoute("/settings")({
  head: () => ({
    meta: [
      { title: "Settings — Faster Notes" },
      { name: "description", content: "Manage your paired server and local data." },
    ],
  }),
  component: Settings,
});

function fmtBytes(n: number) {
  if (!n) return "0 MB";
  const mb = n / (1024 * 1024);
  if (mb < 1024) return `${mb.toFixed(1)} MB`;
  return `${(mb / 1024).toFixed(2)} GB`;
}

function Settings() {
  const { pairing } = usePairing();
  const sync = useSync();
  const [open, setOpen] = useState(false);
  const [usage, setUsage] = useState({ used: 0, quota: 0 });
  const nav = useNavigate();

  useEffect(() => {
    storageEstimate().then((e) =>
      setUsage({ used: e.usage ?? 0, quota: e.quota ?? 0 })
    );
  }, []);

  return (
    <AppShell>
      <div className="px-5 pt-4">
        <h1 className="text-2xl font-semibold tracking-tight">Settings</h1>
      </div>

      <div className="flex-1 space-y-4 px-5 pb-8 pt-4">
        {/* Pairing */}
        <section className="rounded-2xl border border-border bg-card/70 p-4">
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0">
              <p className="text-xs uppercase tracking-wider text-muted-foreground">
                Paired server
              </p>
              <p className="mt-1 truncate font-medium">
                {pairing
                  ? pairing.port === 443
                    ? pairing.server
                    : `${pairing.server}:${pairing.port}`
                  : "Not paired"}
              </p>
              <div className="mt-1 flex items-center gap-2 text-xs">
                <span
                  className={`inline-block h-2 w-2 rounded-full ${
                    sync.serverReachable ? "bg-success" : "bg-warning"
                  }`}
                />
                <span className="text-muted-foreground">
                  {sync.serverReachable ? "Online" : "Unreachable"}
                </span>
                <button
                  onClick={() => checkServer()}
                  className="ml-2 text-primary hover:underline"
                >
                  Test now
                </button>
              </div>
            </div>
            <Button
              size="sm"
              variant="secondary"
              className="rounded-xl"
              onClick={() => setOpen(true)}
            >
              <QrCode className="mr-1.5 h-4 w-4" /> Re-pair
            </Button>
          </div>
        </section>

        {/* Storage */}
        <section className="rounded-2xl border border-border bg-card/70 p-4">
          <div className="flex items-center gap-2 text-xs uppercase tracking-wider text-muted-foreground">
            <HardDrive className="h-3.5 w-3.5" /> Storage
          </div>
          <p className="mt-2 text-sm">
            {fmtBytes(usage.used)} used
            {usage.quota ? ` of ${fmtBytes(usage.quota)} available` : ""}
          </p>
          {usage.quota > 0 && (
            <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-muted">
              <div
                className="h-full bg-primary"
                style={{ width: `${Math.min(100, (usage.used / usage.quota) * 100)}%` }}
              />
            </div>
          )}
        </section>

        {/* Model */}
        <section className="rounded-2xl border border-border bg-card/70 p-4">
          <div className="flex items-center gap-2 text-xs uppercase tracking-wider text-muted-foreground">
            <Cpu className="h-3.5 w-3.5" /> Models
          </div>
          <p className="mt-2 text-sm">Transcription: Whisper (server-side)</p>
          <p className="text-sm">Summarization: Local LLM (server-side)</p>
        </section>

        {/* About */}
        <section className="rounded-2xl border border-border bg-card/70 p-4">
          <div className="flex items-center gap-2 text-xs uppercase tracking-wider text-muted-foreground">
            <Info className="h-3.5 w-3.5" /> About
          </div>
          <p className="mt-2 text-sm text-muted-foreground">
            Faster Notes is an offline-first PWA. Recordings live on your device until
            your paired server is reachable, then sync as transcripts and summaries.
          </p>
        </section>

        {/* Setup guide */}
        <Button
          variant="ghost"
          className="w-full justify-start rounded-2xl"
          onClick={() => nav({ to: "/onboarding" })}
        >
          <BookOpen className="mr-2 h-4 w-4" /> Show setup guide
        </Button>

        {/* Danger zone */}
        <div className="rounded-2xl border border-border bg-card/40 p-2">
          <p className="px-2 pt-2 text-xs uppercase tracking-wider text-muted-foreground">
            Reset
          </p>

          {/* Reset pairing */}
          <AlertDialog>
            <AlertDialogTrigger asChild>
              <Button
                variant="ghost"
                className="w-full justify-start rounded-xl"
                disabled={!pairing}
              >
                <Link2Off className="mr-2 h-4 w-4" />
                Reset pairing
              </Button>
            </AlertDialogTrigger>
            <AlertDialogContent className="rounded-2xl">
              <AlertDialogHeader>
                <AlertDialogTitle>Reset pairing?</AlertDialogTitle>
                <AlertDialogDescription>
                  This forgets your server's address and key. Your recordings and
                  notes stay on this device.
                </AlertDialogDescription>
              </AlertDialogHeader>
              <AlertDialogFooter>
                <AlertDialogCancel className="rounded-xl">Cancel</AlertDialogCancel>
                <AlertDialogAction
                  className="rounded-xl"
                  onClick={async () => {
                    await clearPairing();
                    toast.success("Pairing reset.");
                    nav({ to: "/onboarding" });
                  }}
                >
                  Reset pairing
                </AlertDialogAction>
              </AlertDialogFooter>
            </AlertDialogContent>
          </AlertDialog>

          {/* Clear notes */}
          <AlertDialog>
            <AlertDialogTrigger asChild>
              <Button
                variant="ghost"
                className="w-full justify-start rounded-xl text-destructive hover:text-destructive"
              >
                <Trash2 className="mr-2 h-4 w-4" />
                Clear notes & recordings
              </Button>
            </AlertDialogTrigger>
            <AlertDialogContent className="rounded-2xl">
              <AlertDialogHeader>
                <AlertDialogTitle>Clear all notes?</AlertDialogTitle>
                <AlertDialogDescription>
                  This deletes every recording, transcript, and summary on this
                  device. Your server pairing stays. This cannot be undone.
                </AlertDialogDescription>
              </AlertDialogHeader>
              <AlertDialogFooter>
                <AlertDialogCancel className="rounded-xl">Cancel</AlertDialogCancel>
                <AlertDialogAction
                  className="rounded-xl bg-destructive text-destructive-foreground"
                  onClick={async () => {
                    await clearNotesData();
                    toast.success("Notes cleared.");
                  }}
                >
                  Delete all notes
                </AlertDialogAction>
              </AlertDialogFooter>
            </AlertDialogContent>
          </AlertDialog>
        </div>
      </div>

      <QrScanner
        open={open}
        onClose={() => setOpen(false)}
        onPaired={async (p) => {
          try {
            await setPairing(p);
            setOpen(false);
            const t = toast.loading("Connecting…");
            const r = await testServer();
            toast.dismiss(t);
            if (r.ok) toast.success(`Connected to ${r.url}`);
            else toast.error(r.error ?? "Couldn't connect.", { duration: 8000 });
            checkServer();
          } catch (e) {
            toast.error(e instanceof Error ? e.message : "Pairing failed.");
          }
        }}
      />
    </AppShell>
  );
}
