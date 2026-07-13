"""
Pipeline logging configuration for the DSA-110 Continuum Imaging Pipeline.

This module provides:
- Structured JSON logging for production environments
- Human-readable console logging for development
- Automatic log file rotation to /data/dsa110-contimg/state/logs/
- Context-aware logging with pipeline stage and group ID tracking

Examples
--------
Basic setup at application entry point::

    from dsa110_continuum.utils.logging import setup_logging

    setup_logging()  # Uses defaults from environment

With explicit configuration::

    setup_logging(
        log_level="DEBUG",
        log_dir="/custom/log/path",
        json_format=True,
    )

Using context manager for automatic context injection::

    from dsa110_continuum.utils.logging import log_context

    with log_context(group_id="2025-01-15T12:30:00", pipeline_stage="conversion"):
        logger.info("Starting conversion")  # Automatically includes context
        process_files()
        logger.info("Conversion complete")

Notes
-----
Environment variables:

- ``PIPELINE_LOG_LEVEL``: Logging level (DEBUG, INFO, WARNING, ERROR)
- ``PIPELINE_LOG_DIR``: Log directory path
- ``PIPELINE_LOG_FORMAT``: Log format (json, text)
- ``PIPELINE_LOG_MAX_SIZE``: Max log file size in MB
- ``PIPELINE_LOG_BACKUP_COUNT``: Number of backup files to keep
"""

from __future__ import annotations

import logging
import logging.handlers
import os
import sys
import warnings
from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Any

from dsa110_continuum.utils.env_utils import get_env_path

from .formatters import ColoredFormatter, JSONFormatter

# Context variables for automatic context injection
_log_context: ContextVar[dict[str, Any]] = ContextVar("log_context", default={})

# Base directory for the pipeline
CONTIMG_BASE = str(get_env_path("CONTIMG_BASE_DIR", default="/data/dsa110-contimg"))

# Default configuration
DEFAULT_LOG_DIR = f"{CONTIMG_BASE}/state/logs"
DEFAULT_LOG_LEVEL = "INFO"
DEFAULT_LOG_FORMAT = "text"
DEFAULT_MAX_SIZE_MB = 50
DEFAULT_BACKUP_COUNT = 10

# Log file names by category
LOG_FILES = {
    "main": "pipeline.log",
    "conversion": "conversion.log",
    "streaming": "streaming.log",
    "calibration": "calibration.log",
    "imaging": "imaging.log",
    "api": "api.log",
    "database": "database.log",
    "error": "error.log",  # All errors across all categories
}


class ContextFilter(logging.Filter):
    """Logging filter that injects context variables into log records.

    Adds context from the current context variable to every log record,
    enabling automatic context propagation through async/threaded code.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        """Filter and augment log record with context.

        Parameters
        ----------
        record : logging.LogRecord
            Log record to filter.

        Returns
        -------
        bool
            Always returns True (record is never filtered out).
        """
        # Get current context
        context = _log_context.get()

        # Add context attributes to record
        for key, value in context.items():
            if not hasattr(record, key):
                setattr(record, key, value)

        # Ensure standard context attributes exist
        for attr in ["group_id", "pipeline_stage", "file_path", "ms_path"]:
            if not hasattr(record, attr):
                setattr(record, attr, "")

        return True


def setup_logging(
    log_level: str | None = None,
    log_dir: str | None = None,
    json_format: bool | None = None,
    max_size_mb: int | None = None,
    backup_count: int | None = None,
    console_output: bool = True,
) -> None:
    """Configure logging for the pipeline.

    Should be called once at application startup. Configures:
    - Root logger level
    - Console handler (colored text or JSON)
    - File handlers for each category
    - Error file handler (all errors)

    Parameters
    ----------
    log_level : str, optional
        Logging level (DEBUG, INFO, WARNING, ERROR).
    log_dir : str, optional
        Directory for log files.
    json_format : bool, optional
        Use JSON format for file logs.
    max_size_mb : int, optional
        Maximum log file size before rotation.
    backup_count : int, optional
        Number of backup files to keep.
    console_output : bool
        Enable console output (default: True).
    """
    # Read from environment with fallbacks
    log_level = log_level or os.environ.get("PIPELINE_LOG_LEVEL", DEFAULT_LOG_LEVEL)
    log_dir = log_dir or os.environ.get("PIPELINE_LOG_DIR", DEFAULT_LOG_DIR)

    json_format_env = os.environ.get("PIPELINE_LOG_FORMAT", DEFAULT_LOG_FORMAT)
    if json_format is None:
        json_format = json_format_env.lower() == "json"

    max_size_mb = max_size_mb or int(
        os.environ.get("PIPELINE_LOG_MAX_SIZE", DEFAULT_MAX_SIZE_MB)
    )
    backup_count = backup_count or int(
        os.environ.get("PIPELINE_LOG_BACKUP_COUNT", DEFAULT_BACKUP_COUNT)
    )

    # Create log directory
    log_path = Path(log_dir)
    log_path.mkdir(parents=True, exist_ok=True)

    # Get root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, log_level.upper()))

    # Remove existing handlers
    root_logger.handlers.clear()

    # Suppress verbose library warnings
    warnings.filterwarnings("ignore", message=".*forcing conjugation to be.*")
    warnings.filterwarnings("ignore", message=".*units of the data are uncalib.*")

    # Suppress Numba logging
    logging.getLogger("numba").setLevel(logging.WARNING)

    # Add context filter to root
    context_filter = ContextFilter()
    root_logger.addFilter(context_filter)

    # Route DeprecationWarnings (and other warnings) into the logging system
    # so they appear in RotatingFileHandler log files for tracking.
    logging.captureWarnings(True)
    # Ensure DeprecationWarnings from our package are always emitted
    # (Python suppresses them by default outside __main__).
    warnings.filterwarnings(
        "always", category=DeprecationWarning, module=r"dsa110_continuum\."
    )
    # The py.warnings logger propagates to root, but propagated records
    # bypass the root logger's filters — add the ContextFilter to
    # py.warnings so the custom format fields (group_id, etc.) are set.
    logging.getLogger("py.warnings").addFilter(context_filter)

    # Console handler
    if console_output:
        console_handler = logging.StreamHandler(sys.stderr)
        console_handler.setLevel(logging.DEBUG)
        console_handler.setFormatter(ColoredFormatter(use_colors=True))
        console_handler.addFilter(context_filter)
        root_logger.addHandler(console_handler)

    # File handlers
    file_formatter: logging.Formatter
    if json_format:
        file_formatter = JSONFormatter(include_location=True)
    else:
        file_formatter = logging.Formatter(
            "%(asctime)s %(levelname)-8s [%(name)s] %(message)s "
            "[group_id=%(group_id)s] [stage=%(pipeline_stage)s]"
        )

    # Main log file
    main_handler = logging.handlers.RotatingFileHandler(
        log_path / LOG_FILES["main"],
        maxBytes=max_size_mb * 1024 * 1024,
        backupCount=backup_count,
    )
    main_handler.setLevel(logging.DEBUG)
    main_handler.setFormatter(file_formatter)
    main_handler.addFilter(context_filter)
    root_logger.addHandler(main_handler)

    # Error log file (ERROR and above only)
    error_handler = logging.handlers.RotatingFileHandler(
        log_path / LOG_FILES["error"],
        maxBytes=max_size_mb * 1024 * 1024,
        backupCount=backup_count,
    )
    error_handler.setLevel(logging.ERROR)
    error_handler.setFormatter(file_formatter)
    error_handler.addFilter(context_filter)
    root_logger.addHandler(error_handler)

    # Category-specific loggers
    _setup_category_loggers(log_path, file_formatter, max_size_mb, backup_count)

    # Log startup
    logger = logging.getLogger(__name__)
    logger.info(f"Logging configured: level={log_level}, dir={log_dir}, json={json_format}")


def _setup_category_loggers(
    log_path: Path,
    formatter: logging.Formatter,
    max_size_mb: int,
    backup_count: int,
) -> None:
    """Set up category-specific loggers with their own files.

    Parameters
    ----------
    log_path : Path
        Base directory for log files.
    formatter : logging.Formatter
        Formatter to use for log files.
    max_size_mb : int
        Maximum log file size in MB.
    backup_count : int
        Number of backup files to keep.
    """
    # Mapping of logger name prefix to log file
    category_mapping = {
        "dsa110_continuum.conversion": "conversion",
        "dsa110_continuum.streaming": "streaming",
        "dsa110_continuum.calibration": "calibration",
        "dsa110_continuum.imaging": "imaging",
        "dsa110_continuum.database": "database",
    }

    for logger_prefix, category in category_mapping.items():
        logger = logging.getLogger(logger_prefix)

        # Create rotating file handler for this category
        handler = logging.handlers.RotatingFileHandler(
            log_path / LOG_FILES[category],
            maxBytes=max_size_mb * 1024 * 1024,
            backupCount=backup_count,
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)


@contextmanager
def log_context(**context: Any) -> Generator[None, None, None]:
    """Context manager for adding context to all logs within a block.

    Automatically injects context into every log message within the block,
    even in called functions and async code.

    Parameters
    ----------
    **context : Any
        Key-value pairs to add to log records.

    Yields
    ------
    None

    Examples
    --------
    >>> with log_context(group_id="2025-01-15T12:30:00", pipeline_stage="conversion"):
    ...     logger.info("Starting conversion")  # Includes group_id and pipeline_stage
    ...     process_group(files)
    ...     logger.info("Conversion complete")  # Same context
    """
    # Get current context and merge with new context
    current = _log_context.get()
    merged = {**current, **context}

    # Set new context
    token = _log_context.set(merged)

    try:
        yield
    finally:
        # Reset to previous context
        _log_context.reset(token)


def get_logger(name: str, **default_context: Any) -> logging.Logger | logging.LoggerAdapter:
    """Get a logger with optional default context.

    Convenience function for getting a configured logger.

    Parameters
    ----------
    name : str
        Logger name (usually __name__).
    **default_context : Any
        Default context to include in every log.

    Returns
    -------
    logging.Logger | logging.LoggerAdapter
        Logger or LoggerAdapter with default context.
    """
    logger = logging.getLogger(name)

    if default_context:
        # Create an adapter that includes default context
        class ContextAdapter(logging.LoggerAdapter):
            def process(self, msg: str, kwargs: dict) -> tuple[str, dict]:
                extra = kwargs.get("extra", {})
                extra = {**self.extra, **extra}
                kwargs["extra"] = extra
                return msg, kwargs

        return ContextAdapter(logger, default_context)

    return logger


def log_exception(
    logger: logging.Logger,
    exc: BaseException,
    message: str | None = None,
    level: int = logging.ERROR,
    **extra_context: Any,
) -> None:
    """Log an exception with full context.

    Convenience function for logging exceptions with consistent formatting.
    Automatically extracts context from PipelineError exceptions.

    Parameters
    ----------
    logger : logging.Logger
        Logger instance to use.
    exc : BaseException
        Exception to log.
    message : str, optional
        Optional message to include with the exception.
    level : int
        Log level (default: logging.ERROR).
    **extra_context : Any
        Additional context to include in the log record.

    Examples
    --------
    >>> try:
    ...     process_file(path)
    ... except ConversionError as e:
    ...     log_exception(logger, e, "Failed to process")
    """
    from dsa110_continuum.utils.exceptions import PipelineError

    # Reserved LogRecord attribute names that cannot be used in extra
    RESERVED_KEYS = {
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
        "pathname",
        "process",
        "processName",
        "thread",
        "threadName",
        "exc_info",
        "exc_text",
        "stack_info",
        "message",
    }

    # Build context from exception if it's a PipelineError
    if isinstance(exc, PipelineError):
        raw_context = {**exc.context, **extra_context}
    else:
        raw_context = {
            "error_type": type(exc).__name__,
            "error_message": str(exc),
            **extra_context,
        }

    # Filter out reserved keys to avoid LogRecord conflicts
    context = {k: v for k, v in raw_context.items() if k not in RESERVED_KEYS}

    # Log with exception info
    logger.log(
        level,
        message or str(exc),
        exc_info=True,
        extra=context,
    )
