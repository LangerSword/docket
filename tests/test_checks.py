"""Audit checks, against a fake client. No network and no Flagsmith required."""
from __future__ import annotations

import unittest

from docket.checks import run_audit
from docket.rules import RuleConfig


def experiment(keyless: bool = False, event: str = "purchase_completed") -> dict:
    option = {"id": 11, "string_value": "red", "key": None if keyless else "red"}
    return {
        "id": 7,
        "status": "running",
        "feature": {
            "id": 4,
            "name": "checkout_cta",
            "initial_value": "control",
            "multivariate_options": [option],
        },
        "metrics": [{"id": 1, "metric": 3, "expected_direction": "increase"}],
        "experiment_rollout": {
            "enabled": True,
            "rollout_percentage": 100,
            "feature_state_value": {"type": "string", "value": "control"},
            "multivariate_feature_state_values": [
                {"multivariate_feature_option": 11, "percentage_allocation": 50}
            ],
        },
    }


def results(control_n: int = 500, red_n: int = 500) -> dict:
    return {"results": {"as_of": "2026-11-26T12:00:00Z", "payload": {
        "metrics": [{
            "metric_id": 3,
            "variants": {
                "control": {"n": control_n, "sum": 50.0, "sum_squares": 50.0},
                "red": {"n": red_n, "sum": 90.0, "sum_squares": 90.0},
            },
            "inference": {"red": {"lift": 0.8, "ci_low": 0.2, "ci_high": 1.4,
                                  "chance_to_win": 0.99}},
        }],
        "srm_p_value": None,
        "exposures_timeseries": {"points": [], "granularity": "hour"},
    }}}


class FakeClient:
    def __init__(self, exp: dict, payload: dict, events: list[str] | None = None):
        self.exp = exp
        self.payload = payload
        self.events = events if events is not None else ["purchase_completed"]

    def warehouse_connection(self, env_key: str) -> dict:
        return {"id": 1, "status": "connected", "warehouse_type": "postgres"}

    def experiment(self, env_key: str, experiment_id: int) -> dict:
        return self.exp

    def warehouse_events(self, env_key: str, connection_id: int) -> list[str]:
        return self.events

    def results(self, env_key: str, experiment_id: int) -> dict:
        return self.payload

    def _call(self, method: str, path: str, body=None, headers=None, retries=2):
        return {"results": [{"id": 3, "name": "purchase_completed",
                             "definition": {"version": 1, "event": "purchase_completed"}}]}


def statuses(checks) -> dict[str, str]:
    return {c.name: c.status for c in checks}


class AuditTest(unittest.TestCase):
    def test_good_wiring_has_no_blocks(self):
        checks, docket = run_audit(FakeClient(experiment(), results()), "env", 7,
                                    events_url="http://localhost:8000/api")
        self.assertNotIn("block", statuses(checks).values())
        self.assertIsNotNone(docket)
        # weights were derived from the rollout, so the SRM check ran
        self.assertIsNotNone(docket.srm_p)

    def test_keyless_variant_blocks(self):
        checks, _ = run_audit(FakeClient(experiment(keyless=True), results()), "env", 7)
        self.assertEqual(statuses(checks)["variant keys"], "block")

    def test_metric_event_that_never_arrived_blocks(self):
        checks, _ = run_audit(FakeClient(experiment(), results(), events=["$flag_exposure"]),
                              "env", 7)
        self.assertEqual(statuses(checks)["metric events"], "block")

    def test_cloud_events_url_blocks_even_with_the_right_path(self):
        checks, _ = run_audit(
            FakeClient(experiment(), results()), "env", 7,
            events_url="https://events.api.flagsmith.com/api")
        self.assertEqual(statuses(checks)["events url"], "block")

    def test_local_events_url_passes(self):
        checks, _ = run_audit(FakeClient(experiment(), results()), "env", 7,
                              events_url="http://localhost:8000/api")
        self.assertEqual(statuses(checks)["events url"], "pass")

    def test_dead_arm_blocks(self):
        checks, _ = run_audit(FakeClient(experiment(), results(control_n=500, red_n=0)),
                              "env", 7)
        self.assertEqual(statuses(checks)["traffic both arms"], "block")


if __name__ == "__main__":
    unittest.main()
