"""Logging that redacts secrets before any record leaves the process."""

from __future__ import annotations

import logging
import sys

from .redaction import redact


class RedactingFormatter(logging.Formatter):
    """Format a record, then redact the rendered line.

    Redacting after formatting also covers exception text and interpolated
    arguments, which is the common leak path for repository content.
    """

    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record)).text


def configure_logging(level: int = logging.INFO, stream: object | None = None) -> None:
    handler = logging.StreamHandler(stream if stream is not None else sys.stderr)  # type: ignore[arg-type]
    handler.setFormatter(
        RedactingFormatter("%(asctime)s %(levelname)s %(name)s %(message)s", "%Y-%m-%dT%H:%M:%SZ")
    )
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)


def safe_log(logger: logging.Logger, level: int, message: str, *args: object) -> None:
    logger.log(level, redact(message).text, *args)
