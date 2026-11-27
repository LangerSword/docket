"""Release docket: config parsing, aggregate states, mutation planning."""
from __future__ import annotations

import unittest

from docket.checks import Check
from docket.release import (CaseResult, ReleaseCase, ReleaseConfig, ReleaseResult,
                            ReleaseState, load_release, plan_mutations, ship_plan)
from docket.rules import Decision, RuleConfig, State


def case(cid: int = 1, label: str = "checkout") -> ReleaseCase:
    return ReleaseCase(id=cid, label=label, config=RuleConfig())


def decision(state: State, winner: str | None = None) -> Decision:
    return Decision(state, reasons=[state.value], exit_code=0, winner=winner)


class ConfigTest(unittest.TestCase):
    def test_load_release_applies_overrides(self):
        raw = {
            "release": {"name": "checkout-11", "require_signature": True},
            "experiment": [
                {"id": 7, "label": "cta", "n_min": 120, "weights": {"control": 60, "red": 40}},
                {"id": 8},
            ],
        }
        cfg = load_release(raw)
        self.assertEqual(cfg.name, "checkout-11")
        self.assertTrue(cfg.require_signature)
        self.assertEqual(len(cfg.cases), 2)
        self.assertEqual(cfg.cases[0].label, "cta")
        self.assertEqual(cfg.cases[0].config.n_min, 120)
        self.assertEqual(cfg.cases[0].config.weights, {"control": 60.0, "red": 40.0})
        self.assertEqual(cfg.cases[1].label, "experiment 8")

    def test_no_experiments_is_an_error(self):
        with self.assertRaises(ValueError):
            load_release({"release": {"name": "empty"}})


class AggregateTest(unittest.TestCase):
    def _result(self, cases: list[CaseResult]) -> ReleaseResult:
        return ReleaseResult(config=ReleaseConfig("r", [c.case for c in cases]), cases=cases)

    def test_all_winners_is_cleared(self):
        result = self._result([
            CaseResult(case(1), decision(State.WINNER)),
            CaseResult(case(2, "express"), decision(State.WINNER)),
        ])
        self.assertEqual(result.state, ReleaseState.CLEARED)
        self.assertEqual(result.exit_code, 0)

    def test_guardrail_blocks_the_whole_release(self):
        result = self._result([
            CaseResult(case(1), decision(State.WINNER)),
            CaseResult(case(2, "express"), decision(State.GUARDRAIL)),
        ])
        self.assertEqual(result.state, ReleaseState.BLOCKED)
        self.assertEqual(result.exit_code, 2)

    def test_collecting_is_not_ready(self):
        result = self._result([
            CaseResult(case(1), decision(State.WINNER)),
            CaseResult(case(2, "express"), decision(State.COLLECTING)),
        ])
        self.assertEqual(result.state, ReleaseState.NOT_READY)
        self.assertEqual(result.exit_code, 1)

    def test_unreachable_wins_priority(self):
        result = self._result([
            CaseResult(case(1), None),
            CaseResult(case(2, "express"), decision(State.GUARDRAIL)),
        ])
        self.assertEqual(result.state, ReleaseState.UNREACHABLE)
        self.assertEqual(result.exit_code, 3)

    def test_audit_block_blocks_a_winning_verdict(self):
        checks = [Check("variant keys", "block", "keyless variation")]
        result = self._result([CaseResult(case(1), decision(State.WINNER), checks)])
        self.assertEqual(result.state, ReleaseState.BLOCKED)


class FakeClient:
    def __init__(self) -> None:
        self.completed: list[int] = []
        self.winners: list[tuple[int, str, str]] = []

    def experiment(self, env_key: str, experiment_id: int) -> dict:
        return {
            "id": experiment_id,
            "status": "running",
            "feature": {"id": 4, "name": "checkout", "initial_value": "control",
                        "multivariate_options": [
                            {"id": 11, "key": "red", "string_value": "red"}]},
        }

    def feature_state(self, env_key: str, feature_id: int) -> dict:
        return {"id": 99, "feature": feature_id}

    def complete_experiment(self, env_key: str, experiment_id: int) -> dict:
        self.completed.append(experiment_id)
        return {"status": "completed"}

    def set_winner(self, env_key: str, state_id: int, winner: str,
                   winner_option_id: int, winner_value: str | None = None) -> dict:
        self.winners.append((state_id, winner, winner_value or winner))
        return {"enabled": True}


class MutationPlanTest(unittest.TestCase):
    def test_plan_is_built_before_any_write(self):
        client = FakeClient()
        result = ReleaseResult(
            config=ReleaseConfig("r", [case(1), case(2, "express")]),
            cases=[CaseResult(case(1), decision(State.WINNER, "red")),
                   CaseResult(case(2, "express"), decision(State.WINNER, "red"))],
        )
        plan = plan_mutations(client, "env", result)
        self.assertEqual([item["experiment"] for item in plan], [1, 2])
        self.assertEqual(client.completed, [])
        ship_plan(client, "env", plan)
        self.assertEqual(client.completed, [1, 2])
        self.assertEqual(len(client.winners), 2)

    def test_plan_refuses_a_non_winner(self):
        client = FakeClient()
        result = ReleaseResult(
            config=ReleaseConfig("r", [case(1)]),
            cases=[CaseResult(case(1), decision(State.GUARDRAIL))],
        )
        with self.assertRaises(ValueError):
            plan_mutations(client, "env", result)
        self.assertEqual(client.completed, [])


if __name__ == "__main__":
    unittest.main()