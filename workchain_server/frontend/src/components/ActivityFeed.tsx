import { Link } from "react-router-dom";
import { Eye } from "lucide-react";
import type { ActivityItem } from "../api/types";

function timeAgo(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime();
  const seconds = Math.floor(diff / 1000);
  if (seconds < 60) return "just now";
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  return `${days}d ago`;
}

interface ActivityFeedProps {
  items: ActivityItem[];
}

export function ActivityFeed({ items }: ActivityFeedProps) {
  if (items.length === 0) {
    return (
      <section className="activity-feed">
        <h3 className="activity-feed__heading">Recent Activity</h3>
        <p className="activity-feed__empty">No recent workflow activity.</p>
      </section>
    );
  }

  return (
    <section className="activity-feed">
      <h3 className="activity-feed__heading">Recent Activity</h3>
      <div className="activity-feed__list">
        {items.map((item) => (
          <Link
            key={item.id}
            to={`/workflows/${encodeURIComponent(item.id)}`}
            className="activity-feed__item"
          >
            <span className={`activity-feed__dot activity-feed__dot--${item.status}`} />
            <span className="activity-feed__name">{item.name}</span>
            <span className={`activity-feed__status activity-feed__status--${item.status}`}>
              {item.status}
            </span>
            <span className="activity-feed__time">{timeAgo(item.updated_at)}</span>
            <Eye size={12} className="activity-feed__icon" />
          </Link>
        ))}
      </div>
    </section>
  );
}
