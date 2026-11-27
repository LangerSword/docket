"""One command for the whole walkthrough.

    python demo/demo.py

Brings up self-hosted Flagsmith, creates an admin, seeds two live experiments
and a deliberately broken one, starts the shop against the real SDK, drives
traffic, refreshes the statistics, then runs audit and gate for you. `ship` is
left as an explicit command: the tool never ships without a human asking.

Options:
    --staged        first show a COLLECTING refusal, wait out the 5 min
                    refresh throttle, then load the rest and show the winner
    --skip-express  skip the guardrail experiment
    --sessions N    sessions per experiment (default 1600, i.e. ~800 per arm)
    --no-build      reuse the existing demo image
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
COMPOSE = ROOT / "docker-compose.yml"
BASE = "http://localhost:8000"
SHOP = f"http://localhost:{os.environ.get('DEMO_PORT', '8080')}"
EMAIL = os.environ.get("DEMO_EMAIL", "admin@demo.local")
PASSWORD = os.environ.get("DEMO_PASSWORD", "demo-password")


def run(command: list[str], check: bool = True) -> int:
    print("$ " + " ".join(command), flush=True)
    result = subprocess.run(command, cwd=ROOT)
    if check and result.returncode != 0:
        raise SystemExit(f"command failed ({result.returncode}): {' '.join(command)}")
    return result.returncode


def compose(*args: str) -> None:
    run(["docker", "compose", "-f", str(COMPOSE), *args])


def http_json(url: str, method: str = "GET", body: dict | None = None,
              token: str | None = None, timeout: int = 30) -> tuple[int, object]:
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(url, data=data, method=method)
    request.add_header("Content-Type", "application/json")
    if token:
        request.add_header("Authorization", f"Token {token}")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
            return response.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode(errors="replace")


def wait_http(url: str, timeout: int, label: str) -> None:
    deadline = time.time() + timeout
    last = ""
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=5) as response:
                if response.status == 200:
                    print(f"{label} is up", flush=True)
                    return
        except (urllib.error.URLError, OSError) as exc:
            last = str(exc)
        time.sleep(2)
    raise SystemExit(f"{label} did not come up in {timeout}s: {last}")


def ensure_admin() -> None:
    snippet = (
        "from users.models import FFAdminUser; "
        f"email = {EMAIL!r}; "
        "FFAdminUser.objects.filter(email=email).exists() or "
        f"FFAdminUser.objects.create_superuser(email=email, password={PASSWORD!r}, "
        "first_name='Demo', last_name='Admin')"
    )
    run(["docker", "compose", "-f", str(COMPOSE), "exec", "-T", "flagsmith",
         "flagsmith", "shell", "-c", snippet])


def docket(args: list[str], config: Path) -> int:
    command = [sys.executable, "-m", "docket.cli", "--config", str(config), *args]
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    env.setdefault("DOCKET_EMAIL", EMAIL)
    env.setdefault("DOCKET_PASSWORD", PASSWORD)
    print(f"\n$ docket {' '.join(args)}  (config {config.relative_to(ROOT)})", flush=True)
    return subprocess.run(command, cwd=ROOT, env=env).returncode


def load(page: str, sessions: int, control_rate: float, red_rate: float,
         control_refund: float, red_refund: float, seed: int) -> None:
    run([sys.executable, str(HERE / "load.py"), "--base", SHOP, "--page", page,
         "--sessions", str(sessions), "--control-rate", str(control_rate),
         "--red-rate", str(red_rate), "--control-refund", str(control_refund),
         "--red-refund", str(red_refund), "--seed", str(seed)])


def admin_token() -> str:
    status, data = http_json(BASE + "/api/v1/auth/login/", "POST",
                             {"email": EMAIL, "password": PASSWORD})
    if status != 200 or not isinstance(data, dict):
        raise SystemExit(f"login failed: {status} {data}")
    return data["key"]


def refresh(experiment: int, token: str) -> None:
    ekey = json.loads((HERE / "seed.json").read_text())["environment_key"]
    path = f"/api/v1/environments/{ekey}/experiments/{experiment}/results"
    status, before = http_json(BASE + path + "/", token=token)
    old_as_of = ((before.get("results") or {}) if isinstance(before, dict) else {}).get("as_of")

    for attempt in range(2):
        status, data = http_json(BASE + path + "/refresh/", "POST", token=token)
        if status == 202:
            break
        if status == 429:
            match = re.search(r"available in (\d+) seconds", str(data))
            wait = int(match.group(1)) + 2 if match else 302
            print(f"refresh throttled; waiting {wait}s (Flagsmith allows one per 5 minutes)", flush=True)
            time.sleep(wait)
            continue
        raise SystemExit(f"refresh failed: {status} {data}")

    deadline = time.time() + 120
    while time.time() < deadline:
        status, after = http_json(BASE + path + "/", token=token)
        inner = (after.get("results") or {}) if isinstance(after, dict) else {}
        if inner.get("as_of") and inner.get("as_of") != old_as_of and inner.get("payload"):
            print(f"results refreshed: as_of {inner['as_of']}", flush=True)
            return
        time.sleep(2)
    raise SystemExit("results did not refresh in 120s; check the task processor logs")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the Docket demo end to end")
    parser.add_argument("--staged", action="store_true")
    parser.add_argument("--skip-express", action="store_true")
    parser.add_argument("--sessions", type=int, default=1600)
    parser.add_argument("--no-build", action="store_true")
    parser.add_argument("--bootstrap-only", action="store_true")
    args = parser.parse_args(argv)

    compose("up", "-d")
    wait_http(BASE + "/health", 240, "Flagsmith")
    ensure_admin()
    if args.bootstrap_only:
        print(f"admin ready: {EMAIL} / {PASSWORD}")
        return 0

    run([sys.executable, str(HERE / "seed.py")])
    build = [] if args.no_build else ["--build"]
    compose("--profile", "app", "up", "-d", "--force-recreate", *build, "demo")
    wait_http(SHOP + "/healthz", 120, "demo shop")

    token = admin_token()
    seed = json.loads((HERE / "seed.json").read_text())
    checkout = seed["checkout"]["experiment"]
    express = seed["express"]["experiment"]

    if args.staged:
        print("\n-- stage 1: a small cohort, so the gate must refuse to call it --")
        load("/", 80, 0.09, 0.20, 0.02, 0.02, seed=11)
        refresh(checkout, token)
        docket(["audit", "--events-url", BASE + "/api"], ROOT / "demo" / "docket.toml")
        docket(["gate"], ROOT / "demo" / "docket.toml")
        print("\n-- stage 2: the rest of the traffic --")
        load("/", max(1, args.sessions - 80), 0.09, 0.20, 0.02, 0.02, seed=12)
        refresh(checkout, token)
    else:
        load("/", args.sessions, 0.09, 0.20, 0.02, 0.02, seed=11)
        refresh(checkout, token)

    print("\n== checkout experiment ==")
    docket(["audit", "--events-url", BASE + "/api"], ROOT / "demo" / "docket.toml")
    docket(["gate"], ROOT / "demo" / "docket.toml")

    if not args.skip_express:
        load("/express", args.sessions, 0.09, 0.20, 0.02, 0.10, seed=21)
        refresh(express, token)
        print("\n== guardrail experiment (one-tap checkout looks great, refunds regress) ==")
        docket(["audit", "--events-url", BASE + "/api"], ROOT / "demo" / "docket.express.toml")
        docket(["gate"], ROOT / "demo" / "docket.express.toml")

    print("\n== deliberately broken wiring ==")
    docket(["audit", "--events-url", "https://events.api.flagsmith.com/api"],
            ROOT / "demo" / "docket.broken.toml")

    print(f"""
Done. The gate is a decision, not a dashboard: `ship` is still yours to give.

  docket --config demo/docket.toml ship --confirm --record demo/decision.json
  open http://localhost:8000  (admin@demo.local / demo-password)
  open {SHOP}  (two variants: / and /express)
""")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
