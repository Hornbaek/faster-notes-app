import { createFileRoute } from "@tanstack/react-router";
import { AppShell } from "@/components/AppShell";
import { Compose } from "@/components/compose/Compose";

export const Route = createFileRoute("/")({
  head: () => ({
    meta: [
      { title: "Compose — Faster Notes" },
      { name: "description", content: "Type, record audio, or snap a photo. Works offline." },
    ],
  }),
  component: Index,
});

function Index() {
  return (
    <AppShell>
      <Compose />
    </AppShell>
  );
}

