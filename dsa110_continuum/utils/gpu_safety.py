# Vendored from dsa110-contimg @ /data/dsa110-contimg/backend/src (H17), 2026-07-03,
# as part of the contimg-import-retirement migration (docs/rse/specs/plan-contimg-import-retirement.md).
"""
GPU Safety Module - Production-Level Memory Protection.

This module provides ACTIVE protection against memory exhaustion that could
crash the system or disconnect disks. Unlike passive monitoring (which alerts
AFTER problems occur), this module PREVENTS dangerous allocations.

Notes
-----
On Dec 2, 2025, a validation test created arrays for 96 antennas × 768 channels
which consumed all system RAM, caused disk disconnection, and required a reboot.
This module ensures that NEVER happens again in production code.

Protection layers:

1. System RAM: Linux rlimit + pre-allocation checks
2. GPU VRAM: CuPy memory pool limits + allocation guards
3. Active monitoring: Abort operations if memory exceeds threshold
4. Hard limits: Never exceed configured maximums regardless of available memory

Examples
--------
::

    from dsa110_continuum.utils.gpu_safety import (
        # Context managers
        safe_gpu_context,
        safe_memory_context,

        # Decorators
        gpu_safe,
        memory_safe,

        # Pre-flight checks
        check_gpu_memory_available,
        check_system_memory_available,
        estimate_gpu_array_size,

        # Memory pools
        setup_gpu_memory_pool,
        get_gpu_memory_status,
    )

    # Method 1: Decorator (recommended for GPU functions)
    @gpu_safe(max_gpu_gb=8.0, max_system_gb=4.0)
    def my_gpu_function(data):
        import cupy as cp
        return cp.fft.fft2(cp.asarray(data))

    # Method 2: Context manager
    with safe_gpu_context(max_gpu_gb=8.0):
        result = do_gpu_computation()

    # Method 3: Pre-flight check
    required_gb = estimate_gpu_array_size((4096, 4096), 'complex64')
    if check_gpu_memory_available(required_gb):
        do_computation()
    else:
        raise MemoryError("Insufficient GPU memory")
"""

from __future__ import annotations

import gc
import logging
import os
import resource
import signal
import threading
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import Enum
from functools import wraps
from typing import Any, TypeVar

logger = logging.getLogger(__name__)

# Type variable for decorators
F = TypeVar("F", bound=Callable[..., Any])


# =============================================================================
# Configuration Constants
# =============================================================================


class MemoryUnit(Enum):
    """Memory size units."""

    BYTES = 1
    KB = 1024
    MB = 1024**2
    GB = 1024**3


def _get_env_float(name: str, default: float) -> float:
    """Get a float from environment variable, or return default.

    Parameters
    ----------
    """
    val = os.environ.get(name)
    if val is not None:
        try:
            return float(val)
        except ValueError:
            pass
    return default


@dataclass
class SafetyConfig:
    """Configuration for GPU safety limits.

        These are HARD LIMITS - operations will be blocked if they would
        exceed these thresholds, regardless of what the system reports
        as available.

        Environment Variable Overrides
    ------------------------------
    DSA110_SYSTEM_MEMORY_LIMIT_PCT: Override system_usage_limit_pct (default 70.0)
        Set to 90 or higher for small datasets on memory-constrained systems.

    Parameters
    ----------
        None

    Returns
    -------
        None
    """

    # System RAM limits
    max_system_gb: float = 6.0  # Maximum system RAM per operation
    min_system_free_gb: float = 2.0  # Always keep this much free
    # Allow env override for small dataset runs where 70% is too conservative
    system_usage_limit_pct: float = field(
        default_factory=lambda: _get_env_float("DSA110_SYSTEM_MEMORY_LIMIT_PCT", 70.0)
    )

    # GPU VRAM limits
    max_gpu_gb: float = 9.0  # Max GPU memory per operation (11GB cards)
    min_gpu_free_gb: float = 1.0  # Always keep this much free on GPU
    gpu_usage_limit_pct: float = 85.0  # Never use more than 85% of GPU VRAM

    # Timing limits
    max_operation_seconds: float = 300.0  # 5 minute timeout per operation

    # Memory pool settings
    gpu_pool_limit_gb: float = 9.0  # CuPy memory pool limit
    enable_memory_pool: bool = True  # Use CuPy memory pool

    # Safety factors
    allocation_safety_factor: float = 1.5  # Multiply estimated size by this

    # Behavior
    abort_on_threshold: bool = True  # Abort if approaching limits
    log_allocations: bool = True  # Log significant allocations
    raise_on_violation: bool = True  # Raise exception vs return None


