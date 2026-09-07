import { useEffect, useState } from "react";

import { usePageVisibility } from "@/hooks/usePageVisibility";
import { fetchSubagents } from "@/lib/api";
import type { SubagentsPayload } from "@/lib/types";

const FLEET_REFRESH_MS = 2000;

/** Poll /api/subagents every 2s while the page is visible. */
export function useSubagentFleet(token: string) {
  const pageVisible = usePageVisibility();
  const [payload, setPayload] = useState<SubagentsPayload | null>(null);

  useEffect(() => {
    if (!pageVisible) return undefined;
    let cancelled = false;

    const refresh = async () => {
      try {
        const next = await fetchSubagents(token);
        if (!cancelled) setPayload(next);
      } catch {
        // Keep the last snapshot on transient errors.
      }
    };

    void refresh();
    const interval = window.setInterval(() => void refresh(), FLEET_REFRESH_MS);
    const onFocus = () => void refresh();
    window.addEventListener("focus", onFocus);
    return () => {
      cancelled = true;
      window.clearInterval(interval);
      window.removeEventListener("focus", onFocus);
    };
  }, [pageVisible, token]);

  return payload;
}
