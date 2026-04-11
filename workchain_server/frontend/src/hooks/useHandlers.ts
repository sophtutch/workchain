import { useEffect, useState } from "react";
import { fetchHandlers } from "../api/client";
import type { HandlerDescriptor } from "../api/types";

interface HandlersState {
  handlers: HandlerDescriptor[];
  loading: boolean;
  error: string | null;
}

/**
 * Fetch the handler inventory from /api/v1/handlers once on mount.
 * The backend already filters out completeness checks.
 */
export function useHandlers(): HandlersState {
  const [state, setState] = useState<HandlersState>({
    handlers: [],
    loading: true,
    error: null,
  });

  useEffect(() => {
    let cancelled = false;
    fetchHandlers()
      .then((handlers) => {
        if (!cancelled) setState({ handlers, loading: false, error: null });
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setState({
            handlers: [],
            loading: false,
            error: err instanceof Error ? err.message : "Failed to load handlers",
          });
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return state;
}
