"""Response models for the health endpoint."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class DependencyStatus(BaseModel):
    name: str
    ok: bool
    latency_ms: float
    detail: str | None = Field(
        default=None, description="Version or error text from the real check"
    )


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    app: str
    environment: str
    dependencies: list[DependencyStatus]


class ServiceInfo(BaseModel):
    service: str
    version: str
    health: str
