import PatientScreening from "../../components/PatientScreening";

export default async function Page({
  params,
  searchParams,
}: {
  params: Promise<{ id: string }>;
  searchParams: Promise<{ token?: string | string[] }>;
}) {
  const [{ id }, { token }] = await Promise.all([params, searchParams]);
  const magicToken = Array.isArray(token) ? token[0] : token;

  return (
    <PatientScreening
      patientId={decodeURIComponent(id)}
      token={magicToken || null}
    />
  );
}
