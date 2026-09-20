"use client";

import { useEffect, useState } from "react";
import {
  Database,
  Loader2,
  RefreshCw,
  Search,
  ShieldCheck,
} from "lucide-react";
import {
  ApiError,
  fetchDatabaseSnapshot,
  type DatabasePatient,
  type DatabaseSnapshot,
  type DatabaseWalk,
} from "../lib/api";
import SurveyDetails from "./SurveyDetails";

const stamp = (value?: string | null) => {
  if (!value) return "Not recorded";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
};
const number = (value?: number | null, unit = "") =>
  value == null || !Number.isFinite(value)
    ? "Not recorded"
    : `${value.toFixed(2)}${unit}`;
const words = (value?: string | null) =>
  value ? value.replaceAll("_", " ") : "Not recorded";

function WalkingResult({ walk }: { walk: DatabaseWalk }) {
  return (
    <div className="space-y-3 text-sm">
      <p className="break-words text-xs text-muted-foreground">
        Session {walk.session_id ?? "Legacy"} · {stamp(walk.recorded_at)} ·{" "}
        {walk.source}
      </p>
      <dl className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        {[
          ["Stride length", number(walk.metrics.stride_length_m, " m")],
          ["Asymmetry", number(walk.metrics.asymmetry_pct, "%")],
          ["Cadence", number(walk.metrics.cadence_steps_per_min, " steps/min")],
          ["Fall-risk estimate", number(walk.metrics.fall_risk_score)],
        ].map(([label, value]) => (
          <div key={label} className="rounded-2xl bg-muted p-3">
            <dt className="text-xs text-muted-foreground">{label}</dt>
            <dd className="mt-1 font-semibold">{value}</dd>
          </div>
        ))}
      </dl>
      <p className="text-xs text-muted-foreground">
        Prototype measurements, not a diagnosis. Missing measurements are not
        zero. {walk.stored_frame_count} derived frames stored; raw video is not
        included.
      </p>
    </div>
  );
}

