"use client";

import { useEffect, useState } from "react";
import { Zap } from "lucide-react";
import { API_URL, SummaryResponse } from "../lib/api";

interface CacheStats {
  entries: number;
  cache_hits: number;
  estimated_tokens_saved: number;
}

interface Props {
  refresh: number;
  lastResult: SummaryResponse | null;
}

function lastRequestBadge(last: SummaryResponse | null): {
  label: string;
  classes: string;
} | null {
  if (!last) return null;
  if (last.cached)
    return {
      label: "Cache hit",
      classes: "border-0 bg-pastel-green text-foreground",
    };
  if (last.source === "openai")
    return {
      label: "Fresh LLM call",
      classes: "border-0 bg-pastel-lavender text-foreground",
    };
  return {
    label: "Template fallback",
    classes: "border-0 bg-pastel-peach text-foreground",
  };
}

export default function TokenEfficiency({ refresh, lastResult }: Props) {
  const [stats, setStats] = useState<CacheStats | null>(null);

  useEffect(() => {
    fetch(`${API_URL}/api/summary-cache-stats`)
      .then((r) => (r.ok ? r.json() : Promise.reject(r.status)))
      .then(setStats)
      .catch(() => undefined);
  }, [refresh]);

  const badge = lastRequestBadge(lastResult);

  return (
    <div className="rounded-[1.75rem] border-0 bg-card p-5 shadow-pillow">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Zap className="h-4 w-4 text-pastel-lavender" />
          <span className="text-sm font-medium text-foreground">
            Token Efficiency
          </span>
        </div>
        {badge && (
          <span
            className={`rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase ${badge.classes}`}
          >
            {badge.label}
          </span>
        )}
      </div>
      <p className="mt-1 text-xs text-muted-foreground">
        Heuristic cache: near-identical telemetry reuses summaries instead of
        re-calling the LLM
      </p>
      <div className="mt-3 grid grid-cols-2 gap-3 text-sm">
        <div>
          <p className="text-xs uppercase tracking-wide text-muted-foreground">
            Cached summaries
          </p>
          <p className="text-lg font-semibold text-foreground">
            {stats ? stats.entries : "—"}
          </p>
        </div>
        <div>
          <p className="text-xs uppercase tracking-wide text-muted-foreground">
            Estimated tokens saved
          </p>
          <p className="text-lg font-semibold text-foreground">
            {stats ? stats.estimated_tokens_saved : "—"}
          </p>
        </div>
      </div>
    </div>
  );
}
