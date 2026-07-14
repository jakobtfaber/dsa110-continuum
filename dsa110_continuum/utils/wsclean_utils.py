"""
WSClean utilities for DSA-110 continuum imaging pipeline.

This module provides utilities for interacting with WSClean tools, particularly
the chgcentre utility for phase center manipulation of Measurement Sets.

Supports both native execution (chgcentre in PATH) and Docker execution
via the dsa110-contimg:gpu container.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import Optional, Tuple

import astropy.units as u
from astropy.coordinates import SkyCoord

logger = logging.getLogger(__name__)

__all__ = [
    "build_wsclean_native_env",
    "run_chgcentre",
    "check_chgcentre_available",
    "check_docker_wsclean_available",
    "WSCLEAN_DOCKER_IMAGE",
]

# Docker image containing WSClean with chgcentre
WSCLEAN_DOCKER_IMAGE = os.environ.get("WSCLEAN_DOCKER_IMAGE", "dsa110-contimg:gpu")


def _prepend_env_path(env: dict[str, str], key: str, value: str) -> None:
    if not value:
        return
    current = env.get(key, "")
    paths = [p for p in current.split(":") if p] if current else []
    if value in paths:
        return
    env[key] = f"{value}:{current}" if current else value


def build_wsclean_native_env() -> dict[str, str]:
    """Build a native WSClean environment with IDG/CUDA libs available.

    This mirrors the /usr/local/bin/wsclean wrapper so GPU IDG works even
    when calling the ELF binary directly.
    """
    env = os.environ.copy()

    # IDG library dir (required for GPU IDG kernel sources)
    idg_lib_dir = env.get("IDG_LIB_DIR", "/opt/wsclean/lib")
    if os.path.isdir(idg_lib_dir):
        env["IDG_LIB_DIR"] = idg_lib_dir
        _prepend_env_path(env, "LD_LIBRARY_PATH", idg_lib_dir)

    # CUDA + MPI/UCX libs (match wrapper)
    for lib_path in [
        "/usr/local/openmpi/lib",
        "/usr/local/ucx/lib",
        "/usr/local/cuda-11.1/lib64",
        "/opt/miniforge/envs/casa6/lib",
    ]:
        if os.path.isdir(lib_path):
            _prepend_env_path(env, "LD_LIBRARY_PATH", lib_path)

    # PATH hints (nvcc lives here in our install)
    for bin_path in [
        "/usr/local/openmpi/bin",
        "/usr/local/ucx/bin",
        "/usr/local/cuda-11.1/bin",
    ]:
        if os.path.isdir(bin_path):
            _prepend_env_path(env, "PATH", bin_path)

    # Force system UCX when available (matches wrapper)
    preload_libs = [
        "/usr/local/ucx/lib/libucp.so.0",
        "/usr/local/ucx/lib/libucs.so.0",
        "/usr/local/ucx/lib/libuct.so.0",
        "/usr/local/ucx/lib/libucm.so.0",
    ]
    if all(os.path.exists(p) for p in preload_libs):
        existing = env.get("LD_PRELOAD", "")
        preload = ":".join(preload_libs)
        if existing:
            if preload not in existing:
                env["LD_PRELOAD"] = f"{preload}:{existing}"
        else:
            env["LD_PRELOAD"] = preload

    # Threading: prevent OpenBLAS conflicts (required by WSClean)
    env.setdefault("OPENBLAS_NUM_THREADS", "1")
    env.setdefault("OMP_NUM_THREADS", "8")

    return env


def check_docker_wsclean_available() -> bool:
    """Check if WSClean is available via Docker container.

    Returns
    -------
    bool
        True if the dsa110-contimg:gpu Docker image exists and has chgcentre.
    """
    try:
        result = subprocess.run(
            ["docker", "image", "inspect", WSCLEAN_DOCKER_IMAGE],
            capture_output=True,
            timeout=10,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


def check_chgcentre_available() -> bool:
    """Check if WSClean's chgcentre tool is available.

    Checks in order:
    1. Native chgcentre in system PATH
    2. Docker container with chgcentre (dsa110-contimg:gpu)

    Returns
    -------
    bool
        True if chgcentre is available (native or Docker), False otherwise.

    Examples
    --------
    >>> if check_chgcentre_available():
    ...     print("chgcentre is available")
    ... else:
    ...     print("chgcentre not found, will use CASA phaseshift fallback")
    """
    if shutil.which("chgcentre") is not None:
        return True
    return check_docker_wsclean_available()


def run_chgcentre(
    ms_path: str,
    output_ms: str,
    ra_deg: float,
    dec_deg: float,
    datacolumn: Optional[str] = None,
    force: bool = False,
) -> Tuple[bool, str]:
    """Run WSClean's chgcentre to change the phase center of a Measurement Set.

    This function uses WSClean's chgcentre tool to recalculate UVW values and
    phase-rotate visibilities to a new phase center. It is more self-consistent
    with WSClean imaging than CASA's phaseshift task.

    Supports both native execution (chgcentre in PATH) and Docker execution
    via the dsa110-contimg:gpu container.

    Parameters
    ----------
    ms_path : str
        Path to input Measurement Set.
    output_ms : str
        Path to output Measurement Set (will be created).
    ra_deg : float
        Target right ascension in degrees.
    dec_deg : float
        Target declination in degrees.
    datacolumn : Optional[str]
        Specific data column to process (e.g., "DATA", "CORRECTED_DATA").
        If None, all columns (DATA, MODEL_DATA, CORRECTED_DATA) are processed.
    force : bool
        Force recalculation even if destination matches original phase direction.
        Default is False.

    Returns
    -------
    Tuple[bool, str]
        (success, message) - success is True if chgcentre succeeded, False otherwise.
        message contains either success confirmation or error details.

    Raises
    ------
    FileNotFoundError
        If chgcentre is not available (neither native nor Docker).

    Examples
    --------
    >>> success, msg = run_chgcentre(
    ...     "input.ms",
    ...     "output.ms",
    ...     ra_deg=128.7287,
    ...     dec_deg=55.5725
    ... )
    >>> if success:
    ...     print(f"Phase center changed successfully: {msg}")
    ... else:
    ...     print(f"chgcentre failed: {msg}")

    Notes
    -----
    - chgcentre is included with WSClean version 2.10 and later
    - It automatically updates UVW values and phase-rotates visibilities
    - Unlike CASA's phaseshift, it works correctly with all array types
    - The output MS is created by copying and modifying the input MS
    - Docker execution requires the dsa110-contimg:gpu image
    """
    # Check availability
    use_native = shutil.which("chgcentre") is not None
    use_docker = not use_native and check_docker_wsclean_available()

    if not use_native and not use_docker:
        raise FileNotFoundError(
            "chgcentre not found. Neither native installation nor Docker container "
            f"({WSCLEAN_DOCKER_IMAGE}) is available. "
            "Please install WSClean 2.10+ or ensure Docker image exists."
        )

    # Convert RA/Dec to chgcentre format (HHhMMmSS.ss +DDdMMmSS.s)
    coord = SkyCoord(ra=ra_deg * u.deg, dec=dec_deg * u.deg, frame="icrs")
    ra_hms = coord.ra.to_string(unit=u.hour, sep=("h", "m", "s"), precision=2)
    dec_dms = coord.dec.to_string(unit=u.deg, sep=("d", "m", "s"), precision=1, alwayssign=True)

    execution_mode = "native" if use_native else f"Docker ({WSCLEAN_DOCKER_IMAGE})"
    logger.info(
        "Running chgcentre [%s]: %s -> %s (RA=%s, Dec=%s)",
        execution_mode,
        ms_path,
        output_ms,
        ra_hms,
        dec_dms,
    )

    # First, copy the MS to the output location since chgcentre modifies in-place
    output_path = Path(output_ms)
    if output_path.exists():
        shutil.rmtree(output_path)

    logger.debug("Copying %s to %s", ms_path, output_ms)
    shutil.copytree(ms_path, output_ms, symlinks=True)

    # Build command arguments
    if use_native:
        cmd = ["chgcentre"]
    else:
        # Docker execution with volume mounts
        # Mount the parent directory containing the MS
        ms_parent = str(Path(output_ms).parent.resolve())
        ms_name = Path(output_ms).name

        # Resolve casacore measures data path on the host.
        # chgcentre (via casacore) looks for geodetic/ephemerides tables at
        # /usr/local/share/casacore/data/ inside the container.
        # We mount the host's measures data there.
        #
        # Resolution order:
        # 1. ~/.casarc measures.directory (CASA's canonical config)
        # 2. CASACORE_DATA env var
        # 3. Default /usr/share/casacore/data
        casacore_data_host = None
        casarc_path = Path.home() / ".casarc"
        if casarc_path.exists():
            try:
                for line in casarc_path.read_text().splitlines():
                    if line.strip().startswith("measures.directory"):
                        casacore_data_host = line.split(":", 1)[1].strip()
                        break
            except Exception:
                pass
        if not casacore_data_host or not Path(casacore_data_host).is_dir():
            casacore_data_host = os.environ.get("CASACORE_DATA", "/usr/share/casacore/data")
        casacore_data_container = "/usr/local/share/casacore/data"

        # Use --entrypoint to bypass the container's micromamba entrypoint
        # (which needs /root/) and run chgcentre directly. Combined with
        # --user, this preserves file ownership so the host process can
        # modify the MS afterward (e.g., update_phase_dir_to_target).
        host_uid = os.getuid()
        host_gid = os.getgid()

        cmd = [
            "docker", "run", "--rm",
            "--entrypoint", "chgcentre",
            "--user", f"{host_uid}:{host_gid}",
            "-v", f"{ms_parent}:{ms_parent}",
            "-v", f"{casacore_data_host}:{casacore_data_container}:ro",
            "-w", ms_parent,
            WSCLEAN_DOCKER_IMAGE,
        ]

    if force:
        cmd.append("-f")

    if datacolumn:
        cmd.extend(["-datacolumn", datacolumn])

    # Use absolute path for Docker, relative for native
    if use_docker:
        cmd.extend([str(Path(output_ms).resolve()), ra_hms, dec_dms])
    else:
        cmd.extend([output_ms, ra_hms, dec_dms])

    logger.info("Executing: %s", " ".join(cmd))

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=True,
            timeout=600,  # 10 minute timeout
        )

        logger.info("chgcentre completed successfully [%s]", execution_mode)
        logger.debug("chgcentre stdout: %s", result.stdout)

        return True, f"Phase center changed to RA={ra_deg:.4f}°, Dec={dec_deg:.4f}° [{execution_mode}]"

    except subprocess.CalledProcessError as e:
        error_msg = f"chgcentre failed with exit code {e.returncode}"
        if e.stderr:
            error_msg += f": {e.stderr}"
        logger.error(error_msg)
        logger.debug("chgcentre stdout: %s", e.stdout)

        # Clean up failed output
        if output_path.exists():
            shutil.rmtree(output_path, ignore_errors=True)

        return False, error_msg

    except subprocess.TimeoutExpired:
        error_msg = "chgcentre timed out after 10 minutes"
        logger.error(error_msg)

        # Clean up failed output
        if output_path.exists():
            shutil.rmtree(output_path, ignore_errors=True)

        return False, error_msg

    except Exception as e:
        error_msg = f"Unexpected error running chgcentre: {str(e)}"
        logger.error(error_msg, exc_info=True)

        # Clean up failed output
        if output_path.exists():
            shutil.rmtree(output_path, ignore_errors=True)

        return False, error_msg
