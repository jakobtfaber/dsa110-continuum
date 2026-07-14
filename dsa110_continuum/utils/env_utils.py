"""
Canonical environment variable utilities for DSA-110 continuum imaging.

Provides type-safe environment variable parsing with consistent behavior:

- Boolean parsing: "1", "true", "yes", "y", "on" → True
- Path resolution: Automatic Path() conversion with validation
- Required vs optional: Clear error messages
- Type coercion: int, float, list, etc.

Usage Guidelines
----------------
**Prefer Pydantic BaseSettings for complex configuration** (e.g., UnifiedPipelineConfig).
Use these helpers for simple, one-off environment variable reads.

Examples
--------
>>> from dsa110_continuum.utils.env_utils import get_env_bool, get_env_path
>>> 
>>> # Boolean parsing
>>> debug = get_env_bool("DSA110_DEBUG", default=False)
>>> 
>>> # Required path
>>> base_dir = get_env_path("CONTIMG_BASE_DIR", required=True)
>>> 
>>> # Optional integer with default
>>> workers = get_env_int("MAX_WORKERS", default=4)

See Also
--------
- dsa110_continuum.unified_config.UnifiedPipelineConfig : For complex configuration
- docs/how-to/environment-variables.md : Migration guide
"""

import os
from pathlib import Path
from typing import TypeVar, Callable, Any

__all__ = [
    "get_env_bool",
    "get_env_int",
    "get_env_float",
    "get_env_path",
    "get_env_list",
    "get_required_env",
    "EnvVarError",
]

T = TypeVar("T")


class EnvVarError(Exception):
    """Raised when environment variable parsing or validation fails."""

    pass


def get_env_bool(name: str, default: bool = False) -> bool:
    """
    Parse boolean environment variable.

    Recognizes common boolean representations:
    - True: "1", "true", "yes", "y", "on" (case-insensitive)
    - False: "0", "false", "no", "n", "off", "" (case-insensitive)

    Parameters
    ----------
    name : str
        Environment variable name
    default : bool, optional
        Default value if variable not set (default: False)

    Returns
    -------
    bool
        Parsed boolean value

    Examples
    --------
    >>> os.environ["DSA110_DEBUG"] = "true"
    >>> get_env_bool("DSA110_DEBUG")
    True
    >>> get_env_bool("NONEXISTENT", default=False)
    False
    """
    value = os.environ.get(name)
    if value is None:
        return default

    value_lower = value.lower().strip()
    if value_lower in {"1", "true", "yes", "y", "on"}:
        return True
    elif value_lower in {"0", "false", "no", "n", "off", ""}:
        return False
    else:
        raise EnvVarError(
            f"Invalid boolean value for {name}={value!r}. "
            f"Expected: 1/true/yes/y/on or 0/false/no/n/off"
        )


def get_env_int(name: str, default: int | None = None, required: bool = False) -> int | None:
    """
    Parse integer environment variable.

    Parameters
    ----------
    name : str
        Environment variable name
    default : int, optional
        Default value if variable not set
    required : bool, optional
        If True, raise EnvVarError if variable not set (default: False)

    Returns
    -------
    int or None
        Parsed integer value, or None if not set and not required

    Raises
    ------
    EnvVarError
        If required=True and variable not set, or if value cannot be parsed as int

    Examples
    --------
    >>> os.environ["MAX_WORKERS"] = "8"
    >>> get_env_int("MAX_WORKERS")
    8
    >>> get_env_int("NONEXISTENT", default=4)
    4
    """
    value = os.environ.get(name)
    if value is None:
        if required:
            raise EnvVarError(f"Required environment variable not set: {name}")
        return default

    try:
        return int(value)
    except ValueError as e:
        raise EnvVarError(f"Invalid integer value for {name}={value!r}: {e}") from e


