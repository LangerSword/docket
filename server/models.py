"""Request schemas for the console API. Secrets are SecretStr and never logged."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, SecretStr


class TargetRequest(BaseModel):
    base: str = Field(min_length=1, max_length=300)
    environment: str = Field(min_length=1, max_length=200, pattern=r"^[A-Za-z0-9_-]+$")
    token: SecretStr = Field(min_length=1, max_length=2000)
    config: str = Field(min_length=1, max_length=120_000)
    events_url: str | None = Field(default=None, max_length=300)


class CheckRequest(TargetRequest):
    pass


class ShipRequest(TargetRequest):
    confirm: bool = False
    signing_key: SecretStr | None = Field(default=None, max_length=2000)
    actor: str = Field(default="console", max_length=100)


class VerifyRequest(BaseModel):
    record: dict[str, Any]
    signing_key: SecretStr = Field(min_length=1, max_length=2000)


class ApiError(BaseModel):
    code: str
    message: str
    details: list[dict[str, str]] | None = None


class ApiResponse(BaseModel):
    data: Any
    meta: dict[str, Any] | None = None