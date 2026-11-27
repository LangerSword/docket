"""Release-level decisions: one docket over several experiments.

A real release is rarely one experiment. A docket groups every experiment and
flag change in the release, evaluates each case, and refuses the whole release if
any case is broken, losing or guardrailed.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any

from .checks import Check, run_audit
from .client import ApiError, Flagsmith
from .rules import RuleConfig, Decision


class ReleaseState(str, Enum):
    CLEARED = "cleared"
    NOT_READY = "not_ready"
    BLOCKED = "blocked"
    UNREACHABLE = "unreachable"


EXIT_CODES = {
    ReleaseState.CLEARED: 0,
    ReleaseState.NOT_READY: 1,
    ReleaseState.BLOCKED: 2,
    ReleaseState.UNREACHABLE: 3,
}


@dataclass(frozen=True)
class ReleaseCase:
    id: int
    label: str
    config: RuleConfig


@dataclass
class ReleaseConfig:
    name: str
    cases: list[ReleaseCase]
    require_signature: bool = False


@dataclass
class CaseResult:
    case: ReleaseCase
    verdict: Decision | None
    checks: list[Check] = field(default_factory=list)

    @property
    def state(self) -> str:
        if self.verdict is None:
            return "unreachable"
        return self.verdict.state.value

    @property
    def blocked(self) -> bool:
        return any(check.status == "block" for check in self.checks)

    def reasons(self) -> list[str]:
        reasons = list(self.verdict.reasons) if self.verdict else ["no verdict"]
        if self.blocked:
            names = [c.name for c in self.checks if c.status == "block"]
            reasons.append(f"audit blocks: {', '.join(names)}")
        return reasons


@dataclass
class ReleaseResult:
    config: ReleaseConfig
    cases: list[CaseResult]

    @property
    def state(self) -> ReleaseState:
        if not self.cases:
            return ReleaseState.UNREACHABLE
        states = [case.state for case in self.cases]
        if any(state == "unreachable" for state in states):
            return ReleaseState.UNREACHABLE
        if any(case.blocked for case in self.cases) or any(
                state in {"broken", "losing", "guardrail"} for state in states):
            return ReleaseState.BLOCKED
        if any(state in {"collecting", "inconclusive"} for state in states):
            return ReleaseState.NOT_READY
        if all(state == "winner" for state in states):
            return ReleaseState.CLEARED
        return ReleaseState.BLOCKED

    @property
    def exit_code(self) -> int:
        return EXIT_CODES[self.state]

    def reasons(self) -> list[str]:
        reasons = []
        for case in self.cases:
            if case.verdict is None:
                reasons.append(f"experiment {case.case.id} ({case.case.label}): unreachable")
                continue
            reasons.extend(
                f"experiment {case.case.id} ({case.case.label}): {reason}"
                for reason in case.reasons()
            )
        return reasons

    def to_dict(self) -> dict[str, Any]:
        return {
            "release": self.config.name,
            "state": self.state.value,
            "exit_code": self.exit_code,
            "cases": [
                {
                    "id": case.case.id,
                    "label": case.case.label,
                    "state": case.state,
                    "blocked": case.blocked,
                    "verdict": case.verdict.to_dict() if case.verdict else None,
                    "checks": [vars(check) for check in case.checks],
                }
                for case in self.cases
            ],
            "reasons": self.reasons(),
        }


def load_release(raw: dict[str, Any], base: RuleConfig | None = None) -> ReleaseConfig:
    """Turn ``docket.release.toml`` into cases with per-case rule overrides."""
    base = base or RuleConfig()
    rows = raw.get("experiment") or []
    if not isinstance(rows, list) or not rows:
        raise ValueError("release config has no [[experiment]] entries")
    cases: list[ReleaseCase] = []
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("id"), int):
            raise ValueError("every [[experiment]] needs an integer id")
        cfg = replace(
            base,
            control_key=row.get("control_key", base.control_key),
            n_min=int(row.get("n_min", base.n_min)),
            c_min=int(row.get("c_min", base.c_min)),
            win_threshold=float(row.get("win_threshold", base.win_threshold)),
            expected_direction=row.get("expected_direction", base.expected_direction),
            weights={k: float(v) for k, v in (row.get("weights") or base.weights).items()},
            guardrails=row.get("guardrails", base.guardrails),
            srm_alpha=float(row.get("srm_alpha", base.srm_alpha)),
        )
        cases.append(ReleaseCase(id=row["id"], label=str(row.get("label") or f"experiment {row['id']}"),
                                 config=cfg))
    release = raw.get("release") or {}
    return ReleaseConfig(
        name=str(release.get("name") or "release"),
        cases=cases,
        require_signature=bool(release.get("require_signature", False)),
    )


def evaluate(client: Flagsmith, env_key: str, config: ReleaseConfig,
             events_url: str | None = None) -> ReleaseResult:
    cases: list[CaseResult] = []
    for case in config.cases:
        try:
            checks, verdict = run_audit(client, env_key, case.id, case.config,
                                        events_url=events_url)
        except ApiError:
            checks, verdict = [], None
        cases.append(CaseResult(case=case, verdict=verdict, checks=checks))
    return ReleaseResult(config=config, cases=cases)


def print_result(result: ReleaseResult) -> None:
    print(f"RELEASE: {result.state.value.upper()}  (exit {result.exit_code})  {result.config.name}")
    for case in result.cases:
        if case.verdict is None:
            print(f"  experiment {case.case.id} ({case.case.label}): unreachable")
            continue
        print(f"  experiment {case.case.id} ({case.case.label}): {case.verdict.state.value}"
              + (f", leading {case.verdict.winner}" if case.verdict.winner else ""))
        for reason in case.reasons():
            print(f"    - {reason}")


def plan_mutations(client: Flagsmith, env_key: str, result: ReleaseResult) -> list[dict[str, Any]]:
    """Validate every mutation before writing any of them."""
    plan = []
    for case in result.cases:
        verdict = case.verdict
        if verdict is None or verdict.state.value != "winner" or not verdict.winner:
            raise ValueError(f"experiment {case.case.id} is not a winner")
        exp = client.experiment(env_key, case.case.id)
        if exp.get("status") == "completed":
            raise ValueError(f"experiment {case.case.id} is already completed")
        feature = exp.get("feature") if isinstance(exp.get("feature"), dict) else None
        if not feature or not feature.get("id"):
            raise ValueError(f"experiment {case.case.id} has no feature")
        winner = verdict.winner
        match = next((o for o in feature.get("multivariate_options") or []
                      if winner in (o.get("key"), o.get("string_value"))), None)
        if not match:
            raise ValueError(f"experiment {case.case.id}: winner {winner!r} has no variation")
        state = client.feature_state(env_key, feature["id"])
        plan.append({
            "experiment": case.case.id,
            "label": case.case.label,
            "feature": feature["id"],
            "feature_state": state["id"],
            "winner": winner,
            "value": match.get("string_value") or winner,
            "option": match["id"],
            "allocation": 100,
        })
    return plan


def ship_plan(client: Flagsmith, env_key: str, plan: list[dict[str, Any]]) -> None:
    for item in plan:
        client.complete_experiment(env_key, item["experiment"])
        client.set_winner(env_key, item["feature_state"], item["winner"],
                          item["option"], item["value"])


def record(env_key: str, result: ReleaseResult, plan: list[dict[str, Any]],
           actor: str) -> dict[str, Any]:
    return {
        "tool": "docket",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "release": result.config.name,
        "environment": env_key,
        "aggregate": {"state": result.state.value, "exit_code": result.exit_code},
        "experiments": [
            {
                "id": case.case.id,
                "label": case.case.label,
                "verdict": case.verdict.to_dict() if case.verdict else None,
                "mutation": next((item for item in plan if item["experiment"] == case.case.id), None),
            }
            for case in result.cases
        ],
        "actor": actor,
    }


def record_markdown(data: dict[str, Any]) -> str:
    lines = [
        f"# Release decision: {data['release']}",
        "",
        f"- state: **{data['aggregate']['state']}**",
        f"- environment: {data['environment']}",
        "",
    ]
    for item in data["experiments"]:
        verdict = item.get("verdict") or {}
        mutation = item.get("mutation") or {}
        lines.append(
            f"- experiment {item['id']} ({item['label']}): {verdict.get('state')}, "
            f"leading {verdict.get('winner')}, mutation {mutation.get('winner')} "
            f"at {mutation.get('allocation', 100)}%"
        )
    return "\n".join(lines) + "\n"


def write_record(path: str, data: dict[str, Any]) -> None:
    from pathlib import Path
    out = Path(path)
    out.write_text(json.dumps(data, indent=2))
    out.with_suffix(".md").write_text(record_markdown(data))