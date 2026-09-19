"use client";

import { useCallback, useEffect, useState } from "react";
import {
  Activity,
  AlertTriangle,
  Footprints,
  Gauge,
  Sparkles,
  TrendingDown,
  User,
} from "lucide-react";
import WebcamFeed from "./components/WebcamFeed";
import TrendGraph, { TrendSession } from "./components/TrendGraph";
import TokenEfficiency from "./components/TokenEfficiency";
import {
  API_URL,
  Comparison,
  GaitMetrics,
  SummaryResponse,
  fetchSimulation,
  generateSummary,
} from "./lib/api";

function riskLevel(score: number): { label: string; classes: string } {
  if (score < 0.33)
    return { label: "LOW", classes: "bg-emerald-600/20 text-emerald-300 border-emerald-500/40" };
  if (score < 0.66)
    return { label: "MEDIUM", classes: "bg-amber-600/20 text-amber-300 border-amber-500/40" };
  return { label: "HIGH", classes: "bg-rose-600/20 text-rose-300 border-rose-500/40" };
}

interface CardProps {
  title: string;
  value: string;
  icon: React.ReactNode;
  badge?: { label: string; classes: string };
  sub?: string;
}

function MetricCard({ title, value, icon, badge, sub }: CardProps) {
  return (
    <div className="rounded-xl border border-slate-700 bg-slate-900 p-4">
      <div className="flex items-center justify-between">
        <span className="text-xs uppercase tracking-wide text-slate-400">
          {title}
        </span>
        <span className="text-slate-500">{icon}</span>
      </div>
      <div className="mt-2 flex items-end gap-2">
        <span className="text-2xl font-semibold text-slate-100">{value}</span>
        {badge && (
          <span
            className={`mb-0.5 rounded-full border px-2 py-0.5 text-[10px] font-semibold ${badge.classes}`}
          >
            {badge.label}
          </span>
        )}
      </div>
      {sub && <p className="mt-1 text-[10px] text-slate-500">{sub}</p>}
    </div>
  );
}

