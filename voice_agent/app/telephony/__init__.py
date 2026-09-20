"""Telephony boundary for running the safe survey over a real phone call.

The package is provider-scoped on purpose: Twilio carries the audio, Deepgram
performs streaming speech-to-text and speech synthesis, and
:class:`~app.telephony.call_session.PhoneCallSession` keeps the survey logic in
``app.survey_engine`` unaware of either provider.
"""

from .config import TelephonySettings, load_settings
from .call_session import PhoneCallSession

__all__ = ["TelephonySettings", "load_settings", "PhoneCallSession"]
