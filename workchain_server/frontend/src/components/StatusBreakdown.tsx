import { useNavigate } from "react-router-dom";
import {
  Clock, Loader, CheckCircle2, XCircle, AlertTriangle, Ban,
} from "lucide-react";
import type { WorkflowStats } from "../api/types";

const STATUS_CFG = [
  { key: "pending",       icon: <Clock size={18} />,         label: "Pending" },
  { key: "running",       icon: <Loader size={18} />,        label: "Running" },
  { key: "completed",     icon: <CheckCircle2 size={18} />,  label: "Completed" },
  { key: "failed",        icon: <XCircle size={18} />,       label: "Failed" },
  { key: "needs_review",  icon: <AlertTriangle size={18} />, label: "Review" },
  { key: "cancelled",     icon: <Ban size={18} />,           label: "Cancelled" },
] as const;

interface StatusBreakdownProps {
  counts: WorkflowStats | null;
}

export function StatusBreakdown({ counts }: StatusBreakdownProps) {
  const navigate = useNavigate();

  if (!counts) return null;

  return (
    <div className="status-breakdown">
      {STATUS_CFG.map(({ key, icon, label }) => {
        const count = counts[key as keyof WorkflowStats] ?? 0;
        return (
          <button
            key={key}
            className={`status-breakdown__card status-breakdown__card--${key}`}
            onClick={() => navigate(`/workflows?status=${key}`)}
            title={`View ${label.toLowerCase()} workflows`}
          >
            <div className="status-breakdown__icon">{icon}</div>
            <span className="status-breakdown__count">{count}</span>
            <span className="status-breakdown__label">{label}</span>
          </button>
        );
      })}
    </div>
  );
}
