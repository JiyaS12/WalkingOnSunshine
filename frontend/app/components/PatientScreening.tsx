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
import {
  Card,
  CardAction,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import TrendGraph, { TrendSession } from "./TrendGraph";
import TokenEfficiency from "./TokenEfficiency";
import {
  GaitMetrics,
  JointFrame,
  PatientRecord,
  SummaryResponse,
  ApiError,
  addPatientSession,
  ensureDemoPatient,
  fetchPatient,
  generateSummary,
} from "../lib/api";

// trend labels for a reading that has not been saved to the record yet;
// saved sessions carry a timestamped label, so these never collide
const UNSAVED_LABEL = { live: "Live", upload: "Upload" } as const;

function riskLevel(score: number): { label: string; classes: string } {
  if (score < 0.3)
    return {
      label: "LOW",
      classes: "border-0 rounded-full bg-pastel-green text-foreground",
    };
  if (score < 0.5)
    return {
      label: "MODERATE",
      classes: "border-0 rounded-full bg-pastel-peach/70 text-foreground",
    };
  return {
    label: "HIGH",
    classes: "border-0 rounded-full bg-pastel-peach text-foreground",
  };
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
    <Card className="[--card-spacing:1.5rem]">
      <CardHeader className="pb-0">
        <CardTitle className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
          {title}
        </CardTitle>
        <CardAction>
          <span className="text-muted-foreground">{icon}</span>
        </CardAction>
      </CardHeader>
      <CardContent className="pt-2">
        <div className="flex items-end gap-2">
          <span className="text-3xl font-semibold text-foreground">
            {value}
          </span>
          {badge && (
            <Badge
              variant="outline"
              className={`mb-1 text-[10px] font-semibold ${badge.classes}`}
            >
              {badge.label}
            </Badge>
          )}
        </div>
        {sub && (
          <p className="mt-1 text-xs text-muted-foreground">{sub}</p>
        )}
      </CardContent>
    </Card>
  );
}

