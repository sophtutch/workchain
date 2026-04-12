import { useCallback, useEffect, useRef, useState } from "react";
import type { WorkflowSummary } from "../api/types";
import {
  cancelWorkflow as apiCancel,
  fetchWorkflows,
} from "../api/client";

const POLL_MS = 3000;
const MAX_ERRORS = 3;

export function useWorkflows() {
  const [workflows, setWorkflows] = useState<WorkflowSummary[]>([]);
  const [toast, setToast] = useState<string | null>(null);
  const timerRef = useRef<ReturnType<typeof setTimeout>>();
  const errorCountRef = useRef(0);
  const intervalRef = useRef<ReturnType<typeof setInterval>>();

  const refresh = useCallback(async () => {
    try {
      const resp = await fetchWorkflows({ limit: 50 });
      setWorkflows(resp.items);
      errorCountRef.current = 0;
    } catch (err) {
      console.warn("Failed to refresh workflows:", err);
      errorCountRef.current++;
      if (errorCountRef.current >= MAX_ERRORS && intervalRef.current) {
        clearInterval(intervalRef.current);
        intervalRef.current = undefined;
      }
    }
  }, []);

  useEffect(() => {
    errorCountRef.current = 0;
    refresh();
    intervalRef.current = setInterval(refresh, POLL_MS);
    return () => {
      if (intervalRef.current) clearInterval(intervalRef.current);
    };
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

  return { workflows, cancel, toast, setToast };
}
