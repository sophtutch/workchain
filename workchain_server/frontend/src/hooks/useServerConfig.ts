import { useEffect, useState } from "react";
import type { ServerConfig } from "../api/types";
import { fetchConfig } from "../api/client";

export function useServerConfig() {
  const [config, setConfig] = useState<ServerConfig | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetchConfig()
      .then((c) => {
        if (!cancelled) {
          setConfig(c);
          document.title = c.server_title;
        }
      })
      .catch((err) => {
        console.warn("Failed to load server config:", err);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return config;
}
