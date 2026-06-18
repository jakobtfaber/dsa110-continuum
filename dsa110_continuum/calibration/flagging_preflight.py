# ruff: noqa: D205
"""Preflight checks for calibration flagging dependencies."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any


class PreflightError(Exception):
    """

    Raises
    ------
    FAIL
        FAST
    tool
        is missing or misconfigured
    to
        proceed and fail cryptically later

    """

    def __init__(self, tool: str, reason: str, suggestions: list[str]):
        self.tool = tool
        self.reason = reason
        self.suggestions = suggestions
        super().__init__(self._format_message())

    def _format_message(self) -> str:
        msg = f"Preflight check failed for {self.tool}: {self.reason}"
        msg += "\n\nRequired action:\n"
        msg += "\n".join(f"  - {s}" for s in self.suggestions)
        return msg


def preflight_check_aoflagger(prefer_docker: bool = False) -> dict[str, str]:
    """Verify AOFlagger is available and working BEFORE starting calibration.

    FAIL-FAST: Raises PreflightError with actionable diagnostics if AOFlagger
    is not available, rather than failing cryptically during flagging.

    Parameters
    ----------
    prefer_docker :
        If True, check Docker AOFlagger first (useful for Ubuntu 18.x)

    Returns
    -------
        Dict with 'method' ('docker' or 'native'), 'version', and 'command' info

    Raises
    ------
    PreflightError
        If AOFlagger is not available with actionable suggestions

    """
    logger = logging.getLogger(__name__)

    docker_cmd = shutil.which("docker")
    native_aoflagger = shutil.which("aoflagger")

    # Try Docker first if preferred and available
    if prefer_docker and docker_cmd:
        try:
            result = subprocess.run(
                [docker_cmd, "run", "--rm", "aoflagger:latest", "aoflagger", "--version"],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode == 0:
                version = result.stdout.strip() or result.stderr.strip()
                logger.info(f"AOFlagger preflight OK: Docker ({version})")
                return {
                    "method": "docker",
                    "version": version,
                    "command": f"{docker_cmd} run --rm aoflagger:latest aoflagger",
                }
        except subprocess.TimeoutExpired:
            logger.warning("Docker AOFlagger check timed out - Docker may be slow")
        except subprocess.SubprocessError as e:
            logger.warning(f"Docker AOFlagger check failed: {e}")

    # Try native AOFlagger
    if native_aoflagger:
        try:
            result = subprocess.run(
                [native_aoflagger, "--version"], capture_output=True, text=True, timeout=10
            )
            if result.returncode == 0:
                version = result.stdout.strip() or result.stderr.strip()
                logger.info(f"AOFlagger preflight OK: native ({version})")
                return {"method": "native", "version": version, "command": native_aoflagger}
        except subprocess.SubprocessError as e:
            logger.warning(f"Native AOFlagger check failed: {e}")

    # AOFlagger not available - FAIL FAST with actionable diagnostics
    suggestions = []
    if docker_cmd:
        suggestions.extend(
            [
                "Build the AOFlagger Docker image: docker build -t aoflagger:latest .",
                "Check Docker image exists: docker images | grep aoflagger",
                "Pull from registry if available: docker pull <registry>/aoflagger:latest",
            ]
        )
    else:
        suggestions.extend(
            ["Install Docker (recommended for Ubuntu 18.x)", "Run: sudo apt-get install docker.io"]
        )

    suggestions.extend(
        [
            "Or install native AOFlagger: sudo apt-get install aoflagger",
            "Verify AOFlagger works: aoflagger --version",
        ]
    )

    raise PreflightError(
        tool="AOFlagger",
        reason="No working AOFlagger found (Docker or native)",
        suggestions=suggestions,
    )


def preflight_check_all(
    require_wsclean: bool = False, check_docker_mounts: bool = True
) -> dict[str, Any]:
    """Run all preflight checks before starting calibration pipeline.

    FAIL-FAST: Raises PreflightError on first missing *required* tool.

    Parameters
    ----------
    require_wsclean :
        If True, fail if wsclean not found.
        If False (default), just warn.
    check_docker_mounts :
        If True, also verify Docker volume mounts work

    Returns
    -------
        Dict of tool name -> preflight result info

    Raises
    ------
    PreflightError
        If any required tool is missing

    """
    logger = logging.getLogger(__name__)
    results = {}

    # Required: AOFlagger and CASA
    results["aoflagger"] = preflight_check_aoflagger()
    results["casa"] = preflight_check_casa()

    # Optional Docker mount check
    if check_docker_mounts and results["aoflagger"].get("method") == "docker":
        try:
            results["docker_mounts"] = preflight_check_aoflagger_docker_mounts()
        except PreflightError as e:
            logger.warning(f"Docker mount check failed: {e.reason}")
            results["docker_mounts"] = {"error": e.reason}

    # wsclean - required for imaging, optional for calibration-only
    try:
        results["wsclean"] = preflight_check_wsclean()
    except PreflightError as e:
        if require_wsclean:
            raise
        else:
            logger.warning(f"wsclean not found (imaging will fail): {e.reason}")
            results["wsclean"] = {"error": e.reason, "available": False}

    # Memory check (warning only, never fails)
    results["memory"] = preflight_check_memory()

    return results


def preflight_check_aoflagger_docker_mounts(
    test_paths: list[str] | None = None,
) -> dict[str, bool]:
    """Verify Docker volume mounts work for AOFlagger.

    FAIL-FAST: Verifies that the Docker container can actually see
    the required paths, not just that AOFlagger runs.

    Parameters
    ----------
    test_paths : Optional[List[str]], optional
        List of paths to verify are accessible in container.

    Returns
    -------
    dict
        Mapping of path to accessibility status (True/False)

    Raises
    ------
    PreflightError
        If critical paths are not accessible
    """
    logger = logging.getLogger(__name__)

    if test_paths is None:
        test_paths = ["/data", "/stage", "/dev/shm/dsa110-contimg"]

    docker_cmd = shutil.which("docker")
    if not docker_cmd:
        raise PreflightError(
            tool="Docker",
            reason="Docker not found",
            suggestions=["Install Docker: sudo apt-get install docker.io"],
        )

    results = {}
    failed_paths = []

    for path in test_paths:
        if not os.path.exists(path):
            results[path] = False
            logger.warning(f"Path {path} does not exist on host")
            continue

        try:
            # Test if Docker can see and list the path
            result = subprocess.run(
                [
                    docker_cmd,
                    "run",
                    "--rm",
                    "-v",
                    f"{path}:{path}:ro",
                    "aoflagger:latest",
                    "ls",
                    "-la",
                    path,
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode == 0:
                results[path] = True
                logger.debug(f"Docker mount OK: {path}")
            else:
                results[path] = False
                failed_paths.append(path)
                logger.warning(f"Docker cannot access {path}: {result.stderr}")
        except subprocess.TimeoutExpired:
            results[path] = False
            failed_paths.append(path)
            logger.warning(f"Docker mount test timed out for {path}")
        except subprocess.SubprocessError as e:
            results[path] = False
            failed_paths.append(path)
            logger.warning(f"Docker mount test failed for {path}: {e}")

    if failed_paths:
        raise PreflightError(
            tool="Docker volume mounts",
            reason=f"AOFlagger container cannot access paths: {failed_paths}",
            suggestions=[
                f"Verify paths exist: ls -la {' '.join(failed_paths)}",
                "Check Docker has permission to mount these paths",
                "Ensure no SELinux/AppArmor restrictions",
                "Try: docker run --rm -v /data:/data aoflagger:latest ls /data",
            ],
        )

    logger.info(f"Docker mount preflight OK: {list(results.keys())}")
    return results


def preflight_check_casa() -> dict[str, str]:
    """Verify CASA (casatasks/casacore) is available.

    FAIL-FAST: Raises PreflightError if CASA cannot be imported.

    Returns
    -------
        Dict with 'casatasks_version' and 'casacore_version'

    Raises
    ------
    PreflightError
        If CASA is not available

    """
    logger = logging.getLogger(__name__)
    result = {}
    errors = []

    # Check casacore
    try:
        from dsa110_continuum.adapters import casa_tables as casacore_tables

        result["casacore_version"] = getattr(casacore_tables, "__version__", "unknown")
        logger.debug(f"casacore OK: {result['casacore_version']}")
    except ImportError as e:
        errors.append(f"casacore: {e}")

    # Check CASA availability via service
    try:
        from dsa110_continuum.calibration.casa_service import CASAService

        service = CASAService()
        version = service.get_version()
        if version:
            result["casatasks_version"] = version
            logger.debug(f"CASA OK: {version}")
        else:
            errors.append("CASA version could not be determined")
    except ImportError as e:
        errors.append(f"CASA service unavailable: {e}")

    if errors:
        raise PreflightError(
            tool="CASA",
            reason=f"CASA import failed: {'; '.join(errors)}",
            suggestions=[
                "Activate CASA environment: conda activate casa6",
                "Install casatools: pip install casatools casatasks",
                "Install casacore: conda install -c conda-forge python-casacore",
                "Check Python environment has CASA packages",
            ],
        )

    logger.info(f"CASA preflight OK: casatasks={result.get('casatasks_version')}")
    return result


def preflight_check_wsclean() -> dict[str, str]:
    """Verify wsclean is available for imaging.

    FAIL-FAST: Raises PreflightError if wsclean is not found.

    Returns
    -------
        Dict with 'version' and 'path'

    Raises
    ------
    PreflightError
        If wsclean is not available

    """
    logger = logging.getLogger(__name__)

    wsclean_path = shutil.which("wsclean")

    if wsclean_path:
        try:
            result = subprocess.run(
                [wsclean_path, "--version"], capture_output=True, text=True, timeout=10
            )
            # wsclean outputs version to stderr
            version = result.stderr.strip() or result.stdout.strip()
            # Extract version number (first line usually contains it)
            version_line = version.split("\n")[0] if version else "unknown"
            logger.info(f"wsclean preflight OK: {version_line}")
            return {"version": version_line, "path": wsclean_path}
        except subprocess.SubprocessError as e:
            logger.warning(f"wsclean version check failed: {e}")

    # wsclean not found - check for Docker alternative
    docker_cmd = shutil.which("docker")
    if docker_cmd:
        try:
            result = subprocess.run(
                [docker_cmd, "run", "--rm", "wsclean:latest", "wsclean", "--version"],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode == 0:
                version = result.stderr.strip() or result.stdout.strip()
                version_line = version.split("\n")[0] if version else "unknown"
                logger.info(f"wsclean preflight OK (Docker): {version_line}")
                return {
                    "version": version_line,
                    "path": "docker:wsclean:latest",
                    "method": "docker",
                }
        except subprocess.SubprocessError:
            pass

    raise PreflightError(
        tool="wsclean",
        reason="wsclean not found (native or Docker)",
        suggestions=[
            "Install wsclean: sudo apt-get install wsclean",
            "Or use Docker: docker pull wsclean:latest",
            "Build from source: https://gitlab.com/aroffringa/wsclean",
            "Verify installation: wsclean --version",
        ],
    )


def preflight_check_disk_space(
    path: str, required_gb: float = 50.0, warn_gb: float = 100.0
) -> dict[str, float]:
    """Check available disk space before expensive operations.

    FAIL-FAST: Raises PreflightError if disk space is critically low.

    Parameters
    ----------
    path :
        Path to check (will check the filesystem containing this path)
    required_gb :
        Minimum required space in GB (fails if below)
    warn_gb :
        Warning threshold in GB (logs warning if below)

    Returns
    -------
        Dict with 'available_gb', 'total_gb', 'used_percent'

    Raises
    ------
    PreflightError
        If available space < required_gb

    """
    logger = logging.getLogger(__name__)

    try:
        stat = os.statvfs(path)
        available_bytes = stat.f_bavail * stat.f_frsize
        total_bytes = stat.f_blocks * stat.f_frsize
        available_gb = available_bytes / (1024**3)
        total_gb = total_bytes / (1024**3)
        used_percent = 100 * (1 - available_bytes / total_bytes)

        result = {
            "available_gb": round(available_gb, 1),
            "total_gb": round(total_gb, 1),
            "used_percent": round(used_percent, 1),
            "path": path,
        }

        if available_gb < required_gb:
            raise PreflightError(
                tool="Disk space",
                reason=(
                    f"Only {available_gb:.1f} GB available on {path} "
                    f"(need {required_gb:.1f} GB)"
                ),
                suggestions=[
                    f"Free up space on {path}",
                    "Delete old MS files or intermediate products",
                    "Use a different output directory with more space",
                    f"Current usage: {used_percent:.1f}% of {total_gb:.1f} GB",
                ],
            )

        if available_gb < warn_gb:
            logger.warning(
                f"Low disk space: {available_gb:.1f} GB available on {path} "
                f"(warning threshold: {warn_gb:.1f} GB)"
            )
        else:
            logger.debug(f"Disk space OK: {available_gb:.1f} GB available on {path}")

        return result

    except OSError as e:
        raise PreflightError(
            tool="Disk space",
            reason=f"Cannot check disk space for {path}: {e}",
            suggestions=[
                f"Verify path exists: ls -la {path}",
                "Check filesystem is mounted",
                "Check permissions",
            ],
        )


def preflight_check_output_dir(output_dir: str) -> dict[str, Any]:
    """Verify output directory exists and is writable.

    FAIL-FAST: Raises PreflightError if output cannot be written.

    Parameters
    ----------
    output_dir :
        Path to output directory

    Returns
    -------
        Dict with 'path', 'writable', 'disk_info'

    Raises
    ------
    PreflightError
        If directory is not writable

    """
    logger = logging.getLogger(__name__)
    output_path = Path(output_dir)

    # Create if doesn't exist
    try:
        output_path.mkdir(parents=True, exist_ok=True)
    except (PermissionError, OSError) as e:
        raise PreflightError(
            tool="Output directory",
            reason=f"Cannot create output directory {output_dir}: {e}",
            suggestions=[
                f"Check parent directory permissions: ls -la {output_path.parent}",
                "Create directory manually: mkdir -p {output_dir}",
                "Use a different output directory",
            ],
        )

    # Test write permission
    test_file = output_path / ".write_test"
    try:
        test_file.touch()
        test_file.unlink()
    except (PermissionError, OSError) as e:
        raise PreflightError(
            tool="Output directory",
            reason=f"Output directory {output_dir} is not writable: {e}",
            suggestions=[
                f"Check permissions: ls -la {output_dir}",
                f"Fix permissions: chmod u+w {output_dir}",
                "Use a different output directory",
            ],
        )

    # Also check disk space on output directory
    disk_info = preflight_check_disk_space(output_dir, required_gb=20.0, warn_gb=50.0)

    logger.info(
        f"Output directory preflight OK: {output_dir} ({disk_info['available_gb']} GB available)"
    )
    return {"path": output_dir, "writable": True, "disk_info": disk_info}


def preflight_check_memory(required_gb: float = 8.0, warn_gb: float = 16.0) -> dict[str, float]:
    """Check available system memory.

    Logs warning if memory is low but doesn't fail (OOM will happen at runtime).

    Parameters
    ----------
    required_gb :
        Minimum required memory in GB (logs error if below)
    warn_gb :
        Warning threshold in GB (logs warning if below)

    Returns
    -------
        Dict with 'available_gb', 'total_gb', 'used_percent'

    """
    logger = logging.getLogger(__name__)

    try:
        with open("/proc/meminfo") as f:
            meminfo = {}
            for line in f:
                parts = line.split()
                if len(parts) >= 2:
                    key = parts[0].rstrip(":")
                    value_kb = int(parts[1])
                    meminfo[key] = value_kb

        total_gb = meminfo.get("MemTotal", 0) / (1024**2)
        available_gb = meminfo.get("MemAvailable", meminfo.get("MemFree", 0)) / (1024**2)
        used_percent = 100 * (1 - available_gb / total_gb) if total_gb > 0 else 0

        result = {
            "available_gb": round(available_gb, 1),
            "total_gb": round(total_gb, 1),
            "used_percent": round(used_percent, 1),
        }

        if available_gb < required_gb:
            logger.error(
                f"CRITICAL: Only {available_gb:.1f} GB memory available "
                f"(need {required_gb:.1f} GB). Pipeline may OOM!"
            )
        elif available_gb < warn_gb:
            logger.warning(
                f"Low memory: {available_gb:.1f} GB available (warning threshold: {warn_gb:.1f} GB)"
            )
        else:
            logger.debug(f"Memory OK: {available_gb:.1f} GB available")

        return result

    except (OSError, ValueError, KeyError) as e:
        logger.warning(f"Cannot check memory: {e}")
        return {"available_gb": -1, "total_gb": -1, "used_percent": -1, "error": str(e)}


def preflight_check_strategy_file(strategy_file: str | None) -> bool:
    """Verify AOFlagger strategy file exists if specified.

    FAIL-FAST: Raises PreflightError if strategy file doesn't exist.

    Parameters
    ----------
    strategy_file :
        Path to Lua strategy file, or None for auto-detect
    strategy_file: Optional[str] :


    Returns
    -------
        True if valid (or None for auto-detect)

    Raises
    ------
    PreflightError
        If strategy file specified but doesn't exist

    """
    if strategy_file is None:
        return True

    if not os.path.exists(strategy_file):
        raise PreflightError(
            tool="AOFlagger strategy",
            reason=f"Strategy file not found: {strategy_file}",
            suggestions=[
                f"Check file exists: ls -la {strategy_file}",
                "Use strategy_file=None for AOFlagger auto-detection",
                "Create/download the strategy file",
                "Check path is absolute and correct",
            ],
        )

    logger = logging.getLogger(__name__)
    logger.debug(f"Strategy file OK: {strategy_file}")
    return True
