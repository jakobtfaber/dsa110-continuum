# Vendored from dsa110-contimg @ /data/dsa110-contimg/backend/src (H17), 2026-07-03,
# as part of the contimg-import-retirement migration (docs/rse/specs/plan-contimg-import-retirement.md).
"""
GPU utilities for seamless GPU acceleration across all pipeline modes.

This module provides unified GPU detection, configuration, and Docker command
building that works consistently across CLI, streaming, and Dagster execution modes.

Configuration is now centralized in dsa110_contimg.config.GPUSettings.
This module reads defaults from there and provides runtime detection/overrides.

Example usage:
    from dsa110_continuum.utils.gpu_utils import get_gpu_config, build_docker_command

    # Auto-detect GPU availability
    gpu_config = get_gpu_config()

    # Build Docker command with GPU support if available
    cmd = build_docker_command(
        image="dsa110-contimg:gpu",
        command=["wsclean", "-gridder", "idg", "-idg-mode", "gpu", ...],
        gpu_config=gpu_config,
    )
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from enum import Enum
from functools import lru_cache

import numpy as _np

from dsa110_continuum.unified_config import settings

logger = logging.getLogger(__name__)


class GPUBackend(str, Enum):
    """GPU backend types."""

    NONE = "none"  # No GPU acceleration
    CUDA = "cuda"  # NVIDIA CUDA
    # Future: OPENCL = "opencl"  # OpenCL (AMD, Intel)


@dataclass
class GPUInfo:
    """Information about a detected GPU."""

    index: int
    name: str
    memory_mb: int
    driver_version: str
    cuda_version: str | None = None
    compute_capability: str | None = None

    @property
    def memory_gb(self) -> float:
        """Memory in GB."""
        return self.memory_mb / 1024.0


@dataclass
class GPUConfig:
    """Unified GPU configuration for pipeline execution.

    This configuration is used across all pipeline modes (CLI, streaming, Dagster)
    to ensure consistent GPU behavior. Defaults are loaded from centralized settings.
    """

    # Core settings - defaults from settings.gpu
    enabled: bool = field(default_factory=lambda: settings.gpu.enabled)
    backend: GPUBackend = GPUBackend.CUDA
    device_ids: list[int] = field(default_factory=list)  # Empty = all GPUs

    # Docker settings
    docker_gpu_flag: str = "--gpus all"  # Can be "--gpus 0" for specific GPU

    # WSClean-specific settings - defaults from settings.gpu
    wsclean_gridder: str = field(default_factory=lambda: settings.gpu.gridder)
    wsclean_idg_mode: str = field(default_factory=lambda: settings.gpu.idg_mode)

    # Photometry GPU settings (CuPy)
    photometry_use_gpu: bool = True
    photometry_batch_threshold: int = 100  # Min sources to use GPU

    # Memory management - defaults from settings.gpu
    gpu_memory_fraction: float = field(default_factory=lambda: settings.gpu.memory_fraction)

    # Detected GPU info (populated by detect_gpus())
    gpus: list[GPUInfo] = field(default_factory=list)

    @property
    def has_gpu(self) -> bool:
        """Check if any GPUs are available."""
        return len(self.gpus) > 0

    @property
    def total_gpu_memory_gb(self) -> float:
        """Total GPU memory across all devices."""
        return sum(gpu.memory_gb for gpu in self.gpus)

    @property
    def effective_gridder(self) -> str:
        """Get effective gridder based on GPU availability."""
        if self.enabled and self.has_gpu:
            return self.wsclean_gridder
        return "wgridder"  # CPU fallback

    @property
    def effective_idg_mode(self) -> str:
        """Get effective IDG mode based on GPU availability."""
        if self.enabled and self.has_gpu:
            return self.wsclean_idg_mode
        return "cpu"


def _parse_nvidia_smi_output(output: str) -> list[GPUInfo]:
    """Parse nvidia-smi query output into GPUInfo list."""
    gpus = []
    lines = output.strip().split("\n")

    for i, line in enumerate(lines):
        if not line.strip():
            continue
        parts = line.split(", ")
        if len(parts) >= 3:
            try:
                gpus.append(
                    GPUInfo(
                        index=i,
                        name=parts[0].strip(),
                        memory_mb=int(parts[1].strip().replace(" MiB", "")),
                        driver_version=parts[2].strip(),
                        cuda_version=None,  # Not available via nvidia-smi query
                    )
                )
            except (ValueError, IndexError) as e:
                logger.debug("Failed to parse GPU info from line: %s: %s", line, e)

    return gpus


@lru_cache(maxsize=1)
def detect_gpus() -> list[GPUInfo]:
    """Detect available NVIDIA GPUs.

    Returns
    -------
        List of GPUInfo for each detected GPU
    """
    gpus: list[GPUInfo] = []

    # Try nvidia-smi first (most reliable)
    nvidia_smi = shutil.which("nvidia-smi")
    if nvidia_smi:
        try:
            result = subprocess.run(
                [
                    nvidia_smi,
                    "--query-gpu=name,memory.total,driver_version",
                    "--format=csv,noheader,nounits",
                ],
                capture_output=True,
                text=True,
                timeout=10,
                check=True,
            )
            gpus = _parse_nvidia_smi_output(result.stdout)
            logger.info("Detected %d NVIDIA GPU(s) via nvidia-smi", len(gpus))
            for gpu in gpus:
                logger.debug("  GPU %d: %s (%.1f GB)", gpu.index, gpu.name, gpu.memory_gb)
        except (subprocess.TimeoutExpired, subprocess.SubprocessError) as e:
            logger.debug("nvidia-smi failed: %s", e)

    return gpus


def check_nvidia_docker() -> bool:
    """Check if NVIDIA Docker runtime is available.

    Returns
    -------
        True if nvidia-container-toolkit is properly configured
    """
    docker_cmd = shutil.which("docker")
    if not docker_cmd:
        return False

    try:
        # Check if nvidia runtime is configured
        result = subprocess.run(
            [docker_cmd, "info", "--format", "{{.Runtimes}}"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if result.returncode == 0 and "nvidia" in result.stdout.lower():
            return True

        # Also check for --gpus support (CDI mode)
        result = subprocess.run(
            [
                docker_cmd,
                "run",
                "--rm",
                "--gpus",
                "all",
                "nvidia/cuda:11.1.1-base-ubuntu18.04",
                "echo",
                "ok",
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        return result.returncode == 0

    except (subprocess.TimeoutExpired, subprocess.SubprocessError) as e:
        logger.debug("NVIDIA Docker check failed: %s", e)
        return False


def get_array_module(*, prefer_gpu: bool | None = None, min_elements: int | None = None):
    """Select an array module (cupy or numpy) based on GPU availability.

    Parameters
    ----------
    prefer_gpu : bool
        If True, pick CuPy when available and a GPU is present.
    min_elements : int
        Minimum array size (in elements) to justify GPU overhead.

    Returns
    -------
        tuple
        Tuple of (xp module, is_gpu: bool)
    """
    if prefer_gpu is None:
        prefer_gpu = settings.gpu.prefer_gpu
    if min_elements is None:
        min_elements = settings.gpu.min_array_size

    if not prefer_gpu:
        return _np, False

    try:  # Lazy import to avoid hard dependency
        import cupy as cp  # type: ignore
    except Exception:
        return _np, False

    gpus = detect_gpus()
    if not gpus:
        return _np, False

    try:
        cp.cuda.runtime.getDeviceCount()  # Can still raise if driver missing
    except Exception:
        return _np, False

    # Heuristic: only flip to GPU for sufficiently large arrays
    return cp, True if min_elements > 0 else True


def _build_gpu_flag_string(device_ids: list[int]) -> str:
    """Build Docker GPU flag string."""
    if device_ids:
        return f"--gpus '\"device={','.join(str(d) for d in device_ids)}\"'"
    return "--gpus all"


@lru_cache(maxsize=1)
def get_gpu_config(
    enabled: bool | None = None,
    device_ids: tuple[int, ...] | None = None,
) -> GPUConfig:
    """Get unified GPU configuration with auto-detection.

        This is the main entry point for GPU configuration. It auto-detects
        available GPUs and Docker capabilities, returning a configuration
        that works across all pipeline modes.

    Parameters
    ----------
    enabled : bool or None
        Override auto-detection (None = auto-detect)
    device_ids : list or None
        Specific GPU indices to use (None = all)

    Returns
    -------
        GPUConfig
        GPUConfig with detected capabilities

    Examples
    --------
        >>> config = get_gpu_config()
        >>> if config.has_gpu:
        ...     print(f"Using {len(config.gpus)} GPU(s)")
        >>> else:
        ...     print("CPU-only mode")
    """
    # Detect GPUs
    gpus = detect_gpus()

    # Check Docker GPU support
    has_docker_gpu = check_nvidia_docker() if gpus else False

    # Determine if GPU should be enabled
    if enabled is None:
        enabled = len(gpus) > 0 and has_docker_gpu

    # Build device list
    device_list = list(device_ids) if device_ids else []

    # Build GPU flag
    gpu_flag = _build_gpu_flag_string(device_list)

    config = GPUConfig(
        enabled=enabled,
        backend=GPUBackend.CUDA if gpus else GPUBackend.NONE,
        device_ids=device_list,
        docker_gpu_flag=gpu_flag,
        gpus=gpus,
        wsclean_idg_mode=(settings.gpu.idg_mode if gpus else "cpu"),
    )

    if config.has_gpu:
        logger.info(
            "GPU acceleration enabled: %d GPU(s), %.1f GB total memory",
            len(gpus),
            config.total_gpu_memory_gb,
        )
    else:
        logger.info("GPU acceleration disabled (no GPUs detected or Docker GPU unavailable)")

    return config


def _add_docker_gpu_flags(cmd: list[str], gpu_config: GPUConfig) -> None:
    """Add GPU flags to Docker command."""
    if gpu_config.enabled and gpu_config.has_gpu:
        # Parse gpu_flag (handle quoted format)
        gpu_flag = gpu_config.docker_gpu_flag
        if gpu_flag.startswith("--gpus"):
            parts = gpu_flag.split(None, 1)
            cmd.append(parts[0])  # --gpus
            if len(parts) > 1:
                # Remove surrounding quotes if present
                value = parts[1].strip("'\"")
                cmd.append(value)


def _add_docker_volumes(cmd: list[str], volumes: dict[str, str] | None) -> None:
    """Add volume mounts to Docker command."""
    if volumes:
        for host_path, container_path in volumes.items():
            cmd.extend(["-v", f"{host_path}:{container_path}"])
    else:
        # Default volume mounts for DSA-110 pipeline
        # NOTE: /data is intentionally NOT mounted because it's on NTFS via FUSE,
        # which causes Docker container hangs on kernel 4.15 during cleanup.
        # MS files should be on /stage, outputs on /stage or /dev/shm/dsa110-contimg.
        cmd.extend(["-v", "/dev/shm/dsa110-contimg:/dev/shm/dsa110-contimg"])
        cmd.extend(["-v", "/stage:/stage"])
        cmd.extend(["-v", "/dev/shm:/dev/shm"])


def build_docker_command(
    image: str,
    command: list[str],
    gpu_config: GPUConfig | None = None,
    volumes: dict[str, str] | None = None,
    workdir: str | None = None,
    env_vars: dict[str, str] | None = None,
    extra_flags: list[str] | None = None,
    remove: bool = True,
) -> list[str]:
    """Build Docker command with optional GPU support.

        This is the unified way to build Docker commands across the pipeline.
        It handles GPU flags, volume mounts, and other common options.

    Parameters
    ----------
    image : str
        Docker image name
    command : list
        Command to run inside container
    gpu_config : GPUConfig or None, optional
        GPU configuration (None = auto-detect)
    volumes : dict or None, optional
    Host:container volume mappings
    workdir : str or None, optional
        Working directory inside container
    env_vars : dict or None, optional
        Environment variables to set
    extra_flags : list or None, optional
        Additional Docker flags
    remove : bool, optional
        Remove container after exit (--rm)

    Returns
    -------
        list
        Complete Docker command as list

    Examples
    --------
        >>> cmd = build_docker_command(
        ...     image="dsa110-contimg:gpu",
        ...     command=["wsclean", "-size", "5040", "5040", ...],
        ...     volumes={"/data": "/data", "/stage": "/stage"},
        ... )
        >>> subprocess.run(cmd)
    """
    if gpu_config is None:
        gpu_config = get_gpu_config()

    docker_cmd = shutil.which("docker")
    if not docker_cmd:
        raise RuntimeError("Docker not found in PATH")

    cmd = [docker_cmd, "run"]

    # Basic flags
    if remove:
        cmd.append("--rm")

    # GPU support
    _add_docker_gpu_flags(cmd, gpu_config)

    # Volumes
    _add_docker_volumes(cmd, volumes)

    # Working directory
    if workdir:
        cmd.extend(["-w", workdir])

    # Environment variables
    if env_vars:
        for key, value in env_vars.items():
            cmd.extend(["-e", f"{key}={value}"])

    # Extra flags
    if extra_flags:
        cmd.extend(extra_flags)

    # Image and command
    cmd.append(image)
    cmd.extend(command)

    return cmd


def build_wsclean_gpu_args(gpu_config: GPUConfig | None = None) -> list[str]:
    """Build WSClean GPU-specific arguments.

    Parameters
    ----------
    gpu_config : GPUConfig or None, optional
        GPU configuration (None = auto-detect)

    Returns
    -------
        list
        List of WSClean arguments for GPU acceleration

    Examples
    --------
        >>> args = build_wsclean_gpu_args()
        >>> # Returns ["-gridder", "idg", "-idg-mode", "gpu"] if GPU available
        >>> # Returns ["-gridder", "wgridder"] if no GPU
    """
    if gpu_config is None:
        gpu_config = get_gpu_config()

    args = []

    if gpu_config.enabled and gpu_config.has_gpu:
        args.extend(["-gridder", gpu_config.wsclean_gridder])
        if gpu_config.wsclean_gridder == "idg":
            args.extend(["-idg-mode", gpu_config.effective_idg_mode])
        logger.debug("WSClean GPU args: %s", args)
    else:
        # CPU fallback - use wgridder (still fast, but CPU-only)
        args.extend(["-gridder", "wgridder"])
        logger.debug("WSClean using CPU-only wgridder")

    return args


def get_gpu_env_config() -> GPUConfig:
    """Get GPU configuration from environment variables.

        Environment variables:
    PIPELINE_GPU_ENABLED: "true" or "false" (default: auto-detect)
    PIPELINE_GPU_DEVICES: Comma-separated device IDs (default: all)
    PIPELINE_GPU_GRIDDER: WSClean gridder (default: "idg")
    PIPELINE_GPU_IDG_MODE: IDG mode (default: from settings, typically "gpu")
    PIPELINE_GPU_MEMORY_FRACTION: Max memory fraction (default: 0.9)

    Returns
    -------
        GPUConfig
        GPUConfig from environment
    """
    # Get enabled state
    enabled_str = os.getenv("PIPELINE_GPU_ENABLED", "").lower()
    if enabled_str == "true":
        enabled = True
    elif enabled_str == "false":
        enabled = False
    else:
        enabled = None  # Auto-detect

    # Get device IDs
    devices_str = os.getenv("PIPELINE_GPU_DEVICES", "")
    device_ids: tuple[int, ...] | None = None
    if devices_str:
        try:
            device_ids = tuple(int(d.strip()) for d in devices_str.split(","))
        except ValueError:
            logger.warning("Invalid PIPELINE_GPU_DEVICES: %s", devices_str)

    # Get base config with detection
    config = get_gpu_config(enabled=enabled, device_ids=device_ids)

    # Override with env vars
    config.wsclean_gridder = os.getenv("PIPELINE_GPU_GRIDDER", config.wsclean_gridder)
    config.wsclean_idg_mode = os.getenv("PIPELINE_GPU_IDG_MODE", config.wsclean_idg_mode)

    memory_fraction_str = os.getenv("PIPELINE_GPU_MEMORY_FRACTION", "")
    if memory_fraction_str:
        try:
            config.gpu_memory_fraction = float(memory_fraction_str)
        except ValueError:
            logger.warning("Invalid PIPELINE_GPU_MEMORY_FRACTION: %s", memory_fraction_str)

    return config


# Module-level convenience functions


def is_gpu_available() -> bool:
    """Quick check if GPU acceleration is available."""
    return get_gpu_config().has_gpu


def get_gpu_count() -> int:
    """Get number of available GPUs."""
    return len(get_gpu_config().gpus)


def clear_gpu_cache() -> None:
    """Clear cached GPU detection results.

    Call this if GPU configuration changes (e.g., Docker restarted).
    """
    detect_gpus.cache_clear()
    get_gpu_config.cache_clear()