export default function Home() {
  const [metrics, setMetrics] = useState<GaitMetrics | null>(null);
  const [sessions, setSessions] = useState<TrendSession[]>([]);
  const [patientId, setPatientId] = useState<string>("");
  const [summary, setSummary] = useState<SummaryResponse | null>(null);
  const [summaryLoading, setSummaryLoading] = useState(false);
  const [summaryError, setSummaryError] = useState<string | null>(null);
  const [summaryCount, setSummaryCount] = useState(0);
  const [backendUp, setBackendUp] = useState<boolean | null>(null);

  useEffect(() => {
    fetch(`${API_URL}/api/health`)
      .then((r) => setBackendUp(r.ok))
      .catch(() => setBackendUp(false));
  }, []);

  useEffect(() => {
    fetchSimulation()
      .then((data) => {
        const c = data as Comparison;
        setPatientId(c.patient_id);
        setSessions([
          {
            label: "Day 1",
            asymmetry_pct: c.day_1.asymmetry_pct,
            fall_risk_score: c.day_1.fall_risk_score,
            stride_length_m: c.day_1.stride_length_m,
          },
          {
            label: "Day 14",
            asymmetry_pct: c.day_14.asymmetry_pct,
            fall_risk_score: c.day_14.fall_risk_score,
            stride_length_m: c.day_14.stride_length_m,
          },
        ]);
      })
      .catch(() => undefined);
  }, []);

  const handleMetrics = useCallback(
    (m: GaitMetrics, source: "live" | "simulated" | "upload") => {
      setMetrics(m);
      if (source === "simulated") return;
      const label = source === "upload" ? "Upload" : "Live";
      setSessions((prev) => {
        const point: TrendSession = {
          label,
          asymmetry_pct: m.asymmetry_pct,
          fall_risk_score: m.fall_risk_score,
          stride_length_m: m.stride_length_m,
        };
        const rest = prev.filter((s) => s.label !== label);
        return [...rest, point];
      });
    },
    []
  );

  const handleSummary = useCallback(async () => {
    setSummaryLoading(true);
    setSummaryError(null);
    try {
      setSummary(await generateSummary(patientId || undefined));
      setSummaryCount((c) => c + 1);
    } catch (err) {
      setSummaryError(
        err instanceof Error ? err.message : "summary request failed"
      );
    } finally {
      setSummaryLoading(false);
    }
  }, [patientId]);

  return (
    <main className="min-h-screen bg-slate-950 p-6 text-slate-100">
      <header className="mb-6 flex flex-wrap items-center justify-between gap-4">
        <div className="flex items-center gap-3">
          <Activity className="h-8 w-8 text-emerald-400" />
          <div>
            <h1 className="text-2xl font-bold">GaitGuard AI</h1>
            <p className="text-xs text-slate-400">
              Clinical gait monitoring & fall-risk analytics
            </p>
          </div>
        </div>
        <div className="flex items-center gap-3">
          <span className="flex items-center gap-2 rounded-full border border-slate-700 bg-slate-900 px-3 py-1.5 text-xs text-slate-200">
            <User className="h-3.5 w-3.5 text-slate-400" />
            RGN-0417 · 78 y/o · Geriatric Mobility Trial
          </span>
          <span
            className={`rounded-full border px-3 py-1.5 text-xs font-medium ${
              backendUp
                ? "border-emerald-500/40 bg-emerald-600/20 text-emerald-300"
                : "border-rose-500/40 bg-rose-600/20 text-rose-300"
            }`}
          >
            Backend: {backendUp === null ? "checking…" : backendUp ? "connected" : "offline"}
          </span>
        </div>
      </header>

      <div className="grid gap-6 lg:grid-cols-2">
        <WebcamFeed onMetrics={handleMetrics} />

        <div className="flex flex-col gap-4">
          <div className="grid grid-cols-2 gap-4">
            <MetricCard
              title="Stride Length"
              value={metrics ? `${metrics.stride_length_m.toFixed(2)} m` : "—"}
              icon={<Footprints className="h-4 w-4" />}
              sub={
                metrics?.stride_ratio
                  ? `×${metrics.stride_ratio.toFixed(2)} leg`
                  : undefined
              }
            />
            <MetricCard
              title="Asymmetry"
              value={metrics ? `${metrics.asymmetry_pct.toFixed(1)}%` : "—"}
              icon={<Gauge className="h-4 w-4" />}
            />
            <MetricCard
              title="Velocity Degradation"
              value={
                metrics ? `${metrics.velocity_degradation_pct.toFixed(1)}%` : "—"
              }
              icon={<TrendingDown className="h-4 w-4" />}
            />
            <MetricCard
              title="Fall Risk"
              value={metrics ? metrics.fall_risk_score.toFixed(3) : "—"}
              icon={<AlertTriangle className="h-4 w-4" />}
              badge={metrics ? riskLevel(metrics.fall_risk_score) : undefined}
            />
          </div>
          <MetricCard
            title="Cadence"
            value={
              metrics ? `${metrics.cadence_steps_per_min.toFixed(0)} steps/min` : "—"
            }
            icon={<Activity className="h-4 w-4" />}
          />

          <TrendGraph sessions={sessions} />

          <TokenEfficiency refresh={summaryCount} lastResult={summary} />

          <div className="rounded-xl border border-slate-700 bg-slate-900 p-4">
            <h2 className="text-sm font-medium text-slate-200">
              AI Patient Summary
            </h2>
            <p className="mb-3 text-xs text-slate-400">
              Plain-language clinical summary for patients & care teams
            </p>
            <button
              onClick={handleSummary}
              disabled={summaryLoading}
              className="flex items-center gap-2 rounded-lg bg-emerald-600 px-4 py-2 text-sm font-medium text-white hover:bg-emerald-500 disabled:opacity-50"
            >
              <Sparkles className="h-4 w-4" />
              {summaryLoading ? "Generating…" : "Generate Patient Summary"}
            </button>
            {summaryError && (
              <p className="mt-3 text-xs text-rose-300">{summaryError}</p>
            )}
            {summary && (
              <div className="mt-3">
                <p className="text-sm leading-relaxed text-slate-200">
                  {summary.summary}
                </p>
                <div className="mt-2 flex gap-2 text-[10px]">
                  <span className="rounded-full border border-slate-600 px-2 py-0.5 uppercase text-slate-300">
                    {summary.source}
                  </span>
                  {summary.cached && (
                    <span className="rounded-full border border-sky-500/40 bg-sky-600/20 px-2 py-0.5 uppercase text-sky-300">
                      served from cache
                    </span>
                  )}
                  {summary.estimated_tokens_saved > 0 && (
                    <span className="rounded-full border border-slate-600 px-2 py-0.5 text-slate-400">
                      ~{summary.estimated_tokens_saved} tokens saved
                    </span>
                  )}
                </div>
              </div>
            )}
          </div>
        </div>
      </div>
    </main>
  );
}
