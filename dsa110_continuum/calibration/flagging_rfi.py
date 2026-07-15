# ruff: noqa: D103,D414,I001
"""RFI strategy execution for calibration flagging."""

from __future__ import annotations

import logging
import multiprocessing
import os
import shutil
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path

from dsa110_continuum._lazy_init import require_headless

from dsa110_continuum.config import get_env_path

try:
    from dsa110_continuum.utils.error_context import format_ms_error_with_suggestions
except ImportError:
    def format_ms_error_with_suggestions(
        error: Exception, ms: str, operation: str, suggestions: list[str]
    ) -> str:
        suggestion_text = "\n".join(f"  - {suggestion}" for suggestion in suggestions)
        return f"{operation} failed for {ms}: {error}\n\nSuggestions:\n{suggestion_text}"

def CASAService(*args, **kwargs):
    from dsa110_continuum.calibration import flagging

    return flagging.CASAService(*args, **kwargs)


@contextmanager
def suppress_subprocess_stderr():
    from dsa110_continuum.calibration import flagging

    with flagging.suppress_subprocess_stderr():
        yield


def flag_residual_rfi_clip(*args, **kwargs):
    from dsa110_continuum.calibration import flagging

    return flagging.flag_residual_rfi_clip(*args, **kwargs)


def flag_rfi(
    ms: str,
    datacolumn: str = "data",
    backend: str = "aoflagger",
    aoflagger_path: str | None = None,
    strategy: str | None = None,
    extend_flags: bool = True,
    clip_residual: bool = True,
    clip_sigma: float = 7.0,
    fail_closed: bool = True,
) -> None:
    """Flag RFI using CASA or AOFlagger, with optional post-clip.

    When *backend* is ``"aoflagger"`` and *clip_residual* is *True* (the
    default), a MAD-based amplitude sigma-clip is applied after AOFlagger
    to catch residual RFI that SumThreshold misses on short time-axis data.

    Parameters
    ----------
    ms :
        Path to Measurement Set
    datacolumn :
        Data column to use (default: "data")
    backend :
        Backend to use - "aoflagger" (default) or "casa"
    aoflagger_path :
        Path to aoflagger executable or "docker" (for AOFlagger backend)
    strategy :
        Optional path to custom Lua strategy file (for AOFlagger backend)
    extend_flags :
        If True, extend flags to adjacent channels/times after flagging (default: True)
    clip_residual :
        If True, apply a post-AOFlagger MAD sigma-clip on cross-corr amplitudes
        (default: True).  Only used when *backend* is ``"aoflagger"``.
    clip_sigma :
        Threshold in MAD-σ units for the residual clip (default: 7.0).
    fail_closed :
        Require the validated Stage 1/2/3 chain to complete. Science tile
        processing uses the default; diagnostic callers may explicitly opt
        into legacy warning-only behavior.
    """
    require_headless()
    from dsa110_continuum.utils.ms_permissions import ensure_ms_writable

    ensure_ms_writable(ms)
    if backend == "aoflagger":
        if fail_closed and (not clip_residual or not extend_flags):
            raise ValueError(
                "fail_closed AOFlagger requires residual clipping and flag extension"
            )
        flag_rfi_aoflagger(
            ms, datacolumn=datacolumn, aoflagger_path=aoflagger_path, strategy=strategy
        )

        # Stage 2: MAD-based residual clip on cross-correlation amplitudes.
        # AOFlagger's SumThreshold needs extended time–frequency structure to
        # detect RFI.  DSA-110 drift-scan data has only 24 time samples, so a
        # post-AOFlagger sigma-clip catches the broadband and short-baseline RFI
        # that SumThreshold misses.  Validated: kurtosis 535 → 2.8, +1.2 % cost.
        if clip_residual:
            try:
                flag_residual_rfi_clip(ms, datacolumn=datacolumn, sigma=clip_sigma)
            except Exception:
                logging.getLogger(__name__).warning(
                    "Post-AOFlagger sigma-clip failed; AOFlagger flags are intact.",
                    exc_info=True,
                )
                if fail_closed:
                    raise

        # Extend flags after AOFlagger (if enabled)
        # Note: Flag extension may fail when using Docker due to permission issues
        # (AOFlagger writes as root, making subsequent writes fail). This is non-fatal.
        if extend_flags:
            time.sleep(2)  # Allow file locks to clear
            try:
                flag_extend(
                    ms,
                    flagnearfreq=True,
                    flagneartime=True,
                    extendpols=True,
                    datacolumn=datacolumn,
                )
                logger = logging.getLogger(__name__)
                logger.debug("Flag extension completed successfully")
            except (RuntimeError, PermissionError, OSError) as e:
                # If file lock or permission issue, log warning but don't fail
                logger = logging.getLogger(__name__)
                error_str = str(e).lower()
                if any(
                    term in error_str
                    for term in [
                        "cannot be opened",
                        "not writable",
                        "permission denied",
                        "permission",
                    ]
                ):
                    logger.warning(
                        "Flag extension skipped due to file permission/lock issue "
                        "(common when using Docker AOFlagger). "
                        f"RFI flags from AOFlagger are still applied. Error: {e}"
                    )
                else:
                    logger.warning(
                        f"Flag extension failed: {e}. RFI flags from AOFlagger are still applied."
                    )
                if fail_closed:
                    raise
    else:
        # Two-stage RFI flagging using flagdata modes (tfcrop then rflag)
        service = CASAService()
        with suppress_subprocess_stderr():
            service.flagdata(
                vis=ms,
                mode="tfcrop",
                datacolumn=datacolumn,
                timecutoff=4.0,
                freqcutoff=4.0,
                timefit="line",
                freqfit="poly",
                maxnpieces=5,
                winsize=3,
                extendflags=False,
            )

            service.flagdata(
                vis=ms,
                mode="rflag",
                datacolumn=datacolumn,
                timedevscale=4.0,
                freqdevscale=4.0,
                extendflags=False,
            )
        # Extend flags to adjacent channels/times after flagging (if enabled)
        if extend_flags:
            try:
                flag_extend(
                    ms,
                    flagnearfreq=True,
                    flagneartime=True,
                    extendpols=True,
                    datacolumn=datacolumn,
                )
            except RuntimeError as e:
                # If file lock or permission issue, log warning but don't fail
                logger = logging.getLogger(__name__)
                if "cannot be opened" in str(e) or "not writable" in str(e):
                    logger.warning(
                        f"Could not extend flags due to file lock/permission: {e}. "
                        "Flags from tfcrop+rflag are still applied."
                    )
                else:
                    raise


