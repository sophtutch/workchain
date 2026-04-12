import { useState } from "react";
import {
  ChevronDown,
  Clock,
  Loader,
  CheckCircle2,
  XCircle,
  AlertTriangle,
  RotateCcw,
} from "lucide-react";
import type { StepDetail, AuditEvent } from "../api/types";
import { JsonPanel } from "./JsonPanel";
import { EventTimeline } from "./EventTimeline";

const STATUS_ICONS: Record<string, React.ReactNode> = {
  pending: <Clock size={14} />,
  submitted: <Loader size={14} />,
  running: <Loader size={14} />,
  blocked: <AlertTriangle size={14} />,
  completed: <CheckCircle2 size={14} />,
  failed: <XCircle size={14} />,
};

const RETRYABLE = new Set(["failed"]);

interface StepCardProps {
  step: StepDetail;
  events: AuditEvent[];
  defaultExpanded?: boolean;
  id?: string;
  onRetry?: (stepName: string) => void;
}

export function StepCard({ step, events, defaultExpanded = false, id, onRetry }: StepCardProps) {
  const [expanded, setExpanded] = useState(
    defaultExpanded || step.status === "failed",
  );

  const stepEvents = events.filter((e) => e.step_name === step.name);
  const hasError = step.result?.error != null;
  const errorText = step.result?.error as string | undefined;

  return (
    <div
      id={id}
      className={`step-card step-card--${step.status} ${expanded ? "step-card--expanded" : ""}`}
    >
      <button
        className="step-card__header"
        onClick={() => setExpanded(!expanded)}
      >
        <span className={`step-card__indicator step-card__indicator--${step.status}`} />
        <span className="step-card__name">{step.name}</span>
        <span className={`wf-badge wf-badge--${step.status}`}>
          {STATUS_ICONS[step.status]} {step.status}
        </span>
        <span className="step-card__handler">{step.handler.split(".").pop()}</span>
        {step.is_async && (
          <span className="step-card__mode step-card__mode--async">async</span>
        )}
        {step.attempt > 1 && (
          <span className="step-card__meta">attempt {step.attempt}/{step.retry_policy.max_attempts}</span>
        )}
        {step.is_async && step.last_poll_progress != null && (
          <div className="step-card__poll-bar">
            <div
              className="step-card__poll-fill"
              style={{ width: `${Math.round(step.last_poll_progress * 100)}%` }}
            />
          </div>
        )}
        {RETRYABLE.has(step.status) && onRetry && (
          <button
            className="step-card__retry"
            onClick={(e) => {
              e.stopPropagation();
              onRetry(step.name);
            }}
            title={`Retry ${step.name}`}
          >
            <RotateCcw size={12} /> Retry
          </button>
        )}
        <ChevronDown
          size={14}
          className={`step-card__chevron ${expanded ? "step-card__chevron--open" : ""}`}
        />
      </button>

      {expanded && (
        <div className="step-card__body">
          {/* Error display — prominent */}
          {hasError && (
            <div className="step-card__error">
              <div className="step-card__error-label">Error</div>
              <pre className="step-card__error-text">{errorText}</pre>
            </div>
          )}

          {/* Config */}
          <JsonPanel data={step.config} label="Config" />

          {/* Result */}
          {step.result && !hasError && (
            <JsonPanel data={step.result} label="Result" />
          )}

          {/* Poll info */}
          {step.is_async && step.poll_count > 0 && (
            <div className="step-card__poll-info">
              <span className="step-card__poll-label">Polls</span>
              <span className="step-card__poll-count">{step.poll_count}</span>
              {step.last_poll_message && (
                <span className="step-card__poll-msg">
                  {step.last_poll_message}
                </span>
              )}
            </div>
          )}

          {/* Retry policy */}
          {step.retry_policy.max_attempts > 1 && (
            <div className="step-card__retry-info">
              <span className="step-card__retry-label">Retry policy</span>
              <span>
                {step.retry_policy.max_attempts} attempts, {step.retry_policy.wait_seconds}s base,{" "}
                {step.retry_policy.wait_multiplier}x backoff
              </span>
            </div>
          )}

          {/* Step event timeline */}
          {stepEvents.length > 0 && (
            <div className="step-card__events">
              <div className="step-card__events-label">Event History</div>
              <EventTimeline events={stepEvents} compact />
            </div>
          )}

          {/* Dependencies */}
          {step.depends_on.length > 0 && (
            <div className="step-card__deps">
              <span className="step-card__deps-label">Depends on</span>
              {step.depends_on.map((d) => (
                <code key={d} className="step-card__dep-name">{d}</code>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
