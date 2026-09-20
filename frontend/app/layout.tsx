import type { Metadata } from "next";
import localFont from "next/font/local";
import "./globals.css";
import { cn } from "@/lib/utils";

const geistSans = localFont({
  src: "./fonts/GeistVF.woff",
  variable: "--font-sans",
  weight: "100 900",
});
const geistMono = localFont({
  src: "./fonts/GeistMonoVF.woff",
  variable: "--font-geist-mono",
  weight: "100 900",
});

export const metadata: Metadata = {
  title: "Sana",
  description: "Clinical gait monitoring dashboard",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" className={cn("font-sans", geistSans.variable)}>
      <body
        suppressHydrationWarning
        className={`${geistSans.variable} ${geistMono.variable} antialiased`}
      >
        <div
          aria-hidden
          className="pointer-events-none fixed inset-0 -z-10 overflow-hidden"
        >
          <div className="absolute -left-40 -top-32 h-[32rem] w-[32rem] rounded-full bg-pastel-sage/45 blur-3xl" />
          <div className="absolute -right-48 top-1/3 h-[28rem] w-[28rem] rounded-full bg-pastel-blue/40 blur-3xl" />
          <div className="absolute -bottom-40 left-1/3 h-[26rem] w-[26rem] rounded-full bg-pastel-peach/40 blur-3xl" />
        </div>
        {children}
      </body>
    </html>
  );
}
