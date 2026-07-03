# Vendored from dsa110-contimg @ /data/dsa110-contimg/backend/src (H17), 2026-07-03,
# as part of the contimg-import-retirement migration (docs/rse/specs/plan-contimg-import-retirement.md).
"""
CASA initialization utilities.

Sets up CASA environment variables before importing CASA modules to avoid warnings.
This should be imported before any CASA imports.

CRITICAL: This module redirects CASA log file creation. CASA writes log files
to the current working directory when any CASA module is first imported.

STRATEGY: We temporarily change CWD to the logs directory DURING the casatools
import, then restore it. This ensures the log file is created in the right place
without permanently affecting the CWD.
"""

# Standard library imports that don't trigger CASA
import os
import sys
import threading
import warnings
from contextlib import contextmanager
from pathlib import Path
from dsa110_continuum.utils import get_env_path

# =============================================================================
# CASA Log Directory Setup - MUST happen BEFORE any casatools/casatasks import
# =============================================================================


def _get_casa_log_dir() -> Path:
    """Get the CASA log directory path.

    Checks environment variables in order:
    1. CASA_LOG_DIR (for isolated Docker volumes)
    2. CONTIMG_PATHS__CASA_LOGS_DIR (unified config)
    3. CONTIMG_BASE_DIR/state/logs/casa (default)
    """
    # Check for isolated log directory first (Docker volumes)
    if casa_log_dir := os.environ.get("CASA_LOG_DIR"):
        return Path(casa_log_dir)

    # Check unified config path
    if casa_log_dir := os.environ.get("CONTIMG_PATHS__CASA_LOGS_DIR"):
        return Path(casa_log_dir)

    # Use data_config if available (preferred)
    try:
        from dsa110_continuum.database import data_config

        return data_config.get_logs_dir("casa")
    except ImportError:
        pass

    # Fallback to default
    contimg_base = str(get_env_path("CONTIMG_BASE_DIR", default="/data/dsa110-contimg"))
    return Path(contimg_base) / "state" / "logs" / "casa"


import logging
from dsa110_continuum.adapters.casa import casa_adapter

def ensure_casa_path() -> None:
    """
    Ensure proper CASA configuration using the adapter layer.
    
    This replaces the legacy hardcoded path search and manual symlinking.
    It relies on casaconfig to locate valid data tables and sets up
    environment variables (CASAPATH) accordingly.
    
    If casaconfig cannot find valid data, it raises a RuntimeError to fail fast.
    """
    # Ensure HOME is set correctly (not /root) for .casa config discovery
    if "HOME" not in os.environ or os.environ.get("HOME") == "/root":
        import pwd
        try:
            user_home = pwd.getpwuid(os.getuid()).pw_dir
            os.environ["HOME"] = user_home
        except (KeyError, ImportError):
            user_home = os.path.expanduser("~")
            if user_home and user_home != "/root":
                os.environ["HOME"] = user_home

    # Configure runtime behavior via adapter
    casa_adapter.configure_runtime(disable_auto_updates=True)

    # Verify data availability
    datapath = casa_adapter.datapath
    
    # Check if we have valid measures
    valid_path = None
    if datapath:
        for path in datapath:
            if os.path.exists(os.path.join(path, "geodetic")):
                valid_path = path
                break
    
    if valid_path:
        # Set CASAPATH for legacy tools that might still rely on it
        # CASAPATH format: "path linux host" - we just set the path part
        if "CASAPATH" not in os.environ:
            os.environ["CASAPATH"] = f"{valid_path} linux"
    else:
        # If adapter is available but no data found, log warning
        if casa_adapter.is_available:
            logging.getLogger(__name__).warning(
                "CASA data tables (geodetic) not found in configured datapath: %s", 
                datapath
            )

    # Legacy symlink logic removed - rely on casaconfig via adapter