def _get_default_aoflagger_strategy() -> str | None:
    """Get the default DSA-110 AOFlagger strategy file path.

    Returns
    -------
        Path to dsa110-default.lua if it exists, None otherwise

    """
    # Try multiple possible locations for the strategy file
    possible_paths = [
        get_env_path("CONTIMG_BASE_DIR", default="/data/dsa110-contimg")
        / "config/dsa110-default.lua",
        Path(__file__).parent.parent.parent.parent / "config" / "dsa110-default.lua",
        Path(os.getcwd()) / "config" / "dsa110-default.lua",
    ]

    for strategy_path in possible_paths:
        if strategy_path.exists():
            return str(strategy_path.resolve())

    return None


def flag_rfi_aoflagger(
    ms: str,
    datacolumn: str = "data",
    aoflagger_path: str | None = None,
    strategy: str | None = None,
) -> None:
    """Flag RFI using AOFlagger (faster alternative to CASA tfcrop).

        AOFlagger uses the SumThreshold algorithm which is typically 2-5x faster
        than CASA's tfcrop+rflag combination for large datasets.

        **Note:** On Ubuntu 18.x systems, Docker may be required due to CMake/pybind11
        compatibility issues. The default behavior is to prefer native and fall back to Docker.

    Parameters
    ----------
    ms :
        Path to Measurement Set
    datacolumn :
        Data column to use (default: "data")
    aoflagger_path :
        Path to aoflagger executable, "docker" to force Docker, or None to auto-detect
    strategy :
        Optional path to custom Lua strategy file. If None, AOFlagger auto-detects strategy.
        To force a default strategy globally, set CONTIMG_AOFLAGGER_STRATEGY to a strategy path.

    Raises
    ------
    RuntimeError
        If AOFlagger is not available
    subprocess.CalledProcessError
        If AOFlagger execution fails

    """
    require_headless()
    logger = logging.getLogger(__name__)

    # Determine AOFlagger command
    # Default to native and fall back to Docker when needed
    use_docker = False
    if aoflagger_path:
        if aoflagger_path == "docker":
            # Force Docker usage
            docker_cmd = shutil.which("docker")
            if not docker_cmd:
                suggestions = [
                    "Install Docker",
                    "Verify Docker is in PATH",
                    "Check Docker service is running",
                    "Use --aoflagger-path to specify native AOFlagger location",
                ]
                error_msg = format_ms_error_with_suggestions(
                    RuntimeError("Docker not found but --aoflagger-path=docker was specified"),
                    ms,
                    "AOFlagger setup",
                    suggestions,
                )
                raise RuntimeError(error_msg)
            use_docker = True
            # Use current user ID to avoid permission issues
            user_id = os.getuid()
            group_id = os.getgid()
            aoflagger_cmd = [
                docker_cmd,
                "run",
                "--rm",
                "--user",
                f"{user_id}:{group_id}",
                "-v",
                "/dev/shm/dsa110-contimg:/dev/shm/dsa110-contimg",
                "-v",
                "/data:/data",
                "-v",
                "/stage:/stage",
                "aoflagger:latest",
                "aoflagger",
            ]
        else:
            # Explicit path provided - use it directly
            aoflagger_cmd = [aoflagger_path]
            logger.info(f"Using AOFlagger from explicit path: {aoflagger_path}")
    else:
        # Auto-detect: prefer native, fall back to Docker
        native_aoflagger = shutil.which("aoflagger")
        docker_cmd = shutil.which("docker")

        if native_aoflagger:
            aoflagger_cmd = [native_aoflagger]
            logger.info("Using native AOFlagger")
        else:
            if docker_cmd:
                # Verify image exists before committing to Docker
                try:
                    img_check = subprocess.run(
                        [docker_cmd, "images", "-q", "aoflagger:latest"],
                        capture_output=True,
                        text=True,
                        timeout=5,
                    )
                    if img_check.returncode == 0 and img_check.stdout.strip():
                        use_docker = True
                    else:
                        logger.debug("Docker found but 'aoflagger:latest' image not found.")
                except (subprocess.SubprocessError, OSError):
                    logger.debug("Failed to check Docker images.")

            if use_docker:
                # Docker is available and image exists - use it as fallback
                # Use current user ID to avoid permission issues
                user_id = os.getuid()
                group_id = os.getgid()
                aoflagger_cmd = [
                    docker_cmd,
                    "run",
                    "--rm",
                    "--user",
                    f"{user_id}:{group_id}",
                    "-v",
                    "/dev/shm/dsa110-contimg:/dev/shm/dsa110-contimg",
                    "-v",
                    "/data:/data",
                    "-v",
                    "/stage:/stage",
                    "aoflagger:latest",
                    "aoflagger",
                ]
                logger.debug("Using Docker for AOFlagger (native not found)")
            else:
                suggestions = [
                    "Install native AOFlagger and ensure it's in PATH",
                    "Install Docker and build aoflagger:latest image",
                    "Use --aoflagger-path to specify AOFlagger location",
                    "Check AOFlagger installation documentation",
                ]
                error_msg = format_ms_error_with_suggestions(
                    RuntimeError("AOFlagger not found (native or Docker)."),
                    ms,
                    "AOFlagger setup",
                    suggestions,
                )
                raise RuntimeError(error_msg)

    # Build command
    cmd = aoflagger_cmd.copy()

    # Determine strategy to use
    strategy_to_use = (
        strategy
        or os.environ.get("CONTIMG_AOFLAGGER_STRATEGY")
        or _get_default_aoflagger_strategy()
    )
    if strategy_to_use is None or str(strategy_to_use).strip() == "":
        strategy_to_use = None
    logger.info("AOFlagger strategy: %s", strategy_to_use or "auto-detect")

    # Add strategy if we have one
    if strategy_to_use:
        # When using Docker, ensure the strategy path is accessible inside the container
        if use_docker:
            # Strategy file must be under /data or /stage (mounted volumes)
            strategy_path = Path(strategy_to_use)
            if not str(strategy_path).startswith(("/data", "/stage")):
                # Try to find it under /data
                strategy_name = strategy_path.name
                base_dir = os.environ.get("CONTIMG_BASE_DIR", "/data/dsa110-contimg")
                docker_strategy_path = f"{base_dir}/config/{strategy_name}"
                if Path(
                    str(get_env_path("CONTIMG_BASE_DIR", default="/data/dsa110-contimg"))
                    + "/config/dsa110-default.lua"
                ).exists():
                    strategy_to_use = docker_strategy_path
                    logger.debug(f"Using Docker-accessible strategy path: {strategy_to_use}")
                else:
                    logger.warning(
                        f"Strategy file {strategy_to_use} may not be accessible in Docker "
                        "container. Ensure it's under /data or /stage, or mount it explicitly."
                    )
        cmd.extend(["-strategy", strategy_to_use])

    # Add MS path (required - AOFlagger will auto-detect strategy if not specified)
    # Also add parallel processing flag (use all available cores)

    # Respect CPU affinity if set (e.g. in Docker/Kubernetes)
    try:
        # available in Python 3.3+ on Linux
        n_cores = len(os.sched_getaffinity(0))
    except (AttributeError, NotImplementedError, OSError):
        # Fallback to cpu_count (physical cores on host)
        n_cores = multiprocessing.cpu_count()

    cmd.extend(["-j", str(n_cores)])

    # If datacolumn is specified and not "data", tell AOFlagger to use it
    # Note: "data" is the default for AOFlagger so we don't need to specify it
    if datacolumn and datacolumn.lower() != "data":
        cmd.extend(["-column", datacolumn])

    cmd.append(ms)

    # Execute AOFlagger
    logger.info(f"Running AOFlagger: {' '.join(cmd)}")
    try:
        subprocess.run(cmd, check=True, capture_output=False)
        logger.info(":check: AOFlagger RFI flagging complete")
    except subprocess.CalledProcessError as e:
        logger.error(f"AOFlagger failed with exit code {e.returncode}")
        raise
    except FileNotFoundError:
        suggestions = [
            "Check AOFlagger installation",
            "Verify AOFlagger is in PATH",
            "Use --aoflagger-path to specify AOFlagger location",
            "Check Docker image is available (if using Docker)",
        ]
        error_msg = format_ms_error_with_suggestions(
            FileNotFoundError(f"AOFlagger executable not found: {aoflagger_cmd[0]}"),
            ms,
            "AOFlagger execution",
            suggestions,
        )
        logger.error(error_msg)
        raise RuntimeError(error_msg)