function PatientDetails({ patient }: { patient: DatabasePatient }) {
  const [section, setSection] = useState("Calls");
  return (
    <section
      aria-label="Selected database patient"
      className="rounded-[2rem] bg-card p-5 shadow-pillow sm:p-6"
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="text-xs uppercase tracking-wider text-muted-foreground">
            Patient record
          </p>
          <h3 className="mt-1 text-xl font-semibold">
            {patient.name || patient.patient_id}
          </h3>
          <p className="mt-1 break-all font-mono text-xs text-muted-foreground">
            {patient.patient_id}
          </p>
        </div>
        <span className="rounded-full bg-muted px-3 py-1 text-xs">
          {patient.condition_category
            ? words(patient.condition_category)
            : "Condition not selected"}
        </span>
      </div>
      <nav
        aria-label="Database record sections"
        className="my-5 flex flex-wrap gap-2"
      >
        {["Calls", "Surveys", "Walking", "Record JSON"].map((name) => (
          <button
            key={name}
            type="button"
            aria-pressed={section === name}
            onClick={() => setSection(name)}
            className={`rounded-full px-4 py-2 text-sm ${section === name ? "bg-pastel-sage font-semibold" : "bg-muted hover:bg-pastel-sand"}`}
          >
            {name}
            {name === "Calls"
              ? ` (${patient.calls.length})`
              : name === "Surveys"
                ? ` (${patient.surveys.length})`
                : name === "Walking"
                  ? ` (${patient.gait_sessions.length})`
                  : ""}
          </button>
        ))}
      </nav>
      <div className="space-y-4">
        {section === "Calls" &&
          (patient.calls.length ? (
            [...patient.calls].reverse().map((call) => {
              const surveys = patient.surveys.filter(
                (survey) => survey.call_id === call.call_id,
              );
              const walks = patient.gait_sessions.filter(
                (walk) =>
                  walk.call_id === call.call_id &&
                  walk.attempt_id === call.attempt_id,
              );
              return (
                <article
                  key={call.call_id}
                  className="space-y-3 rounded-2xl border border-border p-4"
                >
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <h4 className="font-semibold">
                      {call.destination_phone ?? "Phone not recorded"}
                    </h4>
                    <span className="rounded-full bg-muted px-3 py-1 text-xs">
                      Call: {words(call.call_status)}
                    </span>
                  </div>
                  <p className="break-all text-xs text-muted-foreground">
                    {stamp(call.created_at)} · Call {call.call_id}
                    <br />
                    Attempt {call.attempt_id}
                  </p>
                  <div className="flex flex-wrap gap-2 text-xs">
                    {[
                      ["Survey", call.survey_status],
                      ["Text", call.sms_status],
                      ["Walking", call.walking.status],
                    ].map(([label, value]) => (
                      <span
                        key={label}
                        className="rounded-full bg-pastel-blue/40 px-3 py-1"
                      >
                        {label}: {words(value)}
                      </span>
                    ))}
                  </div>
                  {call.error_code && (
                    <p className="text-sm text-[#9A4B32]">
                      Reported issue: {words(call.error_code)}
                    </p>
                  )}
                  <details className="rounded-xl bg-muted p-3">
                    <summary className="cursor-pointer text-sm font-medium">
                      Linked survey answers ({surveys.length})
                    </summary>
                    <div className="mt-3 space-y-4">
                      {surveys.length ? (
                        surveys.map((survey, index) => (
                          <SurveyDetails
                            key={survey.survey_id ?? index}
                            survey={survey}
                          />
                        ))
                      ) : (
                        <p className="text-sm text-muted-foreground">
                          No stored survey for this call yet.
                        </p>
                      )}
                    </div>
                  </details>
                  <details className="rounded-xl bg-muted p-3">
                    <summary className="cursor-pointer text-sm font-medium">
                      Linked walking results ({walks.length})
                    </summary>
                    <div className="mt-3 space-y-4">
                      {walks.length ? (
                        walks.map((walk, index) => (
                          <WalkingResult
                            key={walk.session_id ?? index}
                            walk={walk}
                          />
                        ))
                      ) : (
                        <p className="text-sm text-muted-foreground">
                          No saved walk for this call and attempt yet.
                        </p>
                      )}
                    </div>
                  </details>
                  <details className="text-xs">
                    <summary className="cursor-pointer">
                      Walking progress ({call.walking.events.length} events)
                    </summary>
                    <ul className="mt-2 space-y-1">
                      {call.walking.events.map((event) => (
                        <li key={event.event_id}>
                          {event.sequence}. {words(event.event)} ·{" "}
                          {stamp(event.received_at)}
                          {event.error_code
                            ? ` · ${words(event.error_code)}`
                            : ""}
                        </li>
                      ))}
                    </ul>
                    {!call.walking.events.length && (
                      <p className="mt-2 text-muted-foreground">
                        No page activity yet.
                      </p>
                    )}
                  </details>
                </article>
              );
            })
          ) : (
            <p className="rounded-2xl bg-muted p-6 text-sm text-muted-foreground">
              No calls yet. Start a call from the Clinical dashboard when you
              are ready.
            </p>
          ))}
        {section === "Surveys" &&
          (patient.surveys.length ? (
            [...patient.surveys].reverse().map((survey, index) => (
              <article
                key={survey.survey_id ?? index}
                className="rounded-2xl border border-border p-4"
              >
                <SurveyDetails survey={survey} />
              </article>
            ))
          ) : (
            <p className="text-sm text-muted-foreground">
              No surveys saved for this patient yet.
            </p>
          ))}
        {section === "Walking" &&
          (patient.gait_sessions.length ? (
            [...patient.gait_sessions].reverse().map((walk, index) => (
              <article
                key={walk.session_id ?? index}
                className="space-y-3 rounded-2xl border border-border p-4"
              >
                <p className="break-all text-xs text-muted-foreground">
                  Call {walk.call_id ?? "Not linked"} · Attempt{" "}
                  {walk.attempt_id ?? "Not linked"}
                </p>
                <WalkingResult walk={walk} />
              </article>
            ))
          ) : (
            <p className="text-sm text-muted-foreground">
              No walking results saved for this patient yet.
            </p>
          ))}
        {section === "Record JSON" && (
          <>
            <p className="text-xs text-muted-foreground">
              Read-only clinical projection. Credentials, private internal
              fields, link tokens and raw frame arrays are excluded.
            </p>
            <pre
              aria-label="Sanitized patient JSON"
              className="max-h-[32rem] overflow-auto rounded-2xl bg-muted p-4 text-xs"
            >
              {JSON.stringify(patient, null, 2)}
            </pre>
          </>
        )}
      </div>
    </section>
  );
}