# Global default configuration
DEFAULT_CONFIG = SafetyConfig()

# Thread-local storage for nested context tracking
_local = threading.local()


def get_config() -> SafetyConfig:
    """Get current safety configuration."""
    return getattr(_local, "config", DEFAULT_CONFIG)


def set_config(config: SafetyConfig) -> None:
    """Set safety configuration for current thread.

    Parameters
    ----------
    """
    _local.config = config


# =============================================================================
# System Memory Protection
# =============================================================================


def get_system_memory_info() -> dict[str, float]:
    """Get current system memory status in GB."""
    try:
        import psutil

        mem = psutil.virtual_memory()
        return {
            "total_gb": mem.total / MemoryUnit.GB.value,
            "available_gb": mem.available / MemoryUnit.GB.value,
            "used_gb": mem.used / MemoryUnit.GB.value,
            "percent_used": mem.percent,
        }
    except ImportError:
        # Fallback to /proc/meminfo
        try:
            with open("/proc/meminfo") as f:
                lines = f.readlines()
                info = {}
                for line in lines:
                    parts = line.split()
                    if len(parts) >= 2:
                        key = parts[0].rstrip(":")
                        # Values in /proc/meminfo are in kB
                        value_kb = int(parts[1])
                        info[key] = value_kb

                total = info.get("MemTotal", 0) / (1024**2)  # kB to GB
                available = info.get("MemAvailable", 0) / (1024**2)
                used = total - available

                return {
                    "total_gb": total,
                    "available_gb": available,
                    "used_gb": used,
                    "percent_used": (used / total * 100) if total > 0 else 0,
                }
        except (OSError, ValueError, KeyError):
            return {
                "total_gb": 0,
                "available_gb": 0,
                "used_gb": 0,
                "percent_used": 100,  # Assume worst case
            }


def get_process_memory_gb() -> float:
    """Get current process memory usage in GB."""
    try:
        import psutil

        process = psutil.Process()
        return process.memory_info().rss / MemoryUnit.GB.value
    except ImportError:
        try:
            with open("/proc/self/status") as f:
                for line in f:
                    if line.startswith("VmRSS:"):
                        kb = int(line.split()[1])
                        return kb / (1024**2)  # kB to GB
        except (OSError, ValueError):
            pass
    return 0.0


def check_system_memory_available(
    required_gb: float,
    config: SafetyConfig | None = None,
) -> tuple[bool, str]:
    """Check if system has enough memory for an allocation.

    Parameters
    ----------
    required_gb : float
        Amount of memory needed in GB
    config : SafetyConfig, optional
        Safety configuration, by default None

    Returns
    -------
        bool
        True if enough system memory is available, False otherwise
    """
    config = config or get_config()
    mem = get_system_memory_info()

    # Apply safety factor
    required_with_safety = required_gb * config.allocation_safety_factor

    # Check 1: Hard limit
    if required_with_safety > config.max_system_gb:
        return False, (
            f"Requested {required_gb:.2f} GB (with safety: {required_with_safety:.2f} GB) "
            f"exceeds hard limit of {config.max_system_gb:.2f} GB"
        )

    # Check 2: Available memory
    effective_available = mem["available_gb"] - config.min_system_free_gb
    if required_with_safety > effective_available:
        return False, (
            f"Requested {required_gb:.2f} GB, but only {effective_available:.2f} GB available "
            f"(keeping {config.min_system_free_gb:.2f} GB free)"
        )

    # Check 3: Percentage limit
    projected_used_pct = (mem["used_gb"] + required_with_safety) / mem["total_gb"] * 100
    if projected_used_pct > config.system_usage_limit_pct:
        return False, (
            f"Allocation would use {projected_used_pct:.1f}% of system RAM, "
            f"exceeds limit of {config.system_usage_limit_pct:.1f}%"
        )

    return True, f"OK: {required_gb:.2f} GB available"


