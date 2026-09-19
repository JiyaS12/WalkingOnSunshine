"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import {
  Activity,
  AlertTriangle,
  Footprints,
  Gauge,
  Loader2,
  Save,
  Sparkles,
  Stethoscope,
  TrendingDown,
  User,
} from "lucide-react";
import WebcamFeed from "./WebcamFeed";
import TrendGraph, { TrendSession } from "./TrendGraph";
import TokenEfficiency from "./TokenEfficiency";
import {
  GaitMetrics,
  JointFrame,
  PatientRecord,
  SummaryResponse,
  ApiError,
  addPatientSession,
  fetchPatient,
  generateSummary,
} from "../lib/api";

function riskLevel(score: number): { label: string; classes: string } {
  if (score < 0.3)
    return { label: "LOW", classes: "bg-emerald-600/20 text-emerald-300 border-emerald-500/40" };
  if (score < 0.5)
    return { label: "MODERATE", classes: "bg-amber-600/20 text-amber-300 border-amber-500/40" };
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

export default function PatientScreening({ patientId }: { patientId: string }) {
  const [patient, setPatient] = useState<PatientRecord | null>(null);
  const [notFound, setNotFound] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const [metrics, setMetrics] = useState<GaitMetrics | null>(null);
  const [metricsFrames, setMetricsFrames] = useState<JointFrame[] | null>(null);
  const [metricsSource, setMetricsSource] = useState<
    "live" | "upload" | null
  >(null);
  const [sessions, setSessions] = useState<TrendSession[]>([]);
  const [summary, setSummary] = useState<SummaryResponse | null>(null);
  const [summaryLoading, setSummaryLoading] = useState(false);
  const [summaryError, setSummaryError] = useState<string | null>(null);
  const [summaryCount, setSummaryCount] = useState(0);
  const [saveNote, setSaveNote] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const patientGenRef = useRef(0);

  const loadPatient = useCallback(async () => {
    const gen = ++patientGenRef.current;
    try {
      const rec = await fetchPatient(patientId);
      if (gen !== patientGenRef.current) return;
      setPatient(rec);
      setNotFound(false);
      setLoadError(null);
      setSessions(
        rec.gait_sessions.map((s) => ({
          label: s.label,
          asymmetry_pct: s.metrics.asymmetry_pct,
          fall_risk_score: s.metrics.fall_risk_score,
          stride_length_m: s.metrics.stride_length_m,
        }))
      );
    } catch (err) {
      if (gen !== patientGenRef.current) return;
      if (err instanceof ApiError && err.status === 404) {
        setNotFound(true);
      } else {
        setLoadError(err instanceof Error ? err.message : String(err));
      }
    } finally {
      if (gen === patientGenRef.current) setLoading(false);
    }
  }, [patientId]);

  useEffect(() => {
    setLoading(true);
    void loadPatient();
  }, [loadPatient]);

  const handleMetrics = useCallback(
    (m: GaitMetrics, source: "live" | "upload", frames?: JointFrame[]) => {
      setMetrics(m);
      setMetricsSource(source);
      setMetricsFrames(frames ?? null);
      setSessions((prev) => {
        const label = source === "upload" ? "Upload" : "Live";
        const rest = prev.filter((s) => s.label !== label);
        if (!m.gait_detected) return rest;
        return [
          ...rest,
          {
            label,
            asymmetry_pct: m.asymmetry_pct,
            fall_risk_score: m.fall_risk_score,
            stride_length_m: m.stride_length_m,
          },
        ];
      });
      if (source === "upload" && m.gait_detected) {
        void saveSession("upload", m, frames ?? null);
      }
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    []
  );

  const saveSession = useCallback(
    async (
      source: "live" | "upload",
      m: GaitMetrics | null = metrics,
      frames: JointFrame[] | null = metricsFrames
    ) => {
      if (!m) return;
      setSaving(true);
      setSaveNote(null);
      try {
        await addPatientSession(patientId, {
          label: `${source === "live" ? "Live" : "Upload"} ${new Date().toLocaleTimeString("en-GB", { hour12: false })}`,
          source,
          metrics: m,
          frames,
        });
        setSaveNote("Saved — your doctor's portal is updated");
        await loadPatient();
      } catch (err) {
        setSaveNote(
          `Save failed: ${err instanceof Error ? err.message : String(err)}`
        );
      } finally {
        setSaving(false);
      }
    },
    [patientId, metrics, metricsFrames, loadPatient]
  );

  const handleSummary = useCallback(async () => {
    setSummaryLoading(true);
    setSummaryError(null);
    try {
      setSummary(await generateSummary(patientId));
      setSummaryCount((c) => c + 1);
    } catch (err) {
      setSummaryError(
        err instanceof Error ? err.message : "summary request failed"
      );
    } finally {
      setSummaryLoading(false);
    }
  }, [patientId]);

  if (notFound) {
    return (
      <main className="flex min-h-screen items-center justify-center bg-slate-950 p-6 text-slate-100">
        <div className="max-w-sm rounded-xl border border-slate-700 bg-slate-900 p-6 text-center">
          <AlertTriangle className="mx-auto mb-3 h-8 w-8 text-amber-300" />
          <p className="text-sm text-slate-200">
            We couldn&apos;t find this patient link.
          </p>
          <Link
            href="/"
            className="mt-4 inline-block rounded-md bg-emerald-600 px-4 py-2 text-sm font-medium text-white hover:bg-emerald-500"
          >
            Back to home
          </Link>
        </div>
      </main>
    );
  }

  if (loading) {
    return (
      <main className="flex min-h-screen items-center justify-center bg-slate-950 text-slate-400">
        <Loader2 className="mr-2 h-5 w-5 animate-spin" /> Loading your
        screening…
      </main>
    );
  }

  if (loadError) {
    return (
      <main className="flex min-h-screen items-center justify-center bg-slate-950 p-6 text-slate-100">
        <p className="text-sm text-rose-300">
          Failed to load patient: {loadError}
        </p>
      </main>
    );
  }

  const latestSurvey = patient?.surveys?.length
    ? patient.surveys[patient.surveys.length - 1]
    : null;
  const hasSessions = (patient?.gait_sessions?.length ?? 0) > 0;

  return (
    <main className="min-h-screen bg-slate-950 p-6 text-slate-100">
      <header className="mb-6 flex flex-wrap items-center justify-between gap-4">
        <div className="flex items-center gap-3">
          <Activity className="h-8 w-8 text-emerald-400" />
          <div>
            <h1 className="text-2xl font-bold">GaitGuard AI</h1>
            <p className="text-xs text-slate-400">
              Gait screening for {patient?.name ?? patientId} · {patientId}
            </p>
          </div>
        </div>
        <div className="flex items-center gap-3">
          <span className="flex items-center gap-2 rounded-full border border-slate-700 bg-slate-900 px-3 py-1.5 text-xs text-slate-200">
            <User className="h-3.5 w-3.5 text-slate-400" />
            {patient?.name ?? patientId}
          </span>
          <Link
            href="/doctor"
            className="flex items-center gap-2 rounded-full border border-slate-700 bg-slate-900 px-3 py-1.5 text-xs text-slate-200 hover:bg-slate-800"
          >
            <Stethoscope className="h-3.5 w-3.5 text-slate-400" />
            Doctor&apos;s Portal
          </Link>
        </div>
      </header>

      <div className="mb-4 rounded-xl border border-slate-700 bg-slate-900 p-4">
        <h2 className="mb-1 text-sm font-medium text-slate-200">
          From your phone survey
        </h2>
        {latestSurvey ? (
          <div className="flex flex-wrap items-center gap-3 text-xs text-slate-300">
            <span className="rounded-full border border-amber-500/40 bg-amber-600/20 px-2 py-0.5 text-amber-300">
              pain {latestSurvey.pain_scale}/10
            </span>
            <span>
              falls (6 mo): {latestSurvey.fall_history.falls_last_6_months}
              {latestSurvey.fall_history.injured ? " · injured" : ""}
            </span>
            <span>dizziness: {latestSurvey.dizziness ? "yes" : "no"}</span>
            <span className="flex flex-wrap gap-1">
              {latestSurvey.primary_complaints.map((c) => (
                <span
                  key={c}
                  className="rounded-full border border-slate-600 px-2 py-0.5 text-[10px] text-slate-300"
                >
                  {c}
                </span>
              ))}
            </span>
          </div>
        ) : (
          <p className="text-xs text-slate-400">No survey on file yet.</p>
        )}
      </div>

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

          {metrics && !metrics.gait_detected && (
            <p className="rounded-lg border border-amber-500/40 bg-amber-600/10 px-3 py-2 text-xs text-amber-300">
              No walking detected — walk across the frame (or upload a clip
              with walking) to record a session.
            </p>
          )}

          {metricsSource === "live" && metrics && (
            <div className="flex items-center gap-2 rounded-xl border border-slate-700 bg-slate-900 p-3">
              <Save className="h-4 w-4 shrink-0 text-slate-400" />
              <span className="text-xs text-slate-400">
                Save this walk to your record
              </span>
              <button
                onClick={() => void saveSession("live")}
                disabled={saving || !metrics.gait_detected}
                className="rounded-md bg-emerald-600 px-3 py-1 text-xs font-medium text-white hover:bg-emerald-500 disabled:opacity-50"
              >
                {saving ? "Saving…" : "Save this walk"}
              </button>
            </div>
          )}
          {saveNote && (
            <p
              className={`text-xs ${
                saveNote.startsWith("Save failed")
                  ? "text-rose-300"
                  : "text-emerald-300"
              }`}
            >
              {saveNote}
            </p>
          )}

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
              disabled={summaryLoading || !hasSessions}
              className="flex items-center gap-2 rounded-lg bg-emerald-600 px-4 py-2 text-sm font-medium text-white hover:bg-emerald-500 disabled:opacity-50"
            >
              <Sparkles className="h-4 w-4" />
              {summaryLoading ? "Generating…" : "Generate Patient Summary"}
            </button>
            {!hasSessions && (
              <p className="mt-1 text-[11px] text-slate-500">
                Complete a walk or upload first — no sessions on file yet.
              </p>
            )}
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
