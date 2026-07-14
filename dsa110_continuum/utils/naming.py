"""
Naming conventions and validation utilities for DSA-110 continuum imaging pipeline.

This module provides centralized naming validation and sanitization to ensure
consistency and prevent user errors or security issues.

Naming Conventions:
- Group IDs: YYYY-MM-DDTHH:MM:SS (ISO 8601 format, UTC)
- Calibrator Names: Alphanumeric, +, -, _ only (e.g., '0834+555')
- MS Files: <timestamp>.ms (timestamp from group_id)
- Images: <ms_stem>.img-* (derived from MS stem)
- Mosaics: mosaic_<group_id>_<timestamp>.image/.fits
- Calibration Tables: <ms_stem>_<type>cal/ (e.g., <ms_stem>_bpcal/)
"""

import re
import time
from pathlib import Path

# Regex patterns for validation
GROUP_ID_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$")
GROUP_ID_PATTERN_RELAXED = re.compile(
    r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}$"
)  # Accepts space or T
CALIBRATOR_NAME_PATTERN = re.compile(r"^[a-zA-Z0-9+\-_]+$")
DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
TIMESTAMP_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$")

# Invalid characters for file names (Windows + Unix)
INVALID_FILENAME_CHARS = set('/\\<>:"|?*\x00')
INVALID_PATH_CHARS = set('<>:"|?*\x00')  # Allow / and \ for paths


def validate_group_id(group_id: str, strict: bool = True) -> tuple[bool, str | None]:
    """Validate group ID format.

    Parameters
    ----------
    group_id : str
        Group ID to validate
    strict : bool, optional
        If True, requires exact format YYYY-MM-DDTHH:MM:SS.
        If False, accepts space or T as separator. (Default is False)

    Returns
    -------
        tuple
        Tuple of (is_valid, error_message)
    """
    if not isinstance(group_id, str):
        return False, "Group ID must be a string"

    group_id = group_id.strip()
    if not group_id:
        return False, "Group ID cannot be empty"

    pattern = GROUP_ID_PATTERN if strict else GROUP_ID_PATTERN_RELAXED
    if not pattern.match(group_id):
        return (
            False,
            f"Group ID must match format YYYY-MM-DDTHH:MM:SS (got: {group_id})",
        )

    # Validate date/time components
    try:
        from datetime import datetime

        # Normalize separator
        normalized = group_id.replace(" ", "T")
        datetime.strptime(normalized, "%Y-%m-%dT%H:%M:%S")
    except ValueError as e:
        return False, f"Invalid date/time in group ID: {e}"

    return True, None


def normalize_group_id(group_id: str) -> str:
    """Normalize group ID to standard format YYYY-MM-DDTHH:MM:SS.

    Parameters
    ----------
    group_id : str
        Group ID (may contain space or T as separator)

    Returns
    -------
        str
        Normalized group ID in format YYYY-MM-DDTHH:MM:SS

    Raises
    ------
        ValueError
        If group_id cannot be parsed
    """
    if not isinstance(group_id, str):
        raise ValueError(f"Group ID must be a string, got {type(group_id)}")

    group_id = group_id.strip()
    if not group_id:
        raise ValueError("Group ID cannot be empty")

    # Normalize separator (space -> T)
    normalized = group_id.replace(" ", "T")

    # Validate format
    is_valid, error = validate_group_id(normalized, strict=True)
    if not is_valid:
        # Try to parse and reformat
        try:
            from datetime import datetime

            dt = datetime.strptime(normalized, "%Y-%m-%dT%H:%M:%S")
            return dt.strftime("%Y-%m-%dT%H:%M:%S")
        except ValueError:
            raise ValueError(f"Cannot normalize group ID '{group_id}': {error}")

    return normalized


def validate_calibrator_name(name: str) -> tuple[bool, str | None]:
    """Validate calibrator name format.

    Parameters
    ----------
    name : str
        Calibrator name to validate

    Returns
    -------
        tuple
        Tuple of (is_valid, error_message)
    """
    if not isinstance(name, str):
        return False, "Calibrator name must be a string"

    name = name.strip()
    if not name:
        return False, "Calibrator name cannot be empty"

    if len(name) > 50:  # Reasonable limit
        return False, f"Calibrator name too long (max 50 chars, got {len(name)})"

    if not CALIBRATOR_NAME_PATTERN.match(name):
        return (
            False,
            f"Calibrator name contains invalid characters. "
            f"Allowed: alphanumeric, +, -, _ (got: {name})",
        )

    return True, None


def sanitize_calibrator_name(name: str) -> str:
    """Sanitize calibrator name for use in file paths.

    Parameters
    ----------
    name : str
        Calibrator name

    Returns
    -------
        str
        Sanitized name safe for file paths (replaces + and - with _)
    """
    if not isinstance(name, str):
        raise ValueError(f"Calibrator name must be a string, got {type(name)}")

    name = name.strip()
    if not name:
        raise ValueError("Calibrator name cannot be empty")

    # Replace + and - with _ for filesystem safety
    sanitized = name.replace("+", "_").replace("-", "_")

    # Remove any remaining invalid characters
    sanitized = "".join(c for c in sanitized if c.isalnum() or c in ("_",))

    if not sanitized:
        raise ValueError(f"Cannot sanitize calibrator name '{name}'")

    return sanitized


