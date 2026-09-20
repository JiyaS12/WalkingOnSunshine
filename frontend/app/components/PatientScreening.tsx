"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "next/navigation";
import {
  Activity,
  AlertTriangle,
  CheckCircle2,
  Footprints,
  Gauge,
  Loader2,
  RefreshCw,
  Save,
  ShieldAlert,
  TrendingDown,
  User,
  WifiOff,
} from "lucide-react";
import WebcamFeed from "./WebcamFeed";
import TrendGraph, { TrendSession } from "./TrendGraph";
import { Badge } from "../../components/ui/badge";
import {
  Card,
  CardAction,
  CardContent,
  CardHeader,
  CardTitle,
} from "../../components/ui/card";
import {
  ApiError,
  GaitMetrics,
  JointFrame,
  PatientAccessRecord,
  addPatientAccessSession,
  fetchPatientAccess,
} from "../lib/api";

const UNSAVED_LABEL = { live: "Live", upload: "Upload" } as const;
const TOKEN_PATTERN = /^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$/;

type AccessStatus = "loading" | "ready" | "missing" | "invalid" | "offline" | "unavailable";

interface MemoryCredential {
  patientId: string;
  token: string;
}

interface MetricReading {
  epoch: number;
  id: string;
  source: "live" | "upload";
  metrics: GaitMetrics;
  frames: JointFrame[] | null;
  saved: boolean;
}

interface SaveMessage {
  epoch: number;
  kind: "success" | "error";
  text: string;
  retry?: MetricReading;
}

interface CardProps {
  title: string;
  value: string;
  icon: React.ReactNode;
  badge?: { label: string; classes: string };
  sub?: string;
}

function riskLevel(score: number): { label: string; classes: string } {
  if (score < 0.3) {
    return {
      label: "LOW",
      classes: "border-0 rounded-full bg-[#E4F5D6] text-[#4F7A3A]",
    };
  }
  if (score < 0.5) {
    return {
      label: "MODERATE",
      classes: "border-0 rounded-full bg-[#FBEBD2] text-[#9A6B2F]",
    };
  }
  return {
    label: "HIGH",
    classes: "border-0 rounded-full bg-pastel-peach text-[#9A4B32]",
  };
}

function MetricCard({ title, value, icon, badge, sub }: CardProps) {
  return (
    <Card>
      <CardHeader className="pb-0">
        <CardTitle className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
          {title}
        </CardTitle>
        <CardAction>
          <span className="text-muted-foreground">{icon}</span>
        </CardAction>
      </CardHeader>
      <CardContent className="pt-1">
        <div className="flex items-end gap-2">
          <span className="text-2xl font-semibold text-card-foreground">{value}</span>
        {badge && (
          <Badge
            variant="outline"
            className={`mb-0.5 text-[10px] font-semibold ${badge.classes}`}
          >
            {badge.label}
          </Badge>
        )}
        </div>
        {sub && <p className="mt-1 text-[10px] text-muted-foreground">{sub}</p>}
      </CardContent>
    </Card>
  );
}

function AccessPanel({
  status,
  onRetry,
}: {
  status: Exclude<AccessStatus, "loading" | "ready">;
  onRetry: () => void;
}) {
  const content = {
    missing: {
      icon: <ShieldAlert className="mx-auto mb-3 h-9 w-9 text-pastel-peach" />,
      title: "Secure link required",
      detail:
        "Open the complete link sent by your care team. A patient ID by itself cannot open a screening.",
    },
    invalid: {
      icon: <ShieldAlert className="mx-auto mb-3 h-9 w-9 text-pastel-peach" />,
      title: "This link is no longer valid",
      detail:
        "The link may be expired or incomplete. Ask your care team to send a new secure link.",
    },
    offline: {
      icon: <WifiOff className="mx-auto mb-3 h-9 w-9 text-[#9A4B32]" />,
      title: "You appear to be offline",
      detail: "Check your connection, then try opening the screening again.",
    },
    unavailable: {
      icon: <AlertTriangle className="mx-auto mb-3 h-9 w-9 text-[#9A4B32]" />,
      title: "Screening is temporarily unavailable",
      detail: "Please try again. If the problem continues, contact your care team.",
    },
  }[status];

  return (
    <main className="flex min-h-screen items-center justify-center p-6 text-slate-600">
      <div role="alert" className="max-w-sm rounded-3xl bg-white p-8 text-center shadow-pillow">
        {content.icon}
        <h1 className="text-lg font-semibold text-slate-700">{content.title}</h1>
        <p className="mt-2 text-sm leading-relaxed text-slate-400">{content.detail}</p>
        {(status === "offline" || status === "unavailable") && (
          <button
            type="button"
            onClick={onRetry}
            className="mx-auto mt-4 flex items-center gap-2 rounded-2xl bg-pastel-blue px-4 py-2 text-sm font-medium text-slate-700 shadow-pillow-sm"
          >
            <RefreshCw className="h-4 w-4" />
            Try again
          </button>
        )}
      </div>
    </main>
  );
}