def _setup_casa_environment() -> Path:
    """Set up CASA log environment and import casatools with log redirection.

    This function:
    1. Creates the log directory
    2. Sets environment variables
    3. Disables CASA auto-updates before any imports
    4. Temporarily changes CWD during casatools import
    5. Suppresses console output
    6. Restores original CWD

    Returns
    -------
        Path to the log directory
    """
    log_dir = _get_casa_log_dir()
    original_cwd = os.getcwd()

    try:
        log_dir.mkdir(parents=True, exist_ok=True)

        # Set environment variables (some CASA versions may respect these)
        os.environ.setdefault("CASALOGFILE", str(log_dir / "casa.log"))
        os.environ.setdefault("CASALOG_CONFIG_DIR", str(log_dir))

        # CRITICAL: Disable CASA auto-updates BEFORE any CASA imports
        # This prevents the "NoReadme" error during casatools import
        os.environ.setdefault("CASA_DATA_UPDATE", "false")
        os.environ.setdefault("CASA_AUTO_UPDATE", "false")
        os.environ.setdefault("CASA_MEASURES_AUTO_UPDATE", "false")

        # CRITICAL: Check if we are in a bootstrapping process (e.g., spawn/forkserver)
        # Importing casatools/casaconfig may try to spawn processes (e.g. for DBUS),
        # which triggers a RuntimeError if done during the bootstrap phase of a child process.
        import multiprocessing
        if getattr(multiprocessing.current_process(), "_inheriting", False):
            return log_dir

        # Try to import and configure casaconfig before casatools
        try:
            import casaconfig
            try:
                from casaconfig import config as casa_config
            except ImportError:
                casa_config = getattr(casaconfig, "config", None)

            # Disable all auto-update flags
            if casa_config:
                casa_config.auto_update_rules = False
                casa_config.measures_auto_update = False
                casa_config.data_auto_update = False
        except ImportError:
            pass

        # Temporarily change CWD for casatools import
        os.chdir(log_dir)

        # Import casalog and disable console output
        try:
            from casatools import casalog
            casalog.showconsole(False)
        except ImportError:
            pass

        return log_dir
    except (OSError, PermissionError):
        return Path.cwd()
    finally:
        # ALWAYS restore original CWD
        try:
            os.chdir(original_cwd)
        except (OSError, PermissionError):
            pass


# Note: FITS card format INFO messages from casacore C++ code cannot be suppressed
# via Python logging. These messages appear when FITS card values exceed FITS fixed
# format display precision (e.g., CDELT1 = -0.000555555555555556 exceeds 20 chars).
# The values are read correctly despite the warning. These are harmless INFO messages
# from casacore's C++ FITS reader and can be safely ignored.
#
# Note: imregrid WARN messages from CASA C++ code also cannot be suppressed:
# - "_doImagesOverlap" warning: Expected for large images (>1 deg), overlap checking skipped
# - "regrid" warning: Expected for undersampled beams, potential flux loss during regridding
# These are informational warnings about data characteristics, not code errors.


# Repo policy: no CASA initialization at import time (see TestLazyInit).
# Callers run ensure_casa_path() via dsa110_continuum._lazy_init.require_casa().
_CASA_LOG_DIR = _setup_casa_environment()
CASA_LOG_DIR = _CASA_LOG_DIR


def verify_casa_data() -> tuple[bool, str]:
    """Verify that casacore can successfully find and open geodetic tables.

    Returns
    -------
        tuple
        (success, message)
    """
    try:
        # Try to open the Observatories table via the measures system
        # This confirms that geodetic tables are discoverable
        try:
            # We use tables.table and look for the 'Observatories' table in the geodetic path
            # CASA/casacore typically looks for it in 'geodetic/Observatories'
            # We'll try to find it via the measure system implicitly by checking a known observatory
            from casacore import measures

            me = measures.measures()
            # Try to get position of a known observatory
            # OVRO_MMA is the Owens Valley site entry (plain "OVRO" doesn't exist)
            _ = me.observatory("OVRO_MMA")
            return True, "CASA geodetic tables verified (found OVRO_MMA observatory)"
        except Exception as e:
            return False, f"CASA geodetic tables not found or unreadable: {e}"

    except ImportError:
        return False, "casacore not installed"


