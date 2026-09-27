"""Server entry point.

Sets the asyncio event loop policy before uvicorn builds a loop, which psycopg's
async driver requires on Windows. Use this instead of the bare ``uvicorn`` CLI:

    python run.py
"""

from __future__ import annotations

import uvicorn

from app.core.config import get_settings
from app.core.eventloop import configure_event_loop


def main() -> None:
    configure_event_loop()
    settings = get_settings()
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",  # noqa: S104 - container exposure, per compose
        port=8000,
        reload=settings.reload,
        log_level="info",
        # psycopg async cannot use ProactorEventLoop. uvicorn's own factory only
        # yields a selector loop when reloading, so it is supplied explicitly.
        loop="app.core.eventloop:selector_loop_factory",
        # Uploaded files are written to storage/ *while* a request is in flight.
        # Without these excludes the reloader restarts on the upload itself and
        # kills the in-flight ingestion task.
        reload_excludes=[
            "storage/*",
            "scripts/fixtures/*",
            "*.log",
        ],
    )


if __name__ == "__main__":
    main()
