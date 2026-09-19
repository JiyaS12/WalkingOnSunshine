"""Clinical summary generation for GaitGuard AI.

Uses OpenAI (gpt-4o-mini) when OPENAI_API_KEY is set, otherwise falls back to
a deterministic templated summary. Summaries are cached via a heuristic
token-caching layer persisted to backend/.cache/summaries.json.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from openai import OpenAI

CACHE_PATH = Path(__file__).resolve().parent / ".cache" / "summaries.json"

_cache: dict | None = None
_stats = {"estimated_tokens_saved": 0, "cache_hits": 0}

_SYSTEM_PROMPT = (
    "You are a clinical mobility assistant; write plain-language summaries "
    "for patients (no jargon), 3 sentences."
)


def _load_cache() -> dict:
    global _cache
    if _cache is None:
        try:
            _cache = json.loads(CACHE_PATH.read_text())
        except Exception:
            _cache = {}
    return _cache


def _persist_cache() -> None:
    try:
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        CACHE_PATH.write_text(json.dumps(_cache))
    except Exception:
        pass


def _round_metrics(m: dict) -> dict:
    return {
        "stride_length_m": round(m["stride_length_m"], 2),
        "asymmetry_pct": round(m["asymmetry_pct"]),
        "velocity_degradation_pct": round(m["velocity_degradation_pct"]),
        "fall_risk_score": round(m["fall_risk_score"], 2),
        "cadence_steps_per_min": round(m["cadence_steps_per_min"]),
        "frame_count": m["frame_count"],
        "leg_length_m": round(m.get("leg_length_m", 0.0), 2),
        "stride_ratio": round(m.get("stride_ratio", 0.0), 2),
        "knee_flexion_rom_deg": round(m.get("knee_flexion_rom_deg", 0.0)),
        "peak_ankle_speed_mps": round(m.get("peak_ankle_speed_mps", 0.0), 2),
        "gait_detected": m.get("gait_detected", True),
    }


def _cache_key(metrics_day1: dict, metrics_day14: dict, patient_id: str) -> str:
    blob = json.dumps(
        [
            _round_metrics(metrics_day1),
            _round_metrics(metrics_day14),
            patient_id,
        ],
        sort_keys=True,
    )
    return hashlib.sha256(blob.encode()).hexdigest()


def _build_prompt(metrics_day1: dict, metrics_day14: dict, patient_id: str) -> str:
    return (
        f"Patient: {patient_id}. "
        f"Day 1 metrics: {json.dumps(metrics_day1)}. "
        f"Day 14 metrics: {json.dumps(metrics_day14)}. "
        "Focus on stride length, asymmetry, velocity degradation, "
        "and fall risk trend."
    )


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
    cache = _load_cache()
    key = _cache_key(metrics_day1, metrics_day14, patient_id)
    if key in cache:
        entry = dict(cache[key])
        if entry["source"] == "openai":
            saved = (
                len(_build_prompt(metrics_day1, metrics_day14, patient_id))
                + len(entry["summary"])
            ) // 4
            _stats["estimated_tokens_saved"] += saved
            entry["estimated_tokens_saved"] = _stats["estimated_tokens_saved"]
        else:
            entry["estimated_tokens_saved"] = 0
        _stats["cache_hits"] += 1
        entry["cached"] = True
        return entry

    prompt = _build_prompt(metrics_day1, metrics_day14, patient_id)
    summary = None
    source = "template"
    if os.getenv("OPENAI_API_KEY"):
        try:
            client = OpenAI()
            resp = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                max_tokens=150,
            )
            summary = resp.choices[0].message.content.strip()
            source = "openai"
        except Exception:
            summary = None

    if summary is None:
        summary = _template_summary(metrics_day1, metrics_day14, patient_id)
        source = "template"

    result = {
        "summary": summary,
        "source": source,
        "cached": False,
        "estimated_tokens_saved": _stats["estimated_tokens_saved"],
    }
    cache[key] = {"summary": summary, "source": source}
    _persist_cache()
    return result


def cache_stats() -> dict:
    return {
        "entries": len(_load_cache()),
        "cache_hits": _stats["cache_hits"],
        "estimated_tokens_saved": _stats["estimated_tokens_saved"],
    }