def setup_casa_log_directory() -> Path:
    """Set up CASA log file directory (public API).

    CASA writes log files (casa-YYYYMMDD-HHMMSS.log) to the current working
    directory when any CASA module is first imported. This function:

    1. Creates the dedicated CASA logs directory if it doesn't exist
    2. Sets CASALOGFILE environment variable (some CASA versions respect this)
    3. Changes CWD to the logs directory so any log files end up there

    Returns the logs directory path. The caller is responsible for restoring
    CWD if needed (though for log redirection, we typically don't restore).

    Note: This is called automatically at module import time via
    _setup_casa_log_directory_early(). This public function is provided
    for cases where you need to re-run the setup or get the log directory path.
    """
    log_dir = _get_casa_log_dir()

    try:
        log_dir.mkdir(parents=True, exist_ok=True)

        # Set CASALOGFILE - some CASA versions may respect this
        os.environ["CASALOGFILE"] = str(log_dir / "casa.log")

        # Change CWD to logs directory so CASA writes logs there
        # This is the most reliable way to redirect CASA logs
        os.chdir(log_dir)

        return log_dir
    except (OSError, PermissionError):
        # If we can't create/access the directory, logs will go to CWD
        return Path.cwd()


def derive_casa_log_dir() -> Path:
    """Get the CASA logs directory path.

    This is a public convenience function that returns the CASA logs directory.
    Useful for CLI tools that need to know where CASA logs will be written.

    Returns
    -------
    Path
        Path to the CASA logs directory
    """
    return _get_casa_log_dir()


# Thread lock for CASA log environment to prevent race conditions
# when multiple threads change CWD simultaneously
_casa_log_lock = threading.RLock()


@contextmanager
def casa_log_environment():
    """Context manager that sets up CASA logging environment.

    Thread-safe wrapper around CWD changes for CASA log redirection.
    Uses a global lock to prevent race conditions in multi-threaded environments.

    CASA writes log files (casa-YYYYMMDD-HHMMSS.log) to the current working
    directory. This context manager temporarily changes the working directory
    to the centralized logs directory while CASA tasks execute.

    Usage:
        with casa_log_environment():
            from casatasks import tclean
            tclean(...)

    """
    with _casa_log_lock:
        log_dir = _get_casa_log_dir()
        log_dir.mkdir(parents=True, exist_ok=True)
        old_cwd = os.getcwd()
        try:
            os.chdir(log_dir)
            yield log_dir
        finally:
            try:
                os.chdir(old_cwd)
            except (OSError, PermissionError):
                # If restore fails (e.g., directory was deleted), stay in log_dir
                pass


# Cache for imported CASA tasks
_casa_task_cache: dict = {}


def get_casa_task(task_name: str):
    """Import a CASA task with log environment protection and caching.

    .. deprecated:: 1.2.0
        Use :class:`~dsa110_contimg.core.calibration.casa_service.CASAService` instead.
        This function is maintained for backward compatibility but will be
        removed in a future version.

    **Recommended Alternative**::

        from dsa110_contimg.core.calibration.casa_service import CASAService

        service = CASAService()
        service.gaincal(vis="my.ms", caltable="my.G", ...)

    This function:
    1. Changes CWD to the logs directory during import
    2. Caches the imported task for reuse
    3. Restores CWD after import

    Parameters
    ----------
    task_name : str
        Name of the CASA task (e.g., "bandpass", "tclean", "gaincal")

    """
    # Issue deprecation warning
    warnings.warn(
        f"get_casa_task('{task_name}') is deprecated. "
        "Use CASAService instead: "
        "from dsa110_contimg.core.calibration.casa_service import CASAService; "
        f"service = CASAService(); service.{task_name}(...)",
        DeprecationWarning,
        stacklevel=2
    )

    if task_name not in _casa_task_cache:
        with casa_log_environment():
            # Dynamic import from casatasks
            import importlib

            try:
                casatasks = importlib.import_module("casatasks")
                _casa_task_cache[task_name] = getattr(casatasks, task_name)
            except (ImportError, AttributeError):
                # Fallback if casatasks missing or task not found
                warnings.warn(f"Could not import CASA task '{task_name}'")
                return None
    return _casa_task_cache.get(task_name)


