import { useCallback, useEffect, useState } from "react";
import { fetchTemplates } from "../api/client";
import type { WorkflowTemplate } from "../api/types";

/**
 * Fetch workflow templates on mount.  Exposes ``refresh`` to re-fetch
 * after mutations (create, update, delete).
 */
export function useTemplates() {
  const [templates, setTemplates] = useState<WorkflowTemplate[]>([]);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(() => {
    fetchTemplates()
      .then((data) => setTemplates(data))
      .catch((err) => console.warn("Failed to load templates:", err))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  return { templates, loading, refresh };
}
