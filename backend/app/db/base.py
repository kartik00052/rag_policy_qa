"""Declarative base for all V1 tables (PROJECT.md Section 5)."""

from __future__ import annotations

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Base class every ORM model inherits from."""
