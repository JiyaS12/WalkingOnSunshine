from __future__ import annotations

import asyncio
import base64
import json
from dataclasses import dataclass
from urllib import error, request
from urllib.parse import urlencode

from .twilio import TWILIO_API_ROOT, TwilioError, require_e164


@dataclass(frozen=True)
class SentSms:
    message_sid: str
    status: str


def send_sms(
    account_sid: str,
    auth_token: str,
    to_number: str,
    from_number: str,
    body: str,
    timeout: float = 20.0,
) -> SentSms:
    """POST a text message through the same Twilio REST API ``place_call`` uses.

    Mirrors ``twilio.py``'s ``_post_call``: raw ``urllib`` request, Basic auth,
    no Twilio SDK dependency, ``TwilioError`` on failure.
    """

    credentials = base64.b64encode(f"{account_sid}:{auth_token}".encode("utf-8")).decode("ascii")
    fields = [
        ("To", require_e164(to_number)),
        ("From", require_e164(from_number)),
        ("Body", body),
    ]
    req = request.Request(
        f"{TWILIO_API_ROOT}/Accounts/{account_sid}/Messages.json",
        data=urlencode(fields).encode("utf-8"),
        headers={
            "Authorization": f"Basic {credentials}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=timeout) as response:
            payload: dict[str, object] = json.load(response)
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:500]
        raise TwilioError(f"Twilio rejected the text message ({exc.code}): {detail}") from exc
    except Exception as exc:
        raise TwilioError("Could not reach the Twilio API.") from exc
    return SentSms(
        message_sid=str(payload.get("sid", "")),
        status=str(payload.get("status", "unknown")),
    )


async def send_sms_async(**kwargs) -> SentSms:
    return await asyncio.to_thread(lambda: send_sms(**kwargs))
