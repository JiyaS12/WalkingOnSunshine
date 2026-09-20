"""Shared-secret gate for the operator API.

The operator pages run behind a public tunnel so Twilio can reach the same
server, which also exposes the routes that dial patients and return their
transcripts. A single ``OPERATOR_TOKEN`` from the environment must accompany
every ``/api/*`` request as ``Authorization: Bearer <token>``; Twilio's own
webhooks are verified by request signature instead.
"""

from __future__ import annotations

import hmac
import os

from fastapi import HTTPException, Request

OPERATOR_TOKEN_ENV = "OPERATOR_TOKEN"
NOT_CONFIGURED = (
    f"{OPERATOR_TOKEN_ENV} is not set. Add a long random value to .env and restart "
    "so only operators can place calls or read transcripts."
)


class OperatorAuth:
    def __init__(self, token: str | None):
        self.token = token or None

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> OperatorAuth:
        source = env if env is not None else os.environ
        return cls(source.get(OPERATOR_TOKEN_ENV))

    @property
    def configured(self) -> bool:
        return self.token is not None

    def accepts(self, presented: str | None) -> bool:
        return self.token is not None and presented is not None and hmac.compare_digest(
            self.token.encode(), presented.encode()
        )

    def __call__(self, request: Request) -> None:
        """FastAPI dependency: reject when the token is missing or wrong."""

        if not self.configured:
            raise HTTPException(status_code=503, detail=NOT_CONFIGURED)
        header = request.headers.get("authorization", "")
        scheme, _, presented = header.partition(" ")
        if scheme.lower() != "bearer" or not self.accepts(presented.strip()):
            raise HTTPException(
                status_code=401,
                detail="Operator token missing or incorrect.",
                headers={"WWW-Authenticate": "Bearer"},
            )
