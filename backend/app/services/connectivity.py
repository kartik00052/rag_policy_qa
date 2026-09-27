"""Real connectivity checks against Postgres, Qdrant and Redis.

Stage 0's checkpoint is that the backend can actually reach all three - not that
the containers merely report as running. Each check performs a real protocol
round trip and reports latency plus whatever version/detail came back.
"""

from __future__ import annotations

import time

from redis.asyncio import Redis
from sqlalchemy import text

from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.session import get_engine
from app.schemas.health import DependencyStatus
from app.services.qdrant import get_qdrant_client

logger = get_logger(__name__)


async def check_postgres() -> DependencyStatus:
    started = time.perf_counter()
    try:
        async with get_engine().connect() as conn:
            version = (await conn.execute(text("SHOW server_version"))).scalar_one()
        return DependencyStatus(
            name="postgres",
            ok=True,
            latency_ms=round((time.perf_counter() - started) * 1000, 2),
            detail=f"PostgreSQL {version}",
        )
    except Exception as exc:  # noqa: BLE001 - reported, never swallowed
        logger.warning("postgres health check failed: %s", exc)
        return DependencyStatus(
            name="postgres",
            ok=False,
            latency_ms=round((time.perf_counter() - started) * 1000, 2),
            detail=f"{type(exc).__name__}: {exc}"[:300],
        )


async def check_qdrant() -> DependencyStatus:
    started = time.perf_counter()
    try:
        collections = await get_qdrant_client().get_collections()
        return DependencyStatus(
            name="qdrant",
            ok=True,
            latency_ms=round((time.perf_counter() - started) * 1000, 2),
            detail=f"reachable, {len(collections.collections)} collection(s)",
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("qdrant health check failed: %s", exc)
        return DependencyStatus(
            name="qdrant",
            ok=False,
            latency_ms=round((time.perf_counter() - started) * 1000, 2),
            detail=f"{type(exc).__name__}: {exc}"[:300],
        )


async def check_redis() -> DependencyStatus:
    started = time.perf_counter()
    client = Redis.from_url(get_settings().redis_url)
    try:
        await client.ping()
        info = await client.info("server")
        return DependencyStatus(
            name="redis",
            ok=True,
            latency_ms=round((time.perf_counter() - started) * 1000, 2),
            detail=f"redis {info.get('redis_version', '?')}",
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("redis health check failed: %s", exc)
        return DependencyStatus(
            name="redis",
            ok=False,
            latency_ms=round((time.perf_counter() - started) * 1000, 2),
            detail=f"{type(exc).__name__}: {exc}"[:300],
        )
    finally:
        await client.aclose()


async def check_all() -> list[DependencyStatus]:
    return [await check_postgres(), await check_qdrant(), await check_redis()]
