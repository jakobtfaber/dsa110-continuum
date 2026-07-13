"""
Centralized validation functions for CLI and pipeline operations.

This module provides validation utilities using exception-based design,
following Python best practices (aligns with Pydantic, argparse patterns).

Validation functions raise ValidationError when validation fails,
ensuring type safety and enforcing the "parse, don't validate" principle.
"""

import os
import shutil
from pathlib import Path

# IMPORTANT: Import casa_init BEFORE the table adapter to ensure CWD is set to
# the logs directory
from dsa110_continuum.utils.casa_init import ensure_casa_path  # noqa: F401  (call-time CASA setup)

import numpy as np  # noqa: E402

# Repo rule: table access goes through the casatools-backed adapter, never a
# direct python-casacore import (shared-library conflict, see CLAUDE.md)
from dsa110_continuum.adapters import casa_tables as casatables  # noqa: E402
from dsa110_continuum.utils.exceptions import ValidationError  # noqa: E402

# Provide a patchable table symbol for tests
table = casatables.table  # noqa: N816

# Import ValidationError from unified exception hierarchy

# Re-export for backward compatibility
__all__ = [
    "ValidationError",
    "validate_file_path",
    "validate_directory",
    "validate_ms",
    "validate_ms_for_calibration",
    "validate_corrected_data_quality",
    "check_disk_space",
]


def validate_file_path(path: str, must_exist: bool = True, must_readable: bool = True) -> Path:
    """Validate a file path with clear error messages.

    Parameters
    ----------
    path : str
        File path to validate
    must_exist : bool
        Whether file must exist
        (Default value = True)
    must_readable : bool
        Whether file must be readable
        (Default value = True)

    """
    p = Path(path)

    if must_exist and not p.exists():
        raise ValidationError(
            [f"File does not exist: {path}"],
            error_types=(["ms_not_found"] if path.endswith(".ms") else ["file_not_found"]),
            error_details=[{"path": path}],
        )

    if must_exist and not p.is_file():
        raise ValidationError(
            [f"Path is not a file: {path}"],
            error_types=["file_not_found"],
            error_details=[{"path": path}],
        )

    if must_readable and not os.access(path, os.R_OK):
        raise ValidationError(
            [f"File is not readable: {path}"],
            error_types=["permission_denied"],
            error_details=[{"path": path}],
        )

    return p


def validate_directory(
    path: str,
    must_exist: bool = True,
    must_readable: bool = False,
    must_writable: bool = False,
) -> Path:
    """Validate a directory path with clear error messages.

    Parameters
    ----------
    path : str
        Directory path to validate
    must_exist : bool
        Whether directory must exist (if False, creates it)
        (Default value = True)
    must_readable : bool
        Whether directory must be readable
        (Default value = False)
    must_writable : bool
        Whether directory must be writable
        (Default value = False)

    """
    p = Path(path)

    if must_exist:
        if not p.exists():
            raise ValidationError([f"Path does not exist: {path}"])

        if not p.is_dir():
            raise ValidationError([f"Path is not a directory: {path}"])
    else:
        if not p.exists():
            # Try to create it when allowed
            try:
                p.mkdir(parents=True, exist_ok=True)
            except Exception as exc:
                raise ValidationError([f"Cannot create directory {path}: {exc}"])

    if must_readable and not os.access(path, os.R_OK):
        raise ValidationError([f"Directory is not readable: {path}"])

    if must_writable and not os.access(path, os.W_OK):
        raise ValidationError([f"Directory is not writable: {path}"])

    return p


