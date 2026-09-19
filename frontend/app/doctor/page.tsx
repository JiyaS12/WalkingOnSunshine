"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import {
  Loader2,
  Search,
  Sparkles,
  Stethoscope,
  User,
} from "lucide-react";
import SkeletonReplay from "../components/SkeletonReplay";
import TrendGraph, { TrendSession } from "../components/TrendGraph";
import {
  fetchPatient,
  fetchPatients,
  generateSynthesis,
  GaitSession,
  PatientRecord,
  PatientSummary,
  SummaryResponse,
} from "../lib/api";

type RiskFilter = "all" | "high" | "moderate" | "low";

function riskBand(score: number | null | undefined): string {
  if (score === null || score === undefined) return "none";
  if (score >= 0.5) return "high";
  if (score >= 0.3) return "moderate";
  return "low";
}

function riskBadge(score: number | null | undefined) {
  const band = riskBand(score);
  if (band === "high")
    return "bg-rose-600/20 text-rose-300 border-rose-500/40";
  if (band === "moderate")
    return "bg-amber-600/20 text-amber-300 border-amber-500/40";
  if (band === "low")
    return "bg-emerald-600/20 text-emerald-300 border-emerald-500/40";
  return "border-slate-600 text-slate-400";
}

export default function DoctorPortal() {
  const [patients, setPatients] = useState<PatientSummary[]>([]);
  const [listError, setListError] = useState<string | null>(null);
  const [loadingList, setLoadingList] = useState(true);
  const [query, setQuery] = useState("");
  const [riskFilter, setRiskFilter] = useState<RiskFilter>("all");
  const [dizzyOnly, setDizzyOnly] = useState(false);
  const [fallsOnly, setFallsOnly] = useState(false);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [record, setRecord] = useState<PatientRecord | null>(null);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [synthesis, setSynthesis] = useState<SummaryResponse | null>(null);
  const [synthLoading, setSynthLoading] = useState(false);
  const [synthError, setSynthError] = useState<string | null>(null);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const loadList = useCallback((q: string) => {
    setLoadingList(true);
    fetchPatients(q || undefined)
      .then((rows) => {
        setPatients(rows);
        setListError(null);
        setSelectedId((prev) => prev ?? (rows[0]?.patient_id ?? null));
      })
      .catch((err) =>
        setListError(err instanceof Error ? err.message : String(err))
      )
      .finally(() => setLoadingList(false));
  }, []);

  useEffect(() => loadList(""), [loadList]);

  useEffect(() => {
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => loadList(query), 300);
    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
    };
  }, [query, loadList]);

  useEffect(() => {
    if (!selectedId) return;
    setRecord(null);
    setDetailError(null);
    setSynthesis(null);
    setSynthError(null);
    fetchPatient(selectedId)
      .then(setRecord)
      .catch((err) =>
        setDetailError(err instanceof Error ? err.message : String(err))
      );
  }, [selectedId]);

  const filtered = useMemo(
    () =>
      patients.filter((p) => {
        if (riskFilter !== "all" && riskBand(p.latest_fall_risk) !== riskFilter)
          return false;
        if (dizzyOnly && !p.dizziness) return false;
        if (fallsOnly && !(p.falls_last_6_months ?? 0)) return false;
        return true;
      }),
    [patients, riskFilter, dizzyOnly, fallsOnly]
  );

  const latestSurvey = record?.surveys?.length
    ? record.surveys[record.surveys.length - 1]
    : null;
  const sessions = useMemo(
    () => record?.gait_sessions ?? [],
    [record]
  );
  const latestSession = sessions.length ? sessions[sessions.length - 1] : null;
  const firstSession = sessions.length ? sessions[0] : null;
  const replaySession: GaitSession | null = useMemo(() => {
    for (let i = sessions.length - 1; i >= 0; i -= 1) {
      if (sessions[i].frames && sessions[i].frames!.length > 0)
        return sessions[i];
    }
    return null;
  }, [sessions]);

  const trend: TrendSession[] = sessions.map((s) => ({
    label: s.label,
    asymmetry_pct: s.metrics.asymmetry_pct,
    fall_risk_score: s.metrics.fall_risk_score,
    stride_length_m: s.metrics.stride_length_m,
  }));

  const delta = (key: "fall_risk_score" | "asymmetry_pct") =>
    sessions.length > 1
      ? latestSession!.metrics[key] - firstSession!.metrics[key]
      : null;

  const runSynthesis = async () => {
    if (!selectedId) return;
    setSynthLoading(true);
    setSynthError(null);
    try {
      setSynthesis(await generateSynthesis(selectedId));
    } catch (err) {
      setSynthError(err instanceof Error ? err.message : String(err));
    } finally {
      setSynthLoading(false);
    }
  };

  const stat = (label: string, value: string, d?: number | null) => (
    <div className="rounded-lg border border-slate-700 bg-slate-950 px-2 py-1.5">
      <p className="text-[10px] uppercase tracking-wide text-slate-500">
        {label}
      </p>
      <p className="text-sm font-semibold text-slate-100">
        {value}
        {d !== null && d !== undefined && (
          <span
            className={`ml-1 text-[10px] ${
              d <= 0 ? "text-emerald-400" : "text-rose-400"
            }`}
          >
            {d > 0 ? "+" : ""}
            {d.toFixed(2)} vs first
          </span>
        )}
      </p>
    </div>
  );

  return (
    <main className="min-h-screen bg-slate-950 p-6 text-slate-100">
      <header className="mb-6 flex flex-wrap items-center justify-between gap-4">
        <div className="flex items-center gap-3">
          <Stethoscope className="h-8 w-8 text-emerald-400" />
          <div>
            <h1 className="text-2xl font-bold">Doctor&apos;s Portal</h1>
            <p className="text-xs text-slate-400">
              Unified clinical synthesis — surveys + gait telemetry
            </p>
          </div>
        </div>
        <Link
          href="/"
          className="flex items-center gap-2 rounded-full border border-slate-700 bg-slate-900 px-3 py-1.5 text-xs text-slate-200 hover:bg-slate-800"
        >
          <User className="h-3.5 w-3.5 text-slate-400" />
          Patient view
        </Link>
      </header>

      <div className="grid gap-6 lg:grid-cols-[320px_1fr]">
        <div className="rounded-xl border border-slate-700 bg-slate-900 p-4">
          <div className="mb-3 flex items-center gap-2 rounded-lg border border-slate-700 bg-slate-950 px-2 py-1.5">
            <Search className="h-4 w-4 text-slate-500" />
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search id, name, complaint…"
              className="w-full bg-transparent text-sm text-slate-200 outline-none placeholder:text-slate-500"
            />
          </div>
          <div className="mb-3 flex flex-wrap gap-1.5 text-[11px]">
            {(
              [
                ["all", "All"],
                ["high", "High ≥0.5"],
                ["moderate", "Moderate 0.3–0.5"],
                ["low", "Low <0.3"],
              ] as [RiskFilter, string][]
            ).map(([k, label]) => (
              <button
                key={k}
                onClick={() => setRiskFilter(k)}
                className={`rounded-full border px-2 py-0.5 ${
                  riskFilter === k
                    ? "border-emerald-500 bg-emerald-600/20 text-emerald-300"
                    : "border-slate-600 text-slate-300 hover:bg-slate-800"
                }`}
              >
                {label}
              </button>
            ))}
            <button
              onClick={() => setDizzyOnly((v) => !v)}
              className={`rounded-full border px-2 py-0.5 ${
                dizzyOnly
                  ? "border-emerald-500 bg-emerald-600/20 text-emerald-300"
                  : "border-slate-600 text-slate-300 hover:bg-slate-800"
              }`}
            >
              Dizziness
            </button>
            <button
              onClick={() => setFallsOnly((v) => !v)}
              className={`rounded-full border px-2 py-0.5 ${
                fallsOnly
                  ? "border-emerald-500 bg-emerald-600/20 text-emerald-300"
                  : "border-slate-600 text-slate-300 hover:bg-slate-800"
              }`}
            >
              Recent falls
            </button>
          </div>

          {loadingList && (
            <p className="flex items-center gap-2 text-xs text-slate-400">
              <Loader2 className="h-3.5 w-3.5 animate-spin" /> Loading patients…
            </p>
          )}
          {listError && (
            <p className="text-xs text-rose-300">{listError}</p>
          )}
          {!loadingList && !listError && filtered.length === 0 && (
            <p className="text-xs text-slate-400">No patients match.</p>
          )}
          <ul className="flex flex-col gap-2">
            {filtered.map((p) => (
              <li key={p.patient_id}>
                <button
                  onClick={() => setSelectedId(p.patient_id)}
                  className={`w-full rounded-lg border p-2.5 text-left transition-colors ${
                    selectedId === p.patient_id
                      ? "border-emerald-500 bg-emerald-600/10"
                      : "border-slate-700 bg-slate-950 hover:bg-slate-800"
                  }`}
                >
                  <div className="flex items-center justify-between">
                    <span className="text-sm font-medium text-slate-100">
                      {p.name ?? p.patient_id}
                    </span>
                    {p.latest_fall_risk !== null &&
                      p.latest_fall_risk !== undefined && (
                        <span
                          className={`rounded-full border px-1.5 py-0.5 text-[10px] font-semibold ${riskBadge(p.latest_fall_risk)}`}
                        >
                          {p.latest_fall_risk.toFixed(2)}
                        </span>
                      )}
                  </div>
                  <div className="mt-1 flex items-center gap-2 text-[11px] text-slate-400">
                    <span>{p.patient_id}</span>
                    {p.age != null && <span>· {p.age} y/o</span>}
                    {p.pain_scale != null && (
                      <span className="rounded-full border border-amber-500/40 bg-amber-600/20 px-1.5 py-px text-amber-300">
                        pain {p.pain_scale}
                      </span>
                    )}
                  </div>
                  {p.latest_survey_at && (
                    <p className="mt-0.5 text-[10px] text-slate-500">
                      survey {p.latest_survey_at.slice(0, 10)}
                    </p>
                  )}
                </button>
              </li>
            ))}
          </ul>
        </div>

        <div className="rounded-xl border border-slate-700 bg-slate-900 p-4">
          <h2 className="mb-1 text-sm font-medium text-slate-200">
            Unified Clinical Synthesis Report
          </h2>
          <p className="mb-4 text-xs text-slate-400">
            {record
              ? `${record.name ?? record.patient_id} · ${record.patient_id}${
                  record.age ? ` · ${record.age} y/o` : ""
                }`
              : selectedId
                ? "Loading…"
                : "Select a patient"}
          </p>
          {detailError && (
            <p className="text-xs text-rose-300">{detailError}</p>
          )}
          {!record && selectedId && !detailError && (
            <p className="flex items-center gap-2 text-xs text-slate-400">
              <Loader2 className="h-3.5 w-3.5 animate-spin" /> Loading record…
            </p>
          )}
          {!record && !selectedId && (
            <p className="text-xs text-slate-400">No patient selected.</p>
          )}

          {record && (
            <div className="grid gap-4 md:grid-cols-3">
              <div className="rounded-lg border border-slate-700 bg-slate-950 p-3">
                <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-400">
                  Subjective — Phone Survey
                </h3>
                {latestSurvey ? (
                  <div className="flex flex-col gap-2 text-xs">
                    <div>
                      <p className="text-slate-400">
                        Pain: {latestSurvey.pain_scale}/10
                      </p>
                      <div className="mt-1 h-2 w-full rounded-full bg-slate-800">
                        <div
                          className="h-2 rounded-full bg-amber-500"
                          style={{
                            width: `${latestSurvey.pain_scale * 10}%`,
                          }}
                        />
                      </div>
                    </div>
                    <p className="text-slate-300">
                      Falls (6 mo):{" "}
                      {latestSurvey.fall_history.falls_last_6_months}
                      {latestSurvey.fall_history.injured && " · injured"}
                    </p>
                    {latestSurvey.fall_history.last_fall_description && (
                      <p className="text-slate-400">
                        “{latestSurvey.fall_history.last_fall_description}”
                      </p>
                    )}
                    <p className="text-slate-300">
                      Dizziness: {latestSurvey.dizziness ? "yes" : "no"}
                    </p>
                    {latestSurvey.dizziness_notes && (
                      <p className="text-slate-400">
                        {latestSurvey.dizziness_notes}
                      </p>
                    )}
                    <div className="flex flex-wrap gap-1">
                      {latestSurvey.primary_complaints.map((c) => (
                        <span
                          key={c}
                          className="rounded-full border border-slate-600 px-2 py-0.5 text-[10px] text-slate-300"
                        >
                          {c}
                        </span>
                      ))}
                    </div>
                    <p className="text-[10px] text-slate-500">
                      Recorded {latestSurvey.recorded_at?.slice(0, 10) ?? "—"}
                      {latestSurvey.call_id && ` · ${latestSurvey.call_id}`}
                    </p>
                  </div>
                ) : (
                  <p className="text-xs text-slate-500">No survey on file.</p>
                )}
              </div>

              <div className="rounded-lg border border-slate-700 bg-slate-950 p-3">
                <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-400">
                  Objective — Gait Analysis
                </h3>
                {latestSession ? (
                  <div className="flex flex-col gap-2">
                    <div className="grid grid-cols-2 gap-2">
                      {stat(
                        "Fall risk",
                        latestSession.metrics.fall_risk_score.toFixed(2),
                        delta("fall_risk_score")
                      )}
                      {stat(
                        "Asymmetry",
                        `${latestSession.metrics.asymmetry_pct.toFixed(1)}%`,
                        delta("asymmetry_pct")
                      )}
                      {stat(
                        "Stride",
                        `${latestSession.metrics.stride_length_m.toFixed(2)} m`
                      )}
                      {stat(
                        "Cadence",
                        `${latestSession.metrics.cadence_steps_per_min.toFixed(0)} spm`
                      )}
                      {stat(
                        "Knee ROM",
                        `${latestSession.metrics.knee_flexion_rom_deg.toFixed(0)}°`
                      )}
                      {stat(
                        "Session",
                        latestSession.label
                      )}
                    </div>
                    {trend.length > 0 && <TrendGraph sessions={trend} />}
                    {replaySession ? (
                      <SkeletonReplay
                        frames={replaySession.frames!}
                        fps={30}
                      />
                    ) : (
                      <p className="rounded-lg border border-dashed border-slate-700 p-3 text-center text-[11px] text-slate-500">
                        No skeleton replay available
                      </p>
                    )}
                  </div>
                ) : (
                  <p className="text-xs text-slate-500">
                    No gait sessions on file.
                  </p>
                )}
              </div>

              <div className="rounded-lg border border-slate-700 bg-slate-950 p-3">
                <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-400">
                  AI Clinical Summary
                </h3>
                <button
                  onClick={runSynthesis}
                  disabled={synthLoading}
                  className="flex items-center gap-2 rounded-lg bg-emerald-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-emerald-500 disabled:opacity-50"
                >
                  {synthLoading ? (
                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  ) : (
                    <Sparkles className="h-3.5 w-3.5" />
                  )}
                  {synthLoading ? "Generating…" : "Generate synthesis"}
                </button>
                {synthError && (
                  <p className="mt-2 text-xs text-rose-300">{synthError}</p>
                )}
                {synthesis && (
                  <div className="mt-3">
                    <p className="text-xs leading-relaxed text-slate-200">
                      {synthesis.summary}
                    </p>
                    <div className="mt-2 flex flex-wrap gap-1.5 text-[10px]">
                      <span className="rounded-full border border-slate-600 px-2 py-0.5 uppercase text-slate-300">
                        {synthesis.source === "openai" ? "OpenAI" : "template"}
                      </span>
                      {synthesis.cached && (
                        <span className="rounded-full border border-sky-500/40 bg-sky-600/20 px-2 py-0.5 uppercase text-sky-300">
                          cached
                        </span>
                      )}
                    </div>
                  </div>
                )}
              </div>
            </div>
          )}
        </div>
      </div>
    </main>
  );
}
