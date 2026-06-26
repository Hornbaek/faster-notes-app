import { useEffect, useState } from "react";
import { listRecordings, listResults, subscribe, type Recording, type NoteResult, getPairing, type Pairing } from "./db";
import { subscribeSync, type SyncState } from "./sync";

export function useRecordings() {
  const [list, setList] = useState<Recording[]>([]);
  useEffect(() => {
    let mounted = true;
    const load = () => listRecordings().then((r) => mounted && setList(r));
    load();
    const unsub = subscribe(load);
    return () => {
      mounted = false;
      unsub();
    };
  }, []);
  return list;
}

export function useResults() {
  const [list, setList] = useState<NoteResult[]>([]);
  useEffect(() => {
    let mounted = true;
    const load = () => listResults().then((r) => mounted && setList(r));
    load();
    const unsub = subscribe(load);
    return () => {
      mounted = false;
      unsub();
    };
  }, []);
  return list;
}

export function usePairing() {
  const [p, setP] = useState<Pairing | undefined>(undefined);
  const [loaded, setLoaded] = useState(false);
  useEffect(() => {
    let mounted = true;
    const load = () =>
      getPairing().then((v) => {
        if (!mounted) return;
        setP(v);
        setLoaded(true);
      });
    load();
    const unsub = subscribe(load);
    return () => {
      mounted = false;
      unsub();
    };
  }, []);
  return { pairing: p, loaded };
}

const SSR_SYNC: SyncState = {
  online: true,
  serverReachable: true,
  lastChecked: null,
  syncing: false,
};

export function useSync() {
  // Always start from a neutral, deterministic state so SSR and the first
  // client render produce identical markup. Real values arrive after mount.
  const [s, setS] = useState<SyncState>(SSR_SYNC);
  const [mounted, setMounted] = useState(false);
  useEffect(() => {
    setMounted(true);
    const unsub = subscribeSync(setS);
    return () => {
      unsub();
    };
  }, []);
  return mounted ? s : SSR_SYNC;
}
