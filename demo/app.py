"""The demo shop: one page, two flags, real Flagsmith SDK traffic.

Every page view evaluates the flag through the Flagsmith Python SDK, records an
exposure event with the variant key the SDK resolved, and every purchase or
refund records a conversion event with the same identifier. Nothing about the
experiment is faked here: Flagsmith assigns the variant and owns the numbers.

This app is a fixture for the Docket walkthrough, not part of the tool.
"""
from __future__ import annotations

import atexit
import json
import os
import signal
import threading
import uuid
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from flagsmith import Flagsmith
from flagsmith.analytics import EventProcessorConfig

API_URL = os.environ.get("FLAGSMITH_API_URL", "http://localhost:8000/api/v1")
EVENTS_URL = os.environ.get("FLAGSMITH_EVENTS_URL", "http://localhost:8000/api/")
ENV_KEY = os.environ.get("FLAGSMITH_ENV_KEY", "")
PURCHASE_EVENT = os.environ.get("PURCHASE_EVENT", "purchase_completed")
REFUND_EVENT = os.environ.get("REFUND_EVENT", "refund_requested")
PORT = int(os.environ.get("PORT", "8080"))

PAGES = {
    "checkout": {
        "path": "/",
        "flag": os.environ.get("FLAG_CHECKOUT", "checkout_cta"),
        "eyebrow": "Tide & Timber / outerwear",
        "product": "Stormfield Jacket",
        "price": "$148",
        "blurb": "Waxed cotton, storm cuffs, a collar that stands up to the wind. "
                 "Cut for layering and made to be repaired, not replaced.",
        "control": {
            "cta": "Add to cart",
            "note": "Free returns within 30 days.",
        },
        "red": {
            "cta": "Buy now - free shipping",
            "note": "Order within 3 hours and it ships today.",
        },
    },
    "express": {
        "path": "/express",
        "flag": os.environ.get("FLAG_EXPRESS", "express_checkout"),
        "eyebrow": "Tide & Timber / checkout",
        "product": "Stormfield Jacket",
        "price": "$148",
        "blurb": "One page, one decision. The express checkout keeps the receipt "
                 "and the returns link in the same place.",
        "control": {
            "cta": "Pay with card",
            "note": "You will confirm the address on the next step.",
        },
        "red": {
            "cta": "One-tap checkout",
            "note": "Saved address and card, confirmed in a single tap.",
        },
    },
}

if not ENV_KEY:
    raise SystemExit("FLAGSMITH_ENV_KEY is required (run demo/seed.py first)")

flagsmith = Flagsmith(
    environment_key=ENV_KEY,
    api_url=API_URL,
    enable_events=True,
    event_processor_config=EventProcessorConfig(
        events_api_url=EVENTS_URL,
        max_buffer_items=10_000,
        flush_interval_seconds=2.0,
    ),
)

_cache: dict[tuple[str, str], tuple[str, str]] = {}
_cache_lock = threading.Lock()
_exposed: set[tuple[str, str]] = set()
_exposed_lock = threading.Lock()


def evaluate(identifier: str, flag_name: str) -> tuple[str, str]:
    """Return (variant key, value) for this identity, via real Flagsmith bucketing."""
    with _cache_lock:
        hit = _cache.get((identifier, flag_name))
    if hit:
        return hit
    flags = flagsmith.get_identity_flags(identifier=identifier)
    flag = flags.get_flag(flag_name)
    variant = (flag.variant if flag and flag.variant else "control")
    value = str(flag.value) if flag and flag.value is not None else "control"
    with _cache_lock:
        _cache[(identifier, flag_name)] = (variant, value)
    return variant, value


def record_exposure(identifier: str, flag_name: str, variant: str) -> None:
    key = (identifier, flag_name)
    with _exposed_lock:
        if key in _exposed:
            return
        _exposed.add(key)
    flagsmith.track_exposure_event(feature_name=flag_name, identifier=identifier, value=variant)


