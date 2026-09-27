"""Event loop selection.

psycopg's async driver raises InterfaceError on ``ProactorEventLoop``, which is
the Windows default. uvicorn 0.36+ builds its loop from a *factory* and ignores
the event loop policy, and its built-in factory only returns a selector loop
when ``use_subprocess=True`` (reload mode). Turning reload off therefore broke
Postgres connectivity until the factory was passed explicitly - these tests keep
that from coming back silently.
"""

from __future__ import annotations

import asyncio
import sys

from app.core import eventloop

WINDOWS = sys.platform == "win32"


def test_factory_yields_a_loop_psycopg_can_use() -> None:
    loop = eventloop.selector_loop_factory()
    try:
        assert isinstance(loop, asyncio.AbstractEventLoop)
        if WINDOWS:
            assert isinstance(loop, asyncio.SelectorEventLoop)
            assert not isinstance(loop, asyncio.ProactorEventLoop)
    finally:
        loop.close()


def test_factory_takes_no_arguments() -> None:
    """uvicorn hands a custom loop string to asyncio.run(loop_factory=...) and
    calls it with no arguments, so a factory-of-factory yields a class, not a
    loop - which is what broke Postgres connectivity."""
    import inspect

    assert not inspect.signature(eventloop.selector_loop_factory).parameters
    assert isinstance(eventloop.selector_loop_factory(), asyncio.AbstractEventLoop)


def test_each_call_returns_a_fresh_loop() -> None:
    first = eventloop.selector_loop_factory()
    second = eventloop.selector_loop_factory()
    try:
        assert first is not second
    finally:
        first.close()
        second.close()


def test_factory_matches_uvicorns_expected_callable_signature() -> None:
    """uvicorn calls factory() and passes it to asyncio.run(loop_factory=...)."""
    assert callable(eventloop.selector_loop_factory)
    assert isinstance(asyncio.get_event_loop_policy(), type(asyncio.get_event_loop_policy()))


def test_configure_event_loop_is_idempotent() -> None:
    eventloop.configure_event_loop()
    first = asyncio.get_event_loop_policy()
    eventloop.configure_event_loop()
    assert asyncio.get_event_loop_policy() is first


def test_run_py_passes_the_explicit_loop_factory() -> None:
    """Guards the wiring, which is what actually broke."""
    from pathlib import Path

    source = (Path(__file__).resolve().parents[1] / "run.py").read_text(encoding="utf-8")
    assert "app.core.eventloop:selector_loop_factory" in source


def test_loop_factory_is_importable_by_uvicorn() -> None:
    """uvicorn resolves the string as ``<module>:<attribute>``."""
    from uvicorn.config import import_from_string

    resolved = import_from_string("app.core.eventloop:selector_loop_factory")
    assert resolved is eventloop.selector_loop_factory
