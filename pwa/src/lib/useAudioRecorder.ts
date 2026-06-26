import { useCallback, useEffect, useRef, useState } from "react";

export type RecPhase = "idle" | "recording" | "paused" | "denied";

export interface AudioResult {
  blob: Blob;
  mimeType: string;
  durationSec: number;
  url: string;
}

export function useAudioRecorder() {
  const [phase, setPhase] = useState<RecPhase>("idle");
  const [elapsed, setElapsed] = useState(0);
  const [levels, setLevels] = useState<number[]>(() => Array(40).fill(0.05));

  const mediaRef = useRef<MediaRecorder | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const audioCtxRef = useRef<AudioContext | null>(null);
  const analyserRef = useRef<AnalyserNode | null>(null);
  const rafRef = useRef<number | null>(null);
  const startTsRef = useRef(0);
  const accumRef = useRef(0);
  const chunksRef = useRef<Blob[]>([]);
  const resolveRef = useRef<((r: AudioResult | null) => void) | null>(null);

  const cleanup = useCallback(() => {
    if (rafRef.current) cancelAnimationFrame(rafRef.current);
    rafRef.current = null;
    streamRef.current?.getTracks().forEach((t) => t.stop());
    streamRef.current = null;
    if (audioCtxRef.current && audioCtxRef.current.state !== "closed") {
      audioCtxRef.current.close().catch(() => {});
    }
    audioCtxRef.current = null;
    analyserRef.current = null;
    mediaRef.current = null;
  }, []);

  useEffect(() => () => cleanup(), [cleanup]);

  const tick = useCallback(() => {
    const an = analyserRef.current;
    if (an) {
      const data = new Uint8Array(an.frequencyBinCount);
      an.getByteTimeDomainData(data);
      let sum = 0;
      for (let i = 0; i < data.length; i++) {
        const v = (data[i] - 128) / 128;
        sum += v * v;
      }
      const rms = Math.sqrt(sum / data.length);
      const lvl = Math.min(1, rms * 3.5 + 0.05);
      setLevels((prev) => {
        const next = prev.slice(1);
        next.push(lvl);
        return next;
      });
    }
    setElapsed(accumRef.current + (Date.now() - startTsRef.current) / 1000);
    rafRef.current = requestAnimationFrame(tick);
  }, []);

  const start = useCallback(async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      streamRef.current = stream;
      const mime = MediaRecorder.isTypeSupported("audio/webm;codecs=opus")
        ? "audio/webm;codecs=opus"
        : MediaRecorder.isTypeSupported("audio/webm")
          ? "audio/webm"
          : "";
      const mr = mime ? new MediaRecorder(stream, { mimeType: mime }) : new MediaRecorder(stream);
      mediaRef.current = mr;
      chunksRef.current = [];
      mr.ondataavailable = (e) => {
        if (e.data.size > 0) chunksRef.current.push(e.data);
      };
      mr.onstop = () => {
        const type = mr.mimeType || "audio/webm";
        const blob = new Blob(chunksRef.current, { type });
        const durationSec = accumRef.current;
        cleanup();
        setPhase("idle");
        const r: AudioResult = {
          blob,
          mimeType: type,
          durationSec,
          url: URL.createObjectURL(blob),
        };
        resolveRef.current?.(r);
        resolveRef.current = null;
      };

      const Ctx = window.AudioContext || (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
      const ctx = new Ctx();
      audioCtxRef.current = ctx;
      const src = ctx.createMediaStreamSource(stream);
      const an = ctx.createAnalyser();
      an.fftSize = 1024;
      src.connect(an);
      analyserRef.current = an;

      accumRef.current = 0;
      startTsRef.current = Date.now();
      setElapsed(0);
      setLevels(Array(40).fill(0.05));
      mr.start(250);
      setPhase("recording");
      rafRef.current = requestAnimationFrame(tick);
    } catch (e) {
      console.error(e);
      setPhase("denied");
    }
  }, [cleanup, tick]);

  const pause = useCallback(() => {
    mediaRef.current?.pause();
    accumRef.current += (Date.now() - startTsRef.current) / 1000;
    if (rafRef.current) cancelAnimationFrame(rafRef.current);
    rafRef.current = null;
    setPhase("paused");
  }, []);

  const resume = useCallback(() => {
    mediaRef.current?.resume();
    startTsRef.current = Date.now();
    setPhase("recording");
    rafRef.current = requestAnimationFrame(tick);
  }, [tick]);

  const stop = useCallback((): Promise<AudioResult | null> => {
    return new Promise((resolve) => {
      const mr = mediaRef.current;
      if (!mr) {
        resolve(null);
        return;
      }
      if (rafRef.current) cancelAnimationFrame(rafRef.current);
      rafRef.current = null;
      if (mr.state === "recording") {
        accumRef.current += (Date.now() - startTsRef.current) / 1000;
      }
      resolveRef.current = resolve;
      try {
        mr.stop();
      } catch {
        resolve(null);
      }
    });
  }, []);

  const cancel = useCallback(() => {
    resolveRef.current = null;
    try {
      mediaRef.current?.stop();
    } catch {
      // ignore
    }
    cleanup();
    setPhase("idle");
    setElapsed(0);
  }, [cleanup]);

  return { phase, elapsed, levels, start, stop, pause, resume, cancel };
}
