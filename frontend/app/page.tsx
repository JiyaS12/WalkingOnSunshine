import Image from "next/image";
import Link from "next/link";
import { Clock3, MessageSquareText, ShieldCheck, Stethoscope } from "lucide-react";

export default function Home() {
  return (
    <main className="min-h-screen p-6 text-foreground">
      <header className="mx-auto mb-12 flex max-w-3xl flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <Image src="/sana-mark.png" alt="Sana" width={44} height={44} priority className="h-11 w-11 drop-shadow-sm" />
          <div>
            <h1 className="text-2xl font-bold">Sana</h1>
            <p className="text-xs text-muted-foreground">
              Secure walking assessments
            </p>
          </div>
        </div>
        <Link href="/doctor" className="flex items-center gap-2 rounded-full bg-card px-3 py-1.5 text-xs text-foreground shadow-pillow-sm hover:bg-pastel-sand">
          <Stethoscope className="h-3.5 w-3.5" />
          Doctor&apos;s Portal
        </Link>
      </header>

      <section className="mx-auto max-w-3xl rounded-[2.25rem] bg-card p-8 shadow-pillow">
        <div className="mx-auto max-w-xl text-center">
          <ShieldCheck className="mx-auto mb-4 h-11 w-11 text-foreground" />
          <h2 className="text-2xl font-semibold text-foreground">
            Open your secure screening link
          </h2>
          <p className="mt-3 text-sm leading-relaxed text-muted-foreground">
            Your care team will send a personalized link by text message. Open
            that complete link on the device you want to use for your walking
            assessment.
          </p>
        </div>
        <div className="mt-8 grid gap-3 sm:grid-cols-2">
          <div className="rounded-xl border border-border bg-muted p-4">
            <MessageSquareText className="mb-2 h-5 w-5 text-foreground" />
            <p className="text-sm font-medium text-foreground">Use the full link</p>
            <p className="mt-1 text-xs leading-relaxed text-muted-foreground">
              A patient ID alone cannot open an assessment. If the link is
              incomplete, ask your care team to resend it.
            </p>
          </div>
          <div className="rounded-xl border border-border bg-muted p-4">
            <Clock3 className="mb-2 h-5 w-5 text-foreground" />
            <p className="text-sm font-medium text-foreground">Links expire</p>
            <p className="mt-1 text-xs leading-relaxed text-muted-foreground">
              Expiration protects your information. Your care team can issue a
              new link when needed.
            </p>
          </div>
        </div>
      </section>
      <footer className="mx-auto mt-8 flex max-w-3xl justify-center gap-4 text-xs text-muted-foreground">
        <Link href="/privacy" className="underline-offset-2 hover:underline">Privacy Policy</Link>
        <Link href="/terms" className="underline-offset-2 hover:underline">Terms &amp; Conditions</Link>
      </footer>
    </main>
  );
}
