"""Drive shoppers through the demo shop and print what each arm did.

A fixture, not a feature: it opens a session, reads the variant Flagsmith
served, then decides whether this shopper buys and whether they ask for a
refund. The conversion and refund probabilities are inputs, so the same driver
can produce a clean win and a guardrail regression without touching the app.

    python demo/load.py --page / --sessions 1000 --control-rate .10 --red-rate .18
    python demo/load.py --page /express --sessions 1000 \
        --control-rate .10 --red-rate .18 --control-refund .02 --red-refund .10
"""
from __future__ import annotations

import argparse
import json
import random
import re
import sys
import urllib.error
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from http.cookiejar import CookieJar

VARIANT_RE = re.compile(r'data-variant="([^"]+)"')


def one_session(index: int, base: str, page: str, args: argparse.Namespace) -> dict:
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CookieJar()))
    result = {"variant": "unknown", "bought": False, "refunded": False, "error": None}
    try:
        with opener.open(base + page, timeout=30) as response:
            body = response.read().decode("utf-8", errors="replace")
        match = VARIANT_RE.search(body)
        if not match:
            result["error"] = "no data-variant in page"
            return result
        variant = match.group(1)
        result["variant"] = variant
        rng = random.Random(args.seed * 1_000_003 + index)

        buy_rate = args.control_rate if variant == "control" else args.red_rate
        if rng.random() < buy_rate:
            request = urllib.request.Request(base + "/buy", data=b"", method="POST")
            with opener.open(request, timeout=30) as response:
                response.read()
            result["bought"] = True

        refund_rate = args.control_refund if variant == "control" else args.red_refund
        if rng.random() < refund_rate:
            request = urllib.request.Request(base + "/refund", data=b"", method="POST")
            with opener.open(request, timeout=30) as response:
                response.read()
            result["refunded"] = True
    except (urllib.error.URLError, OSError, ValueError) as exc:
        result["error"] = str(exc)[:160]
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Drive demo-shop sessions")
    parser.add_argument("--base", default="http://localhost:8080")
    parser.add_argument("--page", default="/", choices=["/", "/express"])
    parser.add_argument("--sessions", type=int, default=1000)
    parser.add_argument("--control-rate", type=float, default=0.10)
    parser.add_argument("--red-rate", type=float, default=0.18)
    parser.add_argument("--control-refund", type=float, default=0.02)
    parser.add_argument("--red-refund", type=float, default=0.02)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--workers", type=int, default=12)
    parser.add_argument("--flush", action="store_true", default=True)
    args = parser.parse_args(argv)

    counts: Counter[str] = Counter()
    bought: Counter[str] = Counter()
    refunded: Counter[str] = Counter()
    errors: Counter[str] = Counter()

    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futures = [pool.submit(one_session, i, args.base.rstrip("/"), args.page, args)
                   for i in range(args.sessions)]
        for future in as_completed(futures):
            row = future.result()
            variant = row["variant"]
            counts[variant] += 1
            if row["bought"]:
                bought[variant] += 1
            if row["refunded"]:
                refunded[variant] += 1
            if row["error"]:
                errors[row["error"]] += 1

    if args.flush:
        try:
            request = urllib.request.Request(args.base.rstrip("/") + "/flush", data=b"", method="POST")
            with urllib.request.urlopen(request, timeout=60):
                pass
        except (urllib.error.URLError, OSError) as exc:
            print(f"flush failed: {exc}", file=sys.stderr)

    print(json.dumps({
        "page": args.page,
        "sessions": args.sessions,
        "variants": dict(counts),
        "purchases": dict(bought),
        "refunds": dict(refunded),
        "rates": {
            variant: {
                "purchase": round(bought[variant] / counts[variant], 4) if counts[variant] else 0,
                "refund": round(refunded[variant] / counts[variant], 4) if counts[variant] else 0,
            }
            for variant in counts
        },
        "errors": dict(errors),
    }, indent=2))
    return 1 if errors and sum(errors.values()) == args.sessions else 0


if __name__ == "__main__":
    raise SystemExit(main())