def validate_ms(
    ms_path: str, check_empty: bool = True, check_columns: list[str] | None = None
) -> None:
    """Validate a Measurement Set with clear error messages.

    Parameters
    ----------
    ms_path : str
        Path to Measurement Set
    check_empty : bool
        Whether to check if MS is empty
        (Default value = True)
    check_columns : Optional[List[str]]
        Optional list of required column names
        (Default value = None)

    """
    # MS files are directories, not files - validate as directory
    validate_directory(ms_path, must_exist=True, must_readable=True)

    # Check for missing MS table files (indicates data corruption)
    ms_path_obj = Path(ms_path)
    required_table_files = [
        "table.dat",
    ]
    missing_table_files = []
    for table_file in required_table_files:
        table_path = ms_path_obj / table_file
        if not table_path.exists():
            missing_table_files.append(table_file)

    if missing_table_files:
        suggestion = (
            "MS appears corrupted - missing required table files. "
            "Check if MS conversion completed successfully, verify disk space "
            "and permissions, check for interrupted processes, or consider "
            "re-running conversion from original data."
        )
        raise ValidationError(
            [
                f"MS missing required table files: {missing_table_files}. "
                f"Path: {ms_path}. "
                "This indicates data corruption or incomplete conversion."
            ],
            error_types=["ms_missing_table_files"],
            error_details=[{"path": ms_path, "missing": missing_table_files}],
            suggestion=suggestion,
        )

    # Validate MS structure (lazy import CASA dependency)
    # Ensure CASAPATH is set before importing CASA modules
    from dsa110_continuum.utils.casa_init import ensure_casa_path

    ensure_casa_path()

    try:
        table  # ensure symbol exists
    except NameError:
        raise ValidationError(["Cannot import casacore.tables. Is CASA installed?"])

    try:
        with table(ms_path, readonly=True) as tb:
            if check_empty and tb.nrows() == 0:
                raise ValidationError(
                    [f"MS is empty: {ms_path}"],
                    error_types=["ms_empty"],
                    error_details=[{"path": ms_path}],
                )

            if check_columns:
                missing = [c for c in check_columns if c not in tb.colnames()]
                if missing:
                    raise ValidationError(
                        [f"MS missing required columns: {missing}. Path: {ms_path}"],
                        error_types=["ms_missing_columns"],
                        error_details=[{"path": ms_path, "missing": missing}],
                    )
    except ValidationError:
        raise
    except Exception as e:
        # Check if error is related to missing table files
        error_str = str(e).lower()
        if "table.f" in error_str or "cannot open" in error_str or "corrupted" in error_str:
            suggestion = (
                "MS table files may be missing or corrupted. "
                "Check if MS conversion completed successfully, verify disk space "
                "and permissions, check for interrupted processes, or consider "
                "re-running conversion from original data."
            )
            raise ValidationError(
                [
                    f"MS appears corrupted or incomplete: {ms_path}. "
                    f"Error: {e}. "
                    "This may indicate missing table files or data corruption."
                ],
                error_types=["ms_corrupted"],
                error_details=[{"path": ms_path, "error": str(e)}],
                suggestion=suggestion,
            ) from e
        raise ValidationError([f"MS is not readable: {ms_path}. Error: {e}"]) from e


def validate_ms_for_calibration(
    ms_path: str, field: str | None = None, refant: str | None = None
) -> list[str]:
    """Comprehensive MS validation for calibration operations.

        Validates:
        - MS exists and is readable
        - MS is not empty
        - Field exists (if provided)
        - Reference antenna exists (if provided)

    Parameters
    ----------
    ms_path : str
        Path to Measurement Set
    field : str or None, optional
        Optional field selection (for validation)
    refant : str or None, optional
        Optional reference antenna ID (for validation)

    Returns
    -------
        None
    """
    warnings = []

    # Basic MS validation
    validate_ms(
        ms_path,
        check_empty=True,
        check_columns=["DATA", "ANTENNA1", "ANTENNA2", "TIME", "UVW"],
    )

    # Field validation if provided
    if field:
        try:
            from dsa110_continuum.calibration.solve_delay import _resolve_field_ids

            with table(ms_path, readonly=True) as tb:
                field_ids = tb.getcol("FIELD_ID")
                available_fields = sorted(set(field_ids))

            target_ids = _resolve_field_ids(ms_path, field)
            if not target_ids:
                raise ValidationError([f"Cannot resolve field: {field}"])

            missing = set(target_ids) - set(available_fields)
            if missing:
                raise ValidationError(
                    [
                        f"Field(s) not found: {sorted(missing)}. Available fields: {available_fields}"
                    ],
                    error_types=["field_not_found"],
                    error_details=[
                        {
                            "field": field,
                            "missing": sorted(missing),
                            "available": available_fields,
                        }
                    ],
                )
        except ValidationError:
            raise
        except Exception as e:
            raise ValidationError([f"Failed to validate field: {e}"]) from e

    # Reference antenna validation if provided
    if refant:
        try:
            with table(ms_path, readonly=True) as tb:
                ant1 = tb.getcol("ANTENNA1")
                ant2 = tb.getcol("ANTENNA2")
                all_antennas = set(ant1) | set(ant2)

            refant_int = int(refant) if isinstance(refant, str) else refant
            if refant_int not in all_antennas:
                # Try to suggest alternatives
                suggestions = []
                try:
                    from dsa110_continuum.utils.antenna_classification import (
                        get_outrigger_antennas,
                        select_outrigger_refant,
                    )

                    outrigger_refant = select_outrigger_refant(
                        list(all_antennas), preferred_refant=refant_int
                    )
                    if outrigger_refant:
                        suggestions.append(f"Suggested outrigger: {outrigger_refant}")
                    outriggers = get_outrigger_antennas(list(all_antennas))
                    if outriggers:
                        suggestions.append(f"Available outriggers: {outriggers}")
                except (ValueError, KeyError, IndexError):
                    pass

                error_msg = (
                    f"Reference antenna {refant} not found. "
                    f"Available antennas: {sorted(all_antennas)}"
                )
                if suggestions:
                    error_msg += f". {'; '.join(suggestions)}"

                suggested_refant = None
                if outrigger_refant:
                    suggested_refant = outrigger_refant
                else:
                    suggested_refant = sorted(all_antennas)[0] if all_antennas else None

                raise ValidationError(
                    [error_msg],
                    error_types=["refant_not_found"],
                    error_details=[
                        {
                            "refant": refant,
                            "available": sorted(all_antennas),
                            "suggested": suggested_refant,
                        }
                    ],
                )
        except ValidationError:
            raise
        except Exception as e:
            raise ValidationError([f"Failed to validate reference antenna: {e}"]) from e

    # Check flagged data fraction (warning only)
    try:
        with table(ms_path, readonly=True) as tb:
            flags = tb.getcol("FLAG")
            unflagged_fraction = np.sum(~flags) / flags.size if flags.size > 0 else 0
            if unflagged_fraction < 0.1:
                warnings.append(f"Very little unflagged data: {unflagged_fraction * 100:.1f}%")
    except (OSError, RuntimeError, KeyError):
        pass  # Non-fatal check

    return warnings