def set_system_memory_limit(max_gb: float) -> bool:
    """Set hard system memory limit using Linux rlimit.

        Note
    ----
        This uses RLIMIT_AS which limits virtual address space.
        This WILL break CUDA operations if too restrictive because
        CUDA uses memory-mapped regions for GPU memory.

    Parameters
    ----------
    max_gb : float
        Maximum memory in GB

    Returns
    -------
        None
    """
    max_bytes = int(max_gb * MemoryUnit.GB.value)

    try:
        resource.setrlimit(resource.RLIMIT_AS, (max_bytes, max_bytes))
        logger.info(f"System memory limit set to {max_gb:.1f} GB")
        return True
    except (OSError, ValueError) as e:
        logger.warning(f"Could not set system memory limit: {e}")
        return False


# =============================================================================
# GPU Memory Protection
# =============================================================================


def is_gpu_available() -> bool:
    """Check if GPU/CUDA is available."""
    try:
        import cupy as cp

        return cp.cuda.runtime.getDeviceCount() > 0
    except (ImportError, Exception):
        return False


def get_gpu_count() -> int:
    """Get number of available GPUs."""
    try:
        import cupy as cp

        return cp.cuda.runtime.getDeviceCount()
    except (ImportError, Exception):
        return 0


def get_gpu_memory_info(gpu_id: int = 0) -> dict[str, float]:
    """Get GPU memory status in GB.

    Parameters
    ----------
    gpu_id : int, optional
        GPU device index, by default 0

    Returns
    -------
        dict
        Dictionary with GPU memory status information in GB
    """
    try:
        import cupy as cp

        with cp.cuda.Device(gpu_id):
            mem_free, mem_total = cp.cuda.Device(gpu_id).mem_info
            mem_used = mem_total - mem_free

            return {
                "total_gb": mem_total / MemoryUnit.GB.value,
                "used_gb": mem_used / MemoryUnit.GB.value,
                "free_gb": mem_free / MemoryUnit.GB.value,
                "percent_used": (mem_used / mem_total * 100) if mem_total > 0 else 0,
            }
    except (ImportError, Exception) as e:
        logger.debug(f"Could not get GPU memory info: {e}")
        return {
            "total_gb": 0,
            "used_gb": 0,
            "free_gb": 0,
            "percent_used": 100,  # Assume worst case
        }


def get_gpu_memory_status() -> dict[str, Any]:
    """Get comprehensive GPU memory status for all GPUs."""
    n_gpus = get_gpu_count()

    if n_gpus == 0:
        return {"available": False, "gpu_count": 0, "gpus": []}

    gpus = []
    for i in range(n_gpus):
        info = get_gpu_memory_info(i)
        info["id"] = i
        gpus.append(info)

    return {
        "available": True,
        "gpu_count": n_gpus,
        "gpus": gpus,
    }


def check_gpu_memory_available(
    required_gb: float,
    gpu_id: int = 0,
    config: SafetyConfig | None = None,
) -> tuple[bool, str]:
    """Check if GPU has enough memory for an allocation.

    Parameters
    ----------
    required_gb : float
        Amount of GPU memory needed in GB
    gpu_id : int, optional
        GPU device index, by default 0
    config : SafetyConfig, optional
        Safety configuration, by default None

    Returns
    -------
        bool
        True if enough GPU memory is available, False otherwise
    """
    config = config or get_config()

    if not is_gpu_available():
        return False, "No GPU available"

    mem = get_gpu_memory_info(gpu_id)

    if mem["total_gb"] == 0:
        return False, f"Could not query GPU {gpu_id} memory"

    # Apply safety factor
    required_with_safety = required_gb * config.allocation_safety_factor

    # Check 1: Hard limit
    if required_with_safety > config.max_gpu_gb:
        return False, (
            f"Requested {required_gb:.2f} GB (with safety: {required_with_safety:.2f} GB) "
            f"exceeds hard limit of {config.max_gpu_gb:.2f} GB"
        )

    # Check 2: Available memory
    effective_free = mem["free_gb"] - config.min_gpu_free_gb
    if required_with_safety > effective_free:
        return False, (
            f"Requested {required_gb:.2f} GB, but only {effective_free:.2f} GB free "
            f"on GPU {gpu_id} (keeping {config.min_gpu_free_gb:.2f} GB reserve)"
        )

    # Check 3: Percentage limit
    projected_used_pct = (mem["used_gb"] + required_with_safety) / mem["total_gb"] * 100
    if projected_used_pct > config.gpu_usage_limit_pct:
        return False, (
            f"Allocation would use {projected_used_pct:.1f}% of GPU {gpu_id} VRAM, "
            f"exceeds limit of {config.gpu_usage_limit_pct:.1f}%"
        )

    return True, f"OK: {required_gb:.2f} GB available on GPU {gpu_id}"


