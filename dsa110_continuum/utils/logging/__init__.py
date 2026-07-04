# Vendored from dsa110-contimg @ /data/dsa110-contimg/backend/src (H17), 2026-07-03,
# as part of the contimg-import-retirement migration (docs/rse/specs/plan-contimg-import-retirement.md).
"""
Logging utilities for the DSA-110 Continuum Imaging Pipeline.

This package provides:
- Structured JSON logging for production environments
- Human-readable console logging for development
- Automatic log file rotation
- Context-aware logging with pipeline stage and group ID tracking

Quick Start
-----------
Setup at application entry point::

    from dsa110_continuum.utils.logging import setup_logging
    setup_logging()

Use context manager for automatic context injection::

    from dsa110_continuum.utils.logging import log_context
    with log_context(group_id="2025-01-15T12:30:00", pipeline_stage="conversion"):
        logger.info("Starting conversion")

Exports
-------
- setup_logging : Configure pipeline logging
- log_context : Context manager for log context injection
- get_logger : Get a logger with optional default context
- log_exception : Log exceptions with full context
- ContextFilter : Filter that injects context into log records
- JSONFormatter : JSON formatter for structured logging
- ColoredFormatter : Colored console formatter
- LOG_FILES : Mapping of category names to log files
"""

from .casa import (
    CasaLogHandler,
    DsaSyslogger,
    exception_logger,
    warning_logger,
)
from .formatters import ColoredFormatter, JSONFormatter
from .pipeline import (
    LOG_FILES,
    ContextFilter,
    get_logger,
    log_context,
    log_exception,
    setup_logging,
)

__all__ = [
    # Setup functions
    "setup_logging",
    "log_context",
    "get_logger",
    "log_exception",
    # Classes
    "ContextFilter",
    "JSONFormatter",
    "ColoredFormatter",
    "CasaLogHandler",
    "DsaSyslogger",
    # Helper functions
    "exception_logger",
    "warning_logger",
    # Constants
    "LOG_FILES",
]
