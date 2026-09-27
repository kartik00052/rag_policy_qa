"""Health endpoint.

Thin route: the connectivity probing lives in the service layer
(WORKFLOW.md Section 4 - handlers call services, not the other way round).
"""

from __future__ import annotations

from fastapi import APIRouter, Response, status

from app.core.config import get_settings
from app.schemas.health import HealthResponse
from app.services.connectivity import check_all

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
async def health(response: Response) -> HealthResponse:
    dependencies = await check_all()
    healthy = all(dep.ok for dep in dependencies)
    if not healthy:
        # Surface degradation as 503 so orchestrators notice.
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    settings = get_settings()
    return HealthResponse(
        status="ok" if healthy else "degraded",
        app=settings.app_name,
        environment=settings.environment,
        dependencies=dependencies,
    )
