"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "next/navigation";
import Image from "next/image";
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
import {
  Card,
  CardAction,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import TrendGraph, { TrendSession } from "./TrendGraph";
import {
  ApiError,
  GaitMetrics,
  JointFrame,
  PatientAccessRecord,
  PatientSessionInput,
  addPatientAccessSession,
  fetchPatientAccess,
} from "../lib/api";
import { WalkingReporter, type WalkingReport } from "../lib/walkingReporter";
import type { WalkError, WalkEventName } from "../lib/integration";

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
  reporter: WalkingReporter | null;
  body: PatientSessionInput;
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
      classes: "border-0 bg-pastel-green text-foreground",
    };
  }
  if (score < 0.5) {
    return {
      label: "MODERATE",
      classes: "border-0 bg-pastel-peach/70 text-foreground",
    };
  }
  return {
    label: "HIGH",
    classes: "border-0 bg-pastel-peach text-foreground",
  };
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

function AccessPanel({
  status,
  onRetry,
}: {
  status: Exclude<AccessStatus, "loading" | "ready">;
  onRetry: () => void;
}) {
  const content = {
    missing: {
      icon: <ShieldAlert className="mx-auto mb-3 h-9 w-9 text-foreground" />,
      title: "Secure link required",
      detail:
        "Open the complete link sent by your care team. A patient ID by itself cannot open a screening.",
    },
    invalid: {
      icon: <ShieldAlert className="mx-auto mb-3 h-9 w-9 text-foreground" />,
      title: "This link is no longer valid",
      detail:
        "The link may be expired or incomplete. Ask your care team to send a new secure link.",
    },
    offline: {
      icon: <WifiOff className="mx-auto mb-3 h-9 w-9 text-foreground" />,
      title: "You appear to be offline",
      detail: "Check your connection, then try opening the screening again.",
    },
    unavailable: {
      icon: <AlertTriangle className="mx-auto mb-3 h-9 w-9 text-foreground" />,
      title: "Screening is temporarily unavailable",
      detail: "Please try again. If the problem continues, contact your care team.",
    },
  }[status];

  return (
    <main className="flex min-h-screen items-center justify-center bg-muted p-6 text-foreground">
      <div role="alert" className="max-w-sm rounded-xl border border-border bg-card p-6 text-center">
        {content.icon}
        <h1 className="text-lg font-semibold text-foreground">{content.title}</h1>
        <p className="mt-2 text-sm leading-relaxed text-muted-foreground">{content.detail}</p>
        {(status === "offline" || status === "unavailable") && (
          <button
            type="button"
            onClick={onRetry}
            className="mx-auto mt-4 flex items-center gap-2 rounded-md bg-pastel-sage px-4 py-2 text-sm font-medium text-foreground hover:bg-pastel-sagedeep"
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
  const reporterRef = useRef<WalkingReporter | null>(null);
  const [walkingState, setWalkingState] = useState<(WalkingReport & { epoch: number }) | null>(null);
  const bodiesRef = useRef(new WeakMap<GaitMetrics, PatientSessionInput>());

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
    bodiesRef.current = new WeakMap<GaitMetrics, PatientSessionInput>();
    setWalkingState(null);
    setPatientState(null);
    setReadingState(null);
    setSaveMessage(null);
    setSavingReadingId(null);

    return () => {
      reporterRef.current?.dispose();
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
    reporterRef.current?.dispose();
    reporterRef.current = null;
    setReadingState(null);
    setSaveMessage(null);
    setWalkingState(null);
    for (const saveController of saveControllersRef.current) saveController.abort();

    void fetchPatientAccess(patientId, accessToken, controller.signal)
      .then(async (record) => {
        if (controller.signal.aborted || activeEpochRef.current !== epoch) return;
        if (record.patient_id !== patientId) {
          setPatientState(null);
          setAccess({ epoch, status: "invalid" });
          return;
        }
        const reporter = new WalkingReporter(patientId, accessToken, (report) => {
          if (!controller.signal.aborted && activeEpochRef.current === epoch && reporterRef.current === reporter) {
            setWalkingState({ epoch, ...report });
          }
        }, () => {
          if (!controller.signal.aborted && activeEpochRef.current === epoch) {
            setPatientState(null);
            setAccess({ epoch, status: "invalid" });
            controller.abort();
            for (const saveController of saveControllersRef.current) saveController.abort();
          }
        });
        reporterRef.current = reporter;
        await reporter.load();
        if (controller.signal.aborted || activeEpochRef.current !== epoch || reporterRef.current !== reporter) return;
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

    return () => {
      controller.abort();
      reporterRef.current?.dispose();
    };
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
  const walking = walkingState?.epoch === identityEpoch ? walkingState : null;
  const walkClosed = walking?.changed || walking?.stopping || walking?.view?.status === "saved" || walking?.view?.status === "stopped";
  const walkWaiting = walking?.view && walking.view.survey_status !== "stored";
  const activeReporter = reporterRef.current;

  const saveSession = useCallback(
    async (candidate: MetricReading) => {
      const epoch = candidate.epoch;
      if (
        epoch !== activeEpochRef.current ||
        accessToken === null ||
        !tokenIsUsable ||
        !candidate.metrics.gait_detected ||
        candidate.reporter !== reporterRef.current ||
        (candidate.reporter?.blocked && candidate.reporter.view?.status !== "saved") ||
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
        if (candidate.reporter && !await candidate.reporter.verifyScope()) {
          if (controller.signal.aborted) return;
          setSaveMessage({ epoch, kind: "error", text: "The assessment could not be verified. Check your connection or load the current assessment.", ...(!candidate.reporter.blocked ? { retry: candidate } : {}) });
          return;
        }
        if (controller.signal.aborted || candidate.reporter !== reporterRef.current || activeEpochRef.current !== epoch ||
          candidate.reporter?.blocked && candidate.reporter.view?.status !== "saved") return;
        const record = await addPatientAccessSession(
          patientId,
          accessToken,
          candidate.body,
          controller.signal
        );
        if (controller.signal.aborted || activeEpochRef.current !== epoch || candidate.reporter !== reporterRef.current || candidate.reporter?.view?.attempt_id !== candidate.body.attempt_id || candidate.reporter?.blocked && candidate.reporter.view?.status !== "saved") return;
        if (record.patient_id !== patientId) {
          setPatientState(null);
          setAccess({ epoch, status: "invalid" });
          return;
        }

        if (candidate.body.call_id) {
          const stored = record.gait_sessions.find((session) =>
            session.idempotency_key === candidate.id &&
            session.call_id === candidate.body.call_id &&
            session.attempt_id === candidate.body.attempt_id && session.session_id
          );
          if (!stored?.session_id) throw new ApiError("Saved session could not be verified", 500);
          candidate.reporter?.saved(stored.session_id);
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
        if (isAbortError(error) || controller.signal.aborted || activeEpochRef.current !== epoch || candidate.reporter !== reporterRef.current) {
          return;
        }
        if (error instanceof ApiError && [401, 403, 404].includes(error.status)) {
          candidate.reporter?.dispose();
          setPatientState(null);
          setSaveMessage(null);
          setAccess({ epoch, status: "invalid" });
        } else if (error instanceof ApiError && error.status === 409) {
          candidate.reporter?.conflict();
          setSaveMessage({ epoch, kind: "error", text: "This assessment changed or no longer accepts this walk. Load the current assessment or contact your care team." });
        } else if (error instanceof ApiError && error.status === 422) {
          candidate.reporter?.emit("recoverable_error", "save_failed");
          setSaveMessage({
            epoch,
            kind: "error",
            text: "This walk could not be saved. Please record another walk.",
          });
        } else {
          candidate.reporter?.emit("recoverable_error", "save_failed");
          setSaveMessage({
            epoch,
            kind: "error",
            text:
              error instanceof ApiError && error.status === 503
                ? "Saving is temporarily unavailable."
                : "Saving was not confirmed. Check your connection and retry the same walk.",
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
      if (activeEpochRef.current !== epoch || loadAbortRef.current?.signal.aborted || activeReporter !== reporterRef.current || reporterRef.current?.blocked) return;

      let id = metricIdsRef.current.get(metrics);
      if (!id) {
        id = `${source}-${crypto.randomUUID().replaceAll("-", "")}-${++metricSequenceRef.current}`;
        metricIdsRef.current.set(metrics, id);
      }
      let body = bodiesRef.current.get(metrics);
      if (!body) {
        const context = reporterRef.current?.view;
        body = {
          label: `${source === "live" ? "Live" : "Upload"} ${new Date().toLocaleTimeString("en-GB", { hour12: false })}`,
          source, idempotency_key: id, metrics, frames: frames ?? null,
          ...(context ? { call_id: context.call_id, attempt_id: context.attempt_id } : {}),
        };
        bodiesRef.current.set(metrics, body);
      }
      const candidate: MetricReading = {
        epoch,
        id,
        source,
        metrics,
        frames: frames ?? null,
        saved: completedSavesRef.current.has(id),
        reporter: reporterRef.current,
        body,
      };
      setReadingState(candidate);
      setSaveMessage(null);
      if (source === "upload" && metrics.gait_detected) {
        void saveSession(candidate);
      }
    },
    [activeReporter, identityEpoch, saveSession]
  );

  const handleInputReset = useCallback(() => {
    if (activeEpochRef.current !== identityEpoch || loadAbortRef.current?.signal.aborted || activeReporter !== reporterRef.current) return;
    setReadingState((current) => (current?.epoch === identityEpoch ? null : current));
    setSaveMessage((current) => (current?.epoch === identityEpoch ? null : current));
  }, [activeReporter, identityEpoch]);

  const handleLifecycle = useCallback((event: WalkEventName, error?: WalkError) => {
    if (activeEpochRef.current !== identityEpoch || loadAbortRef.current?.signal.aborted || activeReporter !== reporterRef.current) return;
    reporterRef.current?.emit(event, error);
  }, [activeReporter, identityEpoch]);

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
        className="flex min-h-screen items-center justify-center bg-muted text-muted-foreground"
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
  const captureFeedback = (
    <div className="flex flex-col gap-2">
      {metrics && !metrics.gait_detected && (
        <p role="alert" className="rounded-2xl bg-pastel-peach/70 px-3 py-2 text-xs text-foreground">
          No walking detected — walk across the frame or upload a clip with walking before saving.
        </p>
      )}
      {reading?.source === "live" && (
        <div className="flex items-center gap-2 rounded-2xl bg-muted p-3 shadow-pillow-inset">
          {reading.saved ? (
            <CheckCircle2 className="h-4 w-4 shrink-0 text-foreground" />
          ) : (
            <Save className="h-4 w-4 shrink-0 text-muted-foreground" />
          )}
          <span className="flex-1 text-xs text-muted-foreground">
            {reading.saved ? "This walk is saved" : "Save this walk to your record"}
          </span>
          <button
            type="button"
            onClick={() => void saveSession(reading)}
            disabled={isSaving || reading.saved || !reading.metrics.gait_detected || Boolean(walkClosed) || Boolean(walkWaiting)}
            className="rounded-full bg-pastel-sage px-3 py-1 text-xs font-medium text-foreground shadow-pillow-sm hover:bg-pastel-sagedeep disabled:opacity-50"
          >
            {isSaving ? "Saving…" : reading.saved ? "Saved" : "Save this walk"}
          </button>
        </div>
      )}
      {currentSaveMessage && (
        <div
          role={currentSaveMessage.kind === "error" ? "alert" : "status"}
          className={`flex items-center justify-between gap-3 rounded-2xl px-3 py-2 text-xs text-foreground ${
            currentSaveMessage.kind === "error" ? "bg-pastel-peach/70" : "bg-pastel-green"
          }`}
        >
          <span>{currentSaveMessage.text}</span>
          {currentSaveMessage.retry && (
            <button
              type="button"
              disabled={savingReadingId === currentSaveMessage.retry.id}
              onClick={() => void saveSession(currentSaveMessage.retry!)}
              className="flex shrink-0 items-center gap-1 rounded-full bg-card px-2 py-1 font-medium shadow-pillow-sm disabled:opacity-50"
            >
              <RefreshCw className="h-3 w-3" />
              Retry save
            </button>
          )}
        </div>
      )}
      {reading?.source === "upload" && isSaving && (
        <p role="status" className="flex items-center gap-2 text-xs text-muted-foreground">
          <Loader2 className="h-3.5 w-3.5 animate-spin" /> Saving analyzed walk…
        </p>
      )}
    </div>
  );

  return (
    <main className="min-h-screen p-6 text-foreground">
      <header className="mx-auto mb-8 flex max-w-6xl flex-wrap items-center justify-between gap-4">
        <div className="flex items-center gap-3">
          <Image src="/sana-mark.png" alt="Sana" width={44} height={44} priority className="h-11 w-11 drop-shadow-sm" />
          <div>
            <h1 className="text-2xl font-bold">Sana</h1>
            <p className="text-xs text-muted-foreground">Your secure walking assessment</p>
          </div>
        </div>
        <span className="flex items-center gap-2 rounded-full border border-border bg-card px-3 py-1.5 text-xs text-foreground">
          <User className="h-3.5 w-3.5 text-muted-foreground" />
          {patient.name ?? "Patient"}
        </span>
      </header>

      <div className="mx-auto flex max-w-6xl flex-col gap-6">
      <div className="rounded-3xl bg-pastel-sage/60 p-5 shadow-pillow-sm">
        <h2 className="text-sm font-medium text-foreground">Complete one walking test</h2>
        <p className="mt-1 text-xs leading-relaxed text-muted-foreground">
          Use the live camera or upload a walking video. For live capture, wait for calibration, walk across the frame only if safe, then choose Save this walk.
          Uploads are analyzed and saved automatically when walking is detected. Completion is confirmed only after the server saves your walk.
        </p>
        {walking?.view && <p className="mt-2 text-xs">Walking status: {walking.view.status === "saved" && walking.view.session_id ? "Saved to your care team" : walking.view.status.replaceAll("_", " ")}</p>}
        {walking?.stopping && walking.view?.status !== "stopped" && <p className="mt-2 text-xs">Stop requested. Waiting for server confirmation.</p>}
        {walkWaiting && <p className="mt-2 text-xs">Your survey must finish before this walking assessment can begin.</p>}
        {walking?.warning && <p role="status" className="mt-2 text-xs text-foreground">{walking.warning}</p>}
        {walking?.changed ? (
          <button className="mt-2 rounded border px-3 py-1 text-xs" onClick={() => setRetryNonce((value) => value + 1)}>Load current assessment</button>
        ) : walking?.warning && (
          <button className="mt-2 rounded border px-3 py-1 text-xs" onClick={() => { void reporterRef.current?.verifyScope(); void reporterRef.current?.flush(); }}>Retry progress sync</button>
        )}
        {walking?.view && !walkClosed && !walkWaiting && (
          <button className="ml-2 mt-2 rounded border px-3 py-1 text-xs" disabled={savingReadingId !== null}
            onClick={() => {
              if (window.confirm("Stop this assessment? It cannot be restarted without a new assessment from your care team.")) void reporterRef.current?.stop();
            }}>Stop assessment</button>
        )}
      </div>

      <section>
        {!walkClosed && !walkWaiting && <WebcamFeed
          key={identityEpoch}
          onMetrics={handleMetrics}
          onInputReset={handleInputReset}
          onLifecycle={handleLifecycle}
        />}
        <div className="mt-3">{captureFeedback}</div>
      </section>

        <section className="grid grid-cols-2 gap-4 lg:grid-cols-5">
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
          <MetricCard
            title="Cadence"
            value={metrics ? `${metrics.cadence_steps_per_min.toFixed(0)} steps/min` : "—"}
            icon={<Activity className="h-4 w-4" />}
          />
        </section>

        <section>
          <TrendGraph sessions={trendSessions} />
        </section>
      </div>
    </main>
  );
}
