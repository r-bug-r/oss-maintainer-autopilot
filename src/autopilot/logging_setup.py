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


