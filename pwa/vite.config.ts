// @lovable.dev/vite-tanstack-config already includes the following — do NOT add them manually
// or the app will break with duplicate plugins:
//   - tanstackStart, viteReact, tailwindcss, tsConfigPaths, nitro (build-only using cloudflare as a default target),
//     componentTagger (dev-only), VITE_* env injection, @ path alias, React/TanStack dedupe,
//     error logger plugins, and sandbox detection (port/host/strictPort).
// You can pass additional config via defineConfig({ vite: { ... }, etc... }) if needed.
import { defineConfig } from "@lovable.dev/vite-tanstack-config";

// SPA mode is opt-in via SPA_BUILD=1 (set by `npm run build:static`). It is OFF
// for the default `npm run build` that Lovable runs, because SPA prerendering
// crawls a local server which fails inside Lovable's sandboxed build env.
// The static `build:static` output is only used to self-host the PWA from
// FastAPI (faster-notes/dist/client). Lovable hosting uses the normal SSR build.
const SPA = process.env.SPA_BUILD === "1";

export default defineConfig({
  tanstackStart: {
    // Client-only static build (no SSR runtime) so FastAPI can serve plain files.
    spa: { enabled: SPA },
    // Redirect TanStack Start's bundled server entry to src/server.ts (our SSR error wrapper).
    // nitro/vite builds from this
    server: { entry: "server" },
  },
});
