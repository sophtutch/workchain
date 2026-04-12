import { useCallback, useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import type { WorkflowSummary } from "../api/types";
import { fetchWorkflows, cancelWorkflow as apiCancel } from "../api/client";

const PAGE_SIZE = 25;
const DEBOUNCE_MS = 300;
const POLL_MS = 3000;
const MAX_ERRORS = 3;

export function useWorkflowSearch() {
  const [searchParams, setSearchParams] = useSearchParams();
  const [workflows, setWorkflows] = useState<WorkflowSummary[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [toast, setToast] = useState<string | null>(null);
  const debounceRef = useRef<ReturnType<typeof setTimeout>>();
  const toastRef = useRef<ReturnType<typeof setTimeout>>();
  const errorCountRef = useRef(0);
  const intervalRef = useRef<ReturnType<typeof setInterval>>();

  // Read filter state from URL
  const status = searchParams.get("status") || "";
  const search = searchParams.get("search") || "";
  const page = Math.max(1, parseInt(searchParams.get("page") || "1", 10));

  const updateParams = useCallback(
    (updates: Record<string, string>) => {
      setSearchParams((prev) => {
        const next = new URLSearchParams(prev);
        for (const [k, v] of Object.entries(updates)) {
          if (v) next.set(k, v);
          else next.delete(k);
        }
        return next;
      });
    },
    [setSearchParams],
  );

  const setStatus = useCallback(
    (s: string) => updateParams({ status: s, page: "" }),
    [updateParams],
  );

  const setSearch = useCallback(
    (s: string) => {
      clearTimeout(debounceRef.current);
      debounceRef.current = setTimeout(
        () => updateParams({ search: s, page: "" }),
        DEBOUNCE_MS,
      );
    },
    [updateParams],
  );

  const setPage = useCallback(
    (p: number) => updateParams({ page: p > 1 ? String(p) : "" }),
    [updateParams],
  );

  const refresh = useCallback(async () => {
    try {
      const resp = await fetchWorkflows({
        status: status || undefined,
        search: search || undefined,
        limit: PAGE_SIZE,
        skip: (page - 1) * PAGE_SIZE,
      });
      setWorkflows(resp.items);
      setTotal(resp.total);
      errorCountRef.current = 0;
    } catch (err) {
      console.warn("Failed to fetch workflows:", err);
      errorCountRef.current++;
      if (errorCountRef.current >= MAX_ERRORS && intervalRef.current) {
        clearInterval(intervalRef.current);
        intervalRef.current = undefined;
      }
    } finally {
      setLoading(false);
    }
  }, [status, search, page]);

  useEffect(() => {
    setLoading(true);
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
      clearTimeout(toastRef.current);
      toastRef.current = setTimeout(() => setToast(null), 3000);
    }
    return () => clearTimeout(toastRef.current);
  }, [toast]);

  return {
    workflows,
    total,
    loading,
    page,
    pageSize: PAGE_SIZE,
    status,
    search,
    setStatus,
    setSearch,
    setPage,
    cancel,
    toast,
  };
}
