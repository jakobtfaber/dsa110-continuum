# Vendored from dsa110-contimg @ /data/dsa110-contimg/backend/src (H17), 2026-07-03,
# as part of the contimg-import-retirement migration (docs/rse/specs/plan-contimg-import-retirement.md).
"""Path resolution for DSA-110 continuum imaging pipeline.

This module provides the core path resolution logic including the ResolvedPaths
dataclass and functions to resolve paths from environment variables with
proper fallback handling.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


def _get_env(name: str) -> str | None:
    """Get environment variable, returning None if empty string."""
    val = os.environ.get(name)
    return val if val else None


@dataclass(frozen=True)
class ResolvedPaths:
    """Resolved paths for the DSA-110 imaging pipeline.

    All paths are resolved at construction time from environment variables
    with sensible defaults. The dataclass is frozen (immutable) to prevent
    accidental modification.
    """

    base_dir: Path
    staging_dir: Path
    tmpfs_dir: Path
    input_dir: Path
    ms_dir: Path
    stage_scratch_dir: Path
    tmp_dir: Path
    state_dir: Path
    products_dir: Path
    pid_dir: Path

    def as_dict(self) -> dict[str, Path]:
        """Return a dictionary representation of resolved paths."""
        return {
            "base_dir": self.base_dir,
            "staging_dir": self.staging_dir,
            "tmpfs_dir": self.tmpfs_dir,
            "input_dir": self.input_dir,
            "ms_dir": self.ms_dir,
            "stage_scratch_dir": self.stage_scratch_dir,
            "tmp_dir": self.tmp_dir,
            "state_dir": self.state_dir,
            "products_dir": self.products_dir,
            "pid_dir": self.pid_dir,
        }


def _resolve_base_dir_with_source() -> tuple[Path, str]:
    """Resolve base directory with source indicator."""
    env_base = _get_env("CONTIMG_BASE_DIR")
    if env_base:
        return Path(env_base).expanduser().resolve(strict=False), "CONTIMG_BASE_DIR"

    return Path("/data/dsa110-contimg"), "default"


def _resolve_staging_dir_with_source(base_dir: Path) -> tuple[Path, str]:
    """Resolve staging directory with source indicator."""
    env_staging = _get_env("CONTIMG_STAGING_DIR")
    if env_staging:
        p = Path(env_staging)
        if p.name == "ms":
            logger.warning(
                "CONTIMG_STAGING_DIR points to an ms directory; deriving staging_dir from its parent: %s",
                env_staging,
            )
            return p.parent, "CONTIMG_STAGING_DIR(parent)"
        return p, "CONTIMG_STAGING_DIR"

    # Special case for jfaber environment to avoid forbidden path
    if str(base_dir).rstrip("/") == "/data/jfaber/dsa110-contimg":
        return Path("/data/jfaber/stage"), "jfaber_special_case"

    return Path("/stage/dsa110-contimg"), "default"


def _resolve_tmp_dir_with_source(base_dir: Path) -> tuple[Path, str]:
    """Resolve temp directory with source indicator."""
    env_temp = _get_env("CONTIMG_TEMP_DIR")
    if env_temp:
        return Path(env_temp), "CONTIMG_TEMP_DIR"

    # Special case for jfaber environment to avoid forbidden path
    if "jfaber" in str(base_dir):
        return Path("/data/jfaber/tmp"), "jfaber_special_case"

    return base_dir / "tmp", "derived(base_dir/tmp)"


def _resolve_tmpfs_dir_with_source() -> tuple[Path, str]:
    """Resolve tmpfs directory with source indicator."""
    env_tmpfs = _get_env("CONTIMG_TMPFS_DIR")
    if env_tmpfs:
        return Path(env_tmpfs), "CONTIMG_TMPFS_DIR"

    return Path("/dev/shm/dsa110-contimg"), "default"


def _resolve_input_dir_with_source() -> tuple[Path, str]:
    """Resolve input directory with source indicator."""
    value = _get_env("CONTIMG_INPUT_DIR")
    if value:
        return Path(value), "CONTIMG_INPUT_DIR"
    return Path("/data/incoming"), "default"


def _resolve_state_dir_with_source(base_dir: Path) -> tuple[Path, str]:
    """Resolve state directory with source indicator."""
    env_state = _get_env("CONTIMG_STATE_DIR")
    if env_state:
        return Path(env_state), "CONTIMG_STATE_DIR"
    return base_dir / "state", "default"


def _resolve_products_dir_with_source(base_dir: Path) -> tuple[Path, str]:
    """Resolve products directory with source indicator."""
    env_products = _get_env("CONTIMG_PRODUCTS_DIR")
    if env_products:
        return Path(env_products), "CONTIMG_PRODUCTS_DIR"
    return base_dir / "products", "default"


def _resolve_pid_dir_with_source(tmp_dir: Path) -> tuple[Path, str]:
    """Resolve PID directory with source indicator."""
    env_pid = _get_env("CONTIMG_PID_DIR")
    if env_pid:
        return Path(env_pid), "CONTIMG_PID_DIR"
    return tmp_dir / "pids", "derived(tmp_dir)"


def resolve_paths_with_sources() -> tuple[ResolvedPaths, dict[str, str]]:
    """Resolve all paths with their sources.

    Returns
    -------
    tuple[ResolvedPaths, dict[str, str]]
        The resolved paths and a dictionary mapping field names to their sources.
    """
    base_dir, base_src = _resolve_base_dir_with_source()
    staging_dir, staging_src = _resolve_staging_dir_with_source(base_dir)
    tmpfs_dir, tmpfs_src = _resolve_tmpfs_dir_with_source()
    input_dir, input_src = _resolve_input_dir_with_source()
    state_dir, state_src = _resolve_state_dir_with_source(base_dir)
    products_dir, products_src = _resolve_products_dir_with_source(base_dir)

    tmp_dir, tmp_src = _resolve_tmp_dir_with_source(base_dir)
    pid_dir, pid_src = _resolve_pid_dir_with_source(tmp_dir)

    paths = ResolvedPaths(
        base_dir=base_dir,
        staging_dir=staging_dir,
        tmpfs_dir=tmpfs_dir,
        input_dir=input_dir,
        ms_dir=staging_dir / "ms",
        stage_scratch_dir=staging_dir / "scratch",
        tmp_dir=tmp_dir,
        state_dir=state_dir,
        products_dir=products_dir,
        pid_dir=pid_dir,
    )

    sources = {
        "base_dir": base_src,
        "staging_dir": staging_src,
        "tmpfs_dir": tmpfs_src,
        "input_dir": input_src,
        "state_dir": state_src,
        "products_dir": products_src,
        "pid_dir": pid_src,
        "ms_dir": "derived(staging_dir/ms)",
        "stage_scratch_dir": "derived(staging_dir/dev/shm/dsa110-contimg)",
        "tmp_dir": tmp_src,
    }

    return paths, sources


def resolve_paths() -> ResolvedPaths:
    """Resolve all paths from environment variables with sensible defaults.

    Returns
    -------
    ResolvedPaths
        The resolved paths dataclass instance.
    """
    paths, _ = resolve_paths_with_sources()
    return paths


def get_repo_root() -> Path:
    """Get the repository root path.

    Attempts to find the repo root via environment variables or by
    walking up the directory tree looking for git/pyproject.toml markers.

    Returns
    -------
    Path
        The repository root path.
    """
    env_base_dir = _get_env("CONTIMG_BASE_DIR")
    if env_base_dir:
        return Path(env_base_dir).expanduser().resolve(strict=False)

    env_repo_root = _get_env("REPO_ROOT")
    if env_repo_root:
        return Path(env_repo_root).expanduser().resolve(strict=False)

    current = Path(__file__).resolve()
    for parent in [current, *current.parents]:
        if (parent / ".git").exists() or (parent / "pyproject.toml").exists():
            return parent

    parents = current.parents
    if len(parents) >= 5:
        return parents[4]
    return current.parent


def print_inventory() -> None:
    """Print an inventory of all resolved paths to stdout."""
    paths, sources = resolve_paths_with_sources()
    payload = {"paths": {k: str(v) for k, v in paths.as_dict().items()}, "sources": sources}
    print(json.dumps(payload, indent=2, sort_keys=True))


def _main() -> None:
    """Entry point for command-line usage."""
    print_inventory()


if __name__ == "__main__":
    _main()
