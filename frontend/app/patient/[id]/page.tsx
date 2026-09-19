import PatientScreening from "../../components/PatientScreening";

export default function Page({ params }: { params: { id: string } }) {
  return (
    <PatientScreening patientId={decodeURIComponent(params.id)} />
  );
}
