"""Release ledger: append-only records, listing and offline verification."""
from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from verdict.attest import sign_record
from verdict.cli import main
from verdict.ledger import append, read, verify_all

KEY = "ledger-test-key"
RECORD = {
    "tool": "verdict",
    "generated_at": "2026-11-27T12:00:00Z",
    "experiment": 4,
    "verdict": {"state": "winner", "exit_code": 0},
    "mutation": {"winner": "red", "allocation": 100},
}


class LedgerTest(unittest.TestCase):
    def test_append_read_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ledger.jsonl"
            append(path, RECORD)
            append(path, sign_record(RECORD, KEY))
            records = read(path)
            self.assertEqual(len(records), 2)
            self.assertEqual(records[0]["experiment"], 4)
            self.assertIn("signature", records[1])

    def test_missing_ledger_is_empty(self):
        self.assertEqual(read(Path("/tmp/does-not-exist-verdict-ledger.jsonl")), [])

    def test_invalid_line_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ledger.jsonl"
            path.write_text("not json\n")
            with self.assertRaises(ValueError):
                read(path)

    def test_verify_all(self):
        results = verify_all([sign_record(RECORD, KEY), RECORD], KEY)
        self.assertTrue(results[0][0])
        self.assertFalse(results[1][0])


class LedgerCliTest(unittest.TestCase):
    def _write(self, tmp: str) -> Path:
        path = Path(tmp) / "ledger.jsonl"
        append(path, sign_record(RECORD, KEY))
        return path

    def test_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(tmp)
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = main(["ledger", "list", str(path)])
            self.assertEqual(code, 0)
            self.assertIn("experiment 4", out.getvalue())
            self.assertIn("winner", out.getvalue())

    def test_verify_signed_ledger(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(tmp)
            out = io.StringIO()
            with patch.dict(os.environ, {"VERDICT_SIGNING_KEY": KEY}, clear=False):
                with contextlib.redirect_stdout(out):
                    code = main(["ledger", "verify", str(path)])
            self.assertEqual(code, 0)
            self.assertIn("1 record(s), 0 invalid", out.getvalue())

    def test_verify_tampered_ledger_exits_two(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ledger.jsonl"
            bad = sign_record(RECORD, KEY)
            bad["mutation"]["allocation"] = 10
            path.write_text(json.dumps(bad) + "\n")
            out = io.StringIO()
            with patch.dict(os.environ, {"VERDICT_SIGNING_KEY": KEY}, clear=False):
                with contextlib.redirect_stdout(out):
                    code = main(["ledger", "verify", str(path)])
            self.assertEqual(code, 2)
            self.assertIn("INVALID", out.getvalue())


if __name__ == "__main__":
    unittest.main()