def flag_extend(
    ms: str,
    growtime: float = 0.0,
    growfreq: float = 0.0,
    growaround: bool = False,
    flagneartime: bool = False,
    flagnearfreq: bool = False,
    extendpols: bool = True,
    datacolumn: str = "data",
) -> None:
    """Extend existing flags to neighboring data points.

    RFI often affects neighboring channels, times, or correlations through
    hardware responses, cross-talk, or physical proximity. This function
    grows flagged regions appropriately.

    Parameters
    ----------
    ms :
        Path to Measurement Set
    growtime :
        Fraction of time already flagged to flag entire time slot (0-1)
    growfreq :
        Fraction of frequency already flagged to flag entire channel (0-1)
    growaround :
        Flag points if most neighbors are flagged
    flagneartime :
        Flag points immediately before/after flagged regions
    flagnearfreq :
        Flag points immediately adjacent to flagged channels
    extendpols :
        Extend flags across polarization products
    datacolumn :
        Data column to use (default: 'data')
    """
    # Try using CASA flagdata first
    try:
        service = CASAService()
        with suppress_subprocess_stderr():
            service.flagdata(
                vis=ms,
                mode="extend",
                datacolumn=datacolumn,
                growtime=growtime,
                growfreq=growfreq,
                growaround=growaround,
                flagneartime=flagneartime,
                flagnearfreq=flagnearfreq,
                extendpols=extendpols,
                flagbackup=False,
            )
    except RuntimeError as e:
        # If CASA fails due to file lock, try direct casacore approach for simple extension
        if ("cannot be opened" in str(e) or "not writable" in str(e)) and (
            flagneartime or flagnearfreq
        ):
            logger = logging.getLogger(__name__)
            logger.debug("CASA flagdata failed, trying direct casacore flag extension")
            try:
                _extend_flags_direct(
                    ms,
                    flagneartime=flagneartime,
                    flagnearfreq=flagnearfreq,
                    extendpols=extendpols,
                )
            except Exception as e2:
                logger.warning(f"Direct flag extension also failed: {e2}. Flag extension skipped.")
                raise RuntimeError(f"Flag extension failed: {e}") from e
        else:
            raise


