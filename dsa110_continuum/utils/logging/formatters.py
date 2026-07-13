"""
Shared logging formatters for the DSA-110 Continuum Imaging Pipeline.

This module provides reusable log formatters:
- JSONFormatter: Structured JSON output for log aggregation
- ColoredFormatter: Human-readable colored console output

These formatters are used by both the pipeline logging and API logging systems.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime
from typing import Any


class JSONFormatter(logging.Formatter):
    """JSON log formatter for structured logging.

    Outputs logs in JSON format suitable for log aggregation systems.
    Includes all extra context and exception information.

    Parameters
    ----------
    include_timestamp : bool
        Include ISO 8601 timestamp in output (default: True)
    include_hostname : bool
        Include hostname in output (default: False)
    include_location : bool
        Include source file location for all levels (default: False)
    """

    RESERVED_FIELDS = {
        "name",
        "msg",
        "args",
        "created",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "module",
        "msecs",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "stack_info",
        "exc_info",
        "exc_text",
        "thread",
        "threadName",
        "message",
        "asctime",
    }

    def __init__(
        self,
        include_timestamp: bool = True,
        include_hostname: bool = False,
        include_location: bool = False,
    ):
        super().__init__()
        self.include_timestamp = include_timestamp
        self.include_hostname = include_hostname
        self.include_location = include_location
        self._hostname: str | None = None
        if include_hostname:
            import socket

            self._hostname = socket.gethostname()

    def format(self, record: logging.LogRecord) -> str:
        """Format log record as JSON.

        Parameters
        ----------
        record : logging.LogRecord
            Log record to format.

        Returns
        -------
        str
            JSON-formatted log string.
        """
        # Build base log entry
        log_entry: dict[str, Any] = {
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        # Add timestamp
        if self.include_timestamp:
            log_entry["timestamp"] = (
                datetime.utcfromtimestamp(record.created).isoformat() + "Z"
            )

        # Add hostname if configured
        if self.include_hostname and self._hostname:
            log_entry["hostname"] = self._hostname

        # Add source location for errors or if always included
        if self.include_location or record.levelno >= logging.ERROR:
            log_entry["location"] = {
                "module": record.module,
                "function": record.funcName,
                "line": record.lineno,
            }

        # Add extra fields (context)
        extra: dict[str, Any] = {}
        for key, value in record.__dict__.items():
            if key not in self.RESERVED_FIELDS and not key.startswith("_"):
                # Handle non-serializable values
                try:
                    json.dumps(value)
                    extra[key] = value
                except (TypeError, ValueError):
                    extra[key] = str(value)

        if extra:
            log_entry["extra"] = extra

        # Add exception info if present
        if record.exc_info:
            log_entry["exception"] = self.formatException(record.exc_info)

        return json.dumps(log_entry, default=str)


class ColoredFormatter(logging.Formatter):
    """Colored console formatter for human-readable output.

    Adds ANSI color codes based on log level and highlights context.

    Parameters
    ----------
    use_colors : bool
        Enable ANSI color codes (auto-disabled if not a TTY).
    show_context : bool
        Show context attributes (group_id, pipeline_stage, etc.).
    context_attrs : list[str] | None
        List of context attribute names to display.
    """

    COLORS = {
        "DEBUG": "\033[36m",  # Cyan
        "INFO": "\033[32m",  # Green
        "WARNING": "\033[33m",  # Yellow
        "ERROR": "\033[31m",  # Red
        "CRITICAL": "\033[35m",  # Magenta
    }
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"

    def __init__(
        self,
        use_colors: bool = True,
        show_context: bool = True,
        context_attrs: list[str] | None = None,
    ):
        super().__init__()
        self.use_colors = use_colors and sys.stderr.isatty()
        self.show_context = show_context
        self.context_attrs = context_attrs or [
            "group_id",
            "pipeline_stage",
            "request_id",
            "file_path",
        ]

    def format(self, record: logging.LogRecord) -> str:
        """Format log record with colors.

        Parameters
        ----------
        record : logging.LogRecord
            Log record to format.

        Returns
        -------
        str
            Formatted log string with optional ANSI colors.
        """
        # Build timestamp
        timestamp = datetime.utcfromtimestamp(record.created).strftime(
            "%Y-%m-%d %H:%M:%S"
        )

        # Get level with optional color
        level = record.levelname
        if self.use_colors:
            color = self.COLORS.get(level, "")
            level = f"{color}{level:8}{self.RESET}"
        else:
            level = f"{level:8}"

        # Build message
        message = record.getMessage()

        # Add context if present
        context_parts = []
        if self.show_context:
            for attr in self.context_attrs:
                value = getattr(record, attr, None) or ""
                if value:
                    # Truncate request_id to first 8 chars
                    if attr == "request_id" and len(str(value)) > 8:
                        value = str(value)[:8]
                    if self.use_colors:
                        context_parts.append(f"{self.DIM}{attr}={value}{self.RESET}")
                    else:
                        context_parts.append(f"{attr}={value}")

        # Format output
        parts = [f"{timestamp} {level} [{record.name}] {message}"]
        if context_parts:
            parts.append(" | " + " ".join(context_parts))

        output = "".join(parts)

        # Add exception if present
        if record.exc_info:
            output += "\n" + self.formatException(record.exc_info)

        return output