function toTrendSessions(record: PatientAccessRecord | null): TrendSession[] {
  return (record?.gait_sessions ?? []).map((session) => ({
    label: session.label,
    asymmetry_pct: session.metrics.asymmetry_pct,
    fall_risk_score: session.metrics.fall_risk_score,
    stride_length_m: session.metrics.stride_length_m,
  }));
}

function isAbortError(error: unknown): boolean {
  return error instanceof DOMException && error.name === "AbortError";
}

export default function PatientScreening({ patientId }: { patientId: string }) {
  const searchParams = useSearchParams();
  const queryToken = searchParams.get("token");
  const [memoryCredential, setMemoryCredential] = useState<MemoryCredential | null>(
    () => (queryToken !== null ? { patientId, token: queryToken } : null)
  );

  const credential = useMemo(
    () =>
      queryToken !== null
        ? { patientId, token: queryToken }
        : memoryCredential?.patientId === patientId
          ? memoryCredential
          : null,
    [memoryCredential, patientId, queryToken]
  );

  // Capture the credential in component memory, then remove it from the
  // address bar so browser history, copied URLs, and referrers do not retain it.
  useEffect(() => {
    setMemoryCredential((current) => {
      if (queryToken !== null) {
        if (current?.patientId === patientId && current.token === queryToken) return current;
        return { patientId, token: queryToken };
      }
      return current?.patientId === patientId ? current : null;
    });

    if (queryToken !== null) {
      const url = new URL(window.location.href);
      url.searchParams.delete("token");
      window.history.replaceState(
        null,
        "",
        `${url.pathname}${url.search}${url.hash}`
      );
    }
  }, [patientId, queryToken]);

  const identitySignature = `${patientId}\u0000${credential?.token ?? ""}`;
  const identityVersionRef = useRef({ signature: "", epoch: 0 });
  if (identityVersionRef.current.signature !== identitySignature) {
    identityVersionRef.current = {
      signature: identitySignature,
      epoch: identityVersionRef.current.epoch + 1,
    };
  }
  const identityEpoch = identityVersionRef.current.epoch;
  const activeEpochRef = useRef(identityEpoch);
  activeEpochRef.current = identityEpoch;
  const accessToken = credential?.token ?? null;

  const tokenIsUsable =
    accessToken !== null &&
    accessToken.length <= 4096 &&
    TOKEN_PATTERN.test(accessToken);

  const [access, setAccess] = useState<{ epoch: number; status: AccessStatus } | null>(null);
  const [patientState, setPatientState] = useState<{
    epoch: number;
    record: PatientAccessRecord;
  } | null>(null);
  const [readingState, setReadingState] = useState<MetricReading | null>(null);
  const [saveMessage, setSaveMessage] = useState<SaveMessage | null>(null);
  const [savingReadingId, setSavingReadingId] = useState<string | null>(null);
  const [retryNonce, setRetryNonce] = useState(0);

  const loadAbortRef = useRef<AbortController | null>(null);
  const saveControllersRef = useRef(new Set<AbortController>());
  const metricIdsRef = useRef(new WeakMap<GaitMetrics, string>());
  const metricSequenceRef = useRef(0);
  const inFlightSavesRef = useRef(new Set<string>());
  const completedSavesRef = useRef(new Set<string>());

  useEffect(() => {
    loadAbortRef.current?.abort();
    for (const controller of saveControllersRef.current) controller.abort();
    saveControllersRef.current.clear();
    const identitySaveControllers = new Set<AbortController>();
    saveControllersRef.current = identitySaveControllers;
    metricIdsRef.current = new WeakMap<GaitMetrics, string>();
    metricSequenceRef.current = 0;
    inFlightSavesRef.current.clear();
    completedSavesRef.current.clear();
    setPatientState(null);
    setReadingState(null);
    setSaveMessage(null);
    setSavingReadingId(null);

    return () => {
      loadAbortRef.current?.abort();
      for (const controller of identitySaveControllers) controller.abort();
      identitySaveControllers.clear();
    };
  }, [identityEpoch]);

  useEffect(() => {
    const epoch = identityEpoch;
    if (accessToken === null) {
      setAccess({ epoch, status: "missing" });
      return;
    }
    if (!tokenIsUsable) {
      setAccess({ epoch, status: "invalid" });
      return;
    }

    const controller = new AbortController();
    loadAbortRef.current?.abort();
    loadAbortRef.current = controller;
    setAccess({ epoch, status: "loading" });

    void fetchPatientAccess(patientId, accessToken, controller.signal)
      .then((record) => {
        if (controller.signal.aborted || activeEpochRef.current !== epoch) return;
        if (record.patient_id !== patientId) {
          setPatientState(null);
          setAccess({ epoch, status: "invalid" });
          return;
        }
        setPatientState({ epoch, record });
        setAccess({ epoch, status: "ready" });
      })
      .catch((error: unknown) => {
        if (isAbortError(error) || controller.signal.aborted || activeEpochRef.current !== epoch) {
          return;
        }
        setPatientState(null);
        if (error instanceof ApiError && [401, 403, 404].includes(error.status)) {
          setAccess({ epoch, status: "invalid" });
        } else if (error instanceof ApiError && error.status === 503) {
          setAccess({ epoch, status: "unavailable" });
        } else if (!(error instanceof ApiError) || !navigator.onLine) {
          setAccess({ epoch, status: "offline" });
        } else {
          setAccess({ epoch, status: "unavailable" });
        }
      });

    return () => controller.abort();
  }, [accessToken, identityEpoch, patientId, retryNonce, tokenIsUsable]);

  const currentAccess: AccessStatus =
    access?.epoch === identityEpoch
      ? access.status
      : accessToken === null
        ? "missing"
        : tokenIsUsable
          ? "loading"
          : "invalid";
  const patient = patientState?.epoch === identityEpoch ? patientState.record : null;
  const reading = readingState?.epoch === identityEpoch ? readingState : null;
  const currentSaveMessage = saveMessage?.epoch === identityEpoch ? saveMessage : null;

  const saveSession = useCallback(
    async (candidate: MetricReading) => {
      const epoch = candidate.epoch;
      if (
        epoch !== activeEpochRef.current ||
        accessToken === null ||
        !tokenIsUsable ||
        !candidate.metrics.gait_detected ||
        inFlightSavesRef.current.has(candidate.id) ||
        completedSavesRef.current.has(candidate.id)
      ) {
        return;
      }

      inFlightSavesRef.current.add(candidate.id);
      setSavingReadingId(candidate.id);
      setSaveMessage(null);
      const controller = new AbortController();
      saveControllersRef.current.add(controller);

      try {
        const record = await addPatientAccessSession(
          patientId,
          accessToken,
          {
            label: `${candidate.source === "live" ? "Live" : "Upload"} ${new Date().toLocaleTimeString("en-GB", { hour12: false })}`,
            source: candidate.source,
            idempotency_key: candidate.id,
            metrics: candidate.metrics,
            frames: candidate.frames,
          },
          controller.signal
        );
        if (controller.signal.aborted || activeEpochRef.current !== epoch) return;
        if (record.patient_id !== patientId) {
          setPatientState(null);
          setAccess({ epoch, status: "invalid" });
          return;
        }

        completedSavesRef.current.add(candidate.id);
        setPatientState((current) =>
          current?.epoch === epoch &&
          current.record.gait_sessions.length > record.gait_sessions.length
            ? current
            : { epoch, record }
        );
        setReadingState((current) =>
          current?.epoch === epoch && current.id === candidate.id
            ? { ...current, saved: true }
            : current
        );
        setSaveMessage({ epoch, kind: "success", text: "Walk saved successfully." });
      } catch (error: unknown) {
        if (isAbortError(error) || controller.signal.aborted || activeEpochRef.current !== epoch) {
          return;
        }
        if (error instanceof ApiError && [401, 403, 404].includes(error.status)) {
          setPatientState(null);
          setSaveMessage(null);
          setAccess({ epoch, status: "invalid" });
        } else if (error instanceof ApiError && error.status === 422) {
          setSaveMessage({
            epoch,
            kind: "error",
            text: "This walk could not be saved. Please record another walk.",
          });
        } else {
          setSaveMessage({
            epoch,
            kind: "error",
            text:
              error instanceof ApiError && error.status === 503
                ? "Saving is temporarily unavailable."
                : "The walk was not saved. Check your connection and try again.",
            retry: candidate,
          });
        }
      } finally {
        saveControllersRef.current.delete(controller);
        inFlightSavesRef.current.delete(candidate.id);
        if (activeEpochRef.current === epoch) {
          setSavingReadingId((current) => (current === candidate.id ? null : current));
        }
      }
    },
    [accessToken, patientId, tokenIsUsable]
  );

  const handleMetrics = useCallback(
    (metrics: GaitMetrics, source: "live" | "upload", frames?: JointFrame[]) => {
      const epoch = identityEpoch;
      if (activeEpochRef.current !== epoch) return;

      let id = metricIdsRef.current.get(metrics);
      if (!id) {
        id = `${source}-${crypto.randomUUID().replaceAll("-", "")}-${++metricSequenceRef.current}`;
        metricIdsRef.current.set(metrics, id);
      }
      const candidate: MetricReading = {
        epoch,
        id,
        source,
        metrics,
        frames: frames ?? null,
        saved: completedSavesRef.current.has(id),
      };
      setReadingState(candidate);
      setSaveMessage(null);
      if (source === "upload" && metrics.gait_detected) {
        void saveSession(candidate);
      }
    },
    [identityEpoch, saveSession]
  );

  const handleInputReset = useCallback(() => {
    setReadingState((current) => (current?.epoch === identityEpoch ? null : current));
    setSaveMessage((current) => (current?.epoch === identityEpoch ? null : current));
  }, [identityEpoch]);

  const trendSessions = useMemo(() => {
    const sessions = toTrendSessions(patient);
    if (reading?.metrics.gait_detected && !reading.saved) {
      sessions.push({
        label: UNSAVED_LABEL[reading.source],
        asymmetry_pct: reading.metrics.asymmetry_pct,
        fall_risk_score: reading.metrics.fall_risk_score,
        stride_length_m: reading.metrics.stride_length_m,
      });
    }
    return sessions;
  }, [patient, reading]);

  if (currentAccess === "loading") {
    return (
      <main
        role="status"
        className="flex min-h-screen items-center justify-center text-slate-400"
      >
        <Loader2 className="mr-2 h-5 w-5 animate-spin" /> Loading your screening…
      </main>
    );
  }

  if (currentAccess !== "ready" || !patient) {
    return (
      <AccessPanel
        status={currentAccess === "ready" ? "unavailable" : currentAccess}
        onRetry={() => setRetryNonce((value) => value + 1)}
      />
    );
  }

  const metrics = reading?.metrics ?? null;
  const isSaving = reading !== null && savingReadingId === reading.id;

  return (
    <main className="min-h-screen p-6 text-slate-600">
      <header className="mb-6 flex flex-wrap items-center justify-between gap-4">
        <div className="flex items-center gap-3">
          <Activity className="h-8 w-8 text-pastel-blue" />
          <div>
            <h1 className="text-2xl font-bold text-slate-700">GaitGuard AI</h1>
            <p className="text-xs text-slate-400">Your secure walking assessment</p>
          </div>
        </div>
        <span className="flex items-center gap-2 rounded-full border-0 bg-white px-3 py-1.5 text-xs text-slate-600 shadow-pillow-sm">
          <User className="h-3.5 w-3.5 text-slate-400" />
          {patient.name ?? "Patient"}
        </span>
      </header>

      <div className="mb-4 rounded-3xl bg-white p-4 shadow-pillow">
        <h2 className="text-sm font-medium text-slate-700">Complete one walking test</h2>
        <p className="mt-1 text-xs leading-relaxed text-slate-400">
          Use the live camera or upload a walking video. A valid walk is saved securely to your care team.
        </p>
      </div>

      <div className="grid gap-6 lg:grid-cols-2">
        <WebcamFeed
          key={identityEpoch}
          onMetrics={handleMetrics}
          onInputReset={handleInputReset}
        />

        <div className="flex flex-col gap-4">
          <div className="grid grid-cols-2 gap-4">
            <MetricCard
              title="Stride Length"
              value={metrics ? `${metrics.stride_length_m.toFixed(2)} m` : "—"}
              icon={<Footprints className="h-4 w-4" />}
              sub={metrics?.stride_ratio ? `×${metrics.stride_ratio.toFixed(2)} leg` : undefined}
            />
            <MetricCard
              title="Asymmetry"
              value={metrics ? `${metrics.asymmetry_pct.toFixed(1)}%` : "—"}
              icon={<Gauge className="h-4 w-4" />}
            />
            <MetricCard
              title="Velocity Degradation"
              value={metrics ? `${metrics.velocity_degradation_pct.toFixed(1)}%` : "—"}
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
            value={metrics ? `${metrics.cadence_steps_per_min.toFixed(0)} steps/min` : "—"}
            icon={<Activity className="h-4 w-4" />}
          />

          {metrics && !metrics.gait_detected && (
            <p role="alert" className="rounded-2xl border-0 bg-[#FBEBD2] px-3 py-2 text-xs text-slate-600">
              No walking detected — walk across the frame or upload a clip with walking before saving.
            </p>
          )}

          {reading?.source === "live" && (
            <div className="flex items-center gap-2 rounded-3xl bg-white p-4 shadow-pillow">
              {reading.saved ? (
                <CheckCircle2 className="h-4 w-4 shrink-0 text-[#4F7A3A]" />
              ) : (
                <Save className="h-4 w-4 shrink-0 text-slate-400" />
              )}
              <span className="flex-1 text-xs text-slate-400">
                {reading.saved ? "This walk is saved" : "Save this walk to your record"}
              </span>
              <button
                type="button"
                onClick={() => void saveSession(reading)}
                disabled={isSaving || reading.saved || !reading.metrics.gait_detected}
                className="rounded-2xl bg-pastel-blue px-3 py-1.5 text-xs font-medium text-slate-700 shadow-pillow-sm disabled:opacity-50"
              >
                {isSaving ? "Saving…" : reading.saved ? "Saved" : "Save this walk"}
              </button>
            </div>
          )}

          {currentSaveMessage && (
            <div
              role={currentSaveMessage.kind === "error" ? "alert" : "status"}
              className={`flex items-center justify-between gap-3 rounded-2xl border-0 px-3 py-2 text-xs ${
                currentSaveMessage.kind === "error"
                  ? "bg-pastel-peach text-[#9A4B32]"
                  : "bg-[#E4F5D6] text-[#4F7A3A]"
              }`}
            >
              <span>{currentSaveMessage.text}</span>
              {currentSaveMessage.retry && (
                <button
                  type="button"
                  disabled={savingReadingId === currentSaveMessage.retry.id}
                  onClick={() => void saveSession(currentSaveMessage.retry!)}
                  className="flex shrink-0 items-center gap-1 rounded-xl border border-current/40 px-2 py-1 font-medium disabled:opacity-50"
                >
                  <RefreshCw className="h-3 w-3" />
                  Retry save
                </button>
              )}
            </div>
          )}

          {reading?.source === "upload" && isSaving && (
            <p role="status" className="flex items-center gap-2 text-xs text-slate-400">
              <Loader2 className="h-3.5 w-3.5 animate-spin" /> Saving analyzed walk…
            </p>
          )}

          <TrendGraph sessions={trendSessions} />
        </div>
      </div>
    </main>
  );
}
