import { useEffect, useMemo, useRef, useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { Link } from "@tanstack/react-router";
import { Camera, Mic, Pause, Play, Send, Square, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { addRecording } from "@/lib/db";
import { drainQueue } from "@/lib/sync";
import { usePairing } from "@/lib/hooks";
import { useAudioRecorder, type AudioResult } from "@/lib/useAudioRecorder";
import { toast } from "sonner";

function fmt(secs: number) {
  const s = Math.floor(secs);
  const mm = Math.floor(s / 60).toString().padStart(2, "0");
  const ss = (s % 60).toString().padStart(2, "0");
  return `${mm}:${ss}`;
}

// Spoken-language choices. `code` is forced into Whisper (null = auto-detect, which
// tends to mislabel Danish/Swedish as Norwegian). Persisted so it sticks per device.
const LANGS: { code: string | null; label: string }[] = [
  { code: null, label: "Auto" },
  { code: "sv", label: "Svenska" },
  { code: "da", label: "Dansk" },
  { code: "en", label: "English" },
];
const LANG_KEY = "fn_lang";

export function Compose() {
  const { pairing, loaded } = usePairing();
  const [text, setText] = useState("");
  const [audio, setAudio] = useState<AudioResult | null>(null);
  const [images, setImages] = useState<File[]>([]);
  const [sending, setSending] = useState(false);
  const [language, setLanguage] = useState<string | null>(() =>
    typeof localStorage !== "undefined" ? localStorage.getItem(LANG_KEY) : null,
  );
  const fileRef = useRef<HTMLInputElement>(null);
  const rec = useAudioRecorder();

  function pickLang(code: string | null) {
    setLanguage(code);
    if (code) localStorage.setItem(LANG_KEY, code);
    else localStorage.removeItem(LANG_KEY);
  }

  const imageUrls = useMemo(() => images.map((f) => URL.createObjectURL(f)), [images]);
  useEffect(() => () => imageUrls.forEach((u) => URL.revokeObjectURL(u)), [imageUrls]);
  useEffect(
    () => () => {
      if (audio) URL.revokeObjectURL(audio.url);
    },
    [audio],
  );

  const isRecording = rec.phase === "recording" || rec.phase === "paused";
  const hasContent = text.trim().length > 0 || !!audio || images.length > 0;
  const canSend = hasContent && !isRecording && !sending;

  async function toggleRecord() {
    if (rec.phase === "idle" || rec.phase === "denied") {
      if (audio) {
        URL.revokeObjectURL(audio.url);
        setAudio(null);
      }
      await rec.start();
    } else {
      const result = await rec.stop();
      if (result) setAudio(result);
    }
  }

  function onPickPhotos(e: React.ChangeEvent<HTMLInputElement>) {
    // Snapshot the FileList immediately — it's live and gets cleared on reset.
    const picked = e.target.files ? Array.from(e.target.files) : [];
    e.target.value = "";
    if (!picked.length) return;
    setImages((prev) => [...prev, ...picked]);
  }

  function removeImage(i: number) {
    setImages((prev) => prev.filter((_, idx) => idx !== i));
  }

  function clearAudio() {
    if (audio) URL.revokeObjectURL(audio.url);
    setAudio(null);
  }

  async function send() {
    if (!canSend) return;
    setSending(true);
    try {
      const id = `rec_${Date.now()}_${Math.random().toString(36).slice(2, 7)}`;
      const titleBase =
        text.trim().split("\n")[0].slice(0, 60) ||
        (audio ? "Voice note" : images.length ? "Photo note" : "Note");
      await addRecording({
        id,
        createdAt: Date.now(),
        title: titleBase,
        status: "pending",
        text: text.trim() || undefined,
        blob: audio?.blob,
        mimeType: audio?.mimeType,
        durationSec: audio?.durationSec,
        images: images.length ? images.map((f) => f as Blob) : undefined,
        language: language ?? undefined,
      });
      toast.success(
        pairing
          ? "Sent. Will upload when your server is reachable."
          : "Saved. Will send once you pair a server.",
      );
      setText("");
      clearAudio();
      setImages([]);
      drainQueue();
    } finally {
      setSending(false);
    }
  }

  return (
    <div className="flex flex-1 flex-col px-5 pt-4 pb-4">
      {loaded && !pairing && (
        <Link
          to="/onboarding"
          className="mb-3 block rounded-2xl border border-warning/30 bg-warning/10 px-4 py-3 text-sm text-warning-foreground"
        >
          Not paired yet — notes are saved here and will send once you connect a server.
        </Link>
      )}

      <Textarea
        value={text}
        onChange={(e) => setText(e.target.value)}
        placeholder="What's on your mind? Type here, record audio, or snap a photo…"
        className="min-h-[160px] flex-1 resize-none rounded-2xl border-border bg-card/60 p-4 text-[15px] leading-relaxed shadow-none focus-visible:ring-1"
      />

      {/* Attachments */}
      {(audio || images.length > 0) && (
        <div className="mt-3 space-y-3">
          {audio && (
            <div className="flex items-center gap-3 rounded-2xl border border-border bg-card/60 p-3">
              <div className="grid h-9 w-9 shrink-0 place-items-center rounded-full bg-record/15 text-record">
                <Mic className="h-4 w-4" />
              </div>
              <div className="min-w-0 flex-1">
                <audio src={audio.url} controls className="w-full" />
              </div>
              <span className="font-mono text-xs tabular-nums text-muted-foreground">
                {fmt(audio.durationSec)}
              </span>
              <button
                onClick={clearAudio}
                aria-label="Remove audio"
                className="grid h-7 w-7 place-items-center rounded-full text-muted-foreground hover:bg-accent"
              >
                <X className="h-4 w-4" />
              </button>
            </div>
          )}

          {images.length > 0 && (
            <div className="grid grid-cols-4 gap-2">
              {imageUrls.map((url, i) => (
                <div key={i} className="relative aspect-square overflow-hidden rounded-xl border border-border bg-muted">
                  <img src={url} alt="" className="h-full w-full object-cover" />
                  <button
                    onClick={() => removeImage(i)}
                    aria-label="Remove photo"
                    className="absolute right-1 top-1 grid h-6 w-6 place-items-center rounded-full bg-background/85 text-foreground shadow"
                  >
                    <X className="h-3.5 w-3.5" />
                  </button>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Live recording strip */}
      <AnimatePresence>
        {isRecording && (
          <motion.div
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: 8 }}
            className="mt-3 flex items-center gap-3 rounded-2xl border border-record/30 bg-record/10 px-4 py-3"
          >
            <span className="font-mono text-sm tabular-nums">{fmt(rec.elapsed)}</span>
            <div className="flex h-8 flex-1 items-center justify-center gap-[2px]">
              {rec.levels.map((l, i) => (
                <span
                  key={i}
                  className="w-1 rounded-full bg-record"
                  style={{ height: `${Math.max(3, l * 28)}px`, opacity: rec.phase === "paused" ? 0.4 : 0.9 }}
                />
              ))}
            </div>
            {rec.phase === "recording" ? (
              <Button size="sm" variant="ghost" className="rounded-full" onClick={rec.pause}>
                <Pause className="h-4 w-4" />
              </Button>
            ) : (
              <Button size="sm" variant="ghost" className="rounded-full" onClick={rec.resume}>
                <Play className="h-4 w-4" />
              </Button>
            )}
          </motion.div>
        )}
      </AnimatePresence>

      {rec.phase === "denied" && (
        <div className="mt-3 rounded-2xl border border-destructive/30 bg-destructive/10 p-3 text-xs text-destructive-foreground">
          Microphone access was blocked. Allow it in your browser settings to record audio.
        </div>
      )}

      {/* Spoken-language picker (forces Whisper instead of auto-detect) */}
      <div className="mt-3">
        <div className="mb-1 text-[11px] uppercase tracking-wide text-muted-foreground">Spoken language</div>
        <div className="flex items-center gap-1 rounded-2xl border border-border bg-card/60 p-1 text-xs">
          {LANGS.map((l) => (
            <button
              key={l.label}
              onClick={() => pickLang(l.code)}
              className={`flex-1 rounded-xl px-2 py-1.5 transition-colors ${
                language === l.code
                  ? "bg-primary text-primary-foreground"
                  : "text-muted-foreground hover:text-foreground"
              }`}
            >
              {l.label}
            </button>
          ))}
        </div>
      </div>

      {/* Action row */}
      <div className="mt-3 flex items-center gap-2">
        <input
          ref={fileRef}
          type="file"
          accept="image/*"
          capture="environment"
          multiple
          className="hidden"
          onChange={onPickPhotos}
        />
        <Button
          variant="secondary"
          size="lg"
          className="h-12 w-12 shrink-0 rounded-2xl p-0"
          onClick={() => fileRef.current?.click()}
          aria-label="Add photo"
        >
          <Camera className="h-5 w-5" />
        </Button>
        <Button
          variant="secondary"
          size="lg"
          onClick={toggleRecord}
          aria-label={isRecording ? "Stop recording" : "Record audio"}
          className={`h-12 w-12 shrink-0 rounded-2xl p-0 ${
            isRecording ? "bg-record text-white hover:bg-record/90" : ""
          }`}
        >
          {isRecording ? <Square className="h-5 w-5 fill-current" /> : <Mic className="h-5 w-5" />}
        </Button>
        <Button
          size="lg"
          disabled={!canSend}
          onClick={send}
          className="h-12 flex-1 rounded-2xl bg-primary glow-primary"
        >
          <Send className="mr-2 h-4 w-4" />
          Send
        </Button>
      </div>
    </div>
  );
}