export default function ClinicianDatabase({
  onAuthFailure,
}: {
  onAuthFailure: (error: unknown) => boolean;
}) {
  const [snapshot, setSnapshot] = useState<DatabaseSnapshot | null>(null);
  const [query, setQuery] = useState("");
  const [offset, setOffset] = useState(0);
  const [refresh, setRefresh] = useState(0);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    let pending = false;
    let blocked = false;
    let controller: AbortController | null = null;
    setSnapshot(null);
    setError(null);
    setLoading(true);
    const load = async () => {
      if (!active || pending || blocked) return;
      pending = true;
      controller = new AbortController();
      setLoading(true);
      try {
        const result = await fetchDatabaseSnapshot(
          query,
          offset,
          controller.signal,
        );
        if (!active) return;
        setSnapshot(result);
        setError(null);
      } catch (err) {
        if (!active) return;
        setSnapshot(null);
        if (err instanceof ApiError && [401, 403].includes(err.status)) {
          blocked = true;
          setError("Clinician access expired or was denied. Sign in again.");
          onAuthFailure(err);
        } else {
          setError(
            "Database records could not be loaded. Check the connection and retry; no local fallback is shown.",
          );
        }
      } finally {
        pending = false;
        if (active) setLoading(false);
      }
    };
    const initial = setTimeout(() => void load(), query ? 250 : 0);
    const poll = () => {
      if (document.visibilityState === "visible") void load();
    };
    const interval = setInterval(poll, 10_000);
    document.addEventListener("visibilitychange", poll);
    return () => {
      active = false;
      clearTimeout(initial);
      clearInterval(interval);
      document.removeEventListener("visibilitychange", poll);
      controller?.abort();
    };
  }, [query, offset, refresh, onAuthFailure]);

  const patient =
    snapshot?.patients.find((row) => row.patient_id === selectedId) ??
    snapshot?.patients[0];
  return (
    <section aria-label="Clinician database" className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-4 rounded-[2rem] bg-pastel-blue/30 p-6">
        <div className="flex items-start gap-3">
          <Database className="mt-1 h-6 w-6" />
          <div>
            <h2 className="text-2xl font-semibold">Database</h2>
            <p className="mt-1 text-sm text-muted-foreground">
              Patient records, survey answers and walking results. No Supabase
              login needed.
            </p>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <span className="flex items-center gap-1 rounded-full bg-card px-3 py-2 text-xs">
            <ShieldCheck className="h-4 w-4" />
            Read only
          </span>
          <button
            type="button"
            disabled={loading}
            onClick={() => setRefresh((n) => n + 1)}
            className="flex items-center gap-2 rounded-full bg-card px-4 py-2 text-sm shadow-pillow-sm disabled:opacity-50"
          >
            <RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} />
            Refresh records
          </button>
        </div>
      </div>
      <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-muted-foreground">
        <p>
          {snapshot
            ? `${snapshot.source === "supabase" ? "Supabase · integrated patient records" : "Local JSON demo store · not Supabase"} · Read ${stamp(snapshot.read_at)}`
            : "Checking the configured patient store…"}
        </p>
        <p>Refreshes every 10 seconds while visible</p>
      </div>
      {snapshot && (
        <dl className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          {Object.entries(snapshot.totals).map(([name, count]) => (
            <div
              key={name}
              className="rounded-2xl bg-card p-4 shadow-pillow-sm"
            >
              <dt className="text-xs capitalize text-muted-foreground">
                {name === "walking" ? "Saved walks" : name}
              </dt>
              <dd className="mt-1 text-2xl font-semibold">{count}</dd>
            </div>
          ))}
        </dl>
      )}
      <div className="rounded-[2rem] bg-card p-5 shadow-pillow">
        <label className="flex items-center gap-2 rounded-2xl bg-muted px-3 py-2">
          <Search className="h-4 w-4 shrink-0 text-muted-foreground" />
          <span className="sr-only">Search database</span>
          <input
            value={query}
            maxLength={200}
            onChange={(event) => {
              setQuery(event.target.value);
              setOffset(0);
            }}
            placeholder="Search patient, phone number or call ID…"
            className="min-w-0 flex-1 bg-transparent py-1 text-sm outline-none"
          />
        </label>
        {error && (
          <p
            role="alert"
            className="mt-4 rounded-xl bg-pastel-peach/50 p-4 text-sm"
          >
            {error}
          </p>
        )}
        {loading && !snapshot && (
          <p role="status" className="mt-5 flex items-center gap-2 text-sm">
            <Loader2 className="h-4 w-4 animate-spin" />
            Loading database records…
          </p>
        )}
        {snapshot && (
          <>
            <div className="mt-4 overflow-x-auto">
              <table className="w-full text-left text-sm">
                <caption className="sr-only">Database patient records</caption>
                <thead className="border-b border-border text-xs text-muted-foreground">
                  <tr>
                    {[
                      "Patient",
                      "Phone (latest call)",
                      "Calls",
                      "Surveys",
                      "Walks",
                    ].map((heading) => (
                      <th
                        key={heading}
                        scope="col"
                        className="px-3 py-3 font-medium"
                      >
                        {heading}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {snapshot.patients.map((row) => (
                    <tr
                      key={row.patient_id}
                      className={`border-b border-border/50 ${patient?.patient_id === row.patient_id ? "bg-pastel-sage/30" : ""}`}
                    >
                      <td className="px-3 py-3">
                        <button
                          type="button"
                          onClick={() => setSelectedId(row.patient_id)}
                          aria-pressed={patient?.patient_id === row.patient_id}
                          className="text-left hover:underline"
                        >
                          <span className="block font-semibold">
                            {row.name || row.patient_id}
                          </span>
                          <span className="font-mono text-xs text-muted-foreground">
                            {row.patient_id}
                          </span>
                        </button>
                      </td>
                      <td className="whitespace-nowrap px-3 py-3">
                        {row.calls.at(-1)?.destination_phone ?? "Not recorded"}
                      </td>
                      <td className="px-3 py-3">{row.calls.length}</td>
                      <td className="px-3 py-3">{row.surveys.length}</td>
                      <td className="px-3 py-3">{row.gait_sessions.length}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {!snapshot.patients.length && (
              <p className="py-8 text-center text-sm text-muted-foreground">
                {query
                  ? "No patients match your search."
                  : "No patient records on this page."}
              </p>
            )}
            <div className="mt-4 flex items-center justify-between gap-2 text-xs">
              <p>
                {snapshot.total} matching patient
                {snapshot.total === 1 ? "" : "s"} · Page{" "}
                {Math.floor(offset / 25) + 1}
              </p>
              <div className="flex gap-2">
                <button
                  type="button"
                  disabled={!offset || loading}
                  onClick={() => setOffset(Math.max(0, offset - 25))}
                  className="rounded-full bg-muted px-3 py-2 disabled:opacity-40"
                >
                  Previous page
                </button>
                <button
                  type="button"
                  disabled={offset + 25 >= snapshot.total || loading}
                  onClick={() => setOffset(offset + 25)}
                  className="rounded-full bg-muted px-3 py-2 disabled:opacity-40"
                >
                  Next page
                </button>
              </div>
            </div>
          </>
        )}
      </div>
      {patient && <PatientDetails key={patient.patient_id} patient={patient} />}
    </section>
  );
}