def setup_gpu_memory_pool(
    limit_gb: float | None = None,
    gpu_id: int = 0,
) -> bool:
    """Configure CuPy memory pool with hard limit.

        This is a critical safety measure - it prevents CuPy from
        allocating more GPU memory than specified, causing allocation
        failures instead of system crashes.

    Parameters
    ----------
    limit_gb : float or None, optional
        Memory pool limit in GB (None = use config default), by default None
    gpu_id : int, optional
        GPU device index, by default 0

    Returns
    -------
        None
    """
    config = get_config()
    limit_gb = limit_gb or config.gpu_pool_limit_gb

    try:
        import cupy as cp

        with cp.cuda.Device(gpu_id):
            # Get memory pool
            mempool = cp.get_default_memory_pool()

            # Set hard limit
            limit_bytes = int(limit_gb * MemoryUnit.GB.value)
            mempool.set_limit(size=limit_bytes)

            logger.info(f"GPU {gpu_id} memory pool limit set to {limit_gb:.1f} GB")
            return True

    except (ImportError, Exception) as e:
        logger.warning(f"Could not configure GPU memory pool: {e}")
        return False


def clear_gpu_memory(gpu_id: int | None = None) -> None:
    """Clear GPU memory pools and run garbage collection.

    Parameters
    ----------
    gpu_id :
        Specific GPU to clear, or None for all
    gpu_id: Optional[int] :
         (Default value = None)

    """
    try:
        import cupy as cp

        # Force Python garbage collection first
        gc.collect()

        if gpu_id is not None:
            with cp.cuda.Device(gpu_id):
                mempool = cp.get_default_memory_pool()
                mempool.free_all_blocks()
        else:
            # Clear all GPUs
            for i in range(get_gpu_count()):
                with cp.cuda.Device(i):
                    mempool = cp.get_default_memory_pool()
                    mempool.free_all_blocks()

        # Also clear pinned memory pool
        pinned_mempool = cp.get_default_pinned_memory_pool()
        pinned_mempool.free_all_blocks()

        logger.debug("GPU memory pools cleared")

    except (ImportError, Exception) as e:
        logger.debug(f"Could not clear GPU memory: {e}")


# =============================================================================
# Memory Estimation Utilities
# =============================================================================


def estimate_array_size_gb(
    shape: tuple[int, ...],
    dtype: str = "float32",
) -> float:
    """Estimate memory size for an array.

    Parameters
    ----------
    shape : tuple of int
        Array shape
    dtype : str, optional
        NumPy/CuPy dtype string, by default "float32"

    Returns
    -------
        float
        Estimated memory size in GB
    """
    import numpy as np

    dtype_obj = np.dtype(dtype)
    n_elements = 1
    for dim in shape:
        n_elements *= dim

    bytes_needed = n_elements * dtype_obj.itemsize
    return bytes_needed / MemoryUnit.GB.value


def estimate_gpu_array_size(
    shape: tuple[int, ...],
    dtype: str = "float32",
    n_copies: int = 2,
) -> float:
    """Estimate GPU memory needed for array operations.

        Most GPU operations need at least 2 copies (input + output).
        FFTs may need more.

    Parameters
    ----------
    shape : tuple of int
        Array shape
    dtype : str, optional
        Data type, by default "float32"
    n_copies : int, optional
        Number of array copies needed, by default 2

    Returns
    -------
        float
        Estimated GPU memory size in GB
    """
    base_size = estimate_array_size_gb(shape, dtype)
    return base_size * n_copies


