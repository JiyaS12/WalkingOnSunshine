from __future__ import annotations

import os
from dataclasses import dataclass, field

# Overridden by the GAIT_CHECKER_BASE_URL environment variable once a real
# deployment exists; this placeholder keeps tests and local runs working
# without it configured.
DEFAULT_GAIT_CHECKER_BASE_URL = "https://gait.example"


def _default_link(patient_code: str, condition_category: str) -> str:
    del condition_category  # not part of the URL contract (yet) -- see module docstring below
    base = (os.environ.get("GAIT_CHECKER_BASE_URL") or DEFAULT_GAIT_CHECKER_BASE_URL).rstrip("/")
    return f"{base}/patient/{patient_code}"


@dataclass
class GaitHandoff:
    patient_code: str
    condition_category: str
    status: str = "prepared"
    link: str | None = None
    sms_sent: bool = False
    notes: list[str] = field(default_factory=list)


class GaitHandoffService:
    """Prepare the handoff payload, including a real link once configured.

    Link generation reads ``GAIT_CHECKER_BASE_URL`` by default (see
    ``_default_link``) and builds ``<base>/patient/<patient_code>`` -- confirm
    this path shape against the actual gait-checker app before relying on it
    in production; it's a placeholder contract until that's verified.

    Actually *sending* the link (SMS) is a separate step owned by
    ``PhoneCallSession`` in ``app/telephony/call_session.py``, not this
    service -- keeping delivery out of here means this stays testable
    without a real network call.
    """

    def __init__(self, link_generator=None):
        self.link_generator = link_generator or _default_link

    def prepare(self, patient_code: str, condition_category: str) -> GaitHandoff:
        handoff = GaitHandoff(
            patient_code=patient_code,
            condition_category=condition_category,
        )
        handoff.link = self.link_generator(patient_code, condition_category)
        handoff.notes.append("Handoff prepared for gait-checker integration.")
        return handoff
