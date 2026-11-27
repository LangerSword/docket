"""Seed the demo Flagsmith instance: two live experiments, one broken fixture.

Everything is done over the public REST API with an admin token, so the same
script works against any self-hosted Flagsmith. Safe to re-run: it completes a
previous demo experiment before starting a fresh one, and reuses features,
metrics and the warehouse connection when they already exist.
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

BASE = os.environ.get("DEMO_BASE", "http://localhost:8000").rstrip("/")
API = BASE + "/api/v1"
EMAIL = os.environ.get("DEMO_EMAIL", "admin@demo.local")
PASSWORD = os.environ.get("DEMO_PASSWORD", "demo-password")
HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

PURCHASE = "purchase_completed"
REFUND = "refund_requested"
BROKEN_PURCHASE = "purchase_complete"  # deliberate typo for the audit fixture


def call(method: str, path: str, body: dict | None = None,
         token: str | None = None) -> tuple[int, Any]:
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(API + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Token {token}")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read()
            return r.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(errors="replace")


def unwrap(data: Any) -> list:
    if isinstance(data, dict):
        return list(data.get("results") or [])
    return list(data or [])


def login() -> str:
    status, data = call("POST", "/auth/login/", {"email": EMAIL, "password": PASSWORD})
    if status != 200 or not isinstance(data, dict):
        raise SystemExit(
            f"cannot log in as {EMAIL} ({status}): {str(data)[:200]}\n"
            "run `python demo/demo.py --bootstrap-only` or create the admin first")
    return data["key"]


def ensure_project(token: str) -> tuple[int, str, dict]:
    status, data = call("GET", "/projects/?page_size=100", token=token)
    projects = unwrap(data)
    if not projects:
        status, orgs = call("GET", "/organisations/?page_size=100", token=token)
        orgs = unwrap(orgs)
        if not orgs:
            status, org = call("POST", "/organisations/", {"name": "Docket Demo"}, token=token)
            if status not in (200, 201):
                raise SystemExit(f"cannot create organisation: {status} {org}")
            org_id = org["id"]
        else:
            org_id = orgs[0]["id"]
        status, project = call("POST", "/projects/", {"name": "Docket Demo", "organisation": org_id}, token=token)
        if status not in (200, 201):
            raise SystemExit(f"cannot create project: {status} {project}")
        projects = [project]
    project = projects[0]
    pid = project["id"]

    status, data = call("GET", f"/projects/{pid}/environments/?page_size=100", token=token)
    envs = unwrap(data)
    if not envs:
        status, env = call("POST", "/environments/", {"name": "Development", "project": pid}, token=token)
        if status not in (200, 201):
            raise SystemExit(f"cannot create environment: {status} {env}")
        envs = [env]
    env = envs[0]
    ekey = env["api_key"]

    status, data = call("GET", f"/environments/{ekey}/warehouse-connections/", token=token)
    conns = unwrap(data)
    if not conns:
        status, conn = call(
            "POST", f"/environments/{ekey}/warehouse-connections/", token=token,
            body={"warehouse_type": "postgres", "config": {"source": "flagsmith_database"}})
        if status not in (200, 201):
            raise SystemExit(f"cannot create warehouse connection: {status} {conn}")
        conns = [conn]
    conn = conns[0]
    if conn.get("status") != "connected":
        call("POST", f"/environments/{ekey}/warehouse-connections/{conn['id']}/test-warehouse-connection/",
             token=token)
    return pid, ekey, env


def ensure_feature(pid: int, token: str, name: str, keyed: bool = True) -> tuple[int, int]:
    status, data = call("GET", f"/projects/{pid}/features/?page_size=100", token=token)
    features = unwrap(data)
    feature = next((f for f in features if f["name"] == name), None)
    if feature is None:
        status, feature = call(
            "POST", f"/projects/{pid}/features/", token=token,
            body={"name": name, "type": "MULTIVARIATE", "initial_value": "control",
                  "default_enabled": True})
        if status not in (200, 201):
            raise SystemExit(f"cannot create feature {name}: {status} {feature}")
    fid = feature["id"]

    status, data = call("GET", f"/projects/{pid}/features/{fid}/mv-options/?page_size=100", token=token)
    options = unwrap(data)
    option = next((o for o in options if o.get("string_value") == "red"), None)
    if option is None:
        body = {"feature": fid, "type": "unicode", "string_value": "red",
                "default_percentage_allocation": 50}
        if keyed:
            body["key"] = "red"
        status, option = call("POST", f"/projects/{pid}/features/{fid}/mv-options/", token=token, body=body)
        if status not in (200, 201):
            raise SystemExit(f"cannot create variation on {name}: {status} {option}")
    elif keyed and not option.get("key"):
        status, updated = call(
            "PUT", f"/projects/{pid}/features/{fid}/mv-options/{option['id']}/", token=token,
            body={"feature": fid, "type": "unicode", "string_value": "red", "key": "red",
                  "default_percentage_allocation": 50})
        if status in (200, 201):
            option = updated
        else:
            print(f"  warning: variation on {name} is keyless and could not be fixed: {status} {updated}")
    return fid, int(option["id"])


def ensure_metric(ekey: str, token: str, name: str, aggregation: str,
                  direction: str, description: str) -> int:
    status, data = call("GET", f"/environments/{ekey}/experiment-metrics/?page_size=100", token=token)
    metrics = unwrap(data)
    metric = next((m for m in metrics if m.get("name") == name), None)
    if metric:
        return metric["id"]
    status, metric = call(
        "POST", f"/environments/{ekey}/experiment-metrics/", token=token,
        body={"name": name, "description": description, "aggregation": aggregation,
              "direction": direction, "definition": {"version": 1, "event": name}})
    if status not in (200, 201):
        raise SystemExit(f"cannot create metric {name}: {status} {metric}")
    return metric["id"]


def running_experiment(ekey: str, token: str, fid: int) -> dict | None:
    status, data = call("GET", f"/environments/{ekey}/experiments/?page_size=100", token=token)
    for exp in unwrap(data):
        feature = exp.get("feature") or {}
        if feature.get("id") == fid and exp.get("status") == "running":
            return exp
    return None


def complete_running(ekey: str, token: str, fid: int) -> None:
    exp = running_experiment(ekey, token, fid)
    if exp:
        status, _ = call("POST", f"/environments/{ekey}/experiments/{exp['id']}/complete/", token=token)
        print(f"  completed previous experiment {exp['id']} on feature {fid} ({status})")


def start_experiment(ekey: str, token: str, fid: int, name: str, hypothesis: str,
                     primary_id: int, guardrail_id: int, red_id: int) -> int:
    complete_running(ekey, token, fid)
    body = {
        "feature": fid,
        "name": name,
        "hypothesis": hypothesis,
        "metrics": [
            {"metric": str(primary_id), "expected_direction": "increase"},
            {"metric": str(guardrail_id), "expected_direction": "decrease"},
        ],
        "experiment_rollout": {
            "enabled": True,
            "rollout_percentage": 100,
            "feature_state_value": {"value": "control", "type": "string"},
            "multivariate_feature_state_values": [
                {"multivariate_feature_option": red_id, "percentage_allocation": 50},
            ],
        },
    }
    status, exp = call("POST", f"/environments/{ekey}/experiments/", token=token, body=body)
    if status not in (200, 201):
        raise SystemExit(f"cannot create experiment {name}: {status} {exp}")
    xid = exp["id"]
    status, started = call("POST", f"/environments/{ekey}/experiments/{xid}/start/", token=token)
    if status not in (200, 201):
        raise SystemExit(f"cannot start experiment {xid}: {status} {started}")
    return xid


def toml_config(path: Path, ekey: str, exp_id: int, guardrail_id: int | None = None) -> None:
    lines = [
        "[flagsmith]",
        f'base = "{BASE}"',
        f'environment = "{ekey}"',
        "",
        "[experiment]",
        f"id = {exp_id}",
        f'dashboard = "{BASE}"',
        "",
        "[rule]",
        "n_min = 50",
        "c_min = 5",
        "win_threshold = 0.95",
        'expected_direction = "increase"',
        "weights = { control = 50, red = 50 }",
    ]
    if guardrail_id is not None:
        lines += [
            "guardrails = [",
            f'  {{ metric_id = {guardrail_id}, name = "{REFUND}", '
            'expected_direction = "not_increase", tolerance = 0.0 },',
            "]",
        ]
    path.write_text("\n".join(lines) + "\n")


def release_config(path: Path, ekey: str, checkout_id: int, express_id: int,
                   refund_id: int) -> None:
    lines = [
        "[flagsmith]",
        f'base = "{BASE}"',
        f'environment = "{ekey}"',
        "",
        "[release]",
        'name = "demo-release"',
        "require_signature = false",
        "",
    ]
    for xid, label in ((checkout_id, "checkout CTA"), (express_id, "express checkout")):
        lines += [
            "[[experiment]]",
            f"id = {xid}",
            f'label = "{label}"',
            "n_min = 50",
            "c_min = 5",
            "win_threshold = 0.95",
            'expected_direction = "increase"',
            "weights = { control = 50, red = 50 }",
            "guardrails = [",
            f'  {{ metric_id = {refund_id}, name = "{REFUND}", '
            'expected_direction = "not_increase", tolerance = 0.0 },',
            "]",
            "",
        ]
    path.write_text("\n".join(lines) + "\n")


def write_env(ekey: str) -> None:
    (HERE / "demo.env").write_text("\n".join([
        f"FLAGSMITH_ENV_KEY={ekey}",
        "FLAGSMITH_API_URL=http://flagsmith:8000/api/v1",
        "FLAGSMITH_EVENTS_URL=http://flagsmith:8000/api/",
        f"PURCHASE_EVENT={PURCHASE}",
        f"REFUND_EVENT={REFUND}",
        "FLAG_CHECKOUT=checkout_cta",
        "FLAG_EXPRESS=express_checkout",
        "",
    ]))


def main() -> int:
    print(f"seeding {BASE}")
    token = login()
    pid, ekey, env = ensure_project(token)
    print(f"project {pid}, environment {env.get('name')} ({ekey})")

    purchase_id = ensure_metric(ekey, token, PURCHASE, "occurrence", "up",
                                "A shopper completed checkout")
    refund_id = ensure_metric(ekey, token, REFUND, "occurrence", "down",
                              "A shopper asked for a refund (guardrail)")

    checkout_fid, checkout_red = ensure_feature(pid, token, "checkout_cta")
    express_fid, express_red = ensure_feature(pid, token, "express_checkout")
    checkout_xid = start_experiment(
        ekey, token, checkout_fid, "Checkout CTA copy",
        "A more direct call to action converts more shoppers without raising refunds",
        purchase_id, refund_id, checkout_red)
    express_xid = start_experiment(
        ekey, token, express_fid, "Express checkout copy",
        "One-tap checkout converts more shoppers, but must not raise refunds",
        purchase_id, refund_id, express_red)

    stamp = time.strftime("%H%M%S")
    broken_fid, broken_red = ensure_feature(pid, token, f"checkout_broken_{stamp}", keyed=False)
    broken_metric = ensure_metric(ekey, token, BROKEN_PURCHASE, "occurrence", "up",
                                  "Deliberately misnamed fixture metric")
    broken_xid = start_experiment(
        ekey, token, broken_fid, f"Broken wiring fixture {stamp}",
        "This experiment is wired wrong on purpose so audit has something to catch",
        broken_metric, refund_id, broken_red)

    write_env(ekey)
    toml_config(ROOT / "demo" / "docket.toml", ekey, checkout_xid, refund_id)
    toml_config(ROOT / "demo" / "docket.express.toml", ekey, express_xid, refund_id)
    toml_config(ROOT / "demo" / "docket.broken.toml", ekey, broken_xid)
    release_config(ROOT / "demo" / "docket.release.toml", ekey, checkout_xid, express_xid, refund_id)
    (HERE / "seed.json").write_text(json.dumps({
        "environment_key": ekey,
        "base": BASE,
        "purchase_metric": purchase_id,
        "refund_metric": refund_id,
        "checkout": {"experiment": checkout_xid, "feature": checkout_fid},
        "express": {"experiment": express_xid, "feature": express_fid},
        "broken": {"experiment": broken_xid, "feature": broken_fid,
                   "metric_event": BROKEN_PURCHASE},
    }, indent=2) + "\n")

    print(f"checkout experiment {checkout_xid} (running)")
    print(f"express experiment {express_xid} (running)")
    print(f"broken fixture experiment {broken_xid} (running, keyless variation)")
    print("wrote demo/docket.toml, demo/docket.express.toml, demo/docket.broken.toml, demo/docket.release.toml, demo/demo.env")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
