// Cross-platform static (SPA) build for self-hosting the PWA from FastAPI.
// Sets SPA_BUILD=1 (read by vite.config.ts) and runs `vite build`.
// Output: faster-notes/dist/client — served by app.py on the phone port.
//
// VITE_SELF_HOSTED=1 is baked into this build so the app registers the service
// worker (sw.js) only for the self-hosted PWA — NOT for the Lovable-hosted SSR
// build, where /sw.js doesn't exist.
import { spawnSync } from "node:child_process";

const result = spawnSync("vite", ["build"], {
  stdio: "inherit",
  shell: true,
  env: { ...process.env, SPA_BUILD: "1", VITE_SELF_HOSTED: "1" },
});

process.exit(result.status ?? 1);
