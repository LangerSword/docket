"""verdict CLI: audit, gate, ship. Exit codes are the contract."""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import tomllib
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from .attest import sign_record, verify_record
from .checks import run_audit, summarise
from .client import ApiError, Flagsmith
from .ledger import append as ledger_append, read as ledger_read, verify_all as ledger_verify_all
from .report import render
from .rules import EXIT_CODES, RuleConfig, decide, weights_from_experiment


def load_config(path: Path | None) -> tuple[dict, RuleConfig]:
    raw: dict = {}
    if path and path.exists():
        raw = tomllib.loads(path.read_text())
    rule = raw.get("rule") or {}
    cfg = RuleConfig(
        control_key=rule.get("control_key", "control"),
        n_min=int(rule.get("n_min", 50)),
        c_min=int(rule.get("c_min", 5)),
        win_threshold=float(rule.get("win_threshold", 0.95)),
        expected_direction=rule.get("expected_direction", "increase"),
        weights={k: float(v) for k, v in (rule.get("weights") or {}).items()},
        guardrails=rule.get("guardrails") or [],
    )
    return raw, cfg


def make_client(args, raw: dict | None = None) -> Flagsmith:
    raw = raw or {}
    base = (args.base or os.environ.get("VERDICT_BASE")
            or (raw.get("flagsmith") or {}).get("base") or "http://localhost:8000")
    token = args.token or os.environ.get("VERDICT_TOKEN", "")
    if token:
        return Flagsmith(base=base, token=token)
    email = os.environ.get("VERDICT_EMAIL")
    password = os.environ.get("VERDICT_PASSWORD")
    if not (email and password):
        raise SystemExit("no credentials: set VERDICT_TOKEN or VERDICT_EMAIL/VERDICT_PASSWORD")
    return Flagsmith.login(base, email, password)


def print_verdict(v) -> None:
    print(f"VERDICT: {v.state.value.upper()}  (exit {v.exit_code})")
    if v.winner:
        print(f"  leading variant: {v.winner}")
    for r in v.reasons:
        print(f"  - {r}")
    if v.more_traffic_needed:
        print(f"  rough extra identities needed on the leader: ~{v.more_traffic_needed}")
    if v.as_of:
        print(f"  as of: {v.as_of}")


def cmd_audit(args, cfg: RuleConfig) -> int:
    client = make_client(args, getattr(args, "_raw", None))
    checks, verdict = run_audit(client, args.env, args.experiment, cfg,
                                events_url=args.events_url)
    for c in checks:
        mark = {"pass": "PASS", "warn": "WARN", "block": "BLOCK", "manual": "MANUAL"}[c.status]
        print(f"[{mark:6}] {c.name}: {c.detail}")
        if c.fix and c.status != "pass":
            print(f"         fix: {c.fix}")
    print(summarise(checks))
    if args.json:
        print(json.dumps({"checks": [vars(c) for c in checks],
                          "verdict": verdict.to_dict() if verdict else None}, indent=2))
    any_block = any(c.status == "block" for c in checks)
    return 2 if any_block else 0


def decide_with_client(client: Flagsmith, env: str, experiment: int, cfg: RuleConfig):
    """Decide against the latest stored snapshot, deriving the split if needed."""
    if not cfg.weights:
        try:
            cfg = replace(cfg, weights=weights_from_experiment(client.experiment(env, experiment)))
        except ApiError:
            pass
    return decide(client.results(env, experiment), cfg)


def cmd_gate(args, cfg: RuleConfig) -> int:
    client = make_client(args, getattr(args, "_raw", None))
    v = decide_with_client(client, args.env, args.experiment, cfg)
    if args.json:
        print(json.dumps(v.to_dict(), indent=2))
    else:
        print_verdict(v)
    return v.exit_code


