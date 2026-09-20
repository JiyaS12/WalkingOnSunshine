import Image from "next/image";
import Link from "next/link";

export const BRAND = "Sana";

export function LegalPage({
  title,
  updated,
  children,
}: Readonly<{ title: string; updated: string; children: React.ReactNode }>) {
  return (
    <main className="min-h-screen p-6 text-foreground">
      <header className="mx-auto mb-8 flex max-w-3xl flex-wrap items-center justify-between gap-3">
        <Link href="/" className="flex items-center gap-3">
          <Image src="/sana-mark.png" alt={BRAND} width={44} height={44} priority className="h-11 w-11 drop-shadow-sm" />
          <div>
            <p className="text-2xl font-bold">{BRAND}</p>
            <p className="text-xs text-muted-foreground">Secure walking assessments</p>
          </div>
        </Link>
        <nav className="flex gap-2 text-xs">
          <Link href="/privacy" className="rounded-full bg-card px-3 py-1.5 shadow-pillow-sm hover:bg-pastel-sand">
            Privacy Policy
          </Link>
          <Link href="/terms" className="rounded-full bg-card px-3 py-1.5 shadow-pillow-sm hover:bg-pastel-sand">
            Terms &amp; Conditions
          </Link>
        </nav>
      </header>
      <article className="legal mx-auto max-w-3xl rounded-[2.25rem] bg-card p-8 shadow-pillow">
        <h1 className="text-3xl font-semibold">{title}</h1>
        <p className="mt-1 text-xs text-muted-foreground">Last updated: {updated}</p>
        {children}
      </article>
    </main>
  );
}
