import { useEffect, useState } from "react";
import { fetchStats } from "../api/client";
import type { WorkflowStats } from "../api/types";

const POLL_MS = 3000;

/** Poll workflow status counts for the nav bar. */
export function useStats() {
  const [stats, setStats] = useState<WorkflowStats | null>(null);

  useEffect(() => {
    const refresh = () => {
      fetchStats()
        .then(setStats)
        .catch((err) => console.warn("Failed to fetch stats:", err));
    };
    refresh();
    const id = setInterval(refresh, POLL_MS);
    return () => clearInterval(id);
  }, []);

  return stats;
}
