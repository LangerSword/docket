"""Signed decision records: HMAC integrity, tamper detection, CLI verify."""
from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from verdict.attest import sign_record, verify_record
from verdict.cli import main

KEY = "test-signing-key"
RECORD = {
    "tool": "verdict",
    "experiment": 7,
    "as_of": "2026-11-26T12:00:00Z",
    "verdict": {"state": "winner", "exit_code": 0},
    "mutation": {"feature": 4, "winner": "red", "value": "red", "allocation": 100},
    "actor": "test",
}


class AttestTest(unittest.TestCase):
    def test_sign_and_verify(self):
        signed = sign_record(RECORD, KEY)
        ok, message = verify_record(signed, KEY)
        self.assertTrue(ok, message)
        self.assertEqual(signed["signature"]["alg"], "hmac-sha256")

    def test_signing_does_not_mutate_the_input(self):
        sign_record(RECORD, KEY)
        self.assertNotIn("signature", RECORD)

    def test_editing_any_field_invalidates(self):
        signed = sign_record(RECORD, KEY)
        signed["verdict"]["state"] = "guardrail"
        ok, message = verify_record(signed, KEY)
        self.assertFalse(ok)
        self.assertIn("edited", message)

    def test_editing_the_mutation_invalidates(self):
        signed = sign_record(RECORD, KEY)
        signed["mutation"]["allocation"] = 50
        ok, _ = verify_record(signed, KEY)
        self.assertFalse(ok)

    def test_wrong_key_is_rejected(self):
        signed = sign_record(RECORD, KEY)
        ok, message = verify_record(signed, "other-key")
        self.assertFalse(ok)
        self.assertIn("different key", message)

    def test_unsigned_record_is_rejected(self):
        ok, message = verify_record(RECORD, KEY)
        self.assertFalse(ok)
        self.assertIn("not signed", message)


class VerifyCliTest(unittest.TestCase):
    def _run(self, record: dict) -> tuple[int, str]:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "decision.json"
            path.write_text(json.dumps(record))
            output = io.StringIO()
            with patch.dict(os.environ, {"VERDICT_SIGNING_KEY": KEY}, clear=False):
                with contextlib.redirect_stdout(output):
                    code = main(["verify", str(path)])
            return code, output.getvalue()

    def test_valid_record_exits_zero(self):
        code, output = self._run(sign_record(RECORD, KEY))
        self.assertEqual(code, 0)
        self.assertIn("VALID", output)

    def test_tampered_record_exits_two(self):
        signed = sign_record(RECORD, KEY)
        signed["verdict"]["state"] = "losing"
        code, output = self._run(signed)
        self.assertEqual(code, 2)
        self.assertIn("INVALID", output)


if __name__ == "__main__":
    unittest.main()