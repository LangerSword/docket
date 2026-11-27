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


def canonical_bytes(body: dict[str, Any]) -> bytes:
    return json.dumps(body, sort_keys=True, separators=(",", ":"),
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
        return False, "signature does not match the record; it was edited after signing"
    return True, f"valid {ALGORITHM} signature, key {expected_key_id}"