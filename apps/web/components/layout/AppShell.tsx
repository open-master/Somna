"use client";
import { Sidebar } from "./Sidebar";
import { TopBar } from "./TopBar";
import { useUiStore } from "@/lib/store/ui";
import { cn } from "@/lib/utils/cn";

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
  const liveExpanded = useUiStore((s) => s.liveComputerExpanded);
  const closeLiveComputer = useUiStore((s) => s.closeLiveComputer);
  const showRight = Boolean(right && liveExpanded);

  return (
    <div className="flex h-screen w-screen overflow-hidden bg-[radial-gradient(circle_at_top_left,hsl(var(--primary)/0.10),transparent_32%),linear-gradient(180deg,hsl(var(--background)),hsl(var(--muted)/0.35))]">
      <Sidebar />
      <div className="flex flex-col flex-1 min-w-0">
        <TopBar title={title} subtitle={subtitle} showSessionControls={showSessionControls} />
        <div className="relative flex flex-1 min-h-0">
          <main className="flex flex-1 min-w-0 flex-col">{center}</main>
          {showRight ? (
            <>
              <button
                type="button"
                aria-label="关闭 Agent 观察区"
                className="fixed inset-0 z-40 bg-background/60 backdrop-blur-[2px] xl:hidden"
                onClick={() => closeLiveComputer()}
              />
              <aside
                className={cn(
                  "flex max-h-full min-h-0 w-[440px] shrink-0 flex-col border-l bg-background/75",
                  "max-xl:fixed max-xl:inset-y-0 max-xl:right-0 max-xl:z-50 max-xl:shadow-2xl",
                  "xl:relative xl:flex",
                )}
              >
                {right}
              </aside>
            </>
          ) : null}
        </div>
      </div>
    </div>
  );
}
