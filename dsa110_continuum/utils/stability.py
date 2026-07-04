# Vendored from dsa110-contimg @ /data/dsa110-contimg/backend/src (H17), 2026-07-03,
# as part of the contimg-import-retirement migration (docs/rse/specs/plan-contimg-import-retirement.md).
"""
Stability decorators for rigorous versioning and scientific validation.

This module provides tools to explicitly mark the stability level of functions
and classes in the pipeline. This allows for:
1. Programmatic gates (e.g., "don't run BROKEN code in production")
2. Executive traces (reporting which experimental features were used)
3. Clear developer communication regarding scientific validation status.
"""

import functools
import logging
import warnings
from collections.abc import Callable
from enum import Enum
from typing import Any, TypeVar

logger = logging.getLogger(__name__)

F = TypeVar("F", bound=Callable[..., Any])


class Stability(Enum):
    """Stability levels for pipeline components."""

    STABLE = "stable"
    """Scientifically validated against ground truth or real data. Safe for production."""

    EXPERIMENTAL = "experimental"
    """Technically working but scientific validity is unverified. Use with caution."""

    BROKEN = "broken"
    """Known to produce incorrect results or crash. Execution will raise RuntimeError."""

    DEPRECATED = "deprecated"
    """Scheduled for removal. Execution will emit DeprecationWarning."""


def stability(level: Stability, reason: str = "") -> Callable[[F], F]:
    """Decorator to mark the stability level of a function.

    Parameters
    ----------
    level : Stability
        The Stability enum value.
    reason : str, optional
        Explanation for the classification (e.g., "Validated against 3C286") (default "").

    """

    def decorator(func: F) -> F:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            if level == Stability.BROKEN:
                msg = f"Function '{func.__name__}' is marked BROKEN: {reason}"
                logger.error(msg)
                raise RuntimeError(msg)

            if level == Stability.EXPERIMENTAL:
                msg = f"Function '{func.__name__}' is EXPERIMENTAL: {reason}"
                # We use a custom category so these can be filtered if needed
                warnings.warn(msg, UserWarning, stacklevel=2)
                logger.info(msg)

            if level == Stability.DEPRECATED:
                msg = f"Function '{func.__name__}' is DEPRECATED: {reason}"
                warnings.warn(msg, DeprecationWarning, stacklevel=2)
                logger.warning(msg)

            return func(*args, **kwargs)

        # Attach metadata to the wrapper for inspection by tracing tools
        wrapper._stability = level
        wrapper._stability_reason = reason
        return wrapper  # type: ignore

    return decorator
