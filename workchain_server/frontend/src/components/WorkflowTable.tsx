import { useNavigate } from "react-router-dom";
import {
  Clock, Loader, CheckCircle2, XCircle, AlertTriangle, Ban,
} from "lucide-react";
import type { WorkflowSummary } from "../api/types";

const STATUS_ICONS: Record<string, React.ReactNode> = {
  pending: <Clock size={14} />,
  running: <Loader size={14} />,
  completed: <CheckCircle2 size={14} />,
  failed: <XCircle size={14} />,
  needs_review: <AlertTriangle size={14} />,
  cancelled: <Ban size={14} />,
};

const TERMINAL = new Set(["completed", "failed", "needs_review", "cancelled"]);

function formatTime(iso: string): string {
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return "\u2014";
  }
}

interface WorkflowTableProps {
  workflows: WorkflowSummary[];
  loading: boolean;
}

export function WorkflowTable({ workflows, loading }: WorkflowTableProps) {
  const navigate = useNavigate();

  return (
    <div className="table-wrap">
      <table className="wf-table">
        <thead>
          <tr>
            <th>Name</th>
            <th>Status</th>
            <th>Progress</th>
            <th>Started</th>
            <th>Ended</th>
          </tr>
        </thead>
        <tbody>
          {workflows.length === 0 ? (
            <tr>
              <td colSpan={5} className="wf-table__empty">
                {loading ? "Loading..." : "No workflows match your filters."}
              </td>
            </tr>
          ) : (
            workflows.map((wf) => (
              <tr
                key={wf.id}
                className="wf-table__row"
                onClick={() => navigate(`/workflows/${encodeURIComponent(wf.id)}`)}
              >
                <td className="wf-table__name">{wf.name}</td>
                <td>
                  <span className={`wf-badge wf-badge--${wf.status}`}>
                    {STATUS_ICONS[wf.status]} {wf.status}
                  </span>
                </td>
                <td>
                  <div className="wf-progress-bar">
                    <div
                      className={`wf-progress-bar__fill wf-progress-bar__fill--${wf.status}`}
                      style={{ width: `${wf.total_steps ? (wf.completed_steps / wf.total_steps) * 100 : 0}%` }}
                    />
                  </div>
                  <span className="wf-table__mono wf-table__progress-text">{wf.progress}</span>
                </td>
                <td className="wf-table__mono">
                  {wf.created_at ? formatTime(wf.created_at) : "\u2014"}
                </td>
                <td className="wf-table__mono">
                  {TERMINAL.has(wf.status) && wf.updated_at
                    ? formatTime(wf.updated_at)
                    : "\u2014"}
                </td>
              </tr>
            ))
          )}
        </tbody>
      </table>
    </div>
  );
}
