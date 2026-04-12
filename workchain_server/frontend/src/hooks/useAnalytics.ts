import { useEffect, useRef, useState } from "react";
import { fetchAnalytics, fetchActivity } from "../api/client";
import type { WorkflowAnalytics, ActivityItem } from "../api/types";

const POLL_MS = 5000;
const MAX_ERRORS = 3;

/** Poll workflow analytics, recent activity, and recent failures for the dashboard. */
export function useAnalytics() {
  const [analytics, setAnalytics] = useState<WorkflowAnalytics | null>(null);
  const [activity, setActivity] = useState<ActivityItem[]>([]);
  const [failures, setFailures] = useState<ActivityItem[]>([]);
  const errorCountRef = useRef(0);
  const intervalRef = useRef<ReturnType<typeof setInterval>>();

  useEffect(() => {
    const refresh = async () => {
      try {
        const [a, act, fail] = await Promise.all([
          fetchAnalytics(),
          fetchActivity(8),
          fetchActivity(8, "failed"),
        ]);
        setAnalytics(a);
        setActivity(act);
        setFailures(fail);
        errorCountRef.current = 0;
      } catch (err) {
        console.warn("Failed to fetch analytics:", err);
        errorCountRef.current++;
        if (errorCountRef.current >= MAX_ERRORS && intervalRef.current) {
          clearInterval(intervalRef.current);
          intervalRef.current = undefined;
        }
      }
    };
    errorCountRef.current = 0;
    refresh();
    intervalRef.current = setInterval(refresh, POLL_MS);
    return () => {
      if (intervalRef.current) clearInterval(intervalRef.current);
    };
  }, []);

  return { analytics, activity, failures };
}
