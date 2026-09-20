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
  const unanswered = survey.condition_survey?.unanswered_questions?.length
    ? survey.condition_survey.unanswered_questions
    : (survey.condition_survey?.skipped ?? []).map((question_id) => ({question_id, reason: "clarification_limit", clarification_attempts: 3}));
  const needsReview = survey.condition_survey?.needs_human_review || unanswered.length > 0;
  return (
    <div className="space-y-3 text-xs">
      <p className="text-muted-foreground">Recorded {survey.recorded_at ?? "Unknown"} · Call {survey.call_id ?? "Not recorded"}</p>
      {needsReview && (
        <div role="note" className="rounded-xl border border-amber-300 bg-amber-50 p-3 text-amber-950">
          <p className="font-semibold">Needs human review</p>
          <p>Some answers could not be established. They were left unanswered, not guessed. Review the transcript and use clinical judgment; no complete survey score is available.</p>
        </div>
      )}
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
              {unanswered.map((question) => (
                <li key={question.question_id} className="rounded-lg bg-amber-50 p-2 text-amber-950">
                  <p>{questionLabels[question.question_id] ?? question.question_id}: Unanswered — human review required</p>
                  <p>{question.reason === "no_response" ? "No speech recognized" : "Clarification limit reached"} · {question.clarification_attempts} clarification attempts</p>
                </li>
              ))}
            </ul>
            <p className="text-muted-foreground">Individual responses only. No validated HOOS JR interval score or stroke scale is calculated here.</p>
          </>
        ) : <p>Not recorded.</p>}
      </section>
      {!!survey.condition_survey?.transcript?.length && (
        <details className="border-t border-border pt-2">
          <summary className="cursor-pointer font-semibold">Survey transcript ({survey.condition_survey.transcript.length} turns)</summary>
          <p className="mt-2 text-muted-foreground">Speech-recognition text may contain errors. Transcript statements are not confirmed survey answers.</p>
          <ol className="mt-3 max-h-96 space-y-3 overflow-auto" aria-label="Survey transcript">
            {survey.condition_survey.transcript.map((turn, index) => (
              <li key={index} className="rounded-xl bg-muted p-3">
                <p className="font-semibold capitalize">{turn.speaker} · {turn.question_id ? (questionLabels[turn.question_id] ?? turn.question_id) : "Survey"}</p>
                <p className="text-muted-foreground">{turn.recorded_at}</p>
                <p className="whitespace-pre-wrap break-words">{turn.text}</p>
              </li>
            ))}
          </ol>
        </details>
      )}
    </div>
  );
}