def cmd_ship(args, cfg: RuleConfig) -> int:
    client = make_client(args, getattr(args, "_raw", None))
    v = decide_with_client(client, args.env, args.experiment, cfg)
    if v.state.value != "winner":
        print_verdict(v)
        print("REFUSING TO SHIP: the verdict is not WINNER")
        return v.exit_code
    if not args.confirm:
        print_verdict(v)
        print("Would ship. Re-run with --confirm to end the experiment and roll out the winner.")
        return 0

    signing_key = None
    if args.sign:
        signing_key = os.environ.get("VERDICT_SIGNING_KEY", "")
        if not signing_key:
            print("REFUSING TO SIGN: set VERDICT_SIGNING_KEY before using --sign",
                  file=sys.stderr)
            return 3

    exp = client.experiment(args.env, args.experiment)
    if exp.get("status") == "completed":
        print("REFUSING: experiment already completed (results are final)")
        return 2
    feature = exp.get("feature") if isinstance(exp.get("feature"), dict) else None
    if not feature or not feature.get("id"):
        print("REFUSING: cannot resolve the feature from the experiment payload")
        return 3
    fid = feature["id"]
    options = feature.get("multivariate_options") or []
    winner = v.winner
    match = next((o for o in options
                  if winner in (o.get("key"), o.get("string_value"))), None)
    if not match:
        print(f"REFUSING: winner {winner!r} does not map to a variation option")
        return 3
    state = client.feature_state(args.env, fid)
    client.complete_experiment(args.env, args.experiment)
    value = match.get("string_value") or winner
    client.set_winner(args.env, state["id"], winner, match["id"], value)

    record = {
        "tool": "verdict",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "experiment": args.experiment,
        "environment": args.env,
        "as_of": v.as_of,
        "verdict": v.to_dict(),
        "mutation": {"feature": fid, "feature_state": state["id"],
                     "winner": winner, "value": value, "allocation": 100},
        "actor": os.environ.get("VERDICT_ACTOR", os.environ.get("GITHUB_ACTOR", "local")),
    }
    if signing_key:
        record = sign_record(record, signing_key)
    out = Path(args.record or f"verdict-{args.experiment}-{int(time.time())}.json")
    out.write_text(json.dumps(record, indent=2))
    md = out.with_suffix(".md")
    signature = record.get("signature") or {}
    signature_line = (
        f"- signature: {signature.get('alg')} key {signature.get('key_id')} "
        f"(verify with `verdict verify {out.name}`)\n" if signature else "")
    md.write_text(
        f"# Ship decision: experiment {args.experiment}\n\n"
        f"- verdict: **{v.state.value}**\n- leading variant: `{winner}`\n"
        f"- as of: {v.as_of}\n"
        + "".join(f"- {r}\n" for r in v.reasons)
        + f"- mutation: feature {fid}, state {state['id']}, allocation 100/0\n"
        + signature_line)
    signed = f", signed {signature['key_id']}" if signature else ""
    if args.ledger:
        try:
            ledger_append(Path(args.ledger), record)
        except OSError as e:
            print(f"WARNING: ship succeeded but the ledger append failed: {e}", file=sys.stderr)
    print(f"SHIPPED {winner}: experiment ended, weight 100/0, record {out} and {md}{signed}")
    return 0


def cmd_ledger(args, cfg: RuleConfig) -> int:
    path = Path(args.path)
    try:
        records = ledger_read(path)
    except (OSError, ValueError) as e:
        print(f"cannot read ledger: {e}", file=sys.stderr)
        return 3
    if args.action == "list":
        if not records:
            print(f"ledger {path} is empty")
            return 0
        for record in records:
            verdict = record.get("verdict") or {}
            mutation = record.get("mutation") or {}
            signature = record.get("signature") or {}
            print(f"{record.get('generated_at', '?')}  experiment {record.get('experiment')}  "
                  f"{verdict.get('state')}  -> {mutation.get('winner')}  "
                  f"{signature.get('key_id', 'unsigned')}")
        return 0
    key = os.environ.get("VERDICT_SIGNING_KEY", "")
    if not key:
        print("set VERDICT_SIGNING_KEY to verify the ledger", file=sys.stderr)
        return 3
    results = ledger_verify_all(records, key)
    bad = 0
    for index, (ok, message) in enumerate(results, start=1):
        record = records[index - 1]
        status = "VALID" if ok else "INVALID"
        print(f"{index:>3}. {status:7} experiment {record.get('experiment')}: {message}")
        if not ok:
            bad += 1
    print(f"{len(records)} record(s), {bad} invalid")
    return 0 if bad == 0 else 2


