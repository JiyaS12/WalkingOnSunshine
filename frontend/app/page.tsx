"use client";

import { useEffect, useState } from "react";
import Image from "next/image";
import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  ArrowRight,
  Sparkles,
  Stethoscope,
} from "lucide-react";
import {
  API_URL,
  ApiError,
  ensureDemoPatient,
} from "./lib/api";

export default function Home() {
  const [backendUp, setBackendUp] = useState<boolean | null>(null);
  const [idInput, setIdInput] = useState("");
  const [creating, setCreating] = useState(false);
  const [goError, setGoError] = useState<string | null>(null);
  const [authRequired, setAuthRequired] = useState(false);
  const router = useRouter();

  useEffect(() => {
    fetch(`${API_URL}/api/health`)
      .then((r) => setBackendUp(r.ok))
      .catch(() => setBackendUp(false));
  }, []);

  const go = async (override?: string) => {
    const id = (override ?? idInput).trim();
    if (!id || creating) return;
    setCreating(true);
    setGoError(null);
    setAuthRequired(false);
    try {
      await ensureDemoPatient(id);
      router.push(`/patient/${encodeURIComponent(id)}`);
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        setAuthRequired(true);
        return;
      }
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
    <main className="min-h-screen p-6 text-foreground">
      <header className="mb-8 flex flex-wrap items-center justify-between gap-4">
        <div className="flex items-center gap-3">
          <Image src="/sana-mark.png" alt="Sana" width={44} height={44} priority className="h-11 w-11 drop-shadow-sm" />
          <div>
            <h1 className="text-2xl font-bold text-foreground">Sana</h1>
            <p className="text-xs text-muted-foreground">
              Helping you Heal
            </p>
          </div>
        </div>
        <div className="flex items-center gap-3">
          <Link
            href="/doctor"
            className="flex items-center gap-2 rounded-full border-0 bg-card px-3 py-1.5 text-xs text-foreground shadow-pillow-sm hover:bg-pastel-sand"
          >
            <Stethoscope className="h-3.5 w-3.5 text-muted-foreground" />
            Doctor&apos;s Portal
          </Link>
          <span
            className={`rounded-full border-0 px-3 py-1.5 text-xs font-medium shadow-pillow-sm ${
              backendUp
                ? "bg-pastel-green text-foreground"
                : "bg-pastel-peach text-foreground"
            }`}
          >
            Backend: {backendUp === null ? "checking…" : backendUp ? "connected" : "offline"}
          </span>
        </div>
      </header>

      <div className="mx-auto max-w-xl">
        <div className="overflow-hidden rounded-[2.25rem] bg-card shadow-pillow">
          <div className="rounded-t-[2.25rem] bg-gradient-to-r from-pastel-sage via-pastel-green to-pastel-peach/70 px-6 py-3">
            <h2 className="text-sm font-medium text-foreground">
              Patient screening
            </h2>
            <p className="mt-0.5 text-xs text-foreground/70">
              Open your screening link (sent by text) or enter your patient ID.
            </p>
          </div>
          <div className="p-6">

          <button
            onClick={() => {
              setIdInput("RGN-0417");
              void go("RGN-0417");
            }}
            disabled={creating}
            className="mb-3 flex w-full items-center justify-center gap-2 rounded-full border-0 bg-pastel-sage px-4 py-2 text-sm font-medium text-foreground shadow-pillow-sm disabled:opacity-50"
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
              className="flex-1 rounded-2xl border-0 bg-muted px-3 py-2 text-sm text-foreground shadow-pillow-inset outline-none placeholder:text-muted-foreground disabled:opacity-50"
            />
            <button
              onClick={() => void go()}
              disabled={!idInput.trim() || creating}
              className="flex items-center gap-1.5 rounded-full bg-pastel-sage px-4 py-2 text-sm font-medium text-foreground shadow-pillow-sm disabled:opacity-50"
            >
              {creating ? "Opening…" : "Go"} <ArrowRight className="h-4 w-4" />
            </button>
          </div>
          <p className="text-[11px] text-muted-foreground">
            Any new ID auto-creates a demo profile (pain 3/10, no prior falls)
            so you can test right away.
          </p>
          {authRequired && (
            <p className="mt-3 text-xs text-foreground">
              Patient records are protected.{" "}
              <Link
                href={`/doctor?next=${encodeURIComponent("/")}`}
                className="font-medium underline underline-offset-2"
              >
                Sign in as a clinician
              </Link>{" "}
              to open or create a screening.
            </p>
          )}
          {goError && <p className="text-xs text-foreground">{goError}</p>}

          </div>
        </div>
      </div>
    </main>
  );
}