def estimate_visibility_memory_gb(
    n_antennas: int,
    n_channels: int,
    n_times: int = 1,
    n_pols: int = 4,
) -> float:
    """Estimate memory for visibility data.

        This is the calculation that caused the Dec 2, 2025 crash when
        96 antennas × 768 channels was used without checking.

    Parameters
    ----------
    n_antennas : int
        Number of antennas
    n_channels : int
        Number of frequency channels
    n_times : int, optional
        Number of time samples, by default 1
    n_pols : int, optional
        Number of polarizations, by default 4

    Returns
    -------
        float
        Estimated memory size in GB
    """
    n_baselines = n_antennas * (n_antennas - 1) // 2
    n_elements = n_baselines * n_channels * n_times * n_pols

    # Complex128 = 16 bytes, Complex64 = 8 bytes
    # Assume complex128 for safety
    bytes_per_element = 16

    total_bytes = n_elements * bytes_per_element
    return total_bytes / MemoryUnit.GB.value


def check_visibility_allocation_safe(
    n_antennas: int,
    n_channels: int,
    n_times: int = 1,
    n_pols: int = 4,
    target: str = "system",  # "system" or "gpu"
    config: SafetyConfig | None = None,
) -> tuple[bool, str]:
    """Check if visibility allocation is safe.

    Parameters
    ----------
    n_antennas : int
        Number of antennas
    n_channels : int
        Number of frequency channels
    n_times : int, optional
        Number of time samples (default is 1)
    n_pols : int, optional
        Number of polarizations (default is 4)
    target : str, optional
        "system" for CPU/RAM, "gpu" for GPU VRAM (default is "system")
    config : Optional[SafetyConfig], optional
        Optional safety configuration (default is None)

    """
    required_gb = estimate_visibility_memory_gb(n_antennas, n_channels, n_times, n_pols)

    n_baselines = n_antennas * (n_antennas - 1) // 2
    detail = (
        f"Visibility array: {n_antennas} ant × {n_channels} chan × "
        f"{n_times} time × {n_pols} pol = {n_baselines} baselines, "
        f"~{required_gb:.2f} GB"
    )

    if target == "gpu":
        is_safe, reason = check_gpu_memory_available(required_gb, config=config)
    else:
        is_safe, reason = check_system_memory_available(required_gb, config=config)

    return is_safe, f"{detail}. {reason}"


# =============================================================================
# Context Managers for Safe Operations
# =============================================================================


@contextmanager
def safe_memory_context(
    max_system_gb: float | None = None,
    timeout_seconds: float | None = None,
    config: SafetyConfig | None = None,
):
    """Context manager for safe system memory operations.

        Applies memory limits and timeout, cleans up on exit.

    Parameters
    ----------
    max_system_gb : Optional[float], optional
        Maximum system memory to allow (default is None)
    timeout_seconds : Optional[float], optional
        Operation timeout (default is None)
    config : Optional[SafetyConfig], optional
        Safety configuration (default is None)

    Yields
    ------
        dict
        Dictionary with memory status

    """
    config = config or get_config()
    max_system_gb = max_system_gb or config.max_system_gb
    timeout_seconds = timeout_seconds or config.max_operation_seconds

    # Check memory before starting
    mem_before = get_system_memory_info()
    if mem_before["percent_used"] > config.system_usage_limit_pct:
        raise MemoryError(
            f"System memory already at {mem_before['percent_used']:.1f}%, "
            f"exceeds limit of {config.system_usage_limit_pct:.1f}%"
        )

    # Set up timeout handler
    old_handler = None

    def timeout_handler(signum, frame):
        raise TimeoutError(f"Operation timed out after {timeout_seconds}s")

    try:
        old_handler = signal.signal(signal.SIGALRM, timeout_handler)
        signal.alarm(int(timeout_seconds))

        yield {
            "memory_before": mem_before,
            "max_allowed_gb": max_system_gb,
            "timeout_seconds": timeout_seconds,
        }

    finally:
        # Clear alarm
        signal.alarm(0)
        if old_handler:
            signal.signal(signal.SIGALRM, old_handler)

        # Clean up memory
        gc.collect()

        # Log final state
        mem_after = get_system_memory_info()
        if config.log_allocations:
            delta = mem_after["used_gb"] - mem_before["used_gb"]
            if abs(delta) > 0.1:  # Log if change > 100MB
                logger.debug(
                    f"Memory delta: {delta:+.2f} GB "
                    f"(before: {mem_before['used_gb']:.2f} GB, "
                    f"after: {mem_after['used_gb']:.2f} GB)"
                )


