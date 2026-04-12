import { Link } from "react-router-dom";
import {
  ArrowLeft,
  Clock,
  Loader,
  CheckCircle2,
  XCircle,
  AlertTriangle,
  Ban,
} from "lucide-react";

const STATUS_ICONS: Record<string, React.ReactNode> = {
  pending: <Clock size={16} />,
  running: <Loader size={16} />,
  completed: <CheckCircle2 size={16} />,
  failed: <XCircle size={16} />,
  needs_review: <AlertTriangle size={16} />,
  cancelled: <Ban size={16} />,
};

function formatDuration(created: string, updated: string): string {
  const ms = new Date(updated).getTime() - new Date(created).getTime();
  if (ms < 1000) return `${ms}ms`;
  const s = Math.floor(ms / 1000);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ${s % 60}s`;
  const h = Math.floor(m / 60);
  return `${h}h ${m % 60}m`;
}

const TERMINAL = new Set(["completed", "failed", "needs_review", "cancelled"]);

interface DetailHeaderProps {
  workflow: {
    id: string;
    name: string;
    status: string;
    created_at: string;
    updated_at: string;
  };
}

export function DetailHeader({ workflow }: DetailHeaderProps) {
  const isTerminal = TERMINAL.has(workflow.status);

  return (
    <div className="detail-header">
      <Link to="/workflows" className="detail-header__back">
        <ArrowLeft size={14} /> Workflows
      </Link>
      <div className="detail-header__divider" />
      <h1 className="detail-header__name">{workflow.name}</h1>
      <span className={`wf-badge wf-badge--${workflow.status}`}>
        {STATUS_ICONS[workflow.status]} {workflow.status}
      </span>
      <div className="detail-header__timing">
        <span className="detail-header__time">
          {new Date(workflow.created_at).toLocaleString()}
        </span>
        {isTerminal && (
          <span className="detail-header__duration">
            {formatDuration(workflow.created_at, workflow.updated_at)}
          </span>
        )}
      </div>
    </div>
  );
}