def validate_date_string(date_str: str) -> tuple[bool, str | None]:
    """Validate date string format YYYY-MM-DD.

    Parameters
    ----------
    date_str : str
        Date string to validate

    Returns
    -------
        tuple
        Tuple of (is_valid, error_message)
    """
    if not isinstance(date_str, str):
        return False, "Date string must be a string"

    date_str = date_str.strip()
    if not DATE_PATTERN.match(date_str):
        return False, f"Date must match format YYYY-MM-DD (got: {date_str})"

    try:
        from datetime import datetime

        datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError as e:
        return False, f"Invalid date: {e}"

    return True, None


def sanitize_filename(filename: str, max_length: int = 255) -> str:
    """Sanitize filename to remove invalid characters.

    Parameters
    ----------
    filename : str
        Filename to sanitize
    max_length : int, optional
        Maximum filename length (default is 255)

    Returns
    -------
        str
        Sanitized filename safe for filesystem
    """
    if not isinstance(filename, str):
        raise ValueError(f"Filename must be a string, got {type(filename)}")

    # Remove invalid characters
    sanitized = "".join(c for c in filename if c not in INVALID_FILENAME_CHARS)

    # Remove leading/trailing dots and spaces (Windows)
    sanitized = sanitized.strip(". ")

    # Truncate if too long
    if len(sanitized) > max_length:
        # Try to preserve extension
        if "." in sanitized:
            name, ext = sanitized.rsplit(".", 1)
            max_name_len = max_length - len(ext) - 1
            sanitized = name[:max_name_len] + "." + ext
        else:
            sanitized = sanitized[:max_length]

    if not sanitized:
        raise ValueError(f"Cannot sanitize filename '{filename}'")

    return sanitized


def construct_mosaic_id(group_id: str) -> str:
    """Construct mosaic ID from group ID.

    Parameters
    ----------
    group_id : str
        Validated group ID

    Returns
    -------
        str
        Mosaic ID in format: mosaic_<group_id>_<timestamp>
    """
    # Validate group_id first
    is_valid, error = validate_group_id(group_id, strict=False)
    if not is_valid:
        raise ValueError(f"Invalid group_id for mosaic: {error}")

    # Normalize group_id
    normalized_group = normalize_group_id(group_id)

    # Construct mosaic ID
    timestamp = int(time.time())
    mosaic_id = f"mosaic_{normalized_group}_{timestamp}"

    # Sanitize to ensure filesystem safety
    return sanitize_filename(mosaic_id)


def construct_ms_filename(group_id: str) -> str:
    """Construct MS filename from group ID.

    Parameters
    ----------
    group_id : str
        Validated group ID

    Returns
    -------
        str
        MS filename: <group_id>.ms
    """
    # Validate and normalize group_id
    is_valid, error = validate_group_id(group_id, strict=False)
    if not is_valid:
        raise ValueError(f"Invalid group_id for MS filename: {error}")

    normalized_group = normalize_group_id(group_id)
    return f"{normalized_group}.ms"


def construct_image_basename(ms_path: Path) -> str:
    """Construct image basename from MS path.

    Parameters
    ----------
    ms_path : str
        Path to MS file

    Returns
    -------
        str
        Image basename: <ms_stem>.img
    """
    ms_stem = ms_path.stem
    # Sanitize MS stem to ensure filesystem safety
    sanitized_stem = sanitize_filename(ms_stem)
    return f"{sanitized_stem}.img"


def construct_caltable_prefix(ms_path: Path, cal_type: str) -> str:
    """Construct calibration table prefix from MS path and type.

    Parameters
    ----------
    ms_path : str
        Path to MS file
    cal_type : str
        Calibration type ('bpcal', 'gpcal', '2gcal')

    Returns
    -------
        str
        Calibration table prefix: <ms_stem>_<cal_type>
    """
    if cal_type not in ("bpcal", "gpcal", "2gcal"):
        raise ValueError(f"Invalid calibration type: {cal_type}")

    ms_stem = ms_path.stem
    sanitized_stem = sanitize_filename(ms_stem)
    return f"{sanitized_stem}_{cal_type}"


def validate_path_safe(path: Path, base_dir: Path | None = None) -> tuple[bool, str | None]:
    """Validate that a path is safe and within base directory if provided.

    Parameters
    ----------
    path : str
        Path to validate
    base_dir : str, optional
        Optional base directory to ensure path is within

    Returns
    -------
        tuple of (bool, str)
        Tuple of (is_safe, error_message)
    """
    try:
        resolved = path.resolve()
    except Exception as e:
        return False, f"Cannot resolve path: {e}"

    if base_dir is not None:
        try:
            base_resolved = base_dir.resolve()
            resolved.relative_to(base_resolved)
        except (ValueError, AttributeError):
            return (
                False,
                f"Path {resolved} is outside base directory {base_resolved}",
            )

    # Check for path traversal attempts in string representation
    path_str = str(resolved)
    if ".." in path_str or path_str.startswith("/proc") or path_str.startswith("/sys"):
        return False, f"Path contains suspicious components: {path_str}"

    return True, None