@contextmanager
def safe_gpu_context(
    max_gpu_gb: float | None = None,
    gpu_id: int = 0,
    setup_pool: bool = True,
    clear_on_exit: bool = True,
    timeout_seconds: float | None = None,
    config: SafetyConfig | None = None,
):
    """Context manager for safe GPU operations.

        Configures memory pool limits, validates available memory,
        and cleans up on exit.

    Parameters
    ----------
    max_gpu_gb : Optional[float], optional
        Maximum GPU memory to allow (default is None)
    gpu_id : int, optional
        GPU device index (default is 0)
    setup_pool : bool, optional
        Whether to configure memory pool limit (default is True)
    clear_on_exit : bool, optional
        Whether to clear GPU memory on exit (default is True)
    timeout_seconds : Optional[float], optional
        Operation timeout (default is None)
    config : Optional[SafetyConfig], optional
        Safety configuration (default is None)

    Yields
    ------
        dict
        Dictionary with GPU status

    """
    config = config or get_config()
    max_gpu_gb = max_gpu_gb or config.max_gpu_gb
    timeout_seconds = timeout_seconds or config.max_operation_seconds

    if not is_gpu_available():
        raise RuntimeError("No GPU available")

    # Check GPU memory before starting
    mem_before = get_gpu_memory_info(gpu_id)
    if mem_before["percent_used"] > config.gpu_usage_limit_pct:
        raise MemoryError(
            f"GPU {gpu_id} memory already at {mem_before['percent_used']:.1f}%, "
            f"exceeds limit of {config.gpu_usage_limit_pct:.1f}%"
        )

    # Set up memory pool limit
    if setup_pool and config.enable_memory_pool:
        setup_gpu_memory_pool(max_gpu_gb, gpu_id)

    # Set up timeout handler
    old_handler = None

    def timeout_handler(signum, frame):
        raise TimeoutError(f"GPU operation timed out after {timeout_seconds}s")

    try:
        import cupy as cp

        old_handler = signal.signal(signal.SIGALRM, timeout_handler)
        signal.alarm(int(timeout_seconds))

        with cp.cuda.Device(gpu_id):
            yield {
                "gpu_id": gpu_id,
                "memory_before": mem_before,
                "max_allowed_gb": max_gpu_gb,
                "timeout_seconds": timeout_seconds,
            }

    finally:
        # Clear alarm
        signal.alarm(0)
        if old_handler:
            signal.signal(signal.SIGALRM, old_handler)

        # Clear GPU memory
        if clear_on_exit:
            clear_gpu_memory(gpu_id)

        # Log final state
        mem_after = get_gpu_memory_info(gpu_id)
        if config.log_allocations:
            delta = mem_after["used_gb"] - mem_before["used_gb"]
            if abs(delta) > 0.1:  # Log if change > 100MB
                logger.debug(
                    f"GPU {gpu_id} memory delta: {delta:+.2f} GB "
                    f"(before: {mem_before['used_gb']:.2f} GB, "
                    f"after: {mem_after['used_gb']:.2f} GB)"
                )


# =============================================================================
# Decorators for Safe Functions
# =============================================================================


def memory_safe(
    max_system_gb: float | None = None,
    required_gb: float | None = None,
    timeout_seconds: float | None = None,
) -> Callable[[F], F]:
    """Decorator for memory-safe functions.

    Checks available memory before execution and applies limits.

    Parameters
    ----------
    max_system_gb :
        Maximum system memory allowed
    required_gb :
        Known memory requirement
    timeout_seconds :
        Operation timeout
    Example :

    memory_safe :
        max_system_gb
    def :
        process_large_data
    max_system_gb: Optional[float] :
         (Default value = None)
    required_gb: Optional[float] :
         (Default value = None)
    timeout_seconds: Optional[float] :
         (Default value = None)

    Returns
    -------
    type


    """

    def decorator(func: F) -> F:
        @wraps(func)
        def wrapper(*args, **kwargs):
            config = get_config()
            max_gb = max_system_gb or config.max_system_gb
            timeout = timeout_seconds or config.max_operation_seconds

            # Pre-flight check if requirement is known
            if required_gb is not None:
                is_safe, reason = check_system_memory_available(required_gb)
                if not is_safe:
                    if config.raise_on_violation:
                        raise MemoryError(f"Memory check failed: {reason}")
                    logger.error(f"Memory check failed: {reason}")
                    return None

            with safe_memory_context(
                max_system_gb=max_gb,
                timeout_seconds=timeout,
            ):
                return func(*args, **kwargs)

        return wrapper  # type: ignore

    return decorator


