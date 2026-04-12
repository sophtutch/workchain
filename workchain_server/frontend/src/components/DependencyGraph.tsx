import {
  Clock, Loader, CheckCircle2, XCircle, AlertTriangle, Zap,
} from "lucide-react";
import type { StepDetail } from "../api/types";

interface DependencyGraphProps {
  steps: StepDetail[];
  tiers: string[][];
  onStepClick?: (stepName: string) => void;
}

const STATUS_CLASSES: Record<string, string> = {
  pending: "dep-step--pending",
  submitted: "dep-step--running",
  running: "dep-step--running",
  blocked: "dep-step--blocked",
  completed: "dep-step--completed",
  failed: "dep-step--failed",
};

const STATUS_ICONS: Record<string, React.ReactNode> = {
  pending: <Clock size={12} />,
  submitted: <Loader size={12} className="dep-step__spin" />,
  running: <Loader size={12} className="dep-step__spin" />,
  blocked: <AlertTriangle size={12} />,
  completed: <CheckCircle2 size={12} />,
  failed: <XCircle size={12} />,
};

export function DependencyGraph({ steps, tiers, onStepClick }: DependencyGraphProps) {
  const stepMap = new Map(steps.map((s) => [s.name, s]));

  return (
    <div className="dep-graph-inline">
      <div className="dep-graph-inline__flow">
        {tiers.map((tier, ti) => (
          <div key={ti} className="dep-graph-inline__tier-group">
            {ti > 0 && <div className="dep-graph-inline__connector" />}
            <div
              className={`dep-graph-inline__tier ${tier.length > 1 ? "dep-graph-inline__tier--parallel" : ""}`}
            >
              {tier.map((name) => {
                const step = stepMap.get(name);
                if (!step) return null;
                const cls = STATUS_CLASSES[step.status] || "";
                const modeCls = step.is_async
                  ? "dep-step--async"
                  : "dep-step--sync";
                const hasError = step.result?.error != null;
                const errorSnippet = hasError
                  ? String(step.result!.error).split("\n").pop()?.slice(0, 40)
                  : null;

                return (
                  <button
                    key={name}
                    className={`dep-step ${cls} ${modeCls}`}
                    onClick={() => onStepClick?.(name)}
                    title={`${name} (${step.status})`}
                  >
                    <div className="dep-step__top">
                      <span className="dep-step__icon">
                        {STATUS_ICONS[step.status]}
                      </span>
                      <span className="dep-step__name">{name}</span>
                    </div>

                    <div className="dep-step__info">
                      <span className="dep-step__status">{step.status}</span>

                      {step.is_async && (
                        <span className="dep-step__mode-tag">
                          <Zap size={9} /> async
                        </span>
                      )}

                      {step.attempt > 1 && (
                        <span className="dep-step__attempt">
                          attempt {step.attempt}/{step.retry_policy.max_attempts}
                        </span>
                      )}
                    </div>

                    {/* Poll progress bar for async steps */}
                    {step.is_async && step.poll_count > 0 && (
                      <div className="dep-step__progress">
                        <div
                          className="dep-step__progress-fill"
                          style={{
                            width: `${Math.round((step.last_poll_progress ?? 0) * 100)}%`,
                          }}
                        />
                      </div>
                    )}

                    {/* Poll message */}
                    {step.last_poll_message && (
                      <span className="dep-step__poll-msg">
                        {step.last_poll_message}
                      </span>
                    )}

                    {/* Error snippet for failed steps */}
                    {errorSnippet && (
                      <span className="dep-step__error">
                        {errorSnippet}
                      </span>
                    )}
                  </button>
                );
              })}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