def validate_corrected_data_quality(ms_path: str, sample_size: int = 10000) -> list[str]:
    """Validate CORRECTED_DATA column quality.

        **CRITICAL**: If CORRECTED_DATA exists but is unpopulated (all zeros), this indicates
        calibration was attempted but failed. Returns warnings that should cause the caller
        to FAIL rather than proceed with uncalibrated data.

    Parameters
    ----------
    ms_path : str
        Path to Measurement Set
    sample_size : int
        Number of rows to sample for validation
        (Default value = 10000)

    """
    warnings = []

    try:
        with table(ms_path, readonly=True) as tb:
            if "CORRECTED_DATA" not in tb.colnames():
                # No corrected data column - calibration never attempted, this is fine
                return warnings  # Return empty warnings

            # CORRECTED_DATA exists - calibration was attempted, must verify it worked
            n_rows = tb.nrows()
            if n_rows == 0:
                warnings.append(
                    "CORRECTED_DATA column exists but MS has zero rows - calibration may have failed"
                )
                return warnings

            sample_size = min(sample_size, n_rows)

            if sample_size > 0:
                corrected_data = tb.getcol("CORRECTED_DATA", startrow=0, nrow=sample_size)
                flags = tb.getcol("FLAG", startrow=0, nrow=sample_size)

                unflagged = corrected_data[~flags]
                if len(unflagged) == 0:
                    warnings.append(
                        "CORRECTED_DATA column exists but all sampled data is flagged - "
                        "calibration may have failed"
                    )
                else:
                    nonzero_count = np.count_nonzero(np.abs(unflagged) > 1e-10)
                    nonzero_fraction = nonzero_count / len(unflagged)

                    if nonzero_fraction < 0.01:
                        warnings.append(
                            f"CORRECTED_DATA column exists but appears unpopulated "
                            f"({nonzero_fraction * 100:.1f}% non-zero in sampled data) - "
                            f"calibration appears to have failed"
                        )
    except Exception as e:
        # If we can't check, that's a problem - return a warning
        warnings.append(f"Error validating CORRECTED_DATA: {e}")

    return warnings


def check_disk_space(path: str, min_bytes: int | None = None) -> list[str]:
    """Check available disk space for a path.

    Parameters
    ----------
    path : str
        Path to check disk space for
    min_bytes : Optional[int]
        Minimum required bytes (None to skip check)
        (Default value = None)

    """
    warnings = []

    try:
        output_dir = os.path.dirname(os.path.abspath(path))
        os.makedirs(output_dir, exist_ok=True)
        available = shutil.disk_usage(output_dir).free

        if min_bytes and available < min_bytes:
            warnings.append(
                f"Insufficient disk space: need {min_bytes / 1e9:.1f} GB, "
                f"available {available / 1e9:.1f} GB"
            )
    except Exception as e:
        warnings.append(f"Failed to check disk space: {e}")

    return warnings
