"""Decision-record attestation: HMAC-SHA256 over a canonical JSON body.

A decision record is only useful as evidence if it cannot be edited after the
fact. The signature covers every field except ``signature`` itself, so changing
a lift, an interval, an experiment id or the flag mutation invalidates it.
"""
from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any

SIGNATURE_FIELD = "signature"
ALGORITHM = "hmac-sha256"


def _normalise(value: Any) -> Any:
    """Make numbers survive a JSON round trip through another runtime.

    Python writes integral floats as ``179.0`` while JavaScript writes ``179``.
    A record signed before transport would then fail verification after the
    browser parsed and re-serialised it. Normalise integral floats to ints and
    other floats to 12 decimal places before hashing.
    """
    if isinstance(value, float):
        if value.is_integer():
            return int(value)
        return round(value, 12)
    if isinstance(value, dict):
        return {key: _normalise(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_normalise(item) for item in value]
    if isinstance(value, tuple):
        return [_normalise(item) for item in value]
    return value


def canonical_bytes(body: dict[str, Any], legacy: bool = False) -> bytes:
    payload = body if legacy else _normalise(body)
    return json.dumps(payload, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def key_id(key: str) -> str:
    """A public fingerprint for the signing key, never the key itself."""
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:12]


def sign_record(record: dict[str, Any], key: str) -> dict[str, Any]:
    if not key:
        raise ValueError("a signing key is required")
    body = {k: v for k, v in record.items() if k != SIGNATURE_FIELD}
    digest = hmac.new(key.encode("utf-8"), canonical_bytes(body),
                      hashlib.sha256).hexdigest()
    signed = dict(record)
    signed[SIGNATURE_FIELD] = {
        "alg": ALGORITHM,
        "key_id": key_id(key),
        "value": digest,
    }
    return signed


def verify_record(record: dict[str, Any], key: str) -> tuple[bool, str]:
    signature = record.get(SIGNATURE_FIELD)
    if not isinstance(signature, dict):
        return False, "record is not signed"
    if signature.get("alg") != ALGORITHM:
        return False, f"unsupported signature algorithm {signature.get('alg')!r}"
    expected_key_id = key_id(key)
    if signature.get("key_id") != expected_key_id:
        return False, f"signature was made with a different key ({signature.get('key_id')!r}, expected {expected_key_id!r})"
    body = {k: v for k, v in record.items() if k != SIGNATURE_FIELD}
    expected = hmac.new(key.encode("utf-8"), canonical_bytes(body),
                        hashlib.sha256).hexdigest()
    if not hmac.compare_digest(str(signature.get("value", "")), expected):
        # Records signed before numeric normalisation still verify unchanged.
        legacy = hmac.new(key.encode("utf-8"), canonical_bytes(body, legacy=True),
                          hashlib.sha256).hexdigest()
        if not hmac.compare_digest(str(signature.get("value", "")), legacy):
            return False, "signature does not match the record; it was edited after signing"
    return True, f"valid {ALGORITHM} signature, key {expected_key_id}"