"""Append-only release decision ledger.

One JSON object per line. The ledger stores the same records `ship` writes, so
an auditor can verify every decision offline with the signing key.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .attest import verify_record


def append(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True, ensure_ascii=False) + "\n")


def read(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}:{number}: invalid JSON: {exc}") from exc
        if not isinstance(record, dict):
            raise ValueError(f"{path}:{number}: record is not an object")
        records.append(record)
    return records


def verify_all(records: list[dict[str, Any]], key: str) -> list[tuple[bool, str]]:
    results = []
    for record in records:
        results.append(verify_record(record, key))
    return results