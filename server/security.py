"""Network and request safety for the public console API."""
from __future__ import annotations

import ipaddress
import socket
import threading
import time
from collections import defaultdict, deque
from urllib.parse import urlparse

BLOCKED_HOSTS = {
    "metadata.google.internal",
    "metadata",
    "instance-data",
}

ALLOWED_SCHEMES = {"http", "https"}


class TargetError(ValueError):
    """A user-supplied target is not safe to call from this service."""


def validate_base(base: str, allow_private: bool) -> str:
    """Validate a Flagsmith base URL and refuse SSRF targets.

    Public deployments require HTTPS and a public address. A local development
    server may set DOCKET_ALLOW_PRIVATE_TARGETS=1 to reach a desktop Flagsmith.
    """
    raw = (base or "").strip()
    if not raw or len(raw) > 300:
        raise TargetError("base URL is empty or too long")
    parsed = urlparse(raw)
    if parsed.scheme not in ALLOWED_SCHEMES:
        raise TargetError("base URL must use http or https")
    if parsed.username or parsed.password:
        raise TargetError("base URL must not contain credentials")
    if not parsed.hostname:
        raise TargetError("base URL has no host")
    if parsed.query or parsed.fragment:
        raise TargetError("base URL must not contain a query or fragment")
    try:
        port = parsed.port
    except ValueError as exc:
        raise TargetError("base URL has an invalid port") from exc
    if port is not None and not (1 <= port <= 65535):
        raise TargetError("base URL has an invalid port")

    host = parsed.hostname.rstrip(".").lower()
    if host in BLOCKED_HOSTS or host.endswith(".localhost"):
        raise TargetError("base URL points at a blocked host")

    if not allow_private and parsed.scheme != "https":
        raise TargetError("public requests require an HTTPS base URL")

    try:
        resolved = socket.getaddrinfo(host, port or (443 if parsed.scheme == "https" else 80),
                                      type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise TargetError("base URL host could not be resolved") from exc

    addresses = {str(row[4][0]) for row in resolved}
    if not addresses:
        raise TargetError("base URL host could not be resolved")
    for address in addresses:
        try:
            ip = ipaddress.ip_address(address)
        except ValueError as exc:
            raise TargetError("base URL resolved to an invalid address") from exc
        if ip.is_link_local or ip.is_multicast or ip.is_unspecified:
            raise TargetError("base URL resolves to a link-local, multicast or unspecified address")
        if ip.is_private and not allow_private:
            raise TargetError("base URL resolves to a private or reserved address")

    return raw.rstrip("/")


class RateLimiter:
    """Small in-process limiter. API Gateway throttling is the real edge."""

    def __init__(self, per_minute: int) -> None:
        self.per_minute = max(0, per_minute)
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        if self.per_minute <= 0:
            return True
        now = time.monotonic()
        with self._lock:
            bucket = self._hits[key]
            while bucket and now - bucket[0] > 60:
                bucket.popleft()
            if len(bucket) >= self.per_minute:
                return False
            bucket.append(now)
            return True


def client_key(request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()[:64] or "unknown"
    return (request.client.host if request.client else "unknown")[:64]