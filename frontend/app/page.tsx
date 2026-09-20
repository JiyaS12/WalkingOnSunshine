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
    <main className="min-h-screen p-6 text-slate-600">
      <header className="mb-8 flex flex-wrap items-center justify-between gap-4">
        <div className="flex items-center gap-3">
          <Activity className="h-8 w-8 text-pastel-blue" />
          <div>
            <h1 className="text-2xl font-bold text-slate-700">GaitGuard AI</h1>
            <p className="text-xs text-slate-400">
              Clinical gait monitoring & fall-risk analytics
            </p>
          </div>
        </div>
        <div className="flex items-center gap-3">
          <Link
            href="/doctor"
            className="flex items-center gap-2 rounded-full border-0 bg-white px-3 py-1.5 text-xs text-slate-600 shadow-pillow-sm hover:bg-pastel-sand"
          >
            <Stethoscope className="h-3.5 w-3.5 text-slate-400" />
            Doctor&apos;s Portal
          </Link>
          <span
            className={`rounded-full border-0 px-3 py-1.5 text-xs font-medium shadow-pillow-sm ${
              backendUp
                ? "bg-[#E4F5D6] text-[#4F7A3A]"
                : "bg-pastel-peach text-[#9A4B32]"
            }`}
          >
            Backend: {backendUp === null ? "checking…" : backendUp ? "connected" : "offline"}
          </span>
        </div>
      </header>

      <div className="mx-auto max-w-xl">
        <div className="rounded-3xl bg-gradient-to-b from-white to-[#FDFBF7] p-6 shadow-pillow">
          <h2 className="mb-1 text-sm font-medium text-slate-700">
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
            className="mb-3 flex w-full items-center justify-center gap-2 rounded-2xl border-0 bg-pastel-blue px-4 py-2 text-sm font-medium text-slate-700 shadow-pillow-sm disabled:opacity-50"
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
              className="flex-1 rounded-2xl border-0 bg-muted px-3 py-2 text-sm text-slate-600 shadow-pillow-inset outline-none placeholder:text-slate-400 disabled:opacity-50"
            />
            <button
              onClick={() => void go()}
              disabled={!idInput.trim() || creating}
              className="flex items-center gap-1.5 rounded-2xl bg-pastel-blue px-4 py-2 text-sm font-medium text-slate-700 shadow-pillow-sm disabled:opacity-50"
            >
              {creating ? "Opening…" : "Go"} <ArrowRight className="h-4 w-4" />
            </button>
          </div>
          <p className="mb-4 text-[11px] text-slate-500">
            Any new ID auto-creates a demo profile (pain 3/10, no prior falls)
            so you can test right away.
          </p>
          {goError && <p className="mb-4 text-xs text-[#9A4B32]">{goError}</p>}

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
                  className="flex items-center justify-between rounded-2xl border-0 bg-muted px-3 py-2 text-sm shadow-pillow-inset hover:bg-pastel-sand"
                >
                  <span className="text-slate-600">
                    {p.patient_id} — {p.name ?? "unknown"}
                  </span>
                  <User className="h-3.5 w-3.5 text-slate-400" />
                </Link>
              </li>
            ))}
          </ul>
        </div>
      </div>
    </main>
  );
}
