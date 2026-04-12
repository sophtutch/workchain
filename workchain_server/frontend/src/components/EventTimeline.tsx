import type { AuditEvent } from "../api/types";

const EVENT_COLORS: Record<string, string> = {
  step_completed: "var(--c-completed)",
  step_failed: "var(--c-failed)",
  step_running: "var(--c-running)",
  step_submitted: "var(--c-running)",
  step_blocked: "var(--c-needs_review)",
  step_claimed: "var(--neon)",
  step_timeout: "var(--c-failed)",
  step_retried: "var(--c-needs_review)",
  workflow_completed: "var(--c-completed)",
  workflow_failed: "var(--c-failed)",
  workflow_cancelled: "var(--c-cancelled)",
  workflow_created: "var(--neon)",
  poll_checked: "var(--c-needs_review)",
  poll_timeout: "var(--c-failed)",
  poll_max_exceeded: "var(--c-failed)",
  recovery_started: "var(--c-needs_review)",
  recovery_verified: "var(--c-completed)",
  recovery_needs_review: "var(--c-failed)",
  lock_released: "var(--text-muted)",
  heartbeat: "var(--text-muted)",
};

function formatTimeMs(iso: string): string {
  try {
    const d = new Date(iso);
    const h = String(d.getHours()).padStart(2, "0");
    const m = String(d.getMinutes()).padStart(2, "0");
    const s = String(d.getSeconds()).padStart(2, "0");
    const ms = String(d.getMilliseconds()).padStart(3, "0");
    return `${h}:${m}:${s}.${ms}`;
  } catch {
    return iso;
  }
}

function formatEventType(type: string): string {
  return type.replace(/_/g, " ");
}

/** Compute the end timestamp for each event by finding the next event
 *  for the same step (or the next workflow-level event). */
function computeEndTimes(events: AuditEvent[]): Map<number, string | null> {
  const endTimes = new Map<number, string | null>();

  for (let i = 0; i < events.length; i++) {
    const e = events[i];
    let endTime: string | null = null;

    // Find the next event for the same step (or workflow-level successor)
    for (let j = i + 1; j < events.length; j++) {
      const next = events[j];
      if (
        e.step_name &&
        next.step_name === e.step_name
      ) {
        endTime = next.timestamp;
        break;
      }
      if (!e.step_name && !next.step_name) {
        endTime = next.timestamp;
        break;
      }
    }

    endTimes.set(i, endTime);
  }

  return endTimes;
}

interface EventTimelineProps {
  events: AuditEvent[];
  stepFilter?: string;
  compact?: boolean;
}

export function EventTimeline({
  events,
  stepFilter,
  compact = false,
}: EventTimelineProps) {
  const filtered = stepFilter
    ? events.filter((e) => e.step_name === stepFilter)
    : events;

  if (filtered.length === 0) {
    return (
      <div className="event-timeline">
        <p className="event-timeline__empty">No events recorded.</p>
      </div>
    );
  }

  const endTimes = computeEndTimes(filtered);

  return (
    <div className={`event-timeline ${compact ? "event-timeline--compact" : ""}`}>
      <table className="event-timeline__table">
        <thead>
          <tr>
            <th className="event-timeline__th">Start</th>
            <th className="event-timeline__th">End</th>
            <th className="event-timeline__th">Event</th>
            {!stepFilter && <th className="event-timeline__th">Step</th>}
            <th className="event-timeline__th">Transition</th>
            <th className="event-timeline__th">Detail</th>
          </tr>
        </thead>
        <tbody>
          {filtered.map((e, i) => {
            const color = EVENT_COLORS[e.event_type] || "var(--text-muted)";
            const isError = e.error != null;
            const endTime = endTimes.get(i);

            // Build detail fragments
            const details: string[] = [];
            if (e.attempt != null && e.attempt > 1)
              details.push(`attempt ${e.attempt}`);
            if (e.poll_progress != null)
              details.push(`${Math.round(e.poll_progress * 100)}%`);
            if (e.poll_message) details.push(e.poll_message);
            if (e.recovery_action) details.push(e.recovery_action);

            return (
              <tr
                key={`${e.sequence}-${i}`}
                className={`event-timeline__row ${isError ? "event-timeline__row--error" : ""}`}
              >
                <td className="event-timeline__time">
                  {formatTimeMs(e.timestamp)}
                </td>
                <td className="event-timeline__time event-timeline__time--end">
                  {endTime ? formatTimeMs(endTime) : "\u2014"}
                </td>
                <td>
                  <span
                    className="event-timeline__badge"
                    style={{ color, borderColor: color }}
                  >
                    {formatEventType(e.event_type)}
                  </span>
                </td>
                {!stepFilter && (
                  <td className="event-timeline__step">
                    {e.step_name || ""}
                  </td>
                )}
                <td className="event-timeline__transition">
                  {e.step_status && (
                    <>
                      {e.step_status_before && (
                        <>
                          <span className="event-timeline__status-from">
                            {e.step_status_before}
                          </span>
                          {" \u2192 "}
                        </>
                      )}
                      <span className="event-timeline__status-to">
                        {e.step_status}
                      </span>
                    </>
                  )}
                </td>
                <td className="event-timeline__detail">
                  {details.length > 0 && (
                    <span className="event-timeline__meta">
                      {details.join(" \u00b7 ")}
                    </span>
                  )}
                  {isError && (
                    <span
                      className="event-timeline__error-hint"
                      title={e.error || ""}
                    >
                      {e.error?.split("\n").pop()?.slice(0, 60)}
                    </span>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
