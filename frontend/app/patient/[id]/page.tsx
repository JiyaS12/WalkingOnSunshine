import type { Metadata } from "next";
import PatientScreening from "../../components/PatientScreening";

export const metadata: Metadata = {
  referrer: "no-referrer",
  robots: { index: false, follow: false },
};

export default async function Page({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;

  return <PatientScreening patientId={decodeURIComponent(id)} />;
}
