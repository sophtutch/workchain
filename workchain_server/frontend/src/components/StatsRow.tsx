import { Activity, CheckCircle2, Clock, TrendingUp } from "lucide-react";
import type { WorkflowAnalytics } from "../api/types";

function formatDuration(seconds: number | null): string {
  if (seconds == null) return "\u2014";
  if (seconds < 60) return `${Math.round(seconds)}s`;
  if (seconds < 3600) return `${Math.round(seconds / 60)}m`;
  return `${(seconds / 3600).toFixed(1)}h`;
}

function formatRate(rate: number | null): string {
  if (rate == null) return "\u2014";
  return `${Math.round(rate * 100)}%`;
}

interface StatsRowProps {
  analytics: WorkflowAnalytics | null;
}

export function StatsRow({ analytics }: StatsRowProps) {
  const cards = [
    {
      label: "Total Workflows",
      value: analytics?.total_workflows ?? "\u2014",
      icon: <Activity size={18} />,
      className: "stats-row__card--total",
    },
    {
      label: "Success Rate",
      value: formatRate(analytics?.success_rate ?? null),
      icon: <CheckCircle2 size={18} />,
      className: "stats-row__card--success",
    },
    {
      label: "24h Throughput",
      value: analytics?.throughput_24h ?? "\u2014",
      icon: <TrendingUp size={18} />,
      className: "stats-row__card--throughput",
    },
    {
      label: "Avg Duration",
      value: formatDuration(analytics?.avg_duration_seconds ?? null),
      icon: <Clock size={18} />,
      className: "stats-row__card--duration",
    },
  ];

  return (
    <div className="stats-row">
      {cards.map((c) => (
        <div key={c.label} className={`stats-row__card ${c.className}`}>
          <div className="stats-row__icon">{c.icon}</div>
          <div className="stats-row__body">
            <span className="stats-row__value">{c.value}</span>
            <span className="stats-row__label">{c.label}</span>
          </div>
        </div>
      ))}
    </div>
  );
}
