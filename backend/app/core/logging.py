"""Logging setup.

WORKFLOW.md Section 4 requires ingestion stage transitions to be logged, because
the sidebar in PROJECT.md Section 11.2 renders real pipeline states. Those logs
are the audit trail for what actually happened to a document.
"""

from __future__ import annotations

import logging
import sys


def configure_logging(level: str = "INFO") -> None:
    root = logging.getLogger()
    if root.handlers:
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s %(levelname)-5s [%(name)s] %(message)s",
            datefmt="%H:%M:%S",
        )
    )
    root.addHandler(handler)
    root.setLevel(level)

    # These are chatty at INFO and drown out our own pipeline logs.
    for noisy in ("httpx", "httpcore", "urllib3", "qdrant_client", "python_multipart"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
