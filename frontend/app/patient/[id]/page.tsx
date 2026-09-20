import PatientScreening from "../../components/PatientScreening";

export default async function Page({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;

  return <PatientScreening patientId={decodeURIComponent(id)} />;
}
