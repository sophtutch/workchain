import { useEffect, useRef, useState } from "react";
import { fetchStats } from "../api/client";
import type { WorkflowStats } from "../api/types";

const POLL_MS = 3000;
const MAX_ERRORS = 3;

/** Poll workflow status counts for the nav bar. */
export function useStats() {
  const [stats, setStats] = useState<WorkflowStats | null>(null);
  const errorCountRef = useRef(0);
  const intervalRef = useRef<ReturnType<typeof setInterval>>();

  useEffect(() => {
    const refresh = () => {
      fetchStats()
        .then((data) => {
          setStats(data);
          errorCountRef.current = 0;
        })
        .catch((err) => {
          console.warn("Failed to fetch stats:", err);
          errorCountRef.current++;
          if (errorCountRef.current >= MAX_ERRORS && intervalRef.current) {
            clearInterval(intervalRef.current);
            intervalRef.current = undefined;
          }
        });
    };
    errorCountRef.current = 0;
    refresh();
    intervalRef.current = setInterval(refresh, POLL_MS);
    return () => {
      if (intervalRef.current) clearInterval(intervalRef.current);
    };
  }, []);

  return stats;
}