def record_conversion(event: str, identifier: str) -> None:
    flagsmith.track_event(event, identifier=identifier, value=1)


def page_html(page_key: str, variant: str) -> bytes:
    page = PAGES[page_key]
    copy = page["red"] if variant == "red" else page["control"]
    accent = "#e2483d" if variant == "red" else "#e8e1d5"
    ink = "#12100e"
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{page['product']} - Tide &amp; Timber</title>
<style>
  :root {{ color-scheme: light; }}
  * {{ box-sizing: border-box; }}
  body {{ margin:0; background:#f1ece3; color:{ink};
         font:16px/1.55 ui-serif,Georgia,"Times New Roman",serif; }}
  header {{ display:flex; justify-content:space-between; align-items:baseline;
            padding:22px 28px; border-bottom:1px solid #d6cec1; }}
  .brand {{ font:600 13px/1 ui-sans-serif,system-ui,sans-serif; letter-spacing:.22em;
            text-transform:uppercase; }}
  .tiny {{ font:12px/1 ui-sans-serif,system-ui,sans-serif; color:#6d655a; }}
  main {{ max-width:960px; margin:0 auto; padding:56px 28px 80px;
          display:grid; grid-template-columns:1.05fr .95fr; gap:56px; }}
  .art {{ min-height:420px; border-radius:2px; position:relative; overflow:hidden;
          background:radial-gradient(120% 90% at 30% 15%, #6d7b6a 0%, #3f4b41 46%, #222a24 100%); }}
  .art::after {{ content:""; position:absolute; inset:14% 20%; border:1px solid rgba(241,236,227,.32);
                 border-radius:48% 48% 12% 12%; }}
  .eyebrow {{ font:11px/1 ui-sans-serif,system-ui,sans-serif; letter-spacing:.2em;
              text-transform:uppercase; color:#6d655a; margin:0 0 14px; }}
  h1 {{ font-size:42px; line-height:1.02; letter-spacing:-.02em; margin:0 0 10px; font-weight:500; }}
  .price {{ font:15px/1 ui-sans-serif,system-ui,sans-serif; margin:0 0 26px; }}
  p.blurb {{ margin:0 0 30px; max-width:46ch; color:#3d382f; }}
  .sizes {{ display:flex; gap:8px; margin:0 0 26px; }}
  .sizes span {{ border:1px solid #c9c0b1; padding:7px 10px; font:12px/1 ui-sans-serif,system-ui,sans-serif; }}
  form {{ margin:0; }}
  button {{ width:100%; border:0; padding:17px 20px; cursor:pointer; color:{ink if variant != 'red' else '#fff'};
            background:{accent}; font:600 13px/1 ui-sans-serif,system-ui,sans-serif;
            letter-spacing:.08em; text-transform:uppercase; }}
  .note {{ font:12px/1.5 ui-sans-serif,system-ui,sans-serif; color:#6d655a; margin:12px 0 0; }}
  footer {{ max-width:960px; margin:0 auto; padding:0 28px 40px;
            font:11px/1.6 ui-sans-serif,system-ui,sans-serif; color:#8b8171; }}
  code {{ font-family:ui-monospace,SFMono-Regular,Menlo,monospace; }}
  @media (max-width:760px) {{ main {{ grid-template-columns:1fr; gap:28px; }} .art {{ min-height:260px; }} }}
</style></head>
<body data-variant="{variant}" data-flag="{page['flag']}" data-page="{page_key}">
<header><span class="brand">Tide &amp; Timber</span><span class="tiny">Free returns / 30 days</span></header>
<main>
  <div class="art" aria-hidden="true"></div>
  <section>
    <p class="eyebrow">{page['eyebrow']}</p>
    <h1>{page['product']}</h1>
    <p class="price">{page['price']}</p>
    <p class="blurb">{page['blurb']}</p>
    <div class="sizes"><span>S</span><span>M</span><span>L</span><span>XL</span></div>
    <form method="post" action="/buy">
      <button type="submit">{copy['cta']}</button>
    </form>
    <p class="note">{copy['note']}</p>
    <p class="note">Variant <code>{variant}</code> served by Flagsmith flag <code>{page['flag']}</code>.</p>
  </section>
</main>
<footer>Demo shop for the Docket walkthrough. Every page view, purchase and refund
is a real Flagsmith SDK event; the flag owns the variant.</footer>
</body></html>""".encode()


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "TideAndTimber/1.0"

    def log_message(self, fmt, *args):  # keep container logs readable
        return

    def _identity(self) -> tuple[str, bool]:
        cookie = SimpleCookie(self.headers.get("Cookie") or "")
        morsel = cookie.get("docket_id")
        if morsel and morsel.value:
            return morsel.value, False
        return uuid.uuid4().hex, True

    def _send(self, status: int, body: bytes, ctype: str,
              set_cookie: str | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if set_cookie:
            self.send_header("Set-Cookie", set_cookie)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, data: dict, set_cookie: str | None = None) -> None:
        self._send(status, json.dumps(data).encode(), "application/json", set_cookie)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/healthz":
            try:
                flagsmith.get_environment_flags()
            except Exception as exc:  # noqa: BLE001 - health endpoint reports, never raises
                self._json(503, {"ok": False, "error": str(exc)[:200]})
                return
            self._json(200, {"ok": True})
            return
        if parsed.path == "/preview":
            params = parse_qs(parsed.query)
            key = (params.get("page") or ["checkout"])[0]
            variant = (params.get("variant") or ["control"])[0]
            if key not in PAGES or variant not in ("control", "red"):
                self._json(400, {"error": "page=checkout|express, variant=control|red"})
                return
            self._send(200, page_html(key, variant), "text/html; charset=utf-8")
            return
        key = next((k for k, p in PAGES.items() if p["path"] == parsed.path), None)
        if key is None:
            self._json(404, {"error": "not found"})
            return
        identifier, new = self._identity()
        variant = "control"
        try:
            variant, _ = evaluate(identifier, PAGES[key]["flag"])
            record_exposure(identifier, PAGES[key]["flag"], variant)
        except Exception as exc:  # noqa: BLE001 - a shop page must still render
            print(f"flag evaluation failed for {identifier[:8]}: {exc}")
        cookie = f"docket_id={identifier}; Path=/; HttpOnly; SameSite=Lax" if new else None
        self._send(200, page_html(key, variant), "text/html; charset=utf-8", cookie)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            self.rfile.read(length)
        identifier, new = self._identity()
        cookie = f"docket_id={identifier}; Path=/; HttpOnly; SameSite=Lax" if new else None
        if parsed.path == "/buy":
            record_conversion(PURCHASE_EVENT, identifier)
            self._json(200, {"ok": True, "event": PURCHASE_EVENT, "identifier": identifier}, cookie)
            return
        if parsed.path == "/refund":
            record_conversion(REFUND_EVENT, identifier)
            self._json(200, {"ok": True, "event": REFUND_EVENT, "identifier": identifier}, cookie)
            return
        if parsed.path == "/flush":
            flagsmith.flush_events()
            self._json(200, {"ok": True, "flushed": True})
            return
        self._json(404, {"error": "not found"})


def _shutdown(*_args) -> None:
    try:
        flagsmith.flush_events()
        flagsmith.close()
    except Exception:  # noqa: BLE001 - best effort on the way out
        pass


if __name__ == "__main__":
    atexit.register(_shutdown)

    def _stop(_signum, _frame) -> None:
        _shutdown()
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, _stop)
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    server.daemon_threads = True
    print(f"shop listening on :{PORT} flag={PAGES['checkout']['flag']}", flush=True)
    server.serve_forever()
