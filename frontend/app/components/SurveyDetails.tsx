import type { Survey } from "../lib/api";

const questionLabels: Record<string, string> = {
  hoos_stairs: "Going up or down stairs",
  hoos_uneven_surface: "Walking on an uneven surface",
  hoos_rising: "Rising from sitting",
  hoos_bending: "Bending to the floor",
  hoos_lying_bed: "Lying in bed",
  hoos_sitting: "Sitting",
  stroke_balance: "Balance",
  stroke_weakness: "Weakness",
  stroke_stairs: "Stairs",
  stroke_turning: "Turning",
  stroke_walking: "Walking",
  stroke_recovery: "Recovery",
};

const yesNo = (value: boolean | null | undefined) => value == null ? "Unknown" : value ? "Yes" : "No";

export default function SurveyDetails({ survey }: { survey: Survey }) {
  return (
    <div className="space-y-3 text-xs">
      <p className="text-muted-foreground">Recorded {survey.recorded_at ?? "Unknown"} · Call {survey.call_id ?? "Not recorded"}</p>
      <section aria-label="Original generic intake" className="space-y-1">
        <h4 className="font-semibold">Original generic intake</h4>
        <p>Pain: {survey.pain_scale == null ? "Unknown" : `${survey.pain_scale}/10`}</p>
        <p>Falls (6 mo): {survey.fall_history?.falls_last_6_months ?? "Unknown"}</p>
        <p>Fall injury: {yesNo(survey.fall_history?.injured)}</p>
        <p>Fall description: {survey.fall_history?.last_fall_description ?? "Not recorded"}</p>
        <p>Dizziness: {yesNo(survey.dizziness)}</p>
        <p>Dizziness notes: {survey.dizziness_notes ?? "Not recorded"}</p>
        <p>Complaints: {survey.primary_complaints == null ? "Unknown" : survey.primary_complaints.join("; ") || "None reported"}</p>
      </section>
      <section aria-label="Condition-specific answers" className="space-y-1 border-t border-border pt-2">
        <h4 className="font-semibold">Condition-specific answers</h4>
        {survey.condition_survey ? (
          <>
            <p>{survey.condition_survey.instrument === "hoos_jr" ? "HOOS JR" : "Stroke mobility"} · version {survey.condition_survey.version}</p>
            <ul className="space-y-2">
              {survey.condition_survey.answers.map((answer) => (
                <li key={answer.question_id}>
                  <p>{questionLabels[answer.question_id] ?? answer.question_id}: {answer.normalized_value}</p>
                  <p className="text-muted-foreground">{answer.confirmed ? "Confirmed" : "Unconfirmed"} · {answer.acceptance_method.replaceAll("_", " ")}{answer.confirmed_at ? ` · ${answer.confirmed_at}` : ""}</p>
                </li>
              ))}
              {(survey.condition_survey.skipped ?? []).map((questionId) => (
                <li key={questionId}>
                  <p>{questionLabels[questionId] ?? questionId}: not answered</p>
                  <p className="text-muted-foreground">Skipped on the call after repeated clarification · needs review</p>
                </li>
              ))}
            </ul>
            <p className="text-muted-foreground">Individual responses only. No validated HOOS JR interval score or stroke scale is calculated here.</p>
          </>
        ) : <p>Not recorded.</p>}
      </section>
    </div>
  );
}
