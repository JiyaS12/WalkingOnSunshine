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
            "summary-v2",
            metrics_day1 is metrics_day14,
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
        f"Baseline metrics: {json.dumps(metrics_day1)}. "
        f"Latest metrics: {json.dumps(metrics_day14)}. "
        "Focus on stride length, asymmetry, velocity degradation, "
        "and fall risk trend."
    )


def _risk_band(score: float) -> str:
    if score >= 0.5:
        return "high"
    if score >= 0.3:
        return "moderate"
    return "low"


def _template_summary(metrics_day1: dict, metrics_day14: dict, patient_id: str) -> str:
    if metrics_day1 is metrics_day14:
        m = metrics_day14
        return (
            f"Patient {patient_id}: based on the latest walk, fall risk is "
            f"{m['fall_risk_score']:.3f} ({_risk_band(m['fall_risk_score'])}). "
            f"Stride length {m['stride_length_m']:.2f} m, "
            f"asymmetry {m['asymmetry_pct']:.1f}%, "
            f"velocity degradation {m['velocity_degradation_pct']:.1f}%."
        )
    stride_delta = (
        metrics_day14["stride_length_m"] - metrics_day1["stride_length_m"]
    )
    risk_delta = (
        metrics_day14["fall_risk_score"] - metrics_day1["fall_risk_score"]
    )
    if abs(risk_delta) < 1e-9:
        trend = "unchanged"
    elif risk_delta < 0:
        trend = "improved"
    else:
        trend = "worsened"
    return (
        f"Patient {patient_id}: fall risk {trend} from "
        f"{metrics_day1['fall_risk_score']:.3f} (baseline) to "
        f"{metrics_day14['fall_risk_score']:.3f} (latest). "
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


_SYNTHESIS_SYSTEM_PROMPT = (
    "You are a clinical decision-support assistant writing for a physician. "
    "Given a patient's subjective phone-survey responses and objective gait "
    "telemetry, write a concise clinical synthesis (4–6 sentences, plain "
    "clinical English, no bullet points): (1) state whether the reported "
    "symptoms are concordant or discordant with the measured gait changes "
    "and why, (2) name the specific metrics that support this, (3) flag any "
    "red flags for fall risk, (4) end with one suggested next step. Do not "
    "invent data not provided."
)


def _synthesis_key(record: dict) -> str:
    blob = json.dumps(
        [
            "synthesis-v1",
            _synthesis_prompt(record),
            record["gait_sessions"][-1]["metrics"],
        ],
        sort_keys=True,
    )
    return hashlib.sha256(blob.encode()).hexdigest()


def _synthesis_prompt(record: dict) -> str:
    survey = record["surveys"][-1]
    sessions = record["gait_sessions"]
    falls = survey.get("fall_history") or {}
    notes = survey.get("dizziness_notes") or ""
    latest = sessions[-1]
    first = sessions[0]
    gait = f'Latest session "{latest["label"]}": {_round_metrics(latest["metrics"])}.'
    if len(sessions) > 1:
        gait = (
            f'first session "{first["label"]}": '
            f'{_round_metrics(first["metrics"])}. ' + gait
        )
    return (
        f"Patient {record['patient_id']} ({record.get('name')}, "
        f"{record.get('age')}). "
        f"Latest survey ({survey.get('recorded_at')}): "
        f"pain {survey.get('pain_scale')}/10; "
        f"falls in last 6 months: {falls.get('falls_last_6_months', 0)} "
        f"(injured: {bool(falls.get('injured'))}); "
        f"dizziness: {'yes' if survey.get('dizziness') else 'no'} {notes}; "
        f"complaints: {', '.join(survey.get('primary_complaints', []))}. "
        f"Gait telemetry — {gait}"
    )


def _template_synthesis(record: dict) -> str:
    survey = record["surveys"][-1]
    sessions = record["gait_sessions"]
    latest = sessions[-1]["metrics"]
    falls = survey.get("fall_history") or {}
    pain = survey.get("pain_scale", 0)
    n_falls = falls.get("falls_last_6_months", 0)
    dizzy = bool(survey.get("dizziness"))
    risk = latest["fall_risk_score"]
    if len(sessions) > 1:
        first_risk = sessions[0]["metrics"]["fall_risk_score"]
        trend = (
            "up from" if risk > first_risk else "down from"
        ) + f" {first_risk:.2f}"
    else:
        trend = "single session"
    subjective_flag = pain >= 6 or n_falls >= 1 or dizzy
    if subjective_flag and risk >= 0.5:
        verdict, reason = (
            "concordant",
            "reported symptoms and elevated objective fall risk agree",
        )
    elif not subjective_flag and risk < 0.3:
        verdict, reason = (
            "concordant",
            "mild symptoms match the low objective fall risk",
        )
    elif subjective_flag:
        verdict, reason = (
            "discordant",
            "patient reports significant symptoms despite low measured "
            "fall risk — consider non-gait contributors (vestibular, "
            "orthostatic)",
        )
    else:
        verdict, reason = (
            "discordant",
            "elevated measured fall risk despite few reported symptoms — "
            "patient may underestimate their risk",
        )
    return (
        f"Subjective: {record.get('name')} reports pain {pain}/10, "
        f"{n_falls} fall(s) in 6 months, dizziness "
        f"{'present' if dizzy else 'absent'}; primary complaints: "
        f"{', '.join(survey.get('primary_complaints', []))}. "
        f"Objective: fall-risk score {risk:.2f} ({trend}), "
        f"asymmetry {latest['asymmetry_pct']:.1f}%, "
        f"stride {latest['stride_length_m']:.2f} m, "
        f"cadence {latest['cadence_steps_per_min']:.0f} steps/min. "
        f"Correlation: {verdict} — {reason}."
    )


def generate_synthesis(record: dict) -> dict:
    cache = _load_cache()
    key = _synthesis_key(record)
    if key in cache:
        entry = dict(cache[key])
        if entry["source"] == "openai":
            saved = (len(_synthesis_prompt(record)) + len(entry["summary"])) // 4
            _stats["estimated_tokens_saved"] += saved
            entry["estimated_tokens_saved"] = _stats["estimated_tokens_saved"]
        else:
            entry["estimated_tokens_saved"] = 0
        _stats["cache_hits"] += 1
        entry["cached"] = True
        return entry

    prompt = _synthesis_prompt(record)
    summary = None
    if os.getenv("OPENAI_API_KEY"):
        try:
            client = OpenAI()
            resp = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": _SYNTHESIS_SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                max_tokens=300,
            )
            summary = resp.choices[0].message.content.strip()
            source = "openai"
        except Exception:
            summary = None
            source = "template"
    else:
        source = "template"

    if summary is None:
        summary = _template_synthesis(record)
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
