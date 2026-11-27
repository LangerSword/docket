"""Docket console API.

The API is deliberately stateless. Tokens and signing keys arrive with a
request, live in memory for that request, and are never written to disk or logs.
"""
from __future__ import annotations

import json
import logging
import os
import tomllib
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from docket.attest import sign_record, verify_record
from docket.client import ApiError, Flagsmith
from docket.release import evaluate, load_release, plan_mutations, record, record_markdown, ship_plan
from docket.rules import RuleConfig

from .models import CheckRequest, ShipRequest, VerifyRequest
from .security import RateLimiter, TargetError, client_key, validate_base

LOG = logging.getLogger("docket.api")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

ALLOW_PRIVATE = os.getenv("DOCKET_ALLOW_PRIVATE_TARGETS", "0") == "1"
RATE_LIMIT = int(os.getenv("DOCKET_RATE_LIMIT_PER_MINUTE", "30"))
MAX_BODY = 256 * 1024


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, "redirects are not followed", headers, fp)


# Flagsmith API calls are direct. A redirect from a user-supplied host is an SSRF
# path, so the API process never follows one.
urllib.request.install_opener(urllib.request.build_opener(NoRedirect))
ORIGINS = [
    origin.strip()
    for origin in os.getenv(
        "DOCKET_CORS_ORIGINS",
        "https://langersword.github.io,http://localhost:8092,http://localhost:8765,http://127.0.0.1:8765",
    ).split(",")
    if origin.strip()
]

app = FastAPI(title="Docket API", version="1.0.0", docs_url="/api/docs", redoc_url=None)
limiter = RateLimiter(RATE_LIMIT)
demo_path = Path(__file__).with_name("demo.json")

app.add_middleware(
    CORSMiddleware,
    allow_origins=ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["content-type"],
    max_age=600,
)


@app.middleware("http")
async def guard(request: Request, call_next):
    length = request.headers.get("content-length")
    if length and length.isdigit() and int(length) > MAX_BODY:
        return JSONResponse(
            {"error": {"code": "payload_too_large", "message": "request body is too large"}},
            status_code=413,
        )
    if request.method == "POST" and not limiter.allow(client_key(request)):
        return JSONResponse(
            {"error": {"code": "rate_limited", "message": "too many requests; wait a minute"}},
            status_code=429,
            headers={"Retry-After": "60"},
        )
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Frame-Options"] = "DENY"
    return response


@app.exception_handler(HTTPException)
async def http_error(_: Request, exc: HTTPException):
    detail = exc.detail if isinstance(exc.detail, dict) else {"message": str(exc.detail)}
    return JSONResponse({"error": detail}, status_code=exc.status_code)


@app.exception_handler(RequestValidationError)
async def validation_error(_: Request, exc: RequestValidationError):
    details = []
    for error in exc.errors():
        details.append({
            "field": ".".join(str(part) for part in error.get("loc", [])),
            "message": error.get("msg", "invalid value"),
        })
    return JSONResponse(
        {"error": {"code": "validation_error", "message": "request validation failed", "details": details}},
        status_code=422,
    )


@app.exception_handler(Exception)
async def unhandled_error(_: Request, exc: Exception):
    LOG.error("unhandled %s", type(exc).__name__)
    return JSONResponse(
        {"error": {"code": "internal_error", "message": "Docket could not complete the request"}},
        status_code=500,
    )


def _config(text: str) -> tuple[dict[str, Any], RuleConfig]:
    try:
        raw = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise HTTPException(422, {"code": "invalid_config", "message": f"TOML error: {exc}"}) from exc
    rule = raw.get("rule") or {}
    try:
        cfg = RuleConfig(
            control_key=rule.get("control_key", "control"),
            n_min=int(rule.get("n_min", 50)),
            c_min=int(rule.get("c_min", 5)),
            win_threshold=float(rule.get("win_threshold", 0.95)),
            expected_direction=rule.get("expected_direction", "increase"),
            weights={k: float(v) for k, v in (rule.get("weights") or {}).items()},
            guardrails=rule.get("guardrails") or [],
            srm_alpha=float(rule.get("srm_alpha", 0.001)),
        )
        return raw, cfg
    except (TypeError, ValueError) as exc:
        raise HTTPException(422, {"code": "invalid_config", "message": f"invalid rule: {exc}"}) from exc


