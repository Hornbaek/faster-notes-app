import { Link, useRouterState } from "@tanstack/react-router";
import { Mic, ListChecks, NotebookText, Settings as SettingsIcon } from "lucide-react";
import { motion } from "framer-motion";
import { useSync } from "@/lib/hooks";

const tabs = [
  { to: "/", label: "Record", icon: Mic },
  { to: "/queue", label: "Queue", icon: ListChecks },
  { to: "/notes", label: "Notes", icon: NotebookText },
  { to: "/settings", label: "Settings", icon: SettingsIcon },
] as const;

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = useRouterState({ select: (s) => s.location.pathname });
  const sync = useSync();

  return (
    <div className="relative mx-auto flex min-h-dvh max-w-md flex-col bg-background">
      {/* Top status strip */}
      <div className="safe-top px-4 pt-3">
        <div className="flex items-center justify-between text-xs">
          <div className="flex items-center gap-2">
            <span
              className={`inline-block h-2 w-2 rounded-full ${
                !sync.online
                  ? "bg-muted-foreground"
                  : sync.serverReachable
                    ? "bg-success"
                    : "bg-warning"
              }`}
            />
            <span className="text-muted-foreground">
              {!sync.online
                ? "Offline"
                : sync.serverReachable
                  ? "Server reachable"
                  : "Server unreachable"}
            </span>
          </div>
          {sync.syncing && (
            <span className="text-muted-foreground">Syncing…</span>
          )}
        </div>
      </div>

      <main className="flex flex-1 flex-col">{children}</main>

      {/* Bottom tab bar */}
      <nav className="safe-bottom sticky bottom-0 z-40 mt-2 border-t border-border bg-background/85 backdrop-blur-xl">
        <ul className="grid grid-cols-4 px-2 pt-2">
          {tabs.map((t) => {
            const active = pathname === t.to || (t.to !== "/" && pathname.startsWith(t.to));
            const Icon = t.icon;
            return (
              <li key={t.to} className="flex justify-center">
                <Link
                  to={t.to}
                  className="relative flex w-full flex-col items-center gap-1 rounded-xl px-2 py-2 text-[11px] transition-colors"
                >
                  {active && (
                    <motion.span
                      layoutId="tab-active"
                      className="absolute inset-0 rounded-xl bg-accent/60"
                      transition={{ type: "spring", stiffness: 400, damping: 32 }}
                    />
                  )}
                  <Icon
                    className={`relative h-5 w-5 ${
                      active ? "text-primary" : "text-muted-foreground"
                    }`}
                  />
                  <span
                    className={`relative ${
                      active ? "text-foreground" : "text-muted-foreground"
                    }`}
                  >
                    {t.label}
                  </span>
                </Link>
              </li>
            );
          })}
        </ul>
      </nav>
    </div>
  );
}