def cleanup_stray_casa_logs(
    search_dirs: list = None,
    target_dir: Path = None,
    delete: bool = False,
) -> list:
    """Find and optionally move/delete stray CASA log files.

    Parameters
    ----------
    search_dirs : list or None, optional
        Directories to search for stray logs (default: backend root)
    target_dir : str or None, optional
        Directory to move logs to (default: CASA logs dir)
    delete : bool, optional
        If True, delete logs instead of moving

    Returns
    -------
        list
        List of paths found/processed
    """
    import shutil

    if target_dir is None:
        try:
            from dsa110_continuum.unified_config import settings

            target_dir = settings.paths.casa_logs_dir
        except ImportError:
            contimg_base = str(get_env_path("CONTIMG_BASE_DIR", default="/data/dsa110-contimg"))
            target_dir = Path(contimg_base) / "state" / "logs" / "casa"

    if search_dirs is None:
        contimg_base = str(get_env_path("CONTIMG_BASE_DIR", default="/data/dsa110-contimg"))
        search_dirs = [
            Path(contimg_base) / "backend",
        ]

    found_logs = []
    for search_dir in search_dirs:
        search_dir = Path(search_dir)
        if not search_dir.exists():
            continue

        # Find casa-*.log files
        for log_path in search_dir.glob("casa-*.log"):
            # Skip if already in target directory
            if log_path.parent == target_dir:
                continue

            found_logs.append(log_path)

            if delete:
                try:
                    log_path.unlink()
                except (OSError, PermissionError):
                    pass
            elif target_dir:
                try:
                    target_dir.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(log_path), str(target_dir / log_path.name))
                except (OSError, PermissionError, shutil.Error):
                    pass

    return found_logs


# ============================================================================
# Layer 4: Process Exit Cleanup (Atexit Hook)
# ============================================================================
# Register cleanup function that runs when Python process exits.
# This catches CASA logs created by the current process.


def _cleanup_own_casa_logs():
    """Clean up CASA logs created by this process at exit.

    This runs automatically when the Python process exits to catch any logs
    that escaped normal consolidation. Only scans CWD for performance.

    This is a best-effort cleanup - failures are silently ignored to avoid
    breaking process exit.
    """
    try:
        from pathlib import Path
        import shutil

        cwd = Path.cwd()
        casa_log_dir = _get_casa_log_dir()

        # Quick scan of CWD only (not recursive) for performance
        for log_file in cwd.glob("casa-*.log"):
            # Skip if already in target directory
            if log_file.parent == casa_log_dir:
                continue

            try:
                # Ensure target directory exists
                casa_log_dir.mkdir(parents=True, exist_ok=True)

                # Check for filename collision
                target = casa_log_dir / log_file.name
                if target.exists():
                    # Add process PID to avoid collision
                    import os

                    stem = log_file.stem
                    suffix = log_file.suffix
                    target = casa_log_dir / f"{stem}-pid{os.getpid()}{suffix}"

                # Move log file
                shutil.move(str(log_file), str(target))
                # Debug: log_file.name moved successfully (silent in production)

            except Exception:
                # Silently ignore failures - don't break process exit
                pass

    except Exception:
        # Silently ignore all failures - this is best-effort cleanup
        pass


# Register cleanup hook at module import time
# This runs automatically when the process exits normally
import atexit
import sys

# Only register in normal mode (skip if running with -O optimization flag)
if not sys.flags.optimize:
    atexit.register(_cleanup_own_casa_logs)