export default function PatientScreening({ patientId }: { patientId: string }) {
  const [patient, setPatient] = useState<PatientRecord | null>(null);
  const [notFound, setNotFound] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [authRequired, setAuthRequired] = useState(false);
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
  const [demoBusy, setDemoBusy] = useState(false);
  const [demoError, setDemoError] = useState<string | null>(null);
  const patientGenRef = useRef(0);

  const loadPatient = useCallback(async () => {
    const gen = ++patientGenRef.current;
    try {
      const rec = await fetchPatient(patientId);
      if (gen !== patientGenRef.current) return;
      setPatient(rec);
      setNotFound(false);
      setLoadError(null);
      setAuthRequired(false);
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
      } else if (err instanceof ApiError && err.status === 401) {
        setAuthRequired(true);
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

  const handleMetrics = useCallback(
    (m: GaitMetrics, source: "live" | "upload", frames?: JointFrame[]) => {
      setMetrics(m);
      setMetricsSource(source);
      setMetricsFrames(frames ?? null);
      setSessions((prev) => {
        const label = UNSAVED_LABEL[source];
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
    [saveSession]
  );

  // switching input mode discards the previous mode's reading, so the cards
  // never show numbers that belong to an input the user has navigated away from
  const handleInputReset = useCallback(() => {
    setMetrics(null);
    setMetricsSource(null);
    setMetricsFrames(null);
    setSaveNote(null);
    setSessions((prev) =>
      prev.filter(
        (s) => s.label !== UNSAVED_LABEL.live && s.label !== UNSAVED_LABEL.upload
      )
    );
  }, []);

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
      <main className="flex min-h-screen items-center justify-center p-6 text-foreground">
        <div className="max-w-sm rounded-3xl bg-card p-8 text-center shadow-pillow">
          <AlertTriangle className="mx-auto mb-3 h-8 w-8 text-pastel-peach" />
          <p className="text-sm text-foreground">
            We couldn&apos;t find this patient link.
          </p>
          <button
            disabled={demoBusy}
            onClick={() => {
              setDemoBusy(true);
              setDemoError(null);
              void ensureDemoPatient(patientId)
                .then(() => {
                  setNotFound(false);
                  setLoading(true);
                  void loadPatient();
                })
                .catch((err) =>
                  setDemoError(
                    err instanceof ApiError
                      ? err.message
                      : "Could not reach the backend"
                  )
                )
                .finally(() => setDemoBusy(false));
            }}
            className="mt-4 block w-full rounded-2xl bg-pastel-blue px-4 py-2 text-sm font-medium text-foreground shadow-pillow-sm disabled:opacity-50"
          >
            {demoBusy ? "Creating…" : `Create demo profile for ${patientId}`}
          </button>
          {demoError && (
            <p className="mt-2 text-xs text-foreground">{demoError}</p>
          )}
          <Link
            href="/"
            className="mt-4 inline-block rounded-2xl bg-card px-4 py-2 text-sm font-medium text-foreground shadow-pillow-sm"
          >
            Back to home
          </Link>
        </div>
      </main>
    );
  }

  if (loading) {
    return (
      <main className="flex min-h-screen items-center justify-center text-muted-foreground">
        <Loader2 className="mr-2 h-5 w-5 animate-spin" /> Loading your
        screening…
      </main>
    );
  }

  if (authRequired) {
    return (
      <main className="flex min-h-screen items-center justify-center p-6 text-foreground">
        <div className="w-full max-w-sm rounded-[1.75rem] bg-card p-6 text-center shadow-pillow">
          <p className="text-sm">
            Patient records are protected. Sign in as a clinician to open this
            screening.
          </p>
          <Link
            href={`/doctor?next=${encodeURIComponent(`/patient/${patientId}`)}`}
            className="mt-4 inline-block rounded-2xl bg-pastel-blue px-4 py-2 text-sm font-medium text-foreground shadow-pillow-sm hover:bg-pastel-bluedeep"
          >
            Clinician sign-in
          </Link>
        </div>
      </main>
    );
  }

  if (loadError) {
    return (
      <main className="flex min-h-screen items-center justify-center p-6 text-foreground">
        <p className="text-sm text-foreground">
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
    <main className="min-h-screen p-6 text-foreground">
      <header className="mb-8 flex flex-wrap items-center justify-between gap-4">
        <div className="flex items-center gap-3">
          <Activity className="h-8 w-8 text-pastel-blue" />
          <div>
            <h1 className="text-2xl font-bold text-foreground">Sana</h1>
            <p className="text-xs text-muted-foreground">
              Gait screening for {patient?.name ?? patientId} · {patientId}
            </p>
          </div>
        </div>
        <div className="flex items-center gap-3">
          <span className="flex items-center gap-2 rounded-full border-0 bg-card px-3 py-1.5 text-xs text-foreground shadow-pillow-sm">
            <User className="h-3.5 w-3.5 text-muted-foreground" />
            {patient?.name ?? patientId}
          </span>
          <Link
            href="/doctor"
            className="flex items-center gap-2 rounded-full border-0 bg-card px-3 py-1.5 text-xs text-foreground shadow-pillow-sm hover:bg-pastel-sand"
          >
            <Stethoscope className="h-3.5 w-3.5 text-muted-foreground" />
            Doctor&apos;s Portal
          </Link>
        </div>
      </header>

      <div className="mb-6 overflow-hidden rounded-[1.75rem] bg-card shadow-pillow">
        <div className="rounded-t-[1.75rem] bg-pastel-green px-6 py-3">
          <h2 className="text-sm font-medium text-foreground">
            Survey details
          </h2>
        </div>
        <div className="px-6 py-4">
        {latestSurvey ? (
          <div className="flex flex-wrap items-center gap-3 text-xs text-foreground">
            <span className="rounded-full border-0 bg-pastel-peach px-2 py-0.5 text-foreground">
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
                  className="rounded-full border-0 bg-pastel-lavender/60 px-2 py-0.5 text-[10px] text-foreground"
                >
                  {c}
                </span>
              ))}
            </span>
          </div>
        ) : (
          <p className="text-xs text-muted-foreground">No survey on file yet.</p>
        )}
        </div>
      </div>

      <div className="grid gap-6 lg:grid-cols-2">
        <WebcamFeed onMetrics={handleMetrics} onInputReset={handleInputReset} />

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
            <p className="rounded-2xl border-0 bg-pastel-peach/70 px-3 py-2 text-xs text-foreground">
              No walking detected — walk across the frame (or upload a clip
              with walking) to record a session.
            </p>
          )}

          {metricsSource === "live" && metrics && (
            <div className="flex items-center gap-2 rounded-3xl bg-card p-4 shadow-pillow">
              <Save className="h-4 w-4 shrink-0 text-muted-foreground" />
              <span className="text-xs text-muted-foreground">
                Save this walk to your record
              </span>
              <button
                onClick={() => void saveSession("live")}
                disabled={saving || !metrics.gait_detected}
                className="rounded-2xl bg-pastel-blue px-3 py-1.5 text-xs font-medium text-foreground shadow-pillow-sm disabled:opacity-50"
              >
                {saving ? "Saving…" : "Save this walk"}
              </button>
            </div>
          )}
          {saveNote && (
            <p
              className={`text-xs ${
                saveNote.startsWith("Save failed")
                  ? "text-foreground"
                  : "text-foreground"
              }`}
            >
              {saveNote}
            </p>
          )}

          <TrendGraph sessions={sessions} />

          <TokenEfficiency refresh={summaryCount} lastResult={summary} />

          <div className="rounded-3xl bg-card p-5 shadow-pillow">
            <h2 className="text-sm font-medium text-foreground">
              AI Patient Summary
            </h2>
            <p className="mb-3 text-xs text-muted-foreground">
              Plain-language clinical summary for patients & care teams
            </p>
            <button
              onClick={handleSummary}
              disabled={summaryLoading || !hasSessions}
              className="flex items-center gap-2 rounded-2xl bg-pastel-blue px-4 py-2 text-sm font-medium text-foreground shadow-pillow-sm disabled:opacity-50"
            >
              <Sparkles className="h-4 w-4" />
              {summaryLoading ? "Generating…" : "Generate Patient Summary"}
            </button>
            {!hasSessions && (
              <p className="mt-1 text-[11px] text-muted-foreground">
                Complete a walk or upload first — no sessions on file yet.
              </p>
            )}
            {summaryError && (
              <p className="mt-3 text-xs text-foreground">{summaryError}</p>
            )}
            {summary && (
              <div className="mt-3">
                <p className="text-sm leading-relaxed text-foreground">
                  {summary.summary}
                </p>
                <div className="mt-2 flex gap-2 text-[10px]">
                  <span className="rounded-full border-0 bg-muted px-2 py-0.5 uppercase text-muted-foreground">
                    {summary.source}
                  </span>
                  {summary.cached && (
                    <span className="rounded-full border-0 bg-pastel-blue px-2 py-0.5 uppercase text-foreground">
                      served from cache
                    </span>
                  )}
                  {summary.estimated_tokens_saved > 0 && (
                    <span className="rounded-full border-0 bg-muted px-2 py-0.5 text-muted-foreground">
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
