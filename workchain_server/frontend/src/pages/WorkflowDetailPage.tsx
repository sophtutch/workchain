import { useCallback, useEffect, useRef, useState } from "react";
import { useParams } from "react-router-dom";
import { Loader } from "lucide-react";
import { fetchWorkflowDetail, retryStep } from "../api/client";
import type { WorkflowDetailResponse } from "../api/types";
import { DetailHeader } from "../components/DetailHeader";
import { DependencyGraph } from "../components/DependencyGraph";
import { StepCard } from "../components/StepCard";
import { EventTimeline } from "../components/EventTimeline";

const TERMINAL = new Set(["completed", "failed", "needs_review", "cancelled"]);
const POLL_ACTIVE_MS = 1000;  // fast poll while workflow is running
const POLL_IDLE_MS = 5000;    // slow poll while pending/waiting
const MAX_ERRORS = 3;

export function WorkflowDetailPage() {
  const { id } = useParams<{ id: string }>();
  const [data, setData] = useState<WorkflowDetailResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [toast, setToast] = useState<string | null>(null);
  const intervalRef = useRef<ReturnType<typeof setInterval>>();
  const toastRef = useRef<ReturnType<typeof setTimeout>>();
  const lastStatusRef = useRef<string>("");
  const errorCountRef = useRef(0);

  const refresh = useCallback(async () => {
    if (!id) return;
    try {
      const detail = await fetchWorkflowDetail(id);
      setData(detail);
      setError(null);
      errorCountRef.current = 0;

      const status = detail.workflow.status;
      const prevStatus = lastStatusRef.current;
      lastStatusRef.current = status;

      // Adjust poll speed based on workflow state
      if (TERMINAL.has(status)) {
        if (intervalRef.current) {
          clearInterval(intervalRef.current);
          intervalRef.current = undefined;
        }
      } else if (status !== prevStatus) {
        if (intervalRef.current) clearInterval(intervalRef.current);
        const ms = status === "running" ? POLL_ACTIVE_MS : POLL_IDLE_MS;
        intervalRef.current = setInterval(refresh, ms);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load workflow");
      errorCountRef.current++;
      if (errorCountRef.current >= MAX_ERRORS && intervalRef.current) {
        clearInterval(intervalRef.current);
        intervalRef.current = undefined;
      }
    } finally {
      setLoading(false);
    }
  }, [id]);

  useEffect(() => {
    errorCountRef.current = 0;
    refresh();
    intervalRef.current = setInterval(refresh, POLL_ACTIVE_MS);
    return () => {
      if (intervalRef.current) clearInterval(intervalRef.current);
    };
  }, [refresh]);

  // Auto-clear toast
  useEffect(() => {
    if (toast) {
      clearTimeout(toastRef.current);
      toastRef.current = setTimeout(() => setToast(null), 3000);
    }
    return () => clearTimeout(toastRef.current);
  }, [toast]);

  const handleRetry = useCallback(
    async (stepName: string) => {
      if (!id) return;
      try {
        await retryStep(id, stepName);
        setToast(`Retrying '${stepName}'...`);
        refresh();
      } catch (err) {
        setToast(err instanceof Error ? err.message : "Retry failed");
      }
    },
    [id, refresh],
  );

  const scrollToStep = useCallback((name: string) => {
    document.getElementById(`step-${name}`)?.scrollIntoView({
      behavior: "smooth",
      block: "center",
    });
  }, []);

  if (loading && !data) {
    return (
      <div className="detail-page page-grid">
        <div className="detail-page__loading">
          <Loader size={24} className="detail-page__spinner" />
          <span>Loading workflow...</span>
        </div>
      </div>
    );
  }

  if (error && !data) {
    return (
      <div className="detail-page page-grid">
        <div className="detail-page__error">
          <p>{error}</p>
          <button className="btn btn--ghost" onClick={refresh}>
            Retry
          </button>
        </div>
      </div>
    );
  }

  if (!data) return null;

  // Order steps by tier for display
  const tierOrder = new Map<string, number>();
  data.graph.tiers.forEach((tier, i) => {
    tier.forEach((name) => tierOrder.set(name, i));
  });
  const orderedSteps = [...data.steps].sort(
    (a, b) => (tierOrder.get(a.name) ?? 0) - (tierOrder.get(b.name) ?? 0),
  );

  return (
    <div className="detail-page page-grid">
      <DetailHeader workflow={data.workflow} />

      <div className="detail-page__graph">
        <DependencyGraph
          steps={data.steps}
          tiers={data.graph.tiers}
          dependencies={data.graph.dependencies}
          onStepClick={scrollToStep}
        />
      </div>

      <div className="detail-page__content">
        <section className="detail-page__section">
          <h2 className="detail-page__section-title">Steps</h2>
          {orderedSteps.map((step) => (
            <StepCard
              key={step.name}
              id={`step-${step.name}`}
              step={step}
              events={data.events}
              onRetry={handleRetry}
            />
          ))}
        </section>

        <section className="detail-page__section">
          <h2 className="detail-page__section-title">Event Log</h2>
          <EventTimeline events={data.events} />
        </section>
      </div>

      {/* Toast */}
      <div className={`toast ${toast ? "toast--visible" : ""}`}>
        {toast}
      </div>
    </div>
  );
}
