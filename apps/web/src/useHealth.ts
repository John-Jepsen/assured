import { useEffect, useState } from "react";
import { api } from "./api";
import type { HealthResponse } from "./types";

export interface HealthState {
  health: HealthResponse | null;
  ok: boolean;
  loading: boolean;
  error: string | null;
  refresh: () => void;
}

/**
 * Polls /health so the header connection dot and admin providers panel stay
 * current. Poll interval defaults to 10s.
 */
export function useHealth(intervalMs = 10000): HealthState {
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [ok, setOk] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [tick, setTick] = useState(0);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const h = await api.health();
        if (cancelled) return;
        setHealth(h);
        setOk(h.status === "ok" || h.status === "healthy");
        setError(null);
      } catch (e) {
        if (cancelled) return;
        setOk(false);
        setError(e instanceof Error ? e.message : "health check failed");
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    load();
    const id = window.setInterval(load, intervalMs);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, [intervalMs, tick]);

  return { health, ok, loading, error, refresh: () => setTick((t) => t + 1) };
}
