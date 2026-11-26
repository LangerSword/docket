"""Engine tests. The real payload from this instance is the anchor fixture."""
from __future__ import annotations

import json
import math
import unittest
from pathlib import Path

from verdict.rules import (State, RuleConfig, chi_square_p, decide)

FIX = Path(__file__).parent / "fixtures"


def payload(arms: dict, inference: dict, srm=None, as_of="2026-11-20T21:17:00Z",
            extra_metrics: list | None = None) -> dict:
    metrics = [{
        "variants": {k: {"n": v[0], "sum": v[1], "sum_squares": v[1]} for k, v in arms.items()},
        "inference": inference,
        "metric_id": 1,
        "conversions_timeseries": None,
    }] + (extra_metrics or [])
    return {"results": {"as_of": as_of, "refresh_requested_at": as_of,
                        "is_final": False,
                        "payload": {"metrics": metrics, "srm_p_value": srm,
                                    "exposures_timeseries": {"points": [], "granularity": "hour"}}}}


class RealPayloadTest(unittest.TestCase):
    """The payload captured from the live instance on 2026-11-20."""

    def setUp(self):
        self.data = json.loads((FIX / "results_experiment6.json").read_text())

    def test_real_payload_is_inconclusive(self):
        v = decide(self.data, RuleConfig())
        self.assertEqual(v.state, State.INCONCLUSIVE)
        self.assertEqual(v.exit_code, 1)
        self.assertEqual(v.arms["control"].n, 120)
        self.assertIn("crosses zero", " ".join(v.reasons))

    def test_real_payload_winner_is_red(self):
        v = decide(self.data, RuleConfig())
        self.assertEqual(v.winner, "red")


class StateTest(unittest.TestCase):
    def test_winner(self):
        p = payload({"control": (1200, 120), "red": (1200, 180)},
                    {"red": {"lift": 0.5, "ci_low": 0.2, "ci_high": 0.8, "chance_to_win": 0.99}})
        v = decide(p)
        self.assertEqual(v.state, State.WINNER)
        self.assertEqual(v.exit_code, 0)

    def test_collecting_floor(self):
        p = payload({"control": (30, 3), "red": (30, 4)},
                    {"red": {"lift": 0.3, "ci_low": 0.05, "ci_high": 0.6, "chance_to_win": 0.97}})
        v = decide(p)
        self.assertEqual(v.state, State.COLLECTING)

    def test_losing(self):
        p = payload({"control": (500, 100), "red": (500, 80)},
                    {"red": {"lift": -0.2, "ci_low": -0.35, "ci_high": -0.05, "chance_to_win": 0.01}})
        v = decide(p)
        self.assertEqual(v.state, State.LOSING)
        self.assertEqual(v.exit_code, 2)

    def test_dead_arm_is_broken_not_inconclusive(self):
        p = payload({"control": (200, 20), "red": (0, 0)},
                    {"red": {"lift": 0, "ci_low": None, "ci_high": None, "chance_to_win": None}})
        v = decide(p)
        self.assertEqual(v.state, State.BROKEN)

    def test_srm_blocks_even_with_a_winning_number(self):
        p = payload({"control": (2000, 300), "red": (700, 150)},
                    {"red": {"lift": 0.4, "ci_low": 0.2, "ci_high": 0.6, "chance_to_win": 0.999}},
                    srm=0.0001)
        v = decide(p)
        self.assertEqual(v.state, State.BROKEN)

    def test_guardrail_blocks_a_winner(self):
        guard_metric = {
            "variants": {"control": {"n": 1200, "sum": 5000.0, "sum_squares": 0},
                         "red": {"n": 1200, "sum": 4200.0, "sum_squares": 0}},
            "inference": {"red": {"lift": -0.16, "ci_low": -0.22, "ci_high": -0.09,
                                  "chance_to_win": 0.02}},
            "metric_id": 2,
            "conversions_timeseries": None,
        }
        p = payload({"control": (1200, 120), "red": (1200, 180)},
                    {"red": {"lift": 0.5, "ci_low": 0.2, "ci_high": 0.8, "chance_to_win": 0.99}},
                    extra_metrics=[guard_metric])
        cfg = RuleConfig(guardrails=[{"metric_id": 2, "name": "revenue",
                                      "expected_direction": "not_decrease", "tolerance": 0.01}])
        v = decide(p, cfg)
        self.assertEqual(v.state, State.GUARDRAIL)

    def test_missing_control_key_is_inconclusive(self):
        p = payload({"a": (200, 20), "b": (200, 30)},
                    {"b": {"lift": 0.5, "ci_low": 0.2, "ci_high": 0.8, "chance_to_win": 0.99}})
        v = decide(p)
        self.assertEqual(v.state, State.INCONCLUSIVE)

    def test_decrease_direction(self):
        p = payload({"control": (500, 500), "red": (500, 400)},
                    {"red": {"lift": -0.2, "ci_low": -0.35, "ci_high": -0.05, "chance_to_win": 0.01}})
        v = decide(p, RuleConfig(expected_direction="decrease"))
        self.assertEqual(v.state, State.WINNER)

    def test_no_payload_is_unreachable(self):
        v = decide({"results": {"as_of": None, "payload": None}})
        self.assertEqual(v.state, State.UNREACHABLE)

    def test_exit_codes_are_distinct_per_class(self):
        pairs = {
            State.WINNER: 0, State.COLLECTING: 1, State.INCONCLUSIVE: 1,
            State.BROKEN: 2, State.LOSING: 2, State.GUARDRAIL: 2, State.UNREACHABLE: 3,
        }
        from verdict.rules import EXIT_CODES
        self.assertEqual(EXIT_CODES, pairs)


class ChiSquareTest(unittest.TestCase):
    def test_matching_split_is_not_significant(self):
        self.assertGreater(chi_square_p([120, 120], [0.5, 0.5]), 0.9)

    def test_broken_split_is_significant(self):
        self.assertLess(chi_square_p([1200, 300], [0.5, 0.5]), 1e-6)

    def test_degenerate_inputs(self):
        self.assertIsNone(chi_square_p([10], [1.0]))
        self.assertIsNone(chi_square_p([0, 0], [0.5, 0.5]))


if __name__ == "__main__":
    unittest.main()
