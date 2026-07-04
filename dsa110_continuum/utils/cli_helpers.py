# Vendored from dsa110-contimg @ /data/dsa110-contimg/backend/src (H17), 2026-07-03,
# as part of the contimg-import-retirement migration (docs/rse/specs/plan-contimg-import-retirement.md).
"""
Shared utilities for CLI modules to reduce duplication and ensure consistency.

This module provides common CLI patterns:
- CASA environment setup
- Common argument parsers
- Logging configuration
- Context managers for operations

All CLIs should use these utilities to ensure consistent behavior.
"""

import argparse
import logging
import os
from contextlib import contextmanager
from pathlib import Path


def setup_casa_environment() -> None:
    """Configure CASA logging directory. Call at the start of CLI main() functions.

    This is a convenience function for backward compatibility.
    For new code,
    prefer using `CASAService().log_environment()` context manager.

    """
    try:
        from dsa110_continuum.calibration.casa_service import CASAService

        # Use the service's log environment logic
        CASAService()
        # We don't use the context manager here as this function is intended
        # to set up the environment for the remainder of the process.
        from dsa110_continuum.utils.casa_init import derive_casa_log_dir

        log_dir = derive_casa_log_dir()
        os.chdir(str(log_dir))
    except (OSError, RuntimeError) as e:
        # Best-effort; continue if setup fails
        logging.debug("CASA environment setup failed: %s", e)


@contextmanager
def casa_log_environment() -> Path:
    """Context manager for CASA operations that need log directory.

    This is the preferred method for CASA operations as it:
    - Properly manages CWD changes (restores after operation)
    - Doesn't pollute global state
    - Can be nested safely

    Usage:
        with casa_log_environment():
            from casatasks import tclean
            tclean(...)

    """
    from dsa110_continuum.calibration.casa_service import CASAService

    with CASAService().log_environment() as log_dir:
        yield log_dir


def add_common_ms_args(parser: argparse.ArgumentParser, ms_required: bool = True) -> None:
    """Add common MS-related arguments to a parser.

    Parameters
    ----------
    parser :
        ArgumentParser instance to add arguments to
    ms_required :
        Whether --ms argument is required
    parser: argparse.ArgumentParser :

    """
    parser.add_argument("--ms", required=ms_required, help="Path to Measurement Set")


def add_common_field_args(parser: argparse.ArgumentParser) -> None:
    """Add common field selection arguments.

    Parameters
    ----------
    parser: argparse.ArgumentParser :


    """
    parser.add_argument("--field", default="", help="Field selection (name, index, or range)")


def add_common_logging_args(parser: argparse.ArgumentParser) -> None:
    """Add common logging arguments to a parser.

    Adds:
        --verbose, -v: Enable verbose logging
        --log-level: Set logging level (DEBUG, INFO, WARNING, ERROR)

    Parameters
    ----------
    parser: argparse.ArgumentParser :


    """
    parser.add_argument("--verbose", "-v", action="store_true", help="Enable verbose logging")
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Set logging level",
    )


def configure_logging_from_args(args: argparse.Namespace) -> logging.Logger:
    """Configure logging based on CLI arguments.

    Parameters
    ----------
    args : argparse.Namespace
        Parsed arguments object (should have 'verbose' and/or 'log_level' attributes)

    Returns
    -------
        None
    """
    # Determine log level
    level = logging.INFO

    # Check verbose flag first (takes precedence)
    if getattr(args, "verbose", False):
        level = logging.DEBUG

    # Override with explicit log-level if provided
    if hasattr(args, "log_level"):
        level = getattr(logging, args.log_level.upper(), level)

    # Configure logging
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    return logging.getLogger(__name__)


def add_ms_group(parser: argparse.ArgumentParser, required: bool = True) -> argparse._ArgumentGroup:
    """Add MS-related arguments as a group for better help organization.

    Parameters
    ----------
    parser : argparse.ArgumentParser
        ArgumentParser instance
    required : bool, optional
        Whether --ms argument is required (Default value = True)

    Returns
    -------
        None
    """
    group = parser.add_argument_group("Measurement Set")
    group.add_argument("--ms", required=required, help="Path to Measurement Set")
    return group


def add_progress_flag(parser: argparse.ArgumentParser) -> None:
    """Add progress bar control flag.

    Adds:
        --disable-progress: Disable progress bars (useful for non-interactive environments)
        --quiet, -q: Alias for --disable-progress

    Parameters
    ----------
    parser: argparse.ArgumentParser :


    """
    parser.add_argument(
        "--disable-progress",
        action="store_true",
        help="Disable progress bars (useful for non-interactive environments)",
    )
    parser.add_argument("--quiet", "-q", action="store_true", help="Alias for --disable-progress")


# Note: Use should_disable_progress() from utils.progress instead
# This function was removed to consolidate progress control logic.
# Use: from dsa110_continuum.utils.progress import should_disable_progress
# Then: show_progress = not should_disable_progress(args)


def ensure_scratch_dirs() -> dict[str, Path]:
    """Ensure scratch directory structure exists and create if missing.

    Creates the following directory structure under CONTIMG_SCRATCH_DIR:
    - ms/          # Measurement Sets
    - caltables/   # Calibration tables
    - images/       # Per-group images
    - mosaics/      # Final mosaics
    - logs/         # Processing logs
    - tmp/          # Temporary staging (auto-cleaned)

    """
    scratch_base = os.getenv(
        "CONTIMG_SCRATCH_DIR",
        os.environ.get("CONTIMG_TEMP_DIR", "/tmp"),
    )
    scratch_base_path = Path(scratch_base)

    # Get subdirectory paths from env vars or default to scratch_base/{name}
    dirs = {
        "scratch": scratch_base_path,
        "ms": Path(os.getenv("CONTIMG_MS_DIR", str(scratch_base_path / "ms"))),
        "caltables": Path(os.getenv("CONTIMG_CALTABLES_DIR", str(scratch_base_path / "caltables"))),
        "images": Path(os.getenv("CONTIMG_IMAGES_DIR", str(scratch_base_path / "images"))),
        "mosaics": Path(os.getenv("CONTIMG_MOSAICS_DIR", str(scratch_base_path / "mosaics"))),
        "logs": Path(os.getenv("CONTIMG_LOGS_DIR", str(scratch_base_path / "logs"))),
        "tmp": Path(scratch_base_path / "tmp"),
    }

    # Create all directories if they don't exist
    for name, path in dirs.items():
        try:
            path.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            # Log warning but don't fail - some operations may work without all dirs
            logging.warning(f"Failed to create scratch directory {name} at {path}: {e}")

    return dirs
