import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useState } from "react";
import { motion } from "framer-motion";
import { Mic, QrCode, Download, MonitorPlay, Smartphone } from "lucide-react";
import { Button } from "@/components/ui/button";
import { QrScanner } from "@/components/QrScanner";
import { setPairing } from "@/lib/db";
import { testServer } from "@/lib/api";
import { checkServer } from "@/lib/sync";
import { toast } from "sonner";
import { usePairing } from "@/lib/hooks";

export const Route = createFileRoute("/onboarding")({
  head: () => ({
    meta: [
      { title: "Get started — Faster Notes" },
      {
        name: "description",
        content:
          "Install the Faster Notes desktop server, then pair your phone to start recording.",
      },
    ],
  }),
  component: Onboarding,
});

// Once the installer is uploaded, swap this href for the asset URL.
const INSTALLER_HREF: string | null = null;

function Onboarding() {
  const [open, setOpen] = useState(false);
  const { pairing } = usePairing();
  const nav = useNavigate();

  return (
    <div className="mx-auto flex min-h-dvh max-w-md flex-col px-6 pb-10 pt-12">
      <motion.div
        initial={{ scale: 0.85, opacity: 0 }}
        animate={{ scale: 1, opacity: 1 }}
        transition={{ type: "spring", damping: 20 }}
        className="mx-auto grid h-16 w-16 place-items-center rounded-3xl bg-primary glow-primary"
      >
        <Mic className="h-7 w-7 text-primary-foreground" />
      </motion.div>

      <div className="mt-6 text-center">
        <h1 className="text-3xl font-semibold tracking-tight">Get started</h1>
        <p className="mt-3 text-balance text-sm text-muted-foreground">
          Faster Notes pairs your phone with a small server that runs on your
          computer. Three steps and you're set.
        </p>
      </div>

      <ol className="mt-8 space-y-3">
        <Step
          n={1}
          icon={Download}
          title="Install the desktop server"
          body="Download and run the installer on the computer you want to use for transcription."
        >
          {INSTALLER_HREF ? (
            <Button asChild size="sm" className="mt-3 rounded-xl">
              <a href={INSTALLER_HREF} download>
                <Download className="mr-1.5 h-4 w-4" />
                Download installer
              </a>
            </Button>
          ) : (
            <Button size="sm" disabled className="mt-3 rounded-xl">
              <Download className="mr-1.5 h-4 w-4" />
              Installer coming soon
            </Button>
          )}
        </Step>

        <Step
          n={2}
          icon={MonitorPlay}
          title="Open it on your computer"
          body="Launch Faster Notes Server. It will show a QR code with your local address and a one-time key."
        />

        <Step
          n={3}
          icon={Smartphone}
          title="Pair this phone"
          body="Tap the button below and point your camera at the QR code on your computer screen."
        />
      </ol>

      <div className="mt-auto pt-8 space-y-2">
        <Button
          size="lg"
          className="w-full rounded-2xl text-base glow-primary"
          onClick={() => setOpen(true)}
        >
          <QrCode className="mr-2 h-5 w-5" />
          {pairing ? "Re-pair this phone" : "Scan QR code to pair"}
        </Button>
        {pairing && (
          <Button
            variant="ghost"
            className="w-full rounded-2xl"
            onClick={() => nav({ to: "/" })}
          >
            Back to recorder
          </Button>
        )}
        <p className="text-center text-xs text-muted-foreground">
          On the same Wi-Fi for local access, or use remote access via a Cloudflare Tunnel.
        </p>
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
            if (r.ok) {
              toast.success(`Connected to ${r.url}`);
              checkServer();
              nav({ to: "/" });
            } else {
              toast.error(r.error ?? "Couldn't connect.", { duration: 8000 });
            }
          } catch (e) {
            toast.error(e instanceof Error ? e.message : "Pairing failed.");
          }
        }}
      />
    </div>
  );
}

interface StepProps {
  n: number;
  icon: typeof Download;
  title: string;
  body: string;
  children?: React.ReactNode;
}

function Step({ n, icon: I, title, body, children }: StepProps) {
  return (
    <li className="rounded-2xl border border-border bg-card/60 p-4">
      <div className="flex items-start gap-3">
        <div className="relative grid h-10 w-10 shrink-0 place-items-center rounded-xl bg-accent/60">
          <I className="h-5 w-5 text-primary" />
          <span className="absolute -right-1.5 -top-1.5 grid h-5 w-5 place-items-center rounded-full bg-primary text-[11px] font-semibold text-primary-foreground">
            {n}
          </span>
        </div>
        <div className="min-w-0">
          <p className="font-medium">{title}</p>
          <p className="text-sm text-muted-foreground">{body}</p>
          {children}
        </div>
      </div>
    </li>
  );
}
