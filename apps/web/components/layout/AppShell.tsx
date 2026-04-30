"use client";
import { Sidebar } from "./Sidebar";
import { TopBar } from "./TopBar";

export function AppShell({
  title,
  subtitle,
  center,
  right,
  showSessionControls = true,
}: {
  title: string;
  subtitle?: string;
  center: React.ReactNode;
  right?: React.ReactNode;
  showSessionControls?: boolean;
}) {
  return (
    <div className="flex h-screen w-screen overflow-hidden bg-[radial-gradient(circle_at_top_left,hsl(var(--primary)/0.10),transparent_32%),linear-gradient(180deg,hsl(var(--background)),hsl(var(--muted)/0.35))]">
      <Sidebar />
      <div className="flex flex-col flex-1 min-w-0">
        <TopBar title={title} subtitle={subtitle} showSessionControls={showSessionControls} />
        <div className="flex flex-1 min-h-0">
          <main className="flex flex-1 min-w-0 flex-col">{center}</main>
          {right ? (
            <aside className="hidden w-[440px] shrink-0 border-l bg-background/75 xl:flex xl:flex-col">
              {right}
            </aside>
          ) : null}
        </div>
      </div>
    </div>
  );
}
