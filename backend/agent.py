"""Clinical summary generation for GaitGuard AI.

Uses OpenAI (gpt-4o-mini) when OPENAI_API_KEY is set, otherwise falls back to
a deterministic templated summary. API failures never propagate.
"""

from __future__ import annotations

import hashlib
import json
import os

_cache: dict[str, dict] = {}


def _cache_key(metrics_day1: dict, metrics_day14: dict, patient_id: str) -> str:
    blob = json.dumps(
        [metrics_day1, metrics_day14, patient_id], sort_keys=True
    )
    return hashlib.sha256(blob.encode()).hexdigest()


def _template_summary(metrics_day1: dict, metrics_day14: dict, patient_id: str) -> str:
    stride_delta = (
        metrics_day14["stride_length_m"] - metrics_day1["stride_length_m"]
    )
    risk_delta = (
        metrics_day14["fall_risk_score"] - metrics_day1["fall_risk_score"]
    )
    trend = "improved" if risk_delta < 0 else "worsened"
    return (
        f"Patient {patient_id}: fall risk {trend} from "
        f"{metrics_day1['fall_risk_score']:.3f} (day 1) to "
        f"{metrics_day14['fall_risk_score']:.3f} (day 14). "
        f"Stride length changed {stride_delta:+.2f} m "
        f"({metrics_day1['stride_length_m']:.2f} -> "
        f"{metrics_day14['stride_length_m']:.2f}). "
        f"Asymmetry {metrics_day1['asymmetry_pct']:.1f}% -> "
        f"{metrics_day14['asymmetry_pct']:.1f}%. "
        f"Velocity degradation {metrics_day1['velocity_degradation_pct']:.1f}% -> "
        f"{metrics_day14['velocity_degradation_pct']:.1f}%."
    )


def generate_summary(
    metrics_day1: dict, metrics_day14: dict, patient_id: str
) -> dict:
    key = _cache_key(metrics_day1, metrics_day14, patient_id)
    if key in _cache:
        return _cache[key]

    result = None
    if os.getenv("OPENAI_API_KEY"):
        try:
            from openai import OpenAI

            client = OpenAI()
            prompt = (
                "Write a 2-3 sentence clinical mobility summary for a gait "
                f"patient. Patient: {patient_id}. "
                f"Day 1 metrics: {json.dumps(metrics_day1)}. "
                f"Day 14 metrics: {json.dumps(metrics_day14)}. "
                "Focus on stride length, asymmetry, velocity degradation, "
                "and fall risk trend."
            )
            resp = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=150,
            )
            text = resp.choices[0].message.content.strip()
            result = {"summary": text, "source": "openai"}
        except Exception:
            result = None

    if result is None:
        result = {
            "summary": _template_summary(metrics_day1, metrics_day14, patient_id),
            "source": "template",
        }

    _cache[key] = result
    return result