def cmd_verify(args, cfg: RuleConfig) -> int:
    path = Path(args.record)
    try:
        record = json.loads(path.read_text())
    except OSError as e:
        print(f"cannot read {path}: {e}", file=sys.stderr)
        return 3
    except json.JSONDecodeError as e:
        print(f"{path} is not valid JSON: {e}", file=sys.stderr)
        return 3
    if not isinstance(record, dict):
        print(f"{path} does not contain a decision record object", file=sys.stderr)
        return 3
    key = os.environ.get("VERDICT_SIGNING_KEY", "")
    if not key:
        print("set VERDICT_SIGNING_KEY to the key used when the record was signed",
              file=sys.stderr)
        return 3
    ok, message = verify_record(record, key)
    print(f"DECISION RECORD: {'VALID' if ok else 'INVALID'}")
    print(f"  {message}")
    if ok:
        verdict = record.get("verdict") or {}
        print(f"  state {verdict.get('state')} (exit {verdict.get('exit_code')}), "
              f"experiment {record.get('experiment')}")
        mutation = record.get("mutation") or {}
        print(f"  mutation feature {mutation.get('feature')} -> {mutation.get('winner')} "
              f"at {mutation.get('allocation')}%")
    return 0 if ok else 2


def cmd_init(args, cfg: RuleConfig) -> int:
    default_base = os.environ.get("VERDICT_BASE", "http://localhost:8000")
    base = args.base or input(f"Flagsmith URL [{default_base}]: ").strip() or default_base
    env = args.env or input("Environment key: ").strip()
    exp_raw = args.experiment if args.experiment is not None else input("Experiment id: ").strip()
    exp = int(exp_raw)
    weights = (args.weights if args.weights is not None
               else input("Configured split as key=pct,key=pct (blank to skip): ").strip())
    dashboard = args.dashboard or base
    lines = [
        "[flagsmith]",
        f'base = "{base}"',
        f'environment = "{env}"',
        "",
        "[experiment]",
        f"id = {exp}",
        f'dashboard = "{dashboard}"',
        "",
        "[rule]",
        "n_min = 50",
        "c_min = 5",
        "win_threshold = 0.95",
        'expected_direction = "increase"',
    ]
    if weights:
        pairs = [p.split("=", 1) for p in weights.split(",") if "=" in p]
        lines.append("weights = { " + ", ".join(f"{k.strip()} = {float(v)}" for k, v in pairs) + " }")
    out = args.out or Path("verdict.toml")
    out.write_text("\n".join(lines) + "\n")
    print(f"wrote {out}")
    print("next: export VERDICT_TOKEN=<api token> (or VERDICT_EMAIL/VERDICT_PASSWORD), then `verdict audit`")
    token = os.environ.get("VERDICT_TOKEN")
    if token:
        try:
            c = Flagsmith(base=base, token=token)
            e = c.experiment(env, exp)
            print(f"connected: experiment {exp} status {e.get('status')!r}")
        except ApiError as err:
            print(f"WARNING: could not verify the connection: {err}")
    return 0


