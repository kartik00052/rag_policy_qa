"""Event loop setup.

psycopg's async implementation cannot run on asyncio's ``ProactorEventLoop``,
the Windows default, so every entry point (app boot, Alembic, scripts) must
ensure a selector loop exists before any connection is made.

Two mechanisms are needed, because uvicorn 0.36+ stopped using the event loop
*policy*: it now asks for a loop *factory* and constructs the loop itself, which
bypasses ``set_event_loop_policy`` entirely. Its built-in factory returns
``ProactorEventLoop`` on Windows unless it is running with ``use_subprocess=True``
(i.e. reload mode), so relying on reload to get a selector loop silently breaks
as soon as reload is turned off. ``selector_loop_factory`` is therefore passed to
uvicorn explicitly, and ``configure_event_loop`` covers the non-uvicorn entry
points. No-ops on non-Windows platforms, where the default loop already works.
"""

from __future__ import annotations

import asyncio
import sys

_configured = False


def configure_event_loop() -> None:
    """Set the selector policy for entry points that use ``asyncio.run``."""
    global _configured
    if _configured:
        return
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    _configured = True


def selector_loop_factory() -> asyncio.AbstractEventLoop:
    """Zero-argument loop factory for ``uvicorn.run(loop=...)``.

    Note the shape: for a *custom* ``loop=`` string uvicorn returns
    ``import_from_string(value)`` verbatim and hands it straight to
    ``asyncio.run(loop_factory=...)``, so this must create the loop itself.
    The built-in names ("asyncio", "auto", "uvloop") are called with
    ``use_subprocess=`` instead, which is why a factory-of-factory silently
    produces a class where a loop instance is expected.
    """
    if sys.platform == "win32":
        return asyncio.SelectorEventLoop()
    return asyncio.new_event_loop()
