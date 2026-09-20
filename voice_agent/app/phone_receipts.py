"""Durable dispatch receipts, containing no transcripts, URLs, or credentials."""

import json
import sqlite3
from pathlib import Path

from pydantic import Field

from .integration_contract import Contract, PhoneSnapshot


class Receipt(Contract):
    fingerprint: str
    attempt_id: str
    snapshot: PhoneSnapshot
    sms_requests: dict[str, int] = Field(default_factory=dict)
    message_ids: dict[str, str] = Field(default_factory=dict)
    carrier_sequence: int = -1
    stream_started: bool = False
    published_version: int = 0


class ReceiptStore:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.connection = sqlite3.connect(path, check_same_thread=False)
        path.chmod(0o600)
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA synchronous=FULL")
        self.connection.execute("CREATE TABLE IF NOT EXISTS receipts (call_id TEXT PRIMARY KEY, body TEXT NOT NULL)")
        self.connection.commit()

    def get(self, call_id: str) -> Receipt | None:
        row = self.connection.execute("SELECT body FROM receipts WHERE call_id = ?", (call_id,)).fetchone()
        return Receipt.model_validate_json(row[0]) if row else None

    def all(self) -> list[Receipt]:
        return [
            Receipt.model_validate_json(row[0])
            for row in self.connection.execute("SELECT body FROM receipts").fetchall()
        ]

    def save(self, receipt: Receipt) -> None:
        with self.connection:
            self.connection.execute(
                "INSERT INTO receipts VALUES (?, ?) ON CONFLICT(call_id) DO UPDATE SET body=excluded.body",
                (receipt.snapshot.call_id, json.dumps(receipt.model_dump(), sort_keys=True)),
            )

    def reserve(self, receipt: Receipt) -> bool:
        try:
            with self.connection:
                self.connection.execute(
                    "INSERT INTO receipts VALUES (?, ?)",
                    (receipt.snapshot.call_id, json.dumps(receipt.model_dump(), sort_keys=True)),
                )
            return True
        except sqlite3.IntegrityError:
            return False

    def close(self) -> None:
        self.connection.close()
