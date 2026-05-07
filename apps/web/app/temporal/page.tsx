import { AppShell } from "@/components/layout/AppShell";
import { TemporalDashboard } from "@/components/temporal/TemporalDashboard";

export default function TemporalPage() {
  return (
    <AppShell
      title="调度管理"
      subtitle="工作流与运行控制"
      center={<TemporalDashboard />}
      showSessionControls={false}
    />
  );
}
