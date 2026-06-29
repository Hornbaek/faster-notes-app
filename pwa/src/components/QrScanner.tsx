import { useEffect, useRef, useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { QrCode, X, Keyboard } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";

interface Props {
  open: boolean;
  onClose: () => void;
  onPaired: (p: { server: string; port: number; apiKey: string; secure?: boolean }) => void;
}

// Only allow pairing with servers on the local network. This prevents a
// malicious QR code from redirecting audio uploads + API keys to an
// attacker-controlled host on the public internet.
const PRIVATE_HOST_RE =
  /^(?:localhost|(?:[a-z0-9-]+\.)*local|10(?:\.\d{1,3}){3}|192\.168(?:\.\d{1,3}){2}|172\.(?:1[6-9]|2\d|3[01])(?:\.\d{1,3}){2}|127(?:\.\d{1,3}){3}|169\.254(?:\.\d{1,3}){2}|\[?(?:fc|fd)[0-9a-f]{2}:[0-9a-f:]*\]?|\[?fe80:[0-9a-f:]*\]?)$/i;

// A permissive public hostname check used only for remote HTTPS pairing
// (secure + port 443). Requires at least one dot so we still reject garbage.
const PUBLIC_HOST_RE = /^[a-z0-9]([a-z0-9.-]{0,251}[a-z0-9])?$/i;

function validatePairing(input: {
  server: unknown;
  port: unknown;
  apiKey: unknown;
  secure?: unknown;
}): { ok: true; value: { server: string; port: number; apiKey: string; secure?: boolean } } | { ok: false; error: string } {
  const server = typeof input.server === "string" ? input.server.trim() : "";
  const portNum = typeof input.port === "string" ? Number(input.port) : (input.port as number);
  const apiKey = typeof input.apiKey === "string" ? input.apiKey.trim() : "";
  const secure = input.secure !== false;
  const isPrivate = !!server && server.length <= 253 && PRIVATE_HOST_RE.test(server);
  const isRemoteHttps =
    !!server &&
    server.length <= 253 &&
    secure &&
    portNum === 443 &&
    server.includes(".") &&
    PUBLIC_HOST_RE.test(server);
  if (!isPrivate && !isRemoteHttps) {
    return {
      ok: false,
      error:
        "Server must be a local IP, a .local hostname, or a remote HTTPS address (port 443).",
    };
  }
  if (!Number.isInteger(portNum) || portNum < 1 || portNum > 65535) {
    return { ok: false, error: "Port must be a number between 1 and 65535." };
  }
  if (apiKey.length < 16 || apiKey.length > 512) {
    return { ok: false, error: "API key must be at least 16 characters." };
  }
  return {
    ok: true,
    value: { server, port: portNum, apiKey, secure },
  };
}

export function QrScanner({ open, onClose, onPaired }: Props) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const [manual, setManual] = useState(false);
  const [server, setServer] = useState("");
  const [port, setPort] = useState("8766");
  const [apiKey, setApiKey] = useState("");
  const [secure, setSecure] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Always call the latest onPaired without making the scanner effect depend on
  // its identity — the parent passes a fresh inline callback every render, which
  // would otherwise tear down and restart the camera on each re-render.
  const onPairedRef = useRef(onPaired);
  useEffect(() => {
    onPairedRef.current = onPaired;
  });

  useEffect(() => {
    if (!open || manual) return;
    let cancelled = false;
    let handled = false;
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    let scanner: any = null;
    let started = false;

    // Fully tear the camera down (idempotent). html5-qrcode injects its own <video>
    // DOM into the region; if React unmounts that subtree while the camera is still
    // live — e.g. the parent navigates away the instant we pair — the unmount throws a
    // removeChild error that the root error boundary shows as "something went wrong".
    // So stop + clear and AWAIT it before doing anything that can unmount us.
    const teardown = async () => {
      if (!scanner || !started) return;
      started = false;
      try { await scanner.stop(); } catch { /* already stopped */ }
      try { await scanner.clear(); } catch { /* nothing to clear */ }
    };

    (async () => {
      try {
        const mod = await import("html5-qrcode");
        if (cancelled) return;
        const el = containerRef.current;
        if (!el) return;
        el.innerHTML = "";
        const inner = document.createElement("div");
        inner.id = "qr-scanner-region";
        el.appendChild(inner);
        scanner = new mod.Html5Qrcode(inner.id, { verbose: false });
        await scanner.start(
          { facingMode: "environment" },
          { fps: 10, qrbox: { width: 240, height: 240 } },
          (text) => {
            // The decode callback fires ~10×/second; guard so we pair exactly once.
            if (handled) return;
            try {
              const data = JSON.parse(text);
              const result = validatePairing({
                server: data.server,
                port: data.port,
                apiKey: data.apiKey,
                secure: data.secure,
              });
              if (result.ok) {
                handled = true;
                // Camera DOWN first, THEN hand the pairing to the parent — otherwise
                // the parent's navigate/close unmounts us mid-stream and crashes.
                void teardown().finally(() => onPairedRef.current(result.value));
              } else {
                setError(result.error);
              }
            } catch {
              setError("That QR code isn't a valid pairing payload.");
            }
          },
          () => {}
        );
        started = true;
        if (cancelled) void teardown(); // unmounted while the camera was starting
      } catch (e) {
        console.error(e);
        setError("Couldn't open the camera. Use manual entry instead.");
        setManual(true);
      }
    })();

    return () => {
      cancelled = true;
      void teardown();
    };
  }, [open, manual]);

  function submitManual(e: React.FormEvent) {
    e.preventDefault();
    const result = validatePairing({ server, port, apiKey, secure });
    if (!result.ok) {
      setError(result.error);
      return;
    }
    setError(null);
    onPaired(result.value);
  }

  return (
    <AnimatePresence>
      {open && (
        <motion.div
          className="fixed inset-0 z-50 flex items-end justify-center bg-black/70 backdrop-blur-sm"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          onClick={onClose}
        >
          <motion.div
            initial={{ y: "100%" }}
            animate={{ y: 0 }}
            exit={{ y: "100%" }}
            transition={{ type: "spring", damping: 30, stiffness: 280 }}
            className="w-full max-w-md rounded-t-3xl border border-border bg-card p-5 shadow-2xl"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="mb-4 flex items-center justify-between">
              <div className="flex items-center gap-2">
                <QrCode className="h-5 w-5 text-primary" />
                <h2 className="text-lg font-semibold">Pair your server</h2>
              </div>
              <Button variant="ghost" size="icon" onClick={onClose}>
                <X className="h-5 w-5" />
              </Button>
            </div>

            {!manual ? (
              <>
                <div
                  ref={containerRef}
                  className="aspect-square w-full overflow-hidden rounded-2xl bg-black"
                />
                <p className="mt-3 text-center text-sm text-muted-foreground">
                  Point your camera at the QR code shown by your server.
                </p>
                {error && (
                  <p className="mt-2 text-center text-sm text-destructive">{error}</p>
                )}
                <Button
                  variant="ghost"
                  className="mt-3 w-full rounded-xl"
                  onClick={() => setManual(true)}
                >
                  <Keyboard className="mr-2 h-4 w-4" />
                  Enter details manually
                </Button>
              </>
            ) : (
              <form onSubmit={submitManual} className="space-y-3">
                <div>
                  <Label htmlFor="server">Host / IP</Label>
                  <Input
                    id="server"
                    placeholder={
                      secure && port === "443"
                        ? "192.168.1.x or notes.example.com"
                        : "192.168.1.123"
                    }
                    value={server}
                    onChange={(e) => setServer(e.target.value)}
                  />
                </div>
                <div>
                  <Label htmlFor="port">Port</Label>
                  <Input
                    id="port"
                    inputMode="numeric"
                    value={port}
                    onChange={(e) => setPort(e.target.value)}
                  />
                </div>
                <div>
                  <Label htmlFor="key">API key</Label>
                  <Input
                    id="key"
                    value={apiKey}
                    onChange={(e) => setApiKey(e.target.value)}
                  />
                </div>
                <div className="flex items-center justify-between rounded-xl border border-border px-3 py-2">
                  <Label htmlFor="secure" className="text-sm">Use HTTPS</Label>
                  <Switch id="secure" checked={secure} onCheckedChange={setSecure} />
                </div>
                {error && (
                  <p className="text-sm text-destructive">{error}</p>
                )}
                <Button type="submit" className="w-full rounded-xl">
                  Pair
                </Button>
                <Button
                  type="button"
                  variant="ghost"
                  className="w-full rounded-xl"
                  onClick={() => setManual(false)}
                >
                  Back to QR scanner
                </Button>
              </form>
            )}
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