def get_env_float(name: str, default: float | None = None, required: bool = False) -> float | None:
    """
    Parse float environment variable.

    Parameters
    ----------
    name : str
        Environment variable name
    default : float, optional
        Default value if variable not set
    required : bool, optional
        If True, raise EnvVarError if variable not set (default: False)

    Returns
    -------
    float or None
        Parsed float value, or None if not set and not required

    Raises
    ------
    EnvVarError
        If required=True and variable not set, or if value cannot be parsed
    """
    value = os.environ.get(name)
    if value is None:
        if required:
            raise EnvVarError(f"Required environment variable not set: {name}")
        return default

    try:
        return float(value)
    except ValueError as e:
        raise EnvVarError(f"Invalid float value for {name}={value!r}: {e}") from e


def get_env_path(
    name: str,
    default: Path | str | None = None,
    required: bool = False,
    must_exist: bool = False,
) -> Path | None:
    """
    Parse path environment variable.

    Parameters
    ----------
    name : str
        Environment variable name
    default : Path, str, or None, optional
        Default value if variable not set
    required : bool, optional
        If True, raise EnvVarError if variable not set (default: False)
    must_exist : bool, optional
        If True, raise EnvVarError if path does not exist (default: False)

    Returns
    -------
    Path or None
        Parsed Path object, or None if not set and not required

    Raises
    ------
    EnvVarError
        If required=True and variable not set, or if must_exist=True and path missing

    Examples
    --------
    >>> os.environ["CONTIMG_BASE_DIR"] = "/data/dsa110-contimg"
    >>> get_env_path("CONTIMG_BASE_DIR")
    PosixPath('/data/dsa110-contimg')
    >>> get_env_path("NONEXISTENT", default="/tmp")
    PosixPath('/tmp')
    """
    value = os.environ.get(name)
    if value is None:
        if required:
            raise EnvVarError(f"Required environment variable not set: {name}")
        if default is None:
            return None
        path = Path(default)
    else:
        path = Path(value)

    if must_exist and not path.exists():
        raise EnvVarError(f"Path does not exist for {name}={path}")

    return path


def get_env_list(
    name: str,
    default: list[str] | None = None,
    separator: str = ",",
    strip: bool = True,
) -> list[str]:
    """
    Parse comma-separated list environment variable.

    Parameters
    ----------
    name : str
        Environment variable name
    default : list[str], optional
        Default value if variable not set (default: empty list)
    separator : str, optional
        List separator (default: ",")
    strip : bool, optional
        If True, strip whitespace from each item (default: True)

    Returns
    -------
    list[str]
        Parsed list of strings

    Examples
    --------
    >>> os.environ["DSA110_API_KEYS"] = "key1, key2, key3"
    >>> get_env_list("DSA110_API_KEYS")
    ['key1', 'key2', 'key3']
    >>> get_env_list("NONEXISTENT", default=["default"])
    ['default']
    """
    value = os.environ.get(name)
    if value is None:
        return default if default is not None else []

    items = value.split(separator)
    if strip:
        items = [item.strip() for item in items]

    # Filter out empty strings
    return [item for item in items if item]


def get_required_env(name: str, description: str = "") -> str:
    """
    Get required environment variable or raise clear error.

    This is a convenience wrapper for cases where you need a string value
    and want a clear error message if it's missing.

    Parameters
    ----------
    name : str
        Environment variable name
    description : str, optional
        Human-readable description for error message

    Returns
    -------
    str
        Environment variable value

    Raises
    ------
    EnvVarError
        If environment variable is not set

    Examples
    --------
    >>> os.environ["DSA110_JWT_SECRET"] = "secret123"
    >>> get_required_env("DSA110_JWT_SECRET", "JWT signing secret")
    'secret123'
    >>> get_required_env("MISSING_VAR", "Important setting")
    Traceback (most recent call last):
        ...
    EnvVarError: Required environment variable not set: MISSING_VAR (Important setting)
    """
    value = os.environ.get(name)
    if value is None:
        desc = f" ({description})" if description else ""
        raise EnvVarError(f"Required environment variable not set: {name}{desc}")
    return value

