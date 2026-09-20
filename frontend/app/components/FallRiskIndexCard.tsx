import type { GaitMetrics } from "../lib/api";

export default function FallRiskIndexCard({ metrics }: { metrics?: GaitMetrics | null }) {
  const fallback = metrics?.cv_fall_risk_method === "heuristic_fallback";
  const learned = metrics?.cv_fall_risk_method === "learned_fall_history";
  const rejected = metrics?.cv_fall_risk_status === "not_scorable";
  const value = metrics?.cv_fall_risk_index;
  return (
    <section aria-label="Fall-risk index" className="rounded-3xl bg-card p-5 shadow-pillow-sm">
      <h3 className="text-sm font-semibold">Fall-risk index</h3>
      <p className="my-2 text-3xl font-semibold tabular-nums" aria-live="polite">
        {value != null && (fallback || learned) ? `${value} / 100`
          : rejected ? "Retake needed" : metrics ? "Not recorded" : "—"}
      </p>
      <p className="text-sm font-medium">
        {fallback ? "Heuristic fallback" : learned ? "Learned prior-fall association" : ""}
      </p>
      <p className="mt-1 text-xs text-muted-foreground">
        {fallback
          ? "The original heuristic scaled to 1–100. Predictive accuracy is unvalidated; this is not a probability of falling."
          : learned
            ? "Higher means greater association with reported falls in the development cohort. This is not a probability of future falling."
            : rejected
              ? "This recording cannot support a score. Repeat the guided assessment with your full body and feet visible."
              : "Complete a new guided assessment to receive a 1–100 index."}
      </p>
      {metrics?.cv_fall_risk_model_version && (
        <details className="mt-3 text-xs text-muted-foreground">
          <summary>Scoring method and limitations</summary>
          <p>Version: {metrics.cv_fall_risk_model_version}</p>
          {[...new Set(metrics.cv_fall_risk_warnings ?? [])].map((warning) => <p key={warning}>{warning}</p>)}
        </details>
      )}
    </section>
  );
}