def _extend_flags_direct(
    ms: str,
    flagneartime: bool = False,
    flagnearfreq: bool = False,
    extendpols: bool = True,
) -> None:
    """Extend flags directly using casacore.tables (fallback when CASA flagdata fails).

    This is a simpler implementation that only handles adjacent channel/time extension.
    For more complex extension (growaround, growtime, etc.), use CASA flagdata.

    Parameters
    ----------
    """
    try:
        from dsa110_continuum.adapters import casa_tables as casatables
        import numpy as np

        table = casatables.table

        with table(ms, readonly=False, ack=False) as tb:
            flags = tb.getcol("FLAG")

            if flags.size == 0:
                return

            # Create extended flags
            extended_flags = flags.copy()

            # Extend in frequency direction (adjacent channels)
            if flagnearfreq:
                # Shape: (nrows, nchans, npols)
                nrows, nchans, npols = flags.shape
                for row in range(nrows):
                    for pol in range(npols):
                        row_flags = flags[row, :, pol]
                        # Flag channels adjacent to flagged channels
                        flagged_chans = np.where(row_flags)[0]
                        for chan in flagged_chans:
                            if chan > 0:
                                extended_flags[row, chan - 1, pol] = True
                            if chan < nchans - 1:
                                extended_flags[row, chan + 1, pol] = True

            # Extend in time direction (adjacent time samples)
            if flagneartime:
                # Flag time samples adjacent to flagged samples
                nrows, nchans, npols = flags.shape
                for row in range(nrows):
                    if np.any(flags[row]):
                        # Flag adjacent rows (time samples)
                        if row > 0:
                            extended_flags[row - 1] = extended_flags[row - 1] | flags[row]
                        if row < nrows - 1:
                            extended_flags[row + 1] = extended_flags[row + 1] | flags[row]

            # Extend across polarizations
            if extendpols:
                # If any pol is flagged, flag all pols
                nrows, nchans, npols = flags.shape
                for row in range(nrows):
                    for chan in range(nchans):
                        if np.any(flags[row, chan]):
                            extended_flags[row, chan, :] = True

            # Write extended flags back
            tb.putcol("FLAG", extended_flags)
    except Exception as e:
        logger = logging.getLogger(__name__)
        logger.debug(f"Direct flag extension failed: {e}")
        raise
