import { AppShell } from "@/components/layout/AppShell";
import { TemporalDashboard } from "@/components/temporal/TemporalDashboard";

export default function TemporalPage() {
  return (
    <AppShell
      title="Temporal"
      subtitle="Workflow & run control plane"
      center={<TemporalDashboard />}
      showSessionControls={false}
    />
  );
}
