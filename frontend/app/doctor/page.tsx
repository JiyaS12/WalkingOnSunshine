"use client";

import {
  type FormEvent,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import Link from "next/link";
import {
  Copy,
  Loader2,
  LogOut,
  Search,
  ShieldCheck,
  Sparkles,
  Stethoscope,
  User,
} from "lucide-react";
import SkeletonReplay from "../components/SkeletonReplay";
import TrendGraph, { TrendSession } from "../components/TrendGraph";
import {
  ApiError,
  ClinicianSession,
  fetchPatient,
  fetchPatients,
  generateSynthesis,
  getClinicianSession,
  GaitSession,
  PatientRecord,
  PatientSummary,
  signInClinician,
  signOutClinician,
  SummaryResponse,
} from "../lib/api";

type RiskFilter = "all" | "high" | "moderate" | "low";
type AuthState = "checking" | "signed-out" | "signed-in" | "expired";

function riskBand(score: number | null | undefined): string {
  if (score === null || score === undefined) return "none";
  if (score >= 0.5) return "high";
  if (score >= 0.3) return "moderate";
  return "low";
}

function riskBadge(score: number | null | undefined) {
  const band = riskBand(score);
  if (band === "high")
    return "border-0 bg-pastel-peach text-foreground";
  if (band === "moderate")
    return "border-0 bg-pastel-peach/70 text-foreground";
  if (band === "low")
    return "border-0 bg-pastel-green text-foreground";
  return "border-0 bg-muted text-muted-foreground";
}

function safeReturnPath(): string | null {
  if (typeof window === "undefined") return null;
  const next = new URLSearchParams(window.location.search).get("next");
  return next && /^\/(patient\/[^/?#]+)?$/.test(next) ? next : null;
}

export default function DoctorPortal() {
  const [authState, setAuthState] = useState<AuthState>("checking");
  const [session, setSession] = useState<ClinicianSession | null>(null);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [authError, setAuthError] = useState<string | null>(null);
  const [signingIn, setSigningIn] = useState(false);
  const [patients, setPatients] = useState<PatientSummary[]>([]);
  const [listError, setListError] = useState<string | null>(null);
  const [loadingList, setLoadingList] = useState(false);
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
  const detailGenRef = useRef(0);
  const synthGenRef = useRef(0);
  const queryRef = useRef(query);
  queryRef.current = query;
  const selectedIdStateRef = useRef<string | null>(null);
  selectedIdStateRef.current = selectedId;
  const [lastSyncedAt, setLastSyncedAt] = useState<Date | null>(null);
  const [copied, setCopied] = useState(false);

  const clearProtectedData = useCallback(() => {
    detailGenRef.current += 1;
    synthGenRef.current += 1;
    setPatients([]);
    setSelectedId(null);
    setRecord(null);
    setSynthesis(null);
    setListError(null);
    setDetailError(null);
    setSynthError(null);
    setLoadingList(false);
    setSynthLoading(false);
    setLastSyncedAt(null);
  }, []);

  const handleAuthFailure = useCallback(
    (error: unknown): boolean => {
      if (!(error instanceof ApiError) || error.status !== 401) return false;
      const expired = error.code === "session_expired";
      setAuthState(expired ? "expired" : "signed-out");
      setSession(null);
      setAuthError(expired ? "Your clinician session expired. Sign in again." : null);
      clearProtectedData();
      return true;
    },
    [clearProtectedData]
  );

  const loadList = useCallback((q: string, showSpinner = true) => {
    if (showSpinner) setLoadingList(true);
    fetchPatients(q || undefined)
      .then((rows) => {
        setPatients(rows);
        setListError(null);
        setSelectedId((prev) => prev ?? (rows[0]?.patient_id ?? null));
        setLastSyncedAt(new Date());
      })
      .catch((err) => {
        if (!handleAuthFailure(err))
          setListError(err instanceof Error ? err.message : String(err));
      })
      .finally(() => setLoadingList(false));
  }, [handleAuthFailure]);

  const loadDetail = useCallback((id: string, reset = true) => {
    const gen = ++detailGenRef.current;
    if (reset) {
      synthGenRef.current += 1;
      setSynthLoading(false);
      setRecord(null);
      setDetailError(null);
      setSynthesis(null);
      setSynthError(null);
    }
    fetchPatient(id)
      .then((r) => {
        if (gen === detailGenRef.current) {
          setRecord(r);
          setDetailError(null);
          setLastSyncedAt(new Date());
        }
      })
      .catch((err) => {
        if (handleAuthFailure(err)) return;
        if (gen === detailGenRef.current)
          setDetailError(
            err instanceof Error ? err.message : String(err)
          );
      });
  }, [handleAuthFailure]);

  useEffect(() => {
    getClinicianSession()
      .then((activeSession) => {
        setSession(activeSession);
        setAuthState("signed-in");
        setAuthError(null);
      })
      .catch((err) => {
        if (!handleAuthFailure(err)) {
          setAuthState("signed-out");
          setAuthError(err instanceof Error ? err.message : String(err));
        }
      });
  }, [handleAuthFailure]);

  useEffect(() => {
    if (authState === "signed-in") loadList("");
  }, [authState, loadList]);

  useEffect(() => {
    if (authState !== "signed-in" || !session) return;
    const remainingMs = session.expires_at * 1000 - Date.now();
    if (remainingMs <= 0) {
      setAuthState("expired");
      setSession(null);
      setAuthError("Your clinician session expired. Sign in again.");
      clearProtectedData();
      return;
    }
    const timer = setTimeout(() => {
      setAuthState("expired");
      setSession(null);
      setAuthError("Your clinician session expired. Sign in again.");
      clearProtectedData();
    }, Math.min(remainingMs, 2_147_000_000));
    return () => clearTimeout(timer);
  }, [authState, session, clearProtectedData]);

  // live sync: poll list + selected record every 5 s while the tab is visible
  useEffect(() => {
    if (authState !== "signed-in") return;
    const tick = () => {
      if (document.visibilityState !== "visible") return;
      loadList(queryRef.current, false);
      const id = selectedIdStateRef.current;
      if (id) loadDetail(id, false);
    };
    const interval = setInterval(tick, 5000);
    document.addEventListener("visibilitychange", tick);
    return () => {
      clearInterval(interval);
      document.removeEventListener("visibilitychange", tick);
    };
  }, [authState, loadList, loadDetail]);

  useEffect(() => {
    if (authState !== "signed-in") return;
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => loadList(query), 300);
    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
    };
  }, [authState, query, loadList]);

  useEffect(() => {
    if (authState === "signed-in" && selectedId) loadDetail(selectedId);
  }, [authState, selectedId, loadDetail]);

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
    const gen = ++synthGenRef.current;
    setSynthLoading(true);
    setSynthError(null);
    try {
      const result = await generateSynthesis(selectedId);
      if (gen === synthGenRef.current) setSynthesis(result);
    } catch (err) {
      if (handleAuthFailure(err)) return;
      if (gen === synthGenRef.current)
        setSynthError(err instanceof Error ? err.message : String(err));
    } finally {
      if (gen === synthGenRef.current) setSynthLoading(false);
    }
  };

  const handleSignIn = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setSigningIn(true);
    setAuthError(null);
    try {
      const activeSession = await signInClinician(username, password);
      setPassword("");
      const returnPath = safeReturnPath();
      if (returnPath) {
        window.location.assign(returnPath);
        return;
      }
      setSession(activeSession);
      setAuthState("signed-in");
    } catch (err) {
      setAuthState("signed-out");
      setAuthError(err instanceof Error ? err.message : "Sign-in failed");
    } finally {
      setSigningIn(false);
    }
  };

  const handleSignOut = async () => {
    try {
      await signOutClinician();
      setAuthState("signed-out");
      setSession(null);
      setPassword("");
      setAuthError(null);
      clearProtectedData();
    } catch (err) {
      setListError(
        `Sign-out failed: ${err instanceof Error ? err.message : String(err)}`
      );
    }
  };

  const stat = (label: string, value: string, d?: number | null) => (
    <div className="rounded-2xl border-0 bg-muted px-2 py-1.5 shadow-pillow-inset">
      <p className="text-[10px] uppercase tracking-wide text-muted-foreground">
        {label}
      </p>
      <p className="text-sm font-semibold text-foreground">
        {value}
        {d !== null && d !== undefined && (
          <span
            className={`ml-1 text-[10px] ${
              d <= 0 ? "text-foreground" : "text-foreground"
            }`}
          >
            {d > 0 ? "+" : ""}
            {d.toFixed(2)} vs first
          </span>
        )}
      </p>
    </div>
  );

  if (authState === "checking") {
    return (
      <main className="flex min-h-screen items-center justify-center text-muted-foreground">
        <p className="flex items-center gap-2 text-sm">
          <Loader2 className="h-4 w-4 animate-spin" /> Checking clinician session…
        </p>
      </main>
    );
  }

  if (authState !== "signed-in") {
    return (
      <main className="flex min-h-screen items-center justify-center p-6 text-foreground">
        <div className="w-full max-w-sm rounded-[1.75rem] border-0 bg-card p-6 shadow-pillow">
          <div className="mb-5 flex items-center gap-3">
            <ShieldCheck className="h-8 w-8 text-pastel-bluedeep" />
            <div>
              <h1 className="text-xl font-bold">Clinician sign-in</h1>
              <p className="text-xs text-muted-foreground">
                Sign in to access protected patient records.
              </p>
            </div>
          </div>
          {authState === "expired" && (
            <p role="alert" className="mb-3 rounded-2xl border-0 bg-pastel-peach/70 p-2 text-xs text-foreground">
              Your clinician session expired. Sign in again.
            </p>
          )}
          <form onSubmit={handleSignIn} className="space-y-3">
            <label className="block text-xs text-muted-foreground">
              Username
              <input
                autoComplete="username"
                value={username}
                onChange={(event) => setUsername(event.target.value)}
                className="mt-1 w-full rounded-2xl border-0 bg-muted px-3 py-2 text-sm text-foreground shadow-pillow-inset outline-none focus:ring-2 focus:ring-pastel-blue"
              />
            </label>
            <label className="block text-xs text-muted-foreground">
              Password
              <input
                type="password"
                autoComplete="current-password"
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                className="mt-1 w-full rounded-2xl border-0 bg-muted px-3 py-2 text-sm text-foreground shadow-pillow-inset outline-none focus:ring-2 focus:ring-pastel-blue"
              />
            </label>
            {authError && authState !== "expired" && (
              <p role="alert" className="text-xs text-[#9A4B32]">
                {authError}
              </p>
            )}
            <button
              type="submit"
              disabled={signingIn || !username || !password}
              className="flex w-full items-center justify-center gap-2 rounded-2xl bg-pastel-blue px-4 py-2 text-sm font-medium text-foreground shadow-pillow-sm hover:bg-pastel-bluedeep disabled:opacity-50"
            >
              {signingIn && <Loader2 className="h-4 w-4 animate-spin" />}
              {signingIn ? "Signing in…" : "Sign in"}
            </button>
          </form>
          <Link href="/" className="mt-4 block text-center text-xs text-muted-foreground hover:text-foreground">
            Return to patient view
          </Link>
        </div>
      </main>
    );
  }

  return (
    <main className="min-h-screen p-6 text-foreground">
      <header className="mb-8 flex flex-wrap items-center justify-between gap-4">
        <div className="flex items-center gap-3">
          <Stethoscope className="h-8 w-8 text-pastel-blue" />
          <div>
            <h1 className="text-2xl font-bold text-foreground">Doctor&apos;s Portal</h1>
            <p className="text-xs text-muted-foreground">
              Unified clinical synthesis — surveys + gait telemetry
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <span className="rounded-full border-0 bg-muted px-3 py-1.5 text-xs text-muted-foreground shadow-pillow-inset">
            {session?.username}
          </span>
          <button
            onClick={() => void handleSignOut()}
            className="flex items-center gap-2 rounded-full border-0 bg-card px-3 py-1.5 text-xs text-foreground shadow-pillow-sm hover:bg-pastel-sand"
          >
            <LogOut className="h-3.5 w-3.5 text-muted-foreground" />
            Sign out
          </button>
          <Link
            href="/"
            className="flex items-center gap-2 rounded-full border-0 bg-card px-3 py-1.5 text-xs text-foreground shadow-pillow-sm hover:bg-pastel-sand"
          >
            <User className="h-3.5 w-3.5 text-muted-foreground" />
            Patient view
          </Link>
        </div>
      </header>

      <div className="grid gap-6 lg:grid-cols-[320px_1fr]">
        <div className="rounded-[1.75rem] border-0 bg-card p-5 shadow-pillow">
          <div className="mb-3 flex items-center gap-2 rounded-2xl border-0 bg-muted px-2 py-1.5 shadow-pillow-inset">
            <Search className="h-4 w-4 text-muted-foreground" />
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search id, name, complaint…"
              className="w-full bg-transparent text-sm text-foreground outline-none placeholder:text-muted-foreground"
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
                className={`rounded-full border-0 px-2 py-0.5 ${
                  riskFilter === k
                    ? "bg-pastel-blue text-foreground shadow-pillow-sm"
                    : "bg-card text-muted-foreground shadow-pillow-inset"
                }`}
              >
                {label}
              </button>
            ))}
            <button
              onClick={() => setDizzyOnly((v) => !v)}
              className={`rounded-full border-0 px-2 py-0.5 ${
                dizzyOnly
                  ? "bg-pastel-blue text-foreground shadow-pillow-sm"
                  : "bg-card text-muted-foreground shadow-pillow-inset"
              }`}
            >
              Dizziness
            </button>
            <button
              onClick={() => setFallsOnly((v) => !v)}
              className={`rounded-full border-0 px-2 py-0.5 ${
                fallsOnly
                  ? "bg-pastel-blue text-foreground shadow-pillow-sm"
                  : "bg-card text-muted-foreground shadow-pillow-inset"
              }`}
            >
              Recent falls
            </button>
          </div>

          {loadingList && (
            <p className="flex items-center gap-2 text-xs text-muted-foreground">
              <Loader2 className="h-3.5 w-3.5 animate-spin" /> Loading patients…
            </p>
          )}
          {listError && (
            <p className="text-xs text-foreground">{listError}</p>
          )}
          {!loadingList && !listError && filtered.length === 0 && (
            <p className="text-xs text-muted-foreground">No patients match.</p>
          )}
          <ul className="flex flex-col gap-2">
            {filtered.map((p) => (
              <li key={p.patient_id}>
                <button
                  onClick={() => setSelectedId(p.patient_id)}
                  className={`w-full rounded-2xl border-0 p-2.5 text-left transition-colors ${
                    selectedId === p.patient_id
                      ? "bg-pastel-blue/60 shadow-pillow"
                      : "bg-muted shadow-pillow-inset hover:bg-pastel-sand"
                  }`}
                >
                  <div className="flex items-center justify-between">
                    <span className="text-sm font-medium text-foreground">
                      {p.name ?? p.patient_id}
                    </span>
                    {p.latest_fall_risk !== null &&
                      p.latest_fall_risk !== undefined && (
                        <span
                          className={`rounded-full px-1.5 py-0.5 text-[10px] font-semibold ${riskBadge(p.latest_fall_risk)}`}
                        >
                          {p.latest_fall_risk.toFixed(2)}
                        </span>
                      )}
                  </div>
                  <div className="mt-1 flex items-center gap-2 text-[11px] text-muted-foreground">
                    <span>{p.patient_id}</span>
                    {p.age != null && <span>· {p.age} y/o</span>}
                    {p.pain_scale != null && (
                      <span className="rounded-full border-0 bg-pastel-peach px-1.5 py-px text-foreground">
                        pain {p.pain_scale}
                      </span>
                    )}
                  </div>
                  {p.latest_survey_at && (
                    <p className="mt-0.5 text-[10px] text-muted-foreground">
                      survey {p.latest_survey_at.slice(0, 10)}
                    </p>
                  )}
                </button>
              </li>
            ))}
          </ul>
        </div>

        <div className="overflow-hidden rounded-[1.75rem] border-0 bg-card shadow-pillow">
          <div className="rounded-t-[1.75rem] bg-pastel-green px-6 py-3">
            <h2 className="text-sm font-medium text-foreground">
              Unified Clinical Synthesis Report
            </h2>
          </div>
          <div className="p-5">
          <div className="mb-4 flex items-center justify-between text-xs">
            <p className="text-muted-foreground">
              {record
                ? `${record.name ?? record.patient_id} · ${record.patient_id}${
                    record.age ? ` · ${record.age} y/o` : ""
                  }`
                : selectedId
                  ? "Loading…"
                  : "Select a patient"}
            </p>
            {record && (
              <button
                type="button"
                onClick={() => {
                  void navigator.clipboard.writeText(
                    `${window.location.origin}/patient/${encodeURIComponent(record.patient_id)}`
                  );
                  setCopied(true);
                  setTimeout(() => setCopied(false), 2000);
                }}
                className="flex items-center gap-1 rounded-2xl border-0 bg-card px-2 py-0.5 text-[10px] text-muted-foreground shadow-pillow-sm hover:bg-pastel-sand"
              >
                <Copy className="h-3 w-3" />
                {copied ? "Copied" : "Copy patient link"}
              </button>
            )}
            {lastSyncedAt && (
              <span className="flex items-center gap-1.5 text-muted-foreground">
                <span className="h-1.5 w-1.5 rounded-full bg-pastel-green" />
                Live · updated{" "}
                {lastSyncedAt.toLocaleTimeString("en-GB", { hour12: false })}
              </span>
            )}
          </div>
          {detailError && (
            <p className="text-xs text-foreground">{detailError}</p>
          )}
          {!record && selectedId && !detailError && (
            <p className="flex items-center gap-2 text-xs text-muted-foreground">
              <Loader2 className="h-3.5 w-3.5 animate-spin" /> Loading record…
            </p>
          )}
          {!record && !selectedId && (
            <p className="text-xs text-muted-foreground">No patient selected.</p>
          )}

          {record && (
            <div className="grid gap-4 md:grid-cols-3">
              <div className="rounded-2xl border-0 bg-muted p-4 shadow-pillow-inset">
                <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                  Subjective — Phone Survey
                </h3>
                {latestSurvey ? (
                  <div className="flex flex-col gap-2 text-xs">
                    <div>
                      <p className="text-muted-foreground">
                        Pain: {latestSurvey.pain_scale}/10
                      </p>
                      <div className="mt-1 h-2 w-full rounded-full bg-card">
                        <div
                          className="h-2 rounded-full bg-pastel-peach"
                          style={{
                            width: `${latestSurvey.pain_scale * 10}%`,
                          }}
                        />
                      </div>
                    </div>
                    <p className="text-foreground">
                      Falls (6 mo):{" "}
                      {latestSurvey.fall_history.falls_last_6_months}
                      {latestSurvey.fall_history.injured && " · injured"}
                    </p>
                    {latestSurvey.fall_history.last_fall_description && (
                      <p className="text-muted-foreground">
                        “{latestSurvey.fall_history.last_fall_description}”
                      </p>
                    )}
                    <p className="text-foreground">
                      Dizziness: {latestSurvey.dizziness ? "yes" : "no"}
                    </p>
                    {latestSurvey.dizziness_notes && (
                      <p className="text-muted-foreground">
                        {latestSurvey.dizziness_notes}
                      </p>
                    )}
                    <div className="flex flex-wrap gap-1">
                      {latestSurvey.primary_complaints.map((c) => (
                        <span
                          key={c}
                          className="rounded-full border-0 bg-card px-2 py-0.5 text-[10px] text-muted-foreground"
                        >
                          {c}
                        </span>
                      ))}
                    </div>
                    <p className="text-[10px] text-muted-foreground">
                      Recorded {latestSurvey.recorded_at?.slice(0, 10) ?? "—"}
                      {latestSurvey.call_id && ` · ${latestSurvey.call_id}`}
                    </p>
                  </div>
                ) : (
                  <p className="text-xs text-muted-foreground">No survey on file.</p>
                )}
              </div>

              <div className="rounded-2xl border-0 bg-muted p-4 shadow-pillow-inset">
                <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
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
                      <p className="rounded-2xl border-2 border-dashed border-border p-3 text-center text-[11px] text-muted-foreground">
                        No skeleton replay available
                      </p>
                    )}
                  </div>
                ) : (
                  <p className="text-xs text-muted-foreground">
                    No gait sessions on file.
                  </p>
                )}
              </div>

              <div className="rounded-2xl border-0 bg-muted p-4 shadow-pillow-inset">
                <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                  AI Clinical Summary
                </h3>
                <button
                  onClick={runSynthesis}
                  disabled={synthLoading}
                  className="flex items-center gap-2 rounded-2xl bg-pastel-blue px-3 py-1.5 text-xs font-medium text-foreground shadow-pillow-sm disabled:opacity-50"
                >
                  {synthLoading ? (
                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  ) : (
                    <Sparkles className="h-3.5 w-3.5" />
                  )}
                  {synthLoading ? "Generating…" : "Generate synthesis"}
                </button>
                {synthError && (
                  <p className="mt-2 text-xs text-foreground">{synthError}</p>
                )}
                {synthesis && (
                  <div className="mt-3">
                    <p className="text-xs leading-relaxed text-foreground">
                      {synthesis.summary}
                    </p>
                    <div className="mt-2 flex flex-wrap gap-1.5 text-[10px]">
                      <span className="rounded-full border-0 bg-card px-2 py-0.5 uppercase text-muted-foreground">
                        {synthesis.source === "openai" ? "OpenAI" : "template"}
                      </span>
                      {synthesis.cached && (
                        <span className="rounded-full border-0 bg-pastel-blue px-2 py-0.5 uppercase text-foreground">
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
      </div>
    </main>
  );
}
