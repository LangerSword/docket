"""Audit checks: the silent failures the Flagsmith docs themselves name."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from .client import ApiError, Flagsmith
from .rules import RuleConfig, Verdict, decide


@dataclass
class Check:
    name: str
    status: str  # pass | warn | block | manual
    detail: str
    fix: str = ""


def _age_minutes(iso: str | None) -> float | None:
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return None
    return (datetime.now(timezone.utc) - dt).total_seconds() / 60.0


def run_audit(client: Flagsmith, env_key: str, experiment_id: int,
              cfg: RuleConfig | None = None, events_url: str | None = None) -> tuple[list[Check], Verdict | None]:
    cfg = cfg or RuleConfig()
    checks: list[Check] = []

    # 1. warehouse
    try:
        conn = client.warehouse_connection(env_key)
    except ApiError as e:
        checks.append(Check("warehouse", "block", f"cannot read warehouse connections: {e}",
                            "verify the API URL and token"))
        return checks, None
    if not conn:
        checks.append(Check("warehouse", "block", "no warehouse connection on this environment",
                            "connect the Flagsmith database or your own warehouse before experiments"))
    elif conn.get("status") != "connected":
        checks.append(Check("warehouse", "block",
                            f"warehouse status is {conn.get('status')!r} ({conn.get('status_detail') or 'no detail'})",
                            "send one event, or fix the connection and test it"))
    else:
        checks.append(Check("warehouse", "pass", f"{conn.get('warehouse_type')} connection is connected"))

    # 2. experiment running + rollout
    try:
        exp = client.experiment(env_key, experiment_id)
    except ApiError as e:
        checks.append(Check("experiment", "block", f"cannot read experiment {experiment_id}: {e}", "check the id and env key"))
        return checks, None
    status = exp.get("status")
    if status == "running":
        checks.append(Check("experiment", "pass", "experiment is running"))
    else:
        checks.append(Check("experiment", "block", f"experiment status is {status!r}",
                            "start it (POST .../start/) before collecting data"))

    rollout = exp.get("experiment_rollout") or {}
    if rollout.get("enabled") is False or float(rollout.get("rollout_percentage") or 0) <= 0:
        checks.append(Check("rollout", "block", "rollout is disabled or 0%, nobody enters the experiment",
                            "enable the rollout and expose traffic"))
    else:
        checks.append(Check("rollout", "pass", f"rollout at {rollout.get('rollout_percentage')}%"))

    # 3. variant keys + control
    feature_id = exp.get("feature")
    if isinstance(feature_id, dict):
        feature_id = feature_id.get("id")
    project_id = None
    try:
        feature = None
        # experiment payload embeds feature; fall back to project lookup via env
        if isinstance(exp.get("feature"), dict):
            feature = exp["feature"]
        if feature is None and isinstance(exp.get("feature_id"), int):
            feature = None
    except ApiError:
        feature = None

    if isinstance(feature, dict):
        options = feature.get("multivariate_options") or []
        keyless = [o for o in options if not o.get("key")]
        if keyless:
            checks.append(Check(
                "variant keys", "block",
                f"{len(keyless)} variation(s) have no key: {[o.get('string_value') for o in keyless]}",
                "every variation needs a key or it records no exposures (flagsmith feature variant add ... --key ...)"))
        elif options:
            checks.append(Check("variant keys", "pass", f"{len(options)} variation(s), all keyed"))
        control = [o for o in options if (o.get("key") or o.get("string_value")) == cfg.control_key]
        checks.append(Check("control variant", "pass" if control else "warn",
                            "control variant present" if control else
                            f"no variant keyed {cfg.control_key!r}; the stats engine will return no inference",
                            "name the baseline variant exactly 'control'" if not control else ""))

    # 4. metric event present in the warehouse
    metric_event = None
    metrics = exp.get("metrics") or []
    if metrics:
        metric_event = (metrics[0].get("metric") or {}).get("definition", {}).get("event") if isinstance(
            metrics[0].get("metric"), dict) else None
    if not metric_event:
        # experiment detail in this build exposes metrics as ids; use the metrics list
        try:
            rows = client._call("GET", f"/environments/{env_key}/experiment-metrics/")
            for m in rows.get("results", []):
                if m.get("id") == (metrics[0].get("metric") if metrics else None):
                    metric_event = (m.get("definition") or {}).get("event")
                    break
        except ApiError:
            pass
    if metric_event and conn:
        try:
            names = client.warehouse_events(env_key, conn["id"])
        except ApiError:
            names = []
        if names:
            if metric_event in names:
                checks.append(Check("metric event", "pass", f"event {metric_event!r} has arrived in the warehouse"))
            else:
                checks.append(Check("metric event", "block",
                                    f"metric event {metric_event!r} has never arrived; known events: {names[:6]}",
                                    "fix the event name (case-sensitive) or the call site that should send it"))
        else:
            checks.append(Check("metric event", "warn", "warehouse has no events yet", "send one event"))

    # 5. self-hosted events URL
    if events_url is not None:
        ok = events_url.rstrip("/").endswith("/api")
        checks.append(Check("events url", "pass" if ok else "block",
                            f"events url {events_url!r} {'targets' if ok else 'does not target'} your instance",
                            "" if ok else "self-hosted SDKs must send events to <api>/api/ (default is Flagsmith Cloud)"))

    # 6. live data checks
    verdict = None
    try:
        payload = client.results(env_key, experiment_id)
        verdict = decide(payload, cfg)
        age = _age_minutes((payload.get("results") or {}).get("as_of"))
        if age is None:
            checks.append(Check("results", "warn", "no results computed yet", "refresh results, then retry in a few seconds"))
        elif age > 30:
            checks.append(Check("results", "warn", f"results are {age:.0f} min old (refresh is throttled to 1/5 min)",
                                "refresh results"))
        else:
            checks.append(Check("results", "pass", f"results as of {age:.0f} min ago"))

        arm_n = {k: a.n for k, a in verdict.arms.items()}
        if arm_n:
            dead = [k for k, n in arm_n.items() if n == 0]
            checks.append(Check("traffic both arms", "block" if dead else "pass",
                                f"exposures per arm: {arm_n}" + (f" (dead: {dead})" if dead else ""),
                                "a dead arm is a wiring bug: check assignment and identifiers"))
            flat = [k for k, a in verdict.arms.items() if a.n >= cfg.n_min and a.total == 0]
            if flat:
                checks.append(Check("conversions", "warn",
                                    f"no conversions on {flat} despite n >= {cfg.n_min}",
                                    "the conversion event is not firing or uses a different identifier"))
            elif verdict.arms:
                checks.append(Check("conversions", "pass", "conversions recorded on every arm with enough traffic"))
            if verdict.srm_p is not None:
                status = "block" if verdict.srm_p < cfg.srm_alpha else "pass"
                checks.append(Check("sample ratio", status,
                                    f"chi-square p={verdict.srm_p:.3g} against configured split",
                                    "" if status == "pass" else "do not act on results; investigate the split"))
            elif cfg.weights:
                checks.append(Check("sample ratio", "warn", "split weights unknown, SRM not computable",
                                    "put configured weights in verdict.toml"))
    except (ApiError, ValueError) as e:
        checks.append(Check("results", "warn", f"could not read results: {e}", "check the experiment id"))

    return checks, verdict


def summarise(checks: list[Check]) -> str:
    blocks = [c for c in checks if c.status == "block"]
    warns = [c for c in checks if c.status == "warn"]
    label = "BLOCKED" if blocks else ("WARN" if warns else "PASS")
    return f"{label}: {len(blocks)} block, {len(warns)} warn, {len(checks) - len(blocks) - len(warns)} pass"