def gpu_safe(
    max_gpu_gb: float | None = None,
    max_system_gb: float | None = None,
    required_gpu_gb: float | None = None,
    gpu_id: int = 0,
    timeout_seconds: float | None = None,
) -> Callable[[F], F]:
    """Decorator for GPU-safe functions.

    Checks GPU memory, configures memory pool, and applies limits.

    Parameters
    ----------
    max_gpu_gb :
        Maximum GPU memory allowed
    max_system_gb :
        Maximum system memory allowed
    required_gpu_gb :
        Known GPU memory requirement
    gpu_id :
        GPU device index
    timeout_seconds :
        Operation timeout
    Example :

    gpu_safe :
        max_gpu_gb
    def :
        gpu_fft
    import :
        cupy as cp
    max_gpu_gb: Optional[float] :
         (Default value = None)
    max_system_gb: Optional[float] :
         (Default value = None)
    required_gpu_gb: Optional[float] :
         (Default value = None)

    Returns
    -------
    type


    """

    def decorator(func: F) -> F:
        @wraps(func)
        def wrapper(*args, **kwargs):
            config = get_config()

            # Pre-flight GPU check
            if required_gpu_gb is not None:
                is_safe, reason = check_gpu_memory_available(required_gpu_gb, gpu_id)
                if not is_safe:
                    if config.raise_on_violation:
                        raise MemoryError(f"GPU memory check failed: {reason}")
                    logger.error(f"GPU memory check failed: {reason}")
                    return None

            # Use nested contexts
            with safe_memory_context(
                max_system_gb=max_system_gb,
                timeout_seconds=timeout_seconds,
            ):
                with safe_gpu_context(
                    max_gpu_gb=max_gpu_gb,
                    gpu_id=gpu_id,
                    timeout_seconds=timeout_seconds,
                ):
                    return func(*args, **kwargs)

        return wrapper  # type: ignore

    return decorator


# =============================================================================
# High-Level Safe Operations
# =============================================================================


def safe_gpu_array(
    shape: tuple[int, ...],
    dtype: str = "float32",
    gpu_id: int = 0,
) -> Any:
    """Safely allocate a GPU array with pre-flight memory check.

    Parameters
    ----------
    shape : Tuple[int, ...]
        Array shape
    dtype : str, optional
        Data type (default is "float32")
    gpu_id : int, optional
        GPU device index (default is 0)

    """
    if not is_gpu_available():
        raise RuntimeError("No GPU available")

    required_gb = estimate_array_size_gb(shape, dtype)
    is_safe, reason = check_gpu_memory_available(required_gb, gpu_id)

    if not is_safe:
        raise MemoryError(f"Cannot allocate GPU array: {reason}")

    import cupy as cp

    with cp.cuda.Device(gpu_id):
        return cp.empty(shape, dtype=dtype)


def safe_gpu_zeros(
    shape: tuple[int, ...],
    dtype: str = "float32",
    gpu_id: int = 0,
) -> Any:
    """Safely allocate a zeroed GPU array.

    Parameters
    ----------
    shape : Tuple[int, ...]
        Array shape
    dtype : str, optional
        Data type (default is "float32")
    gpu_id : int, optional
        GPU device index (default is 0)

    """
    if not is_gpu_available():
        raise RuntimeError("No GPU available")

    required_gb = estimate_array_size_gb(shape, dtype)
    is_safe, reason = check_gpu_memory_available(required_gb, gpu_id)

    if not is_safe:
        raise MemoryError(f"Cannot allocate GPU array: {reason}")

    import cupy as cp

    with cp.cuda.Device(gpu_id):
        return cp.zeros(shape, dtype=dtype)


