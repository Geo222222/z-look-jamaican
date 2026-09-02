from __future__ import annotations

import base64
import json
import os
from dataclasses import dataclass
from datetime import datetime
from typing import Mapping, Sequence

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


class BookBridgeError(ValueError):
    pass


@dataclass(frozen=True)
class BookProducerIdentity:
    producer: str
    key_id: str
    public_key_b64: str
    allowed_event_prefixes: tuple[str, ...]

    def wire(self) -> dict[str, object]:
        return {
            "producer": self.producer,
            "key_id": self.key_id,
            "public_key_b64": self.public_key_b64,
            "allowed_event_prefixes": list(self.allowed_event_prefixes),
        }


def canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _aware(value: datetime, field: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise BookBridgeError(f"{field} must be timezone-aware")


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    _aware(value, "timestamp")
    return value.isoformat()


def _digest(value: str) -> str:
    if len(value) != 64:
        raise BookBridgeError("payload_digest must be a 64-character SHA-256 hex digest")
    try:
        int(value, 16)
    except ValueError as exc:
        raise BookBridgeError("payload_digest must be hexadecimal") from exc
    return value.lower()


class ZLJBookSigner:
    """Producer-side signer for ZLJ-owned Book Evidence Protocol v2 events.

    The private key remains inside ZLJ's secret boundary. The Book receives only
    the signed envelope and the corresponding registered public identity.
    """

    PRODUCER = "ZLJ"
    PREFIX = "ZLJ."

    def __init__(self, *, key_id: str, private_key: Ed25519PrivateKey) -> None:
        if not key_id:
            raise BookBridgeError("key_id is required")
        self.key_id = key_id
        self._private_key = private_key

    @classmethod
    def from_private_key_b64(cls, *, key_id: str, private_key_b64: str) -> "ZLJBookSigner":
        try:
            private_bytes = base64.b64decode(private_key_b64, validate=True)
            private_key = Ed25519PrivateKey.from_private_bytes(private_bytes)
        except (ValueError, TypeError) as exc:
            raise BookBridgeError("invalid Ed25519 private key material") from exc
        return cls(key_id=key_id, private_key=private_key)

    @property
    def public_identity(self) -> BookProducerIdentity:
        public_bytes = self._private_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        return BookProducerIdentity(
            producer=self.PRODUCER,
            key_id=self.key_id,
            public_key_b64=base64.b64encode(public_bytes).decode("ascii"),
            allowed_event_prefixes=(self.PREFIX,),
        )

    def sign_v2_envelope(
        self,
        *,
        receipt_id: str,
        event_type: str,
        evidence_class: str,
        subject_id: str,
        occurred_at: datetime,
        known_at: datetime,
        produced_at: datetime,
        payload_digest: str,
        privacy_class: str = "CONFIDENTIAL_EVIDENCE",
        visibility_scope: Sequence[str] = ("INSTITUTION",),
        payload_ref: str | None = None,
        correlation_id: str | None = None,
        causation_receipt_id: str | None = None,
        evidence_receipt_ids: Sequence[str] = (),
        source_event_at: datetime | None = None,
        valid_from: datetime | None = None,
        valid_until: datetime | None = None,
    ) -> dict[str, object]:
        if not receipt_id or not subject_id:
            raise BookBridgeError("receipt_id and subject_id are required")
        if not event_type.startswith(self.PREFIX):
            raise BookBridgeError("ZLJ signer may emit only ZLJ.* events")
        if evidence_class not in {"CONSTITUTIONAL", "ECONOMIC", "ANALYTICAL"}:
            raise BookBridgeError("invalid evidence_class")
        if privacy_class not in {
            "PUBLIC_PROOF",
            "PARTICIPANT_PROOF",
            "CONFIDENTIAL_EVIDENCE",
            "SECRET_REGULATED",
        }:
            raise BookBridgeError("invalid privacy_class")
        scopes = tuple(visibility_scope)
        if not scopes or any(not scope for scope in scopes) or len(set(scopes)) != len(scopes):
            raise BookBridgeError("visibility_scope must contain unique non-empty values")
        if privacy_class == "PUBLIC_PROOF" and "PUBLIC" not in scopes:
            raise BookBridgeError("PUBLIC_PROOF requires PUBLIC visibility")
        if privacy_class != "PUBLIC_PROOF" and "PUBLIC" in scopes:
            raise BookBridgeError("non-public evidence cannot include PUBLIC visibility")

        for value, field in (
            (occurred_at, "occurred_at"),
            (known_at, "known_at"),
            (produced_at, "produced_at"),
        ):
            _aware(value, field)
        for value, field in (
            (source_event_at, "source_event_at"),
            (valid_from, "valid_from"),
            (valid_until, "valid_until"),
        ):
            if value is not None:
                _aware(value, field)
        if occurred_at > produced_at:
            raise BookBridgeError("occurred_at cannot be after produced_at")
        if known_at > produced_at:
            raise BookBridgeError("known_at cannot be after produced_at")
        if valid_from is not None and valid_until is not None and valid_until < valid_from:
            raise BookBridgeError("valid_until cannot be before valid_from")

        evidence_ids = tuple(evidence_receipt_ids)
        if any(not value for value in evidence_ids) or len(set(evidence_ids)) != len(evidence_ids):
            raise BookBridgeError("evidence_receipt_ids must contain unique non-empty receipt ids")
        if causation_receipt_id is not None and causation_receipt_id in evidence_ids:
            raise BookBridgeError("primary causation must not be duplicated as evidence")

        body: dict[str, object] = {
            "schema_version": "2.0",
            "receipt_id": receipt_id,
            "producer": self.PRODUCER,
            "producer_key_id": self.key_id,
            "event_type": event_type,
            "evidence_class": evidence_class,
            "subject_id": subject_id,
            "occurred_at": occurred_at.isoformat(),
            "payload_digest": _digest(payload_digest),
            "payload_ref": payload_ref,
            "correlation_id": correlation_id,
            "causation_receipt_id": causation_receipt_id,
            "privacy_class": privacy_class,
            "visibility_scope": list(scopes),
            "evidence_receipt_ids": list(evidence_ids),
            "source_event_at": _iso(source_event_at),
            "known_at": known_at.isoformat(),
            "produced_at": produced_at.isoformat(),
            "valid_from": _iso(valid_from),
            "valid_until": _iso(valid_until),
        }
        signature = self._private_key.sign(canonical_json(body))
        return {**body, "signature": base64.b64encode(signature).decode("ascii")}


def load_zlj_book_signer_from_env(env: Mapping[str, str] | None = None) -> ZLJBookSigner:
    source = os.environ if env is None else env
    key_id = source.get("ZLJ_BOOK_KEY_ID", "")
    private_key_b64 = source.get("ZLJ_BOOK_ED25519_PRIVATE_KEY_B64", "")
    if not key_id or not private_key_b64:
        raise BookBridgeError(
            "ZLJ Book signing is unavailable: ZLJ_BOOK_KEY_ID and "
            "ZLJ_BOOK_ED25519_PRIVATE_KEY_B64 are required"
        )
    return ZLJBookSigner.from_private_key_b64(key_id=key_id, private_key_b64=private_key_b64)
