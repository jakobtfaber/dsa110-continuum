# Vendored from dsa110-contimg @ /data/dsa110-contimg/backend/src (H17), 2026-07-03,
# as part of the contimg-import-retirement migration (docs/rse/specs/plan-contimg-import-retirement.md).
"""
Timing and metrics decorators for DSA-110 pipeline functions.

This module provides decorators that automatically handle:
- Execution timing (start/end with high-precision perf_counter)
- Structured logging (success/failure with duration)
- Optional database metrics recording
- Exception handling with consistent patterns

Usage:
    from dsa110_continuum.utils.decorators import timed

    @timed
    def my_function(x, y):
        return x + y

    # With custom operation name
    @timed("custom_operation_name")
    def another_function():
        pass

    # With metrics recording to database
    @timed(record_metrics=True)
    def critical_function():
        pass

This replaces ~800 lines of scattered timing boilerplate across 80+ functions.
"""

import functools
import logging
import time
from collections.abc import Callable
from typing import Any, ParamSpec, TypeVar, overload

P = ParamSpec("P")
R = TypeVar("R")

logger = logging.getLogger(__name__)


class TimingResult:
    """Container for timing information from a decorated function call."""

    __slots__ = ("operation", "duration_ms", "success", "error", "result")

    def __init__(
        self,
        operation: str,
        duration_ms: float,
        success: bool,
        error: str | None = None,
        result: Any = None,
    ):
        self.operation = operation
        self.duration_ms = duration_ms
        self.success = success
        self.error = error
        self.result = result


def _get_operation_name(func: Callable, custom_name: str | None = None) -> str:
    """Generate operation name from function or custom override.

    Parameters
    ----------
    """
    if custom_name:
        return custom_name
    module = getattr(func, "__module__", "unknown")
    name = getattr(func, "__name__", "unknown")
    # Use short module path (last two components)
    parts = module.split(".")
    short_module = ".".join(parts[-2:]) if len(parts) >= 2 else module
    return f"{short_module}.{name}"