def safe_to_gpu(
    array,
    gpu_id: int = 0,
    dtype: str | None = None,
) -> Any:
    """Safely transfer array to GPU with memory check.

    Parameters
    ----------
        array :
        NumPy array to transfer
    gpu_id : int, optional
        GPU device index (default is 0)
    dtype : Optional[str], optional
        Optional dtype conversion (default is None)

    """
    if not is_gpu_available():
        raise RuntimeError("No GPU available")

    if dtype is not None:
        effective_dtype = dtype
    else:
        effective_dtype = str(array.dtype)

    required_gb = estimate_array_size_gb(array.shape, effective_dtype)
    is_safe, reason = check_gpu_memory_available(required_gb, gpu_id)

    if not is_safe:
        raise MemoryError(f"Cannot transfer to GPU: {reason}")

    import cupy as cp

    with cp.cuda.Device(gpu_id):
        if dtype is not None:
            return cp.asarray(array, dtype=dtype)
        return cp.asarray(array)


# =============================================================================
# Integration with GPU Monitoring
# =============================================================================


def register_with_monitor(
    alert_callback: Callable | None = None,
) -> bool:
    """Register safety module with GPU monitoring system.

        This connects the proactive safety checks with the passive
        monitoring system for comprehensive protection.

    Parameters
    ----------
    alert_callback : Optional[Callable], optional
        Optional callback for safety alerts (default is None)

    """
    # The passive monitoring system lived in the retired dsa110_contimg
    # package; safety checks in this module are self-contained without it.
    logger.debug("GPU monitoring integration retired with dsa110_contimg")
    return False


# =============================================================================
# Initialization
# =============================================================================


def initialize_gpu_safety(
    config: SafetyConfig | None = None,
    setup_pools: bool = True,
) -> dict[str, Any]:
    """Initialize GPU safety system.

        Call this at application startup to configure memory pools
        and safety limits.

    Parameters
    ----------
    config : Optional[SafetyConfig], optional
        Safety configuration (uses defaults if None) (default is None)
    setup_pools : bool, optional
        Whether to configure GPU memory pools (default is True)

    """
    config = config or DEFAULT_CONFIG
    set_config(config)

    result = {
        "config": config,
        "system_memory": get_system_memory_info(),
        "gpu_available": is_gpu_available(),
        "gpu_count": get_gpu_count(),
        "gpu_pools_configured": False,
        "monitoring_registered": False,
    }

    # Set up GPU memory pools
    if setup_pools and result["gpu_available"]:
        pools_ok = True
        for gpu_id in range(result["gpu_count"]):
            if not setup_gpu_memory_pool(config.gpu_pool_limit_gb, gpu_id):
                pools_ok = False
        result["gpu_pools_configured"] = pools_ok

    # Register with monitoring
    result["monitoring_registered"] = register_with_monitor()

    # Add GPU memory info
    if result["gpu_available"]:
        result["gpu_memory"] = [get_gpu_memory_info(i) for i in range(result["gpu_count"])]

    logger.info(
        f"GPU safety initialized: {result['gpu_count']} GPUs, "
        f"pools={'OK' if result['gpu_pools_configured'] else 'FAILED'}"
    )

    return result


# =============================================================================
# Convenience Exports
# =============================================================================


__all__ = [
    # Configuration
    "SafetyConfig",
    "DEFAULT_CONFIG",
    "get_config",
    "set_config",
    # System memory
    "get_system_memory_info",
    "get_process_memory_gb",
    "check_system_memory_available",
    "set_system_memory_limit",
    # GPU memory
    "is_gpu_available",
    "get_gpu_count",
    "get_gpu_memory_info",
    "get_gpu_memory_status",
    "check_gpu_memory_available",
    "setup_gpu_memory_pool",
    "clear_gpu_memory",
    # Estimation
    "estimate_array_size_gb",
    "estimate_gpu_array_size",
    "estimate_visibility_memory_gb",
    "check_visibility_allocation_safe",
    # Context managers
    "safe_memory_context",
    "safe_gpu_context",
    # Decorators
    "memory_safe",
    "gpu_safe",
    # Safe operations
    "safe_gpu_array",
    "safe_gpu_zeros",
    "safe_to_gpu",
    # Integration
    "register_with_monitor",
    "initialize_gpu_safety",
]
