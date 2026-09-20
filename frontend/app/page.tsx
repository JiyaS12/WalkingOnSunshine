"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  Activity,
  ArrowRight,
  Loader2,
  Sparkles,
  Stethoscope,
  User,
} from "lucide-react";
import {
  API_URL,
  ApiError,
  PatientSummary,
  ensureDemoPatient,
  fetchPatients,
} from "./lib/api";

export default function Home() {
  const [backendUp, setBackendUp] = useState<boolean | null>(null);
  const [patients, setPatients] = useState<PatientSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [idInput, setIdInput] = useState("");
  const [creating, setCreating] = useState(false);
  const [goError, setGoError] = useState<string | null>(null);
  const router = useRouter();

  useEffect(() => {
    fetch(`${API_URL}/api/health`)
      .then((r) => setBackendUp(r.ok))
      .catch(() => setBackendUp(false));
    fetchPatients()
      .then(setPatients)
      .catch(() => undefined)
      .finally(() => setLoading(false));
  }, []);

  const go = async (override?: string) => {
    const id = (override ?? idInput).trim();
    if (!id || creating) return;
    setCreating(true);
    setGoError(null);
    try {
      await ensureDemoPatient(id);
      router.push(`/patient/${encodeURIComponent(id)}`);
    } catch (err) {
      setGoError(
        err instanceof ApiError
          ? err.message
          : "Could not reach the backend"
      );
    } finally {
      setCreating(false);
    }
  };

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
          <Link
            href="/doctor"
            className="flex items-center gap-2 rounded-full border border-slate-700 bg-slate-900 px-3 py-1.5 text-xs text-slate-200 hover:bg-slate-800"
          >
            <Stethoscope className="h-3.5 w-3.5 text-slate-400" />
            Doctor&apos;s Portal
          </Link>
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

      <div className="mx-auto max-w-xl">
        <div className="rounded-xl border border-slate-700 bg-slate-900 p-5">
          <h2 className="mb-1 text-sm font-medium text-slate-200">
            Patient screening
          </h2>
          <p className="mb-4 text-xs text-slate-400">
            Open your screening link (sent by text) or enter your patient ID.
          </p>

          <button
            onClick={() => {
              setIdInput("RGN-0417");
              void go("RGN-0417");
            }}
            disabled={creating}
            className="mb-3 flex w-full items-center justify-center gap-2 rounded-lg border border-emerald-500/60 px-4 py-2 text-sm font-medium text-emerald-300 hover:bg-emerald-600/10 disabled:opacity-50"
          >
            <Sparkles className="h-4 w-4" />
            Load Demo Patient RGN-0417
          </button>

          <div className="mb-1 flex gap-2">
            <input
              value={idInput}
              onChange={(e) => setIdInput(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && void go()}
              placeholder="Enter your patient ID"
              disabled={creating}
              className="flex-1 rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-200 outline-none placeholder:text-slate-500 disabled:opacity-50"
            />
            <button
              onClick={() => void go()}
              disabled={!idInput.trim() || creating}
              className="flex items-center gap-1.5 rounded-lg bg-emerald-600 px-4 py-2 text-sm font-medium text-white hover:bg-emerald-500 disabled:opacity-50"
            >
              {creating ? "Opening…" : "Go"} <ArrowRight className="h-4 w-4" />
            </button>
          </div>
          <p className="mb-4 text-[11px] text-slate-500">
            Any new ID auto-creates a demo profile (pain 3/10, no prior falls)
            so you can test right away.
          </p>
          {goError && <p className="mb-4 text-xs text-rose-300">{goError}</p>}

          <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-400">
            Patient links
          </h3>
          {loading && (
            <p className="flex items-center gap-2 text-xs text-slate-400">
              <Loader2 className="h-3.5 w-3.5 animate-spin" /> Loading…
            </p>
          )}
          {!loading && patients.length === 0 && (
            <p className="text-xs text-slate-500">No patients found.</p>
          )}
          <ul className="flex flex-col gap-1.5">
            {patients.map((p) => (
              <li key={p.patient_id}>
                <Link
                  href={`/patient/${encodeURIComponent(p.patient_id)}`}
                  className="flex items-center justify-between rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm hover:bg-slate-800"
                >
                  <span className="text-slate-200">
                    {p.patient_id} — {p.name ?? "unknown"}
                  </span>
                  <User className="h-3.5 w-3.5 text-slate-500" />
                </Link>
              </li>
            ))}
          </ul>
        </div>
      </div>
    </main>
  );
}
