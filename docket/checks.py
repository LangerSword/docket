"""Audit checks: the silent failures the Flagsmith docs themselves name."""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone

from .client import ApiError, Flagsmith
from .rules import RuleConfig, Decision, decide, weights_from_experiment


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


def _metric_events(client: Flagsmith, env_key: str, exp: dict) -> dict[int, str]:
    """Map metric id -> the event name the app must send, per experiment metric."""
    ids = []
    for row in exp.get("metrics") or []:
        metric = row.get("metric")
        mid = metric.get("id") if isinstance(metric, dict) else metric
        if isinstance(mid, int):
            ids.append(mid)
    if not ids:
        return {}
    try:
        rows = client._call("GET", f"/environments/{env_key}/experiment-metrics/?page_size=100")
    except ApiError:
        return {}
    items = rows.get("results", rows) if isinstance(rows, dict) else rows
    result: dict[int, str] = {}
    for m in items:
        mid = m.get("id")
        event = (m.get("definition") or {}).get("event")
        if isinstance(mid, int) and mid in ids and event:
            result[mid] = event
    return result


def run_audit(client: Flagsmith, env_key: str, experiment_id: int,
              cfg: RuleConfig | None = None, events_url: str | None = None) -> tuple[list[Check], Decision | None]:
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

    # 3. variants: every variation keyed, a usable control value, sane split
    feature = exp.get("feature") if isinstance(exp.get("feature"), dict) else None
    if feature:
        options = feature.get("multivariate_options") or []
        keyless = [o for o in options if not o.get("key")]
        if keyless:
            checks.append(Check(
                "variant keys", "block",
                f"{len(keyless)} variation(s) have no key: {[o.get('string_value') for o in keyless]}",
                "set a key on every variation, or it records no exposures"))
        elif options:
            checks.append(Check("variant keys", "pass", f"{len(options)} variation(s), all keyed"))

        control_value = (rollout.get("feature_state_value") or {}).get("value") \
            if isinstance(rollout.get("feature_state_value"), dict) else feature.get("initial_value")
        if control_value not in (None, ""):
            checks.append(Check("control variant", "pass",
                                f"control is the feature's own value {control_value!r}"))
        else:
            checks.append(Check("control variant", "warn",
                                "the feature has no default value; control traffic cannot be attributed",
                                "give the feature an initial value (conventionally 'control')"))

        allocated = sum(float(a.get("percentage_allocation") or 0)
                        for a in rollout.get("multivariate_feature_state_values") or [])
        if options and allocated >= 100:
            checks.append(Check("allocation", "block",
                                f"variations claim {allocated:g}% of traffic; control gets none",
                                "leave part of the rollout to the control value"))
        else:
            checks.append(Check("allocation", "pass",
                                f"variations {allocated:g}%, control {100 - allocated:g}%"))

    # 4. every metric event has actually arrived in the warehouse
    events = _metric_events(client, env_key, exp)
    if events and conn:
        try:
            names = client.warehouse_events(env_key, conn["id"])
        except ApiError:
            names = []
        if names:
            missing = [e for e in events.values() if e not in names]
            if missing:
                checks.append(Check("metric events", "block",
                                    f"never arrived: {missing}; known: {names[:6]}",
                                    "fix the event name (case-sensitive) or the call site that should send it"))
            else:
                checks.append(Check("metric events", "pass",
                                    f"{', '.join(sorted(set(events.values())))} arrived in the warehouse"))
        else:
            checks.append(Check("metric events", "warn", "warehouse has no events yet", "send one event"))
    elif events:
        checks.append(Check("metric events", "warn", "no warehouse connection; cannot verify event names",
                            "connect a warehouse"))

    # 5. self-hosted events URL
    if events_url is not None:
        from urllib.parse import urlparse
        parsed = urlparse(events_url)
        host = (parsed.netloc or "").lower()
        cloud = host.endswith("flagsmith.com") or host.endswith("flagsmith.io")
        path_ok = parsed.path.rstrip("/").endswith("/api")
        ok = path_ok and not cloud
        if cloud:
            detail = f"events url {events_url!r} points at Flagsmith Cloud; self-hosted events never arrive"
            fix = "point the SDK's events URL at <your instance>/api/"
        elif not path_ok:
            detail = f"events url {events_url!r} does not end in /api/"
            fix = "self-hosted SDKs append v1/events, so the events URL must end in /api/"
        else:
            detail = f"events url {events_url!r} targets your instance"
            fix = ""
        checks.append(Check("events url", "pass" if ok else "block", detail, fix))

    # 6. live data checks
    docket = None
    try:
        if not cfg.weights:
            cfg = replace(cfg, weights=weights_from_experiment(exp))
        payload = client.results(env_key, experiment_id)
        docket = decide(payload, cfg)
        age = _age_minutes((payload.get("results") or {}).get("as_of"))
        if age is None:
            checks.append(Check("results", "warn", "no results computed yet", "refresh results, then retry in a few seconds"))
        elif age > 30:
            checks.append(Check("results", "warn", f"results are {age:.0f} min old (refresh is throttled to 1/5 min)",
                                "refresh results"))
        else:
            checks.append(Check("results", "pass", f"results as of {age:.0f} min ago"))

        arm_n = {k: a.n for k, a in docket.arms.items()}
        if arm_n:
            dead = [k for k, n in arm_n.items() if n == 0]
            checks.append(Check("traffic both arms", "block" if dead else "pass",
                                f"exposures per arm: {arm_n}" + (f" (dead: {dead})" if dead else ""),
                                "a dead arm is a wiring bug: check assignment and identifiers"))
            flat = [k for k, a in docket.arms.items() if a.n >= cfg.n_min and a.total == 0]
            if flat:
                checks.append(Check("conversions", "warn",
                                    f"no conversions on {flat} despite n >= {cfg.n_min}",
                                    "the conversion event is not firing or uses a different identifier"))
            elif docket.arms:
                checks.append(Check("conversions", "pass", "conversions recorded on every arm with enough traffic"))
            if docket.srm_p is not None:
                status = "block" if docket.srm_p < cfg.srm_alpha else "pass"
                checks.append(Check("sample ratio", status,
                                    f"chi-square p={docket.srm_p:.3g} against configured split",
                                    "" if status == "pass" else "do not act on results; investigate the split"))
            elif cfg.weights:
                checks.append(Check("sample ratio", "warn", "split weights unknown, SRM not computable",
                                    "put configured weights in docket.toml"))
    except (ApiError, ValueError) as e:
        checks.append(Check("results", "warn", f"could not read results: {e}", "check the experiment id"))

    return checks, docket


def summarise(checks: list[Check]) -> str:
    blocks = [c for c in checks if c.status == "block"]
    warns = [c for c in checks if c.status == "warn"]
    label = "BLOCKED" if blocks else ("WARN" if warns else "PASS")
    return f"{label}: {len(blocks)} block, {len(warns)} warn, {len(checks) - len(blocks) - len(warns)} pass"
