import { useNavigate } from "react-router-dom";
import {
  Users, Database, GitBranch, Film, Brain, AlertTriangle,
  Server, ShoppingCart, Workflow, Pencil, Trash2, Play,
} from "lucide-react";
import type { WorkflowTemplate } from "../api/types";

interface TemplateCatalogProps {
  templates: WorkflowTemplate[];
  loading: boolean;
  onSelect: (template: WorkflowTemplate) => void;
  onDelete?: (template: WorkflowTemplate) => void;
}

const ICON_MAP: Record<string, React.ReactNode> = {
  "Customer Onboarding": <Users size={22} />,
  "Data Pipeline ETL": <Database size={22} />,
  "CI/CD Pipeline": <GitBranch size={22} />,
  "Media Processing": <Film size={22} />,
  "ML Training Pipeline": <Brain size={22} />,
  "Incident Response": <AlertTriangle size={22} />,
  "Infrastructure Provisioning": <Server size={22} />,
  "Order Fulfillment": <ShoppingCart size={22} />,
};

function templateIcon(name: string): React.ReactNode {
  return ICON_MAP[name] ?? <Workflow size={22} />;
}

/**
 * Responsive card grid of workflow templates on the dashboard.
 * Each card has Launch (click body), Edit, and Delete actions.
 */
export function TemplateCatalog({
  templates,
  loading,
  onSelect,
  onDelete,
}: TemplateCatalogProps) {
  const navigate = useNavigate();

  if (loading) {
    return (
      <section className="tpl-catalog">
        <h2 className="tpl-catalog__heading">Workflows</h2>
        <div className="tpl-catalog__loading">Loading templates…</div>
      </section>
    );
  }

  if (templates.length === 0) return null;

  return (
    <section className="tpl-catalog">
      <h2 className="tpl-catalog__heading">Workflows</h2>
      <div className="tpl-catalog__grid">
        {templates.map((t) => (
          <div key={t.id} className="tpl-card">
            <div
              className="tpl-card__main"
              onClick={() => onSelect(t)}
              role="button"
              tabIndex={0}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault();
                  onSelect(t);
                }
              }}
            >
              <div className="tpl-card__icon">
                {templateIcon(t.name)}
              </div>
              <div className="tpl-card__body">
                <div className="tpl-card__name">{t.name}</div>
                {t.description && (
                  <div className="tpl-card__desc">{t.description}</div>
                )}
                <div className="tpl-card__meta">
                  {t.steps.length} steps
                </div>
              </div>
            </div>
            <div className="tpl-card__actions">
              <button
                type="button"
                className="tpl-card__run"
                onClick={(e) => {
                  e.stopPropagation();
                  onSelect(t);
                }}
                title="Run workflow from template"
              >
                <Play size={12} />
              </button>
              <button
                type="button"
                className="tpl-card__edit"
                onClick={(e) => {
                  e.stopPropagation();
                  navigate(`/designer?template=${encodeURIComponent(t.id)}`);
                }}
                title="Edit template in designer"
              >
                <Pencil size={12} />
              </button>
              <button
                type="button"
                className="tpl-card__delete"
                onClick={(e) => {
                  e.stopPropagation();
                  if (window.confirm(`Delete template "${t.name}"?`)) {
                    onDelete?.(t);
                  }
                }}
                title="Delete template"
              >
                <Trash2 size={12} />
              </button>
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}
