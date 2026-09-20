from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import re
from dataclasses import dataclass
from urllib import error, request
from urllib.parse import urlencode
from xml.sax.saxutils import escape, quoteattr

TWILIO_API_ROOT = "https://api.twilio.com/2010-04-01"
E164 = re.compile(r"^\+[1-9]\d{6,14}$")


class TwilioError(RuntimeError):
    """Raised when Twilio rejects a call request."""


@dataclass(frozen=True)
class PlacedCall:
    call_sid: str
    status: str
    to_number: str
    # False when the account refused ``StatusCallback``: Twilio will not tell
    # us about busy/no-answer, so the caller must time the call out itself.
    status_callback: bool = True


def require_e164(number: str) -> str:
    candidate = number.strip().replace(" ", "")
    if not E164.match(candidate):
        raise ValueError(
            f"Phone number '{number}' must be in E.164 format, for example +14155550123."
        )
    return candidate


def media_stream_twiml(stream_url: str, parameters: dict[str, str]) -> str:
    """Build the TwiML that hands the live call audio to our websocket.

    ``<Stream url>`` cannot carry a query string, so per-call values travel as
    ``<Parameter>`` children and arrive in the stream ``start`` message.
    """

    children = "".join(
        f"<Parameter name={quoteattr(name)} value={quoteattr(value)}/>"
        for name, value in parameters.items()
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<Response>"
        "<Connect>"
        f"<Stream url={quoteattr(stream_url)}>{children}</Stream>"
        "</Connect>"
        "</Response>"
    )


def hangup_twiml(message: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f"<Response><Say>{escape(message)}</Say><Hangup/></Response>"
    )


def validate_signature(auth_token: str, url: str, params: dict[str, str], signature: str) -> bool:
    """Verify the ``X-Twilio-Signature`` header of a form-encoded webhook."""

    payload = url + "".join(f"{key}{params[key]}" for key in sorted(params))
    digest = hmac.new(auth_token.encode("utf-8"), payload.encode("utf-8"), hashlib.sha1).digest()
    return hmac.compare_digest(base64.b64encode(digest).decode("ascii"), signature or "")


def _post_call(
    account_sid: str,
    auth_token: str,
    fields: list[tuple[str, str]],
    timeout: float,
) -> dict[str, object]:
    credentials = base64.b64encode(f"{account_sid}:{auth_token}".encode("utf-8")).decode("ascii")
    req = request.Request(
        f"{TWILIO_API_ROOT}/Accounts/{account_sid}/Calls.json",
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
            return payload
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:500]
        raise TwilioError(f"Twilio rejected the call request ({exc.code}): {detail}") from exc
    except Exception as exc:
        raise TwilioError("Could not reach the Twilio API.") from exc


def place_call(
    account_sid: str,
    auth_token: str,
    to_number: str,
    from_number: str,
    answer_url: str,
    status_callback_url: str | None = None,
    timeout: float = 20.0,
) -> PlacedCall:
    """Dial ``to_number`` and point Twilio at ``answer_url`` for the TwiML.

    Trial accounts reject optional parameters, so the request is retried with
    progressively fewer of them instead of failing.
    """

    minimal = [
        ("To", require_e164(to_number)),
        ("From", require_e164(from_number)),
        ("Url", answer_url),
    ]
    status_fields: list[tuple[str, str]] = []
    if status_callback_url:
        status_fields.append(("StatusCallback", status_callback_url))
        status_fields.append(("StatusCallbackMethod", "POST"))
        for event in ("initiated", "ringing", "answered", "completed"):
            status_fields.append(("StatusCallbackEvent", event))

    attempts = [
        minimal + [("Method", "POST")] + status_fields,
        minimal + [("Method", "POST")],
        minimal,
    ]
    for index, fields in enumerate(attempts):
        try:
            payload = _post_call(account_sid, auth_token, fields, timeout)
            accepted = fields
            break
        except TwilioError as exc:
            last_attempt = index == len(attempts) - 1
            if last_attempt or "limited parameter access" not in str(exc):
                raise
    return PlacedCall(
        call_sid=str(payload.get("sid", "")),
        status=str(payload.get("status", "unknown")),
        to_number=str(payload.get("to", to_number)),
        status_callback=bool(status_fields) and accepted is attempts[0],
    )


async def place_call_async(**kwargs) -> PlacedCall:
    return await asyncio.to_thread(lambda: place_call(**kwargs))
