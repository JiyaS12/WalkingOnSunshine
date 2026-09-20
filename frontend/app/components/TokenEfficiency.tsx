"use client";

import { useEffect, useState } from "react";
import { Zap } from "lucide-react";
import {
  fetchSummaryCacheStats,
  SummaryCacheStats,
  SummaryResponse,
} from "../lib/api";

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
      classes: "border-sky-500/40 bg-sky-600/20 text-sky-300",
    };
  if (last.source === "openai")
    return {
      label: "Fresh LLM call",
      classes: "border-emerald-500/40 bg-emerald-600/20 text-emerald-300",
    };
  return {
    label: "Template fallback",
    classes: "border-amber-500/40 bg-amber-600/20 text-amber-300",
  };
}

export default function TokenEfficiency({ refresh, lastResult }: Props) {
  const [stats, setStats] = useState<SummaryCacheStats | null>(null);

  useEffect(() => {
    fetchSummaryCacheStats()
      .then(setStats)
      .catch(() => undefined);
  }, [refresh]);

  const badge = lastRequestBadge(lastResult);

  return (
    <div className="rounded-xl border border-slate-700 bg-slate-900 p-4">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Zap className="h-4 w-4 text-amber-300" />
          <span className="text-sm font-medium text-slate-200">
            Token Efficiency
          </span>
        </div>
        {badge && (
          <span
            className={`rounded-full border px-2 py-0.5 text-[10px] font-semibold uppercase ${badge.classes}`}
          >
            {badge.label}
          </span>
        )}
      </div>
      <p className="mt-1 text-xs text-slate-400">
        Heuristic cache: near-identical telemetry reuses summaries instead of
        re-calling the LLM
      </p>
      <div className="mt-3 grid grid-cols-2 gap-3 text-sm">
        <div>
          <p className="text-xs uppercase tracking-wide text-slate-500">
            Cached summaries
          </p>
          <p className="text-lg font-semibold text-slate-100">
            {stats ? stats.entries : "—"}
          </p>
        </div>
        <div>
          <p className="text-xs uppercase tracking-wide text-slate-500">
            Estimated tokens saved
          </p>
          <p className="text-lg font-semibold text-slate-100">
            {stats ? stats.estimated_tokens_saved : "—"}
          </p>
        </div>
      </div>
    </div>
  );
}
