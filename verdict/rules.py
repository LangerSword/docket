"""Decision engine. Pure functions, no I/O. This is the product's IP.

Seven states, not two: a gate that cannot tell "not enough data" from
"bad data" from "losing" will ship on noise.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class State(str, Enum):
    UNREACHABLE = "unreachable"
    BROKEN = "broken"
    COLLECTING = "collecting"
    LOSING = "losing"
    GUARDRAIL = "guardrail"
    INCONCLUSIVE = "inconclusive"
    WINNER = "winner"


EXIT_CODES = {
    State.WINNER: 0,
    State.COLLECTING: 1,
    State.INCONCLUSIVE: 1,
    State.BROKEN: 2,
    State.LOSING: 2,
    State.GUARDRAIL: 2,
    State.UNREACHABLE: 3,
}


@dataclass(frozen=True)
class Arm:
    key: str
    n: int
    total: float

    @property
    def mean(self) -> float:
        return self.total / self.n if self.n else 0.0


@dataclass(frozen=True)
class Inference:
    lift: float | None = None
    ci_low: float | None = None
    ci_high: float | None = None
    chance_to_win: float | None = None


@dataclass
class Verdict:
    state: State
    reasons: list[str] = field(default_factory=list)
    arms: dict[str, Arm] = field(default_factory=dict)
    inference: dict[str, Inference] = field(default_factory=dict)
    srm_p: float | None = None
    as_of: str | None = None
    winner: str | None = None
    more_traffic_needed: int | None = None
    exit_code: int = 3

    def to_dict(self) -> dict:
        return {
            "state": self.state.value,
            "exit_code": self.exit_code,
            "reasons": self.reasons,
            "arms": {k: {"n": a.n, "total": a.total, "mean": round(a.mean, 6)}
                     for k, a in self.arms.items()},
            "inference": {k: vars(v) for k, v in self.inference.items()},
            "srm_p": self.srm_p,
            "as_of": self.as_of,
            "winner": self.winner,
            "more_traffic_needed": self.more_traffic_needed,
        }


@dataclass
class RuleConfig:
    control_key: str = "control"
    n_min: int = 50
    c_min: int = 5
    win_threshold: float = 0.95
    expected_direction: str = "increase"  # increase | decrease
    weights: dict[str, float] = field(default_factory=dict)  # configured split, optional
    guardrails: list[dict[str, Any]] = field(default_factory=list)
    srm_alpha: float = 0.001


def chi_square_p(observed: list[int], weights: list[float]) -> float | None:
    """Two-sided chi-square survival for observed vs expected shares.

    Uses the exact base cases + recurrence Flagsmith itself uses, so no scipy.
    """
    total = sum(observed)
    if len(observed) < 2 or total == 0 or any(w <= 0 for w in weights):
        return None
    expected = [total * w for w in weights]
    statistic = sum((o - e) ** 2 / e for o, e in zip(observed, expected))
    y = statistic / 2.0
    dof = len(observed) - 1
    if dof == 1:
        return math.erfc(math.sqrt(y))
    survival = 0.0
    a = dof / 2.0
    # Abramowitz-Stegun style upper incomplete gamma via series (dof>=2 only).
    s = 0.0
    term = 1.0 / a
    for k in range(100):
        s += term
        term *= y / (a + k + 1)
        if abs(term) < 1e-12 * abs(s):
            break
    survival = 1.0 - math.exp(-y + a * math.log(y) - math.lgamma(a) + math.log(max(s, 1e-300)))
    return max(0.0, min(1.0, survival))


def weights_from_experiment(exp: dict) -> dict[str, float]:
    """The configured traffic split, read from the experiment's rollout.

    Flagsmith reports traffic that falls through to the feature's own value as
    the variant key ``control``; only the variations are listed as allocations.
    """
    rollout = exp.get("experiment_rollout") or {}
    feature = exp.get("feature")
    if not isinstance(feature, dict):
        feature = {}
    options = {o.get("id"): o for o in (feature.get("multivariate_options") or [])}
    shares: dict[str, float] = {}
    allocated = 0.0
    for allocation in rollout.get("multivariate_feature_state_values") or []:
        option = options.get(allocation.get("multivariate_feature_option")) or {}
        key = option.get("key")
        pct = float(allocation.get("percentage_allocation") or 0)
        allocated += pct
        if key:
            shares[key] = pct
    remainder = 100.0 - allocated
    if 0.0 <= remainder <= 100.0:
        shares["control"] = remainder
    return shares


def _primary_metric(payload: dict) -> dict:
    metrics = payload.get("metrics") or []
    if not metrics:
        raise ValueError("results payload has no metrics")
    return metrics[0]


def decide(payload: dict, cfg: RuleConfig | None = None) -> Verdict:
    """Turn a Flagsmith results payload into a state with reasons.

    Never raises for data problems: returns BROKEN with the reason, so the CLI
    can print a useful refusal instead of a traceback.
    """
    cfg = cfg or RuleConfig()
    inner = payload.get("results") or payload
    body = inner.get("payload") if isinstance(inner, dict) else None
    as_of = inner.get("as_of") if isinstance(inner, dict) else None

    if not body:
        return Verdict(State.UNREACHABLE, ["results not computed yet; refresh and retry"],
                       as_of=as_of, exit_code=EXIT_CODES[State.UNREACHABLE])

    try:
        metric = _primary_metric(body)
    except ValueError as e:
        return Verdict(State.BROKEN, [str(e)], as_of=as_of,
                       exit_code=EXIT_CODES[State.BROKEN])

    arms = {k: Arm(k, int(v.get("n", 0)), float(v.get("sum", 0.0)))
            for k, v in (metric.get("variants") or {}).items()}
    inference = {k: Inference(**{f: v.get(f) for f in ("lift", "ci_low", "ci_high", "chance_to_win")})
                 for k, v in (metric.get("inference") or {}).items()}
    if not arms:
        return Verdict(State.BROKEN, ["no exposures recorded on any arm; instrumentation is not firing"],
                       as_of=as_of, srm_p=body.get("srm_p_value"),
                       exit_code=EXIT_CODES[State.BROKEN])

    reasons: list[str] = []
    verdict = Verdict(State.INCONCLUSIVE, reasons=reasons, arms=arms, inference=inference,
                      as_of=as_of, srm_p=body.get("srm_p_value"))
    verdict.exit_code = EXIT_CODES[State.INCONCLUSIVE]

    # 1. Dead arm: a variant that never received traffic is a wiring bug, not a result.
    dead = [k for k, a in arms.items() if a.n == 0]
    if dead:
        verdict.state = State.BROKEN
        verdict.reasons = [f"variant {k!r} has zero exposures: assignment is not reaching the code" for k in dead]
        verdict.exit_code = EXIT_CODES[State.BROKEN]
        return verdict

    # 2. Sample ratio mismatch: use the platform value when present, else compute.
    srm_p = body.get("srm_p_value")
    if srm_p is None and cfg.weights:
        keys = [k for k in cfg.weights if k in arms]
        if len(keys) == len(arms):
            srm_p = chi_square_p([arms[k].n for k in keys],
                                 [cfg.weights[k] / 100.0 for k in keys])
            verdict.srm_p = srm_p
    if srm_p is not None and srm_p < cfg.srm_alpha:
        verdict.state = State.BROKEN
        verdict.reasons = [f"sample ratio mismatch, p={srm_p:.2e}: the split broke, results are not trustworthy"]
        verdict.exit_code = EXIT_CODES[State.BROKEN]
        return verdict

    # 3. Collecting: floors before any statistic means anything.
    short = [k for k, a in arms.items() if a.n < cfg.n_min]
    low_conv = [k for k, a in arms.items() if a.n >= cfg.n_min and a.total < cfg.c_min]
    if short or low_conv:
        verdict.state = State.COLLECTING
        verdict.reasons = ([f"{k}: n={arms[k].n} < {cfg.n_min}" for k in short] +
                           [f"{k}: conversions={arms[k].total:g} < {cfg.c_min}" for k in low_conv])
        verdict.exit_code = EXIT_CODES[State.COLLECTING]
        return verdict

    control = arms.get(cfg.control_key)
    if control is None:
        verdict.state = State.INCONCLUSIVE
        verdict.reasons = [f"no variant keyed {cfg.control_key!r}: the engine cannot compute lift"]
        verdict.exit_code = EXIT_CODES[State.INCONCLUSIVE]
        return verdict

    treatments = {k: v for k, v in inference.items() if k != cfg.control_key}
    if not treatments:
        verdict.reasons = ["no inference for any treatment yet"]
        return verdict

    # 4. Pick the leading treatment, honouring expected direction.
    def effective_chance(inf: Inference) -> float | None:
        if inf.chance_to_win is None:
            return None
        return inf.chance_to_win if cfg.expected_direction == "increase" else 1.0 - inf.chance_to_win

    ranked = sorted(
        ((k, effective_chance(v or Inference())) for k, v in treatments.items()),
        key=lambda kv: (kv[1] is not None, kv[1] or 0.0), reverse=True)
    best_key, best_chance = ranked[0]
    best = treatments[best_key] or Inference()
    verdict.winner = best_key

    def interval_clears(inf: Inference) -> bool:
        if inf.ci_low is None or inf.ci_high is None:
            return False
        return inf.ci_low > 0 if cfg.expected_direction == "increase" else inf.ci_high < 0

    if best_chance is None or best.ci_low is None or best.ci_high is None:
        verdict.reasons = [f"{best_key}: the engine returned no inference (no spread, or no control mean)"]
        return verdict

    verdict.reasons = [
        f"{best_key}: lift {best.lift:+.1%}, 95% interval [{best.ci_low:+.1%}, {best.ci_high:+.1%}], "
        f"chance to win {best_chance:.1%}"
    ]

    # 5. Guardrails: a winner that regressed something else is a refusal. A
    # guardrail with no inference yet is not a pass: it is unmeasured.
    for g in cfg.guardrails:
        gid = g.get("metric_id")
        rows = [m for m in body.get("metrics") or [] if m.get("metric_id") == gid]
        if not rows:
            verdict.state = State.INCONCLUSIVE
            verdict.reasons = [f"guardrail metric {g.get('name', gid)} is not in the results payload; do not ship unmeasured"]
            verdict.exit_code = EXIT_CODES[State.INCONCLUSIVE]
            return verdict
        ginf = (rows[0].get("inference") or {}).get(best_key)
        if not ginf:
            verdict.state = State.INCONCLUSIVE
            verdict.reasons = [f"guardrail metric {g.get('name', gid)} has no inference yet; keep collecting"]
            verdict.exit_code = EXIT_CODES[State.INCONCLUSIVE]
            return verdict
        direction = g.get("expected_direction", "not_increase")
        tol = float(g.get("tolerance", 0.0))
        low, high = ginf.get("ci_low"), ginf.get("ci_high")
        if low is None or high is None:
            verdict.state = State.INCONCLUSIVE
            verdict.reasons = [f"guardrail metric {g.get('name', gid)} has no interval yet; keep collecting"]
            verdict.exit_code = EXIT_CODES[State.INCONCLUSIVE]
            return verdict
        violated = (
            (direction == "not_increase" and low - tol > 0) or
            (direction == "not_decrease" and high + tol < 0)
        )
        if violated:
            verdict.state = State.GUARDRAIL
            verdict.reasons.append(
                f"guardrail metric {g.get('name', gid)} moved the wrong way: "
                f"[{low:+.1%}, {high:+.1%}] vs tolerance {tol:.1%}")
            verdict.exit_code = EXIT_CODES[State.GUARDRAIL]
            return verdict

    # 6. Verdict.
    if best_chance <= 0.05 or (not interval_clears(best) and
                               ((cfg.expected_direction == "increase" and best.ci_high < 0) or
                                (cfg.expected_direction == "decrease" and best.ci_low > 0))):
        verdict.state = State.LOSING
        verdict.reasons.append(f"{best_key} is losing; do not ship")
        verdict.exit_code = EXIT_CODES[State.LOSING]
        return verdict

    if best_chance >= cfg.win_threshold and interval_clears(best):
        verdict.state = State.WINNER
        verdict.exit_code = EXIT_CODES[State.WINNER]
        return verdict

    # Inconclusive: say what it would take, roughly (interval width scales as 1/sqrt(n)).
    if best.lift and best.lift > 0:
        width = best.ci_high - best.ci_low
        target = 2 * abs(best.lift)
        if width > 0 and target > 0:
            ratio = (width / target) ** 2
            verdict.more_traffic_needed = int(arms[best_key].n * max(ratio, 1.0) - arms[best_key].n)
    verdict.state = State.INCONCLUSIVE
    verdict.exit_code = EXIT_CODES[State.INCONCLUSIVE]
    verdict.reasons.append("interval still crosses zero; not enough traffic to call it")
    return verdict
