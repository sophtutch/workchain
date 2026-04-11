import { useState } from "react";
import {
  Clock, Loader, CheckCircle2, XCircle, AlertTriangle, Ban,
  FileText, XOctagon, List,
} from "lucide-react";
import { useWorkflows } from "../hooks/useWorkflows";
import { useTemplates } from "../hooks/useTemplates";
import { useHandlers } from "../hooks/useHandlers";
import { deleteTemplate } from "../api/client";
import { TemplateCatalog } from "../components/TemplateCatalog";
import { TemplateLaunchModal } from "../components/TemplateLaunchModal";
import type { WorkflowTemplate } from "../api/types";

const STATUS_ICONS: Record<string, React.ReactNode> = {
  pending: <Clock size={18} />,
  running: <Loader size={18} />,
  completed: <CheckCircle2 size={18} />,
  failed: <XCircle size={18} />,
  needs_review: <AlertTriangle size={18} />,
  cancelled: <Ban size={18} />,
};

const TERMINAL = new Set(["completed", "failed", "needs_review", "cancelled"]);

export function DashboardPage() {
  const { workflows, cancel, toast, setToast } = useWorkflows();
  const { templates, loading: templatesLoading, refresh: refreshTemplates } = useTemplates();
  const { handlers } = useHandlers();
  const [selectedTemplate, setSelectedTemplate] = useState<WorkflowTemplate | null>(null);

  return (
    <div className="dashboard">
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

      {/* Workflows table */}
      <h2 className="dashboard__heading">
        <List size={16} /> Workflows
      </h2>
      <div className="table-wrap">
        <table className="wf-table">
          <thead>
            <tr>
              <th>Name</th>
              <th>Status</th>
              <th>Progress</th>
              <th>Created</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            {workflows.length === 0 ? (
              <tr>
                <td colSpan={5} className="wf-table__empty">
                  No workflows yet. Select a template above to create one.
                </td>
              </tr>
            ) : (
              workflows.map((wf) => (
                <tr key={wf.id}>
                  <td className="wf-table__name">{wf.name}</td>
                  <td>
                    <span className={`wf-badge wf-badge--${wf.status}`}>
                      {STATUS_ICONS[wf.status]} {wf.status}
                    </span>
                  </td>
                  <td className="wf-table__mono">{wf.progress}</td>
                  <td className="wf-table__mono">
                    {wf.created_at
                      ? new Date(wf.created_at).toLocaleString()
                      : "\u2014"}
                  </td>
                  <td className="wf-table__actions">
                    {wf.status !== "pending" ? (
                      <a
                        href={`/api/v1/workflows/${encodeURIComponent(wf.id)}/report`}
                        target="_blank"
                        rel="noreferrer"
                        className="wf-table__link"
                      >
                        <FileText size={14} /> Report
                      </a>
                    ) : (
                      <span className="wf-table__muted">pending</span>
                    )}
                    {!TERMINAL.has(wf.status) && (
                      <button
                        className="wf-table__cancel"
                        onClick={() => cancel(wf.id)}
                      >
                        <XOctagon size={12} /> Cancel
                      </button>
                    )}
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      {/* Launch modal */}
      {selectedTemplate && (
        <TemplateLaunchModal
          template={selectedTemplate}
          handlers={handlers}
          onClose={() => setSelectedTemplate(null)}
          onLaunched={(name, id) => {
            setSelectedTemplate(null);
            setToast(`Launched '${name}' (${id.slice(0, 8)}…)`);
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
