import { Activity, Clock3, MessageSquareText, ShieldCheck } from "lucide-react";

export default function Home() {
  return (
    <main className="min-h-screen bg-slate-950 p-6 text-slate-100">
      <header className="mx-auto mb-12 flex max-w-3xl items-center gap-3">
        <Activity className="h-8 w-8 text-emerald-400" />
        <div className="flex items-center gap-3">
          <div>
            <h1 className="text-2xl font-bold">GaitGuard AI</h1>
            <p className="text-xs text-slate-400">
              Secure walking assessments
            </p>
          </div>
        </div>
      </header>

      <section className="mx-auto max-w-3xl rounded-2xl border border-slate-700 bg-slate-900 p-8 shadow-2xl shadow-slate-950/40">
        <div className="mx-auto max-w-xl text-center">
          <ShieldCheck className="mx-auto mb-4 h-11 w-11 text-emerald-400" />
          <h2 className="text-2xl font-semibold text-slate-100">
            Open your secure screening link
          </h2>
          <p className="mt-3 text-sm leading-relaxed text-slate-400">
            Your care team will send a personalized link by text message. Open
            that complete link on the device you want to use for your walking
            assessment.
          </p>
        </div>
        <div className="mt-8 grid gap-3 sm:grid-cols-2">
          <div className="rounded-xl border border-slate-700 bg-slate-950 p-4">
            <MessageSquareText className="mb-2 h-5 w-5 text-sky-400" />
            <p className="text-sm font-medium text-slate-200">Use the full link</p>
            <p className="mt-1 text-xs leading-relaxed text-slate-500">
              A patient ID alone cannot open an assessment. If the link is
              incomplete, ask your care team to resend it.
            </p>
          </div>
          <div className="rounded-xl border border-slate-700 bg-slate-950 p-4">
            <Clock3 className="mb-2 h-5 w-5 text-amber-400" />
            <p className="text-sm font-medium text-slate-200">Links expire</p>
            <p className="mt-1 text-xs leading-relaxed text-slate-500">
              Expiration protects your information. Your care team can issue a
              new link when needed.
            </p>
          </div>
        </div>
      </section>
    </main>
  );
}
