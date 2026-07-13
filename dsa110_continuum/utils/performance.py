"""
Performance metrics and monitoring utilities.

This module provides utilities for tracking and analyzing performance metrics
across the DSA-110 continuum imaging pipeline. Use the ``track_performance``
decorator to automatically track execution time for operations.

Examples
--------
>>> from dsa110_continuum.utils.performance import track_performance
>>> @track_performance("subband_loading")
... def load_subbands(file_list):
...     # ... loading logic ...
...     return uv_data
>>>
>>> # Later, get performance statistics
>>> from dsa110_continuum.utils.performance import get_performance_stats
>>> stats = get_performance_stats()
>>> print(f"Average subband loading time: {stats['subband_loading']['mean']:.2f}s")
"""

import logging
import time
from functools import wraps

import numpy as np

logger = logging.getLogger(__name__)

# Global performance metrics storage
_performance_metrics: dict[str, list[float]] = {}


def track_performance(operation_name: str, log_result: bool = False):
    """Decorator to track operation performance.

        Tracks execution time for decorated functions and stores metrics
        in a global dictionary. Metrics can be retrieved later using
        `get_performance_stats()`.

    Parameters
    ----------
    operation_name : str
        Name to identify this operation in metrics
    log_result : bool
        If True, log the execution time after each call
        (Default value = False)

    """

    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            start = time.perf_counter()
            try:
                result = func(*args, **kwargs)
                elapsed = time.perf_counter() - start
                _performance_metrics.setdefault(operation_name, []).append(elapsed)

                if log_result:
                    logger.debug(
                        f"Performance: {operation_name} took {elapsed:.3f}s "
                        f"(args: {len(args)} positional, {len(kwargs)} keyword)"
                    )

                return result
            except Exception as e:
                elapsed = time.perf_counter() - start
                error_name = f"{operation_name}_error"
                _performance_metrics.setdefault(error_name, []).append(elapsed)

                if log_result:
                    logger.debug(f"Performance: {operation_name} failed after {elapsed:.3f}s: {e}")

                raise

            # Ensure result is returned even if metrics logic fails (though metrics logic is safe)
            return result

        return wrapper

    return decorator


def get_performance_stats(
    operation_name: str | None = None,
) -> dict[str, dict[str, float]]:
    """Get performance statistics for tracked operations.

    Parameters
    ----------
    operation_name : Optional[str]
        If provided, return stats only for this operation.
        If None, return stats for all operations.
        (Default value = None)

    """
    stats = {}

    operations = [operation_name] if operation_name else list(_performance_metrics.keys())

    for op in operations:
        if op not in _performance_metrics:
            continue

        times = _performance_metrics[op]
        if not times:
            continue

        stats[op] = {
            "mean": float(np.mean(times)),
            "median": float(np.median(times)),
            "min": float(np.min(times)),
            "max": float(np.max(times)),
            "std": float(np.std(times)),
            "count": len(times),
        }

    return stats


def clear_performance_metrics(operation_name: str | None = None) -> None:
    """Clear performance metrics.

    Parameters
    ----------
    operation_name : str or None
        If provided, clear only this operation's metrics.
        If None, clear all metrics.

    Returns
    -------
        None

    Examples
    --------
        # Clear all metrics
        clear_performance_metrics()

        # Clear only subband_loading metrics
        clear_performance_metrics("subband_loading")
    """
    if operation_name:
        if operation_name in _performance_metrics:
            del _performance_metrics[operation_name]
    else:
        _performance_metrics.clear()


def get_performance_summary() -> str:
    """Get a human-readable summary of performance metrics."""
    stats = get_performance_stats()

    if not stats:
        return "No performance metrics recorded yet."

    lines = []
    for op, op_stats in sorted(stats.items()):
        lines.append(
            f"{op}: "
            f"mean={op_stats['mean']:.3f}s, "
            f"median={op_stats['median']:.3f}s, "
            f"min={op_stats['min']:.3f}s, "
            f"max={op_stats['max']:.3f}s "
            f"(count={op_stats['count']})"
        )

    return "\n".join(lines)
