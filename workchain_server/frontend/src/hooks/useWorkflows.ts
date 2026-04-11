import { useCallback, useEffect, useRef, useState } from "react";
import type { WorkflowStats, WorkflowSummary } from "../api/types";
import {
  cancelWorkflow as apiCancel,
  fetchStats,
  fetchWorkflows,
} from "../api/client";

const POLL_MS = 3000;

export function useWorkflows() {
  const [workflows, setWorkflows] = useState<WorkflowSummary[]>([]);
  const [stats, setStats] = useState<WorkflowStats | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const timerRef = useRef<ReturnType<typeof setTimeout>>();

  const refresh = useCallback(async () => {
    try {
      const [wf, st] = await Promise.all([fetchWorkflows(), fetchStats()]);
      setWorkflows(wf);
      setStats(st);
    } catch (err) {
      console.warn("Failed to refresh workflows:", err);
    }
  }, []);

  useEffect(() => {
    refresh();
    const id = setInterval(refresh, POLL_MS);
    return () => clearInterval(id);
  }, [refresh]);

  const cancel = useCallback(
    async (workflowId: string) => {
      try {
        await apiCancel(workflowId);
        setToast("Workflow cancelled");
        refresh();
      } catch (err) {
        setToast(err instanceof Error ? err.message : "Cancel failed");
      }
    },
    [refresh],
  );

  // Auto-clear toast
  useEffect(() => {
    if (toast) {
      clearTimeout(timerRef.current);
      timerRef.current = setTimeout(() => setToast(null), 3000);
    }
    return () => clearTimeout(timerRef.current);
  }, [toast]);

  return { workflows, stats, cancel, toast, setToast };
}