def _target(body) -> str:
    try:
        return validate_base(body.base, ALLOW_PRIVATE)
    except TargetError as exc:
        raise HTTPException(422, {"code": "invalid_target", "message": str(exc)}) from exc


def _release(body):
    raw, base_cfg = _config(body.config)
    try:
        return load_release(raw, base_cfg), raw
    except ValueError as exc:
        raise HTTPException(422, {"code": "invalid_release", "message": str(exc)}) from exc


@app.get("/")
def index():
    return {
        "name": "Docket API",
        "version": "1.0.0",
        "endpoints": [
            "/api/v1/health",
            "/api/v1/demo",
            "/api/v1/release/check",
            "/api/v1/release/ship",
            "/api/v1/verify",
        ],
    }


@app.get("/api/v1/health")
def health():
    return {"status": "ok"}


@app.get("/api/v1/demo")
def demo():
    if not demo_path.exists():
        raise HTTPException(404, {"code": "no_demo", "message": "demo snapshot is not installed"})
    return JSONResponse(json.loads(demo_path.read_text()))


@app.post("/api/v1/release/check")
def release_check(body: CheckRequest):
    base = _target(body)
    release, _ = _release(body)
    client = Flagsmith(base=base, token=body.token.get_secret_value())
    try:
        result = evaluate(client, body.environment, release, events_url=body.events_url)
    except ApiError as exc:
        raise HTTPException(502, {"code": "flagsmith_unreachable", "message": f"Flagsmith returned {exc.status}"}) from exc
    return {
        "data": result.to_dict(),
        "meta": {"release": release.name, "source": "live", "private_target": ALLOW_PRIVATE},
    }


@app.post("/api/v1/release/ship")
def release_ship(body: ShipRequest):
    if not body.confirm:
        raise HTTPException(422, {"code": "confirmation_required", "message": "confirm must be true"})
    base = _target(body)
    release, _ = _release(body)
    signing_key = body.signing_key.get_secret_value() if body.signing_key else ""
    if release.require_signature and not signing_key:
        raise HTTPException(422, {"code": "signing_key_required", "message": "this release requires a signing key"})
    client = Flagsmith(base=base, token=body.token.get_secret_value())
    try:
        result = evaluate(client, body.environment, release, events_url=body.events_url)
    except ApiError as exc:
        raise HTTPException(502, {"code": "flagsmith_unreachable", "message": f"Flagsmith returned {exc.status}"}) from exc
    if result.state.value != "cleared":
        raise HTTPException(
            409,
            {
                "code": "release_blocked",
                "message": "the docket is not cleared",
                "docket": result.to_dict(),
            },
        )
    try:
        plan = plan_mutations(client, body.environment, result)
    except (ValueError, ApiError) as exc:
        raise HTTPException(409, {"code": "mutation_refused", "message": str(exc)}) from exc
    try:
        ship_plan(client, body.environment, plan)
    except ApiError as exc:
        raise HTTPException(502, {"code": "ship_failed", "message": f"Flagsmith returned {exc.status} after pre-validation"}) from exc
    data = record(body.environment, result, plan, body.actor)
    if signing_key:
        data = sign_record(data, signing_key)
    return {
        "data": data,
        "meta": {"release": release.name, "markdown": record_markdown(data)},
    }


@app.post("/api/v1/verify")
def verify(body: VerifyRequest):
    size = len(json.dumps(body.record))
    if size > MAX_BODY:
        raise HTTPException(413, {"code": "record_too_large", "message": "record is too large"})
    ok, message = verify_record(body.record, body.signing_key.get_secret_value())
    return {"data": {"valid": ok, "message": message}}