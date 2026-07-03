# Vendored from dsa110-contimg @ /data/dsa110-contimg/backend/src (H17), 2026-07-03,
# workflow/pipeline/structured_logging.py, as part of the contimg-import-retirement migration
# (docs/rse/specs/plan-contimg-import-retirement.md, Phase 4).
"""Structured logging for pipeline components.

Provides structured logging with context and correlation IDs.
Uses structlog if available, falls back to standard logging with JSON formatting.
"""

from __future__ import annotations

import json
import logging
from contextvars import ContextVar
from typing import Any
from uuid import uuid4

# Context variable for correlation ID
correlation_id: ContextVar[str | None] = ContextVar("correlation_id", default=None)

try:
    import structlog

    STRUCTLOG_AVAILABLE = True
except ImportError:
    STRUCTLOG_AVAILABLE = False


def get_correlation_id() -> str:
    """Get or create correlation ID for request tracing."""
    corr_id = correlation_id.get()
    if corr_id is None:
        corr_id = str(uuid4())
        correlation_id.set(corr_id)
    return corr_id


def set_correlation_id(corr_id: str | None = None) -> str:
    """Set correlation ID for request tracing."""
    if corr_id is None:
        corr_id = str(uuid4())
    correlation_id.set(corr_id)
    return corr_id


def configure_structured_logging(log_level: str = "INFO") -> None:
    """Configure structured logging."""
    if STRUCTLOG_AVAILABLE:
        structlog.configure(
            processors=[
                structlog.contextvars.merge_contextvars,
                structlog.processors.add_log_level,
                structlog.processors.TimeStamper(fmt="iso"),
                structlog.processors.StackInfoRenderer(),
                structlog.processors.format_exc_info,
                structlog.processors.JSONRenderer(),
            ],
            wrapper_class=structlog.make_filtering_bound_logger(logging.getLevelName(log_level)),
            context_class=dict,
            logger_factory=structlog.PrintLoggerFactory(),
            cache_logger_on_first_use=False,
        )
    else:
        # Configure standard logging with JSON formatter
        logging.basicConfig(
            level=getattr(logging, log_level.upper()),
            format="%(asctime)s %(name)s %(levelname)s %(message)s",
        )


def get_logger(name: str) -> Any:
    """Get structured logger."""
    if STRUCTLOG_AVAILABLE:
        return structlog.get_logger(name)
    else:
        logger = logging.getLogger(name)
        return StructuredLoggerAdapter(logger)


class StructuredLoggerAdapter:
    """Adapter for standard logging to provide structured logging interface."""

    def __init__(self, logger: logging.Logger):
        self.logger = logger

    def _log(self, level: int, event: str, **kwargs):
        """Log with structured context."""
        corr_id = get_correlation_id()
        context = {"event": event, "correlation_id": corr_id, **kwargs}
        message = json.dumps(context)
        self.logger.log(level, message)

    def info(self, event: str, **kwargs):
        """Log info level."""
        self._log(logging.INFO, event, **kwargs)

    def warning(self, event: str, **kwargs):
        """Log warning level."""
        self._log(logging.WARNING, event, **kwargs)

    def error(self, event: str, **kwargs):
        """Log error level."""
        self._log(logging.ERROR, event, **kwargs)

    def debug(self, event: str, **kwargs):
        """Log debug level."""
        self._log(logging.DEBUG, event, **kwargs)

    def exception(self, event: str, **kwargs):
        """Log exception."""
        self._log(logging.ERROR, event, exc_info=True, **kwargs)


# Convenience functions for common logging patterns


def log_ese_detection(
    logger: Any,
    source_id: str | None,
    candidates_found: int,
    duration_seconds: float,
    min_sigma: float = 5.0,
    **kwargs,
):
    """Log ESE detection event."""
    logger.info(
        "ese_detection_completed",
        event_type="ese_detection",
        source_id=source_id or "all",
        candidates_found=candidates_found,
        duration_seconds=duration_seconds,
        min_sigma=min_sigma,
        correlation_id=get_correlation_id(),
        **kwargs,
    )


def log_calibration_solve(
    logger: Any,
    ms_path: str,
    calibrator_name: str,
    calibration_type: str,
    duration_seconds: float,
    status: str,
    **kwargs,
):
    """Log calibration solve event."""
    logger.info(
        "calibration_solve_completed",
        event_type="calibration_solve",
        ms_path=ms_path,
        calibrator_name=calibrator_name,
        calibration_type=calibration_type,
        duration_seconds=duration_seconds,
        status=status,
        correlation_id=get_correlation_id(),
        **kwargs,
    )


def log_photometry_measurement(
    logger: Any,
    method: str,
    fits_path: str,
    ra_deg: float,
    dec_deg: float,
    duration_seconds: float,
    status: str,
    **kwargs,
):
    """Log photometry measurement event."""
    logger.info(
        "photometry_measurement_completed",
        event_type="photometry_measurement",
        method=method,
        fits_path=fits_path,
        ra_deg=ra_deg,
        dec_deg=dec_deg,
        duration_seconds=duration_seconds,
        status=status,
        correlation_id=get_correlation_id(),
        **kwargs,
    )


def log_pipeline_stage(
    logger: Any, stage_name: str, duration_seconds: float, status: str, **kwargs
):
    """Log pipeline stage event."""
    logger.info(
        "pipeline_stage_completed",
        event_type="pipeline_stage",
        stage_name=stage_name,
        duration_seconds=duration_seconds,
        status=status,
        correlation_id=get_correlation_id(),
        **kwargs,
    )


def log_error(
    logger: Any,
    component: str,
    error: Exception,
    context: dict[str, Any] | None = None,
    **kwargs,
):
    """Log error with context."""
    logger.exception(
        "error_occurred",
        event_type="error",
        component=component,
        error_type=type(error).__name__,
        error_message=str(error),
        context=context or {},
        correlation_id=get_correlation_id(),
        **kwargs,
    )