def cmd_report(args, cfg: RuleConfig) -> int:
    client = make_client(args, getattr(args, "_raw", None))
    checks, verdict = run_audit(client, args.env, args.experiment, cfg,
                                events_url=args.events_url)
    if verdict is None:
        verdict = decide(client.results(args.env, args.experiment), cfg)
    project = args.project or ((getattr(args, "_raw", {}) or {}).get("experiment") or {}).get("project") or 1
    html_text = render(verdict.to_dict(), [vars(c) for c in checks] if checks else None,
                       args.experiment, args.env,
                       args.dashboard or "http://localhost:8000", project=int(project))
    out = Path(args.out or f"verdict-{args.experiment}.html")
    out.write_text(html_text)
    print(f"report written: {out.resolve()}")
    return 2 if any(c.status == "block" for c in checks) else 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="verdict", description="Experiment lifecycle control for Flagsmith")
    p.add_argument("--base", help="Flagsmith base URL (default: verdict.toml, $VERDICT_BASE, localhost:8000)")
    p.add_argument("--token", help="API token (default $VERDICT_TOKEN)")
    p.add_argument("--config", type=Path, default=Path("verdict.toml"))
    p.add_argument("--version", action="version", version="verdict 0.1.0")
    sub = p.add_subparsers(dest="command", required=True)

    def add_common(sp, need_experiment: bool = True):
        sp.add_argument("--env", help="environment key (default: verdict.toml)")
        if need_experiment:
            sp.add_argument("--experiment", type=int, help="experiment id (default: verdict.toml)")
        sp.add_argument("--json", action="store_true")

    sp_init = sub.add_parser("init", help="write verdict.toml")
    sp_init.add_argument("--env")
    sp_init.add_argument("--experiment", type=int)
    sp_init.add_argument("--dashboard")
    sp_init.add_argument("--weights")
    sp_init.add_argument("--out", type=Path)
    sp_init.set_defaults(func=cmd_init)

    sp_audit = sub.add_parser("audit", help="check wiring before trusting data")
    add_common(sp_audit)
    sp_audit.add_argument("--events-url", help="URL the app's SDK sends events to")
    sp_audit.set_defaults(func=cmd_audit)

    sp_gate = sub.add_parser("gate", help="decide whether the release may proceed")
    add_common(sp_gate)
    sp_gate.set_defaults(func=cmd_gate)

    sp_ship = sub.add_parser("ship", help="end the experiment and roll out the winner")
    add_common(sp_ship)
    sp_ship.add_argument("--confirm", action="store_true")
    sp_ship.add_argument("--record", help="path for the decision record")
    sp_ship.add_argument("--sign", action="store_true",
                         help="sign the decision record with $VERDICT_SIGNING_KEY")
    sp_ship.add_argument("--ledger", help="append the record to an audit ledger (JSONL)")
    sp_ship.set_defaults(func=cmd_ship)

    sp_verify = sub.add_parser("verify", help="verify a signed decision record")
    sp_verify.add_argument("record", type=Path, help="path to the decision record JSON")
    sp_verify.set_defaults(func=cmd_verify)

    sp_ledger = sub.add_parser("ledger", help="inspect or verify the release decision ledger")
    ledger_sub = sp_ledger.add_subparsers(dest="action", required=True)
    sp_ledger_list = ledger_sub.add_parser("list", help="list decisions in a ledger")
    sp_ledger_list.add_argument("path", type=Path)
    sp_ledger_list.set_defaults(func=cmd_ledger)
    sp_ledger_verify = ledger_sub.add_parser("verify", help="verify every signed record")
    sp_ledger_verify.add_argument("path", type=Path)
    sp_ledger_verify.set_defaults(func=cmd_ledger)

    sp_report = sub.add_parser("report", help="write a standalone HTML decision report")
    add_common(sp_report)
    sp_report.add_argument("--events-url")
    sp_report.add_argument("--out", help="output html path")
    sp_report.add_argument("--dashboard", help="Flagsmith dashboard URL for the deep link")
    sp_report.add_argument("--project", type=int, help="Flagsmith project id for the deep link (default 1)")
    sp_report.set_defaults(func=cmd_report)

    args = p.parse_args(argv)
    raw, cfg = load_config(args.config)
    args._raw = raw
    fs = raw.get("flagsmith") or {}
    ex = raw.get("experiment") or {}
    args.env = getattr(args, "env", None) or fs.get("environment")
    if hasattr(args, "experiment") and args.experiment is None:
        args.experiment = ex.get("id")
    if args.command in {"audit", "gate", "ship", "report"} and not (args.env and args.experiment):
        p.error("--env and --experiment are required (or provide them in verdict.toml)")
    try:
        return args.func(args, cfg)
    except ApiError as e:
        print(f"ERROR {e.status}: {e}", file=sys.stderr)
        return 3
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
