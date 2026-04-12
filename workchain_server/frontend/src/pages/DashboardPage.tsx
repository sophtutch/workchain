import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useAnalytics } from "../hooks/useAnalytics";
import { useTemplates } from "../hooks/useTemplates";
import { useHandlers } from "../hooks/useHandlers";
import { deleteTemplate } from "../api/client";
import { StatsRow } from "../components/StatsRow";
import { StatusBreakdown } from "../components/StatusBreakdown";
import { ActivityFeed } from "../components/ActivityFeed";
import { TemplateCatalog } from "../components/TemplateCatalog";
import { TemplateLaunchModal } from "../components/TemplateLaunchModal";
import type { WorkflowTemplate } from "../api/types";

export function DashboardPage() {
  const { analytics, activity } = useAnalytics();
  const { templates, loading: templatesLoading, refresh: refreshTemplates } = useTemplates();
  const { handlers } = useHandlers();
  const navigate = useNavigate();
  const [selectedTemplate, setSelectedTemplate] = useState<WorkflowTemplate | null>(null);
  const [toast, setToast] = useState<string | null>(null);

  return (
    <div className="dashboard">
      {/* Key metrics */}
      <StatsRow analytics={analytics} />

      {/* Status breakdown */}
      <StatusBreakdown counts={analytics?.status_counts ?? null} />

      {/* Recent activity */}
      <ActivityFeed items={activity} />

      {/* Template catalog */}
      <TemplateCatalog
        templates={templates}
        loading={templatesLoading}
        onSelect={setSelectedTemplate}
        onDelete={(t) => {
          deleteTemplate(t.id)
            .then(() => refreshTemplates())
            .catch((err) => setToast(`Delete failed: ${err.message}`));
        }}
      />

      {/* Launch modal */}
      {selectedTemplate && (
        <TemplateLaunchModal
          template={selectedTemplate}
          handlers={handlers}
          onClose={() => setSelectedTemplate(null)}
          onLaunched={(_name, id) => {
            setSelectedTemplate(null);
            navigate(`/workflows/${encodeURIComponent(id)}`);
          }}
        />
      )}

      {/* Toast */}
      <div className={`toast ${toast ? "toast--visible" : ""}`}>
        {toast}
      </div>
    </div>
  );
}