def _record_to_database(
    operation: str,
    duration_ms: float,
    success: bool,
    error: str | None = None,
    **extra_fields,
) -> None:
    """Record timing metrics to the unified database.

    This is a best-effort operation - failures are logged but don't propagate.

    NOTE: Imports are done inside the function to avoid circular imports
    since this module may be imported by modules that config/database depend on.

    Parameters
    ----------
    """
    try:
        # Lazy imports to avoid circular dependency issues
        from dsa110_continuum.database.unified import Database
        from dsa110_continuum.unified_config import settings

        db = Database(settings.database.path)
        try:
            db.execute(
                """
                INSERT INTO operation_metrics (operation, duration_ms, success, error, recorded_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (operation, duration_ms, 1 if success else 0, error, time.time()),
            )
        finally:
            db.close()
    except Exception as e:
        # Metrics recording should never break the main operation
        logger.debug("Failed to record metrics to database: %s", e)


@overload
def timed(func: Callable[P, R]) -> Callable[P, R]: ...


@overload
def timed(
    operation_name: str | None = None,
    *,
    record_metrics: bool = False,
    log_level: int = logging.INFO,
    include_args: bool = False,
) -> Callable[[Callable[P, R]], Callable[P, R]]: ...


def timed(
    func_or_name: Callable[P, R] | str | None = None,
    *,
    record_metrics: bool = False,
    log_level: int = logging.INFO,
    include_args: bool = False,
) -> Callable[P, R] | Callable[[Callable[P, R]], Callable[P, R]]:
    """Decorator for automatic timing, logging, and optional metrics recording.

    Can be used with or without arguments::

        @timed
        def my_func(): ...

        @timed("custom_name")
        def my_func(): ...

        @timed(record_metrics=True, log_level=logging.DEBUG)
        def my_func(): ...

    The decorator:
        1. Records start time with time.perf_counter()
        2. Executes the wrapped function
        3. Records end time and computes duration
        4. Logs success/failure with duration
        5. Optionally records metrics to database
        6. Re-raises any exceptions (after logging)

    Parameters
    ----------
    func_or_name : Callable or str, optional
        Either the function to wrap (when used without parens) or a custom
        operation name string.
    record_metrics : bool, optional
        Whether to record timing metrics to database. Default False.
    log_level : int, optional
        Logging level for timing messages. Default logging.INFO.
    include_args : bool, optional
        Whether to include function args in log messages. Default False.

    Returns
    -------
    Callable
        The decorated function with timing/logging behavior.
    """

    def decorator(func: Callable[P, R], custom_name: str | None = None) -> Callable[P, R]:
        operation = _get_operation_name(func, custom_name)

        @functools.wraps(func)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            start_time = time.perf_counter()
            success = True
            error_msg: str | None = None
            result: R

            # Build log extra fields
            extra: dict = {"operation": operation}
            if include_args:
                extra["args"] = repr(args)[:200]  # Truncate long args
                extra["kwargs"] = repr(kwargs)[:200]

            try:
                result = func(*args, **kwargs)
                return result
            except Exception as e:
                success = False
                error_msg = f"{type(e).__name__}: {str(e)[:200]}"
                raise
            finally:
                duration_ms = (time.perf_counter() - start_time) * 1000
                extra["duration_ms"] = round(duration_ms, 2)
                extra["success"] = success

                if success:
                    logger.log(
                        log_level,
                        "%s completed in %.2fms",
                        operation,
                        duration_ms,
                        extra=extra,
                    )
                else:
                    extra["error"] = error_msg
                    logger.error(
                        "%s failed after %.2fms: %s",
                        operation,
                        duration_ms,
                        error_msg,
                        extra=extra,
                    )

                if record_metrics:
                    _record_to_database(
                        operation=operation,
                        duration_ms=duration_ms,
                        success=success,
                        error=error_msg,
                    )

        return wrapper

    # Handle both @timed and @timed(...) usage patterns
    if func_or_name is None:
        # @timed() with parentheses but no args
        return lambda f: decorator(f, None)
    elif callable(func_or_name):
        # @timed without parentheses - func_or_name is the function
        return decorator(func_or_name, None)
    else:
        # @timed("name") or @timed(record_metrics=True) - func_or_name is the custom name
        return lambda f: decorator(f, func_or_name)


def timed_context(operation: str, log_level: int = logging.INFO, record_metrics: bool = False):
    """Context manager for timing code blocks that aren't functions.

    Usage:
        with timed_context("my_operation"):
            # code to time
            pass

        with timed_context("database_query", record_metrics=True):
            db.execute(...)
    """
    import contextlib

    @contextlib.contextmanager
    def _context():
        start_time = time.perf_counter()
        success = True
        error_msg: str | None = None

        try:
            yield
        except Exception as e:
            success = False
            error_msg = f"{type(e).__name__}: {str(e)[:200]}"
            raise
        finally:
            duration_ms = (time.perf_counter() - start_time) * 1000
            extra = {
                "operation": operation,
                "duration_ms": round(duration_ms, 2),
                "success": success,
            }

            if success:
                logger.log(
                    log_level,
                    "%s completed in %.2fms",
                    operation,
                    duration_ms,
                    extra=extra,
                )
            else:
                extra["error"] = error_msg
                logger.error(
                    "%s failed after %.2fms: %s",
                    operation,
                    duration_ms,
                    error_msg,
                    extra=extra,
                )

            if record_metrics:
                _record_to_database(
                    operation=operation,
                    duration_ms=duration_ms,
                    success=success,
                    error=error_msg,
                )

    return _context()


# Convenience aliases for common logging levels
def timed_debug(
    func_or_name: Callable[P, R] | str | None = None,
    **kwargs,
) -> Callable[P, R] | Callable[[Callable[P, R]], Callable[P, R]]:
    """@timed with DEBUG log level.

    Parameters
    ----------
    func_or_name : Union[Callable[P, R], str, None]
        Function to be timed or its name as a string. (Default value = None)
    **kwargs
        Additional keyword arguments to pass to the function.
    """
    return timed(func_or_name, log_level=logging.DEBUG, **kwargs)


def timed_verbose(
    func_or_name: Callable[P, R] | str | None = None,
    **kwargs,
) -> Callable[P, R] | Callable[[Callable[P, R]], Callable[P, R]]:
    """@timed with DEBUG log level and argument logging.

    Parameters
    ----------
    func_or_name : Union[Callable[P, R], str, None], optional
        Function to wrap or custom name for the timer, by default None.
    **kwargs : dict
        Additional keyword arguments passed to timed().

    Returns
    -------
    Callable
        Decorated function with timing and argument logging.
    """
    return timed(func_or_name, log_level=logging.DEBUG, include_args=True, **kwargs)
