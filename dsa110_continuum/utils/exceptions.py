# Vendored from dsa110-contimg @ /data/dsa110-contimg/backend/src (H17), 2026-07-03,
# as part of the contimg-import-retirement migration (docs/rse/specs/plan-contimg-import-retirement.md).
"""
Custom exception classes for the DSA-110 Continuum Imaging Pipeline.

This module provides pipeline-specific exceptions with formal error codes
for clearer error semantics, structured logging, and consistent error
handling across all pipeline stages.

Error Code Taxonomy
-------------------
Error codes are permanent API contracts (see CONTRIBUTING.md). Format: ABC###

    VIS001-VIS999 : Visibility/UVH5 file operations (read, grouping, columns)
    CAL001-CAL999 : Calibration (solutions, tables, solver convergence)
    GPU001-GPU999 : Resource/hardware errors (memory, GPU, disk)
    RFI001-RFI999 : RFI flagging (preflight, strategies, thresholds)
    IMG001-IMG999 : Imaging (wsclean, tclean, beam, deconvolution)
    DB001-DB999   : Database (connection, migration, query, lock)
    QUE001-QUE999 : Queue/Dagster (state transitions, retries)
    CFG001-CFG999 : Configuration (missing params, invalid values)

Notes
-----
Exception Hierarchy::

    PipelineError (base)
    ├── SubbandGroupingError (VIS010-VIS019)
    ├── ConversionError (VIS020-VIS039)
    ├── DatabaseError (DB001-DB099)
    │   └── DatabaseMigrationError (DB050-DB059)
    ├── CalibrationError (CAL001-CAL099)
    ├── ImagingError (IMG001-IMG099)
    ├── QueueError (QUE001-QUE099)
    └── ValidationError (CFG001-CFG099)

Examples
--------
::

    from dsa110_continuum.utils.exceptions import (
        SubbandGroupingError,
        ConversionError,
        DatabaseError,
        ErrorCode,
    )

    # Raise with error code (preferred)
    raise SubbandGroupingError(
        "Incomplete subband group",
        code=ErrorCode.VIS_INCOMPLETE_GROUP,
        group_id="2025-01-15T12:30:00",
        expected_count=16,
        actual_count=14,
    )

    # Handle with structured logging
    try:
        convert_data(...)
    except ConversionError as e:
        logger.error(str(e), extra=e.context)
        # Access error_code for metrics: e.code -> "VIS020"
        raise

Logging Integration
-------------------
All pipeline exceptions include:

- ``code``: Formal error code string (e.g., "VIS010")
- ``context``: Structured dict for logger.error(..., extra=context)
- ``to_dict()``: JSON-serializable representation with troubleshooting URL

"""

from __future__ import annotations

import traceback
from datetime import datetime
from enum import Enum
from typing import Any

# =============================================================================
# Error Code Registry
# =============================================================================


class ErrorCode(str, Enum):
    """
    Formal error codes for the DSA-110 pipeline.

    These codes are **immutable API contracts**. Once assigned:
    - Never reuse a retired code
    - Never change the meaning of an existing code
    - Add new codes for new error conditions

    See CONTRIBUTING.md for the Error Code Stability Policy.
    """

    # -------------------------------------------------------------------------
    # VIS: Visibility / UVH5 Errors (VIS001-VIS999)
    # -------------------------------------------------------------------------
    VIS_GENERIC = "VIS001"
    VIS_FILE_NOT_FOUND = "VIS002"
    VIS_READ_ERROR = "VIS003"
    VIS_MISSING_COLUMN = "VIS004"
    VIS_CORRUPT_HEADER = "VIS005"
    VIS_INCOMPLETE_GROUP = "VIS010"
    VIS_DUPLICATE_SUBBAND = "VIS011"
    VIS_TIME_TOLERANCE_EXCEEDED = "VIS012"
    VIS_CONVERSION_FAILED = "VIS020"
    VIS_MS_WRITE_ERROR = "VIS021"
    VIS_ANTENNA_POSITION_ERROR = "VIS022"

    # -------------------------------------------------------------------------
    # CAL: Calibration Errors (CAL001-CAL999)
    # -------------------------------------------------------------------------
    CAL_GENERIC = "CAL001"
    CAL_TABLE_NOT_FOUND = "CAL002"
    CAL_CALIBRATOR_NOT_FOUND = "CAL003"
    CAL_SOLVER_CONVERGENCE = "CAL010"
    CAL_BAD_SOLUTION = "CAL011"
    CAL_APPLY_FAILED = "CAL020"
    CAL_FLAGGING_ERROR = "CAL030"
    CAL_PREFLIGHT_ERROR = "CAL031"

    # -------------------------------------------------------------------------
    # GPU: Resource / Hardware Errors (GPU001-GPU999)
    # -------------------------------------------------------------------------
    GPU_GENERIC = "GPU001"
    GPU_MEMORY_ERROR = "GPU002"
    GPU_DRIVER_ERROR = "GPU003"
    GPU_NOT_AVAILABLE = "GPU004"
    GPU_CUDA_ERROR = "GPU010"

    # -------------------------------------------------------------------------
    # RFI: Flagging Errors (RFI001-RFI999)
    # -------------------------------------------------------------------------
    RFI_GENERIC = "RFI001"
    RFI_STRATEGY_ERROR = "RFI002"
    RFI_THRESHOLD_ERROR = "RFI003"

    # -------------------------------------------------------------------------
    # IMG: Imaging Errors (IMG001-IMG999)
    # -------------------------------------------------------------------------
    IMG_GENERIC = "IMG001"
    IMG_WSCLEAN_FAILED = "IMG002"
    IMG_TCLEAN_FAILED = "IMG003"
    IMG_FILE_NOT_FOUND = "IMG004"
    IMG_BEAM_ERROR = "IMG010"
    IMG_DECONVOLUTION_ERROR = "IMG011"

    # -------------------------------------------------------------------------
    # DB: Database Errors (DB001-DB999)
    # -------------------------------------------------------------------------
    DB_GENERIC = "DB001"
    DB_CONNECTION_ERROR = "DB002"
    DB_QUERY_ERROR = "DB003"
    DB_LOCK_TIMEOUT = "DB004"
    DB_MIGRATION_ERROR = "DB050"
    DB_INTEGRITY_ERROR = "DB060"

    # -------------------------------------------------------------------------
    # QUE: Queue / Dagster Errors (QUE001-QUE999)
    # -------------------------------------------------------------------------
    QUE_GENERIC = "QUE001"
    QUE_STATE_TRANSITION_ERROR = "QUE002"
    QUE_RETRY_EXHAUSTED = "QUE003"

    # -------------------------------------------------------------------------
    # CFG: Configuration / Validation Errors (CFG001-CFG999)
    # -------------------------------------------------------------------------
    CFG_GENERIC = "CFG001"
    CFG_MISSING_PARAMETER = "CFG002"
    CFG_INVALID_VALUE = "CFG003"
    CFG_INVALID_PATH = "CFG004"

    # -------------------------------------------------------------------------
    # FIL: Filtering / Quality Errors (FIL001-FIL999)
    # -------------------------------------------------------------------------
    FIL_GENERIC = "FIL001"
    FIL_RFI_SATURATION = "FIL002"
    FIL_BAD_METADATA = "FIL003"


# Base URL for troubleshooting documentation
TROUBLESHOOTING_BASE_URL = (
    "https://dsa110-contimg.readthedocs.io/en/latest/operations/troubleshooting.html"
)


def get_troubleshooting_url(code: str | ErrorCode) -> str:
    """
    Get the troubleshooting documentation URL for an error code.

    Parameters
    ----------
    code : str or ErrorCode
        The error code (e.g., "VIS010" or ErrorCode.VIS_INCOMPLETE_GROUP)

    Returns
    -------
    str
        URL to the troubleshooting section for this error code.
    """
    code_str = code.value if isinstance(code, ErrorCode) else code
    return f"{TROUBLESHOOTING_BASE_URL}#{code_str.lower()}"


class PipelineError(Exception):
    """
    Base exception for all DSA-110 pipeline errors.

    Provides structured context for logging, error tracking, and JSON
    serialization with formal error codes.

    Parameters
    ----------
    message : str
        Human-readable error message.
    code : str or ErrorCode, optional
        Formal error code (e.g., "VIS010"). If not provided, uses the
        class default or "PIP001" for generic errors.
    pipeline_stage : str, default "unknown"
        Which pipeline stage raised the error.
    recoverable : bool, default False
        Whether the error allows continued processing.
    original_exception : BaseException, optional
        The underlying exception, if any.
    **context : Any
        Additional structured data for logging.

    Attributes
    ----------
    message : str
        Human-readable error message.
    code : str
        Formal error code (e.g., "VIS010").
    pipeline_stage : str
        Which pipeline stage raised the error.
    recoverable : bool
        Whether processing can continue.
    timestamp : str
        ISO-8601 timestamp when the error occurred.
    original_exception : BaseException or None
        The underlying exception.

    Examples
    --------
    >>> from dsa110_continuum.utils.exceptions import PipelineError, ErrorCode
    >>> err = PipelineError("Test error", code=ErrorCode.VIS_GENERIC)
    >>> err.code
    'VIS001'
    >>> err.to_dict()['error_code']
    'VIS001'
    """

    # Subclasses override this
    default_code: str | ErrorCode = "PIP001"

    def __init__(
        self,
        message: str,
        code: str | ErrorCode | None = None,
        pipeline_stage: str = "unknown",
        recoverable: bool = False,
        original_exception: BaseException | None = None,
        **context: Any,
    ) -> None:
        super().__init__(message)
        self.message = message

        # Resolve error code
        if code is not None:
            self.code = code.value if isinstance(code, ErrorCode) else code
        else:
            default = self.default_code
            self.code = default.value if isinstance(default, ErrorCode) else default

        self.pipeline_stage = pipeline_stage
        self.recoverable = recoverable
        self.original_exception = original_exception
        self.timestamp = datetime.utcnow().isoformat()
        self._context = context

        # Capture traceback if original exception provided
        if original_exception:
            self._traceback = traceback.format_exception(
                type(original_exception),
                original_exception,
                original_exception.__traceback__,
            )
        else:
            self._traceback = None

    @property
    def context(self) -> dict[str, Any]:
        """Get structured context for logging."""
        base_context = {
            "error_code": self.code,
            "error_type": self.__class__.__name__,
            "message": self.message,
            "pipeline_stage": self.pipeline_stage,
            "recoverable": self.recoverable,
            "timestamp": self.timestamp,
        }

        if self.original_exception:
            base_context["original_error"] = str(self.original_exception)
            base_context["original_type"] = type(self.original_exception).__name__

        if self._traceback:
            base_context["traceback"] = "".join(self._traceback)

        return {**base_context, **self._context}

    def to_dict(self) -> dict[str, Any]:
        """
        Convert exception to JSON-serializable dictionary.

        Returns
        -------
        dict
            Dictionary with error_code, message, details, troubleshooting_url.
        """
        return {
            "error_code": self.code,
            "message": self.message,
            "details": {
                "pipeline_stage": self.pipeline_stage,
                "recoverable": self.recoverable,
                "timestamp": self.timestamp,
                **self._context,
            },
            "troubleshooting_url": get_troubleshooting_url(self.code),
        }

    def __str__(self) -> str:
        """Format error message with key context."""
        parts = [self.message]

        if self.pipeline_stage != "unknown":
            parts.append(f"[stage={self.pipeline_stage}]")

        # Include key context items in message
        key_items = ["group_id", "file_path", "ms_path", "db_name"]
        for key in key_items:
            if key in self._context:
                parts.append(f"[{key}={self._context[key]}]")

        return " ".join(parts)

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}({self.message!r}, context={self._context})"


# =============================================================================
# Subband Grouping Errors
# =============================================================================


class SubbandGroupingError(PipelineError):
    """
    Error during subband file grouping.

    Raised when:
    - Expected 16 subbands but found fewer (incomplete group)
    - Duplicate subband indices in a group
    - Time tolerance exceeded for group formation
    - Missing or corrupted subband files

    Parameters
    ----------
    message : str
        Human-readable error message.
    code : str or ErrorCode, optional
        Error code (default: VIS_INCOMPLETE_GROUP).
    group_id : str, default ""
        Observation group identifier (timestamp).
    expected_count : int, default 16
        Expected number of subbands.
    actual_count : int, default 0
        Actual number found.
    missing_subbands : list of str, optional
        List of missing subband identifiers.
    file_list : list of str, optional
        List of files in the group.
    recoverable : bool, default True
        Whether processing can continue (usually can skip and continue).
    **context : Any
        Additional context for logging.
    """

    default_code = ErrorCode.VIS_INCOMPLETE_GROUP

    def __init__(
        self,
        message: str,
        code: str | ErrorCode | None = None,
        group_id: str = "",
        expected_count: int = 16,
        actual_count: int = 0,
        missing_subbands: list[str] | None = None,
        file_list: list[str] | None = None,
        recoverable: bool = True,  # Often can skip and continue
        **context: Any,
    ) -> None:
        super().__init__(
            message,
            code=code,
            pipeline_stage="subband_grouping",
            recoverable=recoverable,
            group_id=group_id,
            expected_count=expected_count,
            actual_count=actual_count,
            missing_subbands=missing_subbands or [],
            file_list=file_list or [],
            **context,
        )


class IncompleteSubbandGroupError(SubbandGroupingError):
    """Specific error for groups with missing subbands (VIS010)."""

    default_code = ErrorCode.VIS_INCOMPLETE_GROUP

    def __init__(
        self,
        group_id: str,
        expected_count: int,
        actual_count: int,
        missing_subbands: list[str] | None = None,
        **context: Any,
    ) -> None:
        message = (
            f"Incomplete subband group: expected {expected_count} subbands, found {actual_count}"
        )
        super().__init__(
            message,
            group_id=group_id,
            expected_count=expected_count,
            actual_count=actual_count,
            missing_subbands=missing_subbands,
            recoverable=True,  # Can skip incomplete groups
            **context,
        )


# =============================================================================
# Conversion Errors
# =============================================================================


class ConversionError(PipelineError):
    """
    Error during UVH5 to Measurement Set conversion (VIS020).

    Raised when UVH5 file read, subband combination, MS write,
    antenna position update, or field configuration fails.

    Parameters
    ----------
    message : str
        Human-readable error message.
    code : str or ErrorCode, optional
        Error code (default: VIS_CONVERSION_FAILED).
    input_path : str, default ""
        Path to input UVH5 file(s).
    output_path : str, default ""
        Path to output MS.
    group_id : str, default ""
        Observation group identifier.
    writer_type : str, default ""
        Type of MS writer used.
    original_exception : BaseException, optional
        The underlying exception.
    recoverable : bool, default False
        Whether processing can continue.
    **context : Any
        Additional context for logging.
    """

    default_code = ErrorCode.VIS_CONVERSION_FAILED

    def __init__(
        self,
        message: str,
        code: str | ErrorCode | None = None,
        input_path: str = "",
        output_path: str = "",
        group_id: str = "",
        writer_type: str = "",
        original_exception: BaseException | None = None,
        recoverable: bool = False,
        **context: Any,
    ) -> None:
        super().__init__(
            message,
            code=code,
            pipeline_stage="conversion",
            recoverable=recoverable,
            original_exception=original_exception,
            input_path=input_path,
            output_path=output_path,
            group_id=group_id,
            writer_type=writer_type,
            **context,
        )


class UVH5ReadError(ConversionError):
    """Error reading UVH5 file (VIS003)."""

    default_code = ErrorCode.VIS_READ_ERROR

    def __init__(
        self,
        file_path: str,
        reason: str = "",
        original_exception: BaseException | None = None,
        **context: Any,
    ) -> None:
        message = f"Failed to read UVH5 file: {file_path}"
        if reason:
            message += f" ({reason})"
        super().__init__(
            message,
            input_path=file_path,
            original_exception=original_exception,
            reason=reason,
            **context,
        )


class MSWriteError(ConversionError):
    """Error writing Measurement Set (VIS021)."""

    default_code = ErrorCode.VIS_MS_WRITE_ERROR

    def __init__(
        self,
        output_path: str,
        reason: str = "",
        original_exception: BaseException | None = None,
        **context: Any,
    ) -> None:
        message = f"Failed to write Measurement Set: {output_path}"
        if reason:
            message += f" ({reason})"
        super().__init__(
            message,
            output_path=output_path,
            original_exception=original_exception,
            reason=reason,
            recoverable=False,
            **context,
        )


# =============================================================================
# Database Errors
# =============================================================================


class DatabaseError(PipelineError):
    """
    Error during database operations (DB001).

    Raised when database connection, query, transaction, or integrity
    constraint operations fail.

    Parameters
    ----------
    message : str
        Human-readable error message.
    code : str or ErrorCode, optional
        Error code (default: DB_GENERIC).
    db_name : str, default ""
        Name of the database (products, ingest, etc.).
    db_path : str, default ""
        Path to the database file.
    operation : str, default ""
        What operation was attempted (insert, update, query).
    table_name : str, default ""
        Which table was affected.
    original_exception : BaseException, optional
        The underlying exception.
    recoverable : bool, default False
        Whether processing can continue.
    **context : Any
        Additional context for logging.
    """

    default_code = ErrorCode.DB_GENERIC

    def __init__(
        self,
        message: str,
        code: str | ErrorCode | None = None,
        db_name: str = "",
        db_path: str = "",
        operation: str = "",
        table_name: str = "",
        original_exception: BaseException | None = None,
        recoverable: bool = False,
        **context: Any,
    ) -> None:
        super().__init__(
            message,
            code=code,
            pipeline_stage="database",
            recoverable=recoverable,
            original_exception=original_exception,
            db_name=db_name,
            db_path=db_path,
            operation=operation,
            table_name=table_name,
            **context,
        )


class DatabaseMigrationError(DatabaseError):
    """Error during database schema migration (DB050)."""

    default_code = ErrorCode.DB_MIGRATION_ERROR

    def __init__(
        self,
        db_name: str,
        migration_version: str = "",
        reason: str = "",
        original_exception: BaseException | None = None,
        **context: Any,
    ) -> None:
        message = f"Database migration failed for {db_name}"
        if migration_version:
            message += f" (version: {migration_version})"
        if reason:
            message += f": {reason}"
        super().__init__(
            message,
            db_name=db_name,
            operation="migration",
            original_exception=original_exception,
            migration_version=migration_version,
            reason=reason,
            recoverable=False,
            **context,
        )


class DatabaseConnectionError(DatabaseError):
    """Error connecting to database (DB002)."""

    default_code = ErrorCode.DB_CONNECTION_ERROR

    def __init__(
        self,
        db_name: str,
        db_path: str = "",
        reason: str = "",
        original_exception: BaseException | None = None,
        **context: Any,
    ) -> None:
        message = f"Failed to connect to database: {db_name}"
        if reason:
            message += f" ({reason})"
        super().__init__(
            message,
            db_name=db_name,
            db_path=db_path,
            operation="connect",
            original_exception=original_exception,
            reason=reason,
            recoverable=False,
            **context,
        )


class DatabaseLockError(DatabaseError):
    """Database lock timeout error (DB004)."""

    default_code = ErrorCode.DB_LOCK_TIMEOUT

    def __init__(
        self,
        db_name: str,
        timeout_seconds: float = 30.0,
        original_exception: BaseException | None = None,
        **context: Any,
    ) -> None:
        message = f"Database lock timeout ({timeout_seconds}s) for {db_name}"
        super().__init__(
            message,
            db_name=db_name,
            operation="lock",
            original_exception=original_exception,
            timeout_seconds=timeout_seconds,
            recoverable=True,  # Can retry
            **context,
        )


# =============================================================================
# Queue Errors
# =============================================================================


class QueueError(PipelineError):
    """
    Error during streaming queue / Dagster operations (QUE001).

    Raised when queue state transition, record insertion, or invalid
    queue state is encountered.

    Parameters
    ----------
    message : str
        Human-readable error message.
    code : str or ErrorCode, optional
        Error code (default: QUE_GENERIC).
    group_id : str, default ""
        Observation group identifier.
    current_state : str, default ""
        Current queue state.
    target_state : str, default ""
        Intended state transition.
    queue_db : str, default ""
        Path to queue database.
    original_exception : BaseException, optional
        The underlying exception.
    recoverable : bool, default True
        Whether processing can continue.
    **context : Any
        Additional context for logging.
    """

    default_code = ErrorCode.QUE_GENERIC

    def __init__(
        self,
        message: str,
        code: str | ErrorCode | None = None,
        group_id: str = "",
        current_state: str = "",
        target_state: str = "",
        queue_db: str = "",
        original_exception: BaseException | None = None,
        recoverable: bool = True,
        **context: Any,
    ) -> None:
        super().__init__(
            message,
            code=code,
            pipeline_stage="queue",
            recoverable=recoverable,
            original_exception=original_exception,
            group_id=group_id,
            current_state=current_state,
            target_state=target_state,
            queue_db=queue_db,
            **context,
        )


class QueueStateTransitionError(QueueError):
    """Invalid queue state transition (QUE002)."""

    default_code = ErrorCode.QUE_STATE_TRANSITION_ERROR

    def __init__(
        self,
        group_id: str,
        current_state: str,
        target_state: str,
        reason: str = "",
        **context: Any,
    ) -> None:
        message = (
            f"Invalid queue state transition for {group_id}: {current_state} -> {target_state}"
        )
        if reason:
            message += f" ({reason})"
        super().__init__(
            message,
            group_id=group_id,
            current_state=current_state,
            target_state=target_state,
            reason=reason,
            recoverable=False,
            **context,
        )


# =============================================================================
# Calibration Errors
# =============================================================================


class CalibrationError(PipelineError):
    """
    Error during calibration operations (CAL001).

    Raised when calibration table not found, calibration apply fails,
    calibrator not found in catalog, or solution quality is poor.

    Parameters
    ----------
    message : str
        Human-readable error message.
    code : str or ErrorCode, optional
        Error code (default: CAL_GENERIC).
    ms_path : str, default ""
        Path to Measurement Set.
    cal_table : str, default ""
        Path to calibration table.
    calibrator : str, default ""
        Calibrator source name.
    original_exception : BaseException, optional
        The underlying exception.
    recoverable : bool, default False
        Whether processing can continue.
    **context : Any
        Additional context for logging.
    """

    default_code = ErrorCode.CAL_GENERIC

    def __init__(
        self,
        message: str,
        code: str | ErrorCode | None = None,
        ms_path: str = "",
        cal_table: str = "",
        calibrator: str = "",
        original_exception: BaseException | None = None,
        recoverable: bool = False,
        **context: Any,
    ) -> None:
        super().__init__(
            message,
            code=code,
            pipeline_stage="calibration",
            recoverable=recoverable,
            original_exception=original_exception,
            ms_path=ms_path,
            cal_table=cal_table,
            calibrator=calibrator,
            **context,
        )


class CalibrationTableNotFoundError(CalibrationError):
    """Calibration table not found (CAL002)."""

    default_code = ErrorCode.CAL_TABLE_NOT_FOUND

    def __init__(
        self,
        ms_path: str,
        cal_table: str,
        **context: Any,
    ) -> None:
        message = f"Calibration table not found: {cal_table} for MS {ms_path}"
        super().__init__(
            message,
            ms_path=ms_path,
            cal_table=cal_table,
            recoverable=False,
            **context,
        )


class CalibratorNotFoundError(CalibrationError):
    """Calibrator source not found in catalog (CAL003)."""

    default_code = ErrorCode.CAL_CALIBRATOR_NOT_FOUND

    def __init__(
        self,
        calibrator: str,
        ms_path: str = "",
        catalog: str = "",
        **context: Any,
    ) -> None:
        message = f"Calibrator {calibrator} not found in catalog"
        if catalog:
            message += f" ({catalog})"
        super().__init__(
            message,
            ms_path=ms_path,
            calibrator=calibrator,
            catalog=catalog,
            recoverable=False,
            **context,
        )


# =============================================================================
# Imaging Errors
# =============================================================================


class ImagingError(PipelineError):
    """
    Error during imaging operations (IMG001).

    Raised when WSClean or tclean fails, image file not found,
    or image quality check fails.

    Parameters
    ----------
    message : str
        Human-readable error message.
    code : str or ErrorCode, optional
        Error code (default: IMG_GENERIC).
    ms_path : str, default ""
        Path to Measurement Set.
    image_path : str, default ""
        Path to output image.
    imager : str, default ""
        Imaging tool used (wsclean, tclean).
    original_exception : BaseException, optional
        The underlying exception.
    recoverable : bool, default False
        Whether processing can continue.
    **context : Any
        Additional context for logging.
    """

    default_code = ErrorCode.IMG_GENERIC

    def __init__(
        self,
        message: str,
        code: str | ErrorCode | None = None,
        ms_path: str = "",
        image_path: str = "",
        imager: str = "",
        original_exception: BaseException | None = None,
        recoverable: bool = False,
        **context: Any,
    ) -> None:
        super().__init__(
            message,
            code=code,
            pipeline_stage="imaging",
            recoverable=recoverable,
            original_exception=original_exception,
            ms_path=ms_path,
            image_path=image_path,
            imager=imager,
            **context,
        )


class ImageNotFoundError(ImagingError):
    """Image file not found (IMG004)."""

    default_code = ErrorCode.IMG_FILE_NOT_FOUND

    def __init__(
        self,
        image_path: str,
        **context: Any,
    ) -> None:
        message = f"Image not found: {image_path}"
        super().__init__(
            message,
            image_path=image_path,
            recoverable=False,
            **context,
        )


# =============================================================================
# Validation Errors
# =============================================================================


class ValidationError(PipelineError):
    """
    Error during input validation (CFG001).

    Raised when required parameters missing, parameter values out of range,
    invalid file formats, or inconsistent input data.

    Parameters
    ----------
    message : str
        Human-readable error message.
    code : str or ErrorCode, optional
        Error code (default: CFG_GENERIC).
    field : str, default ""
        Name of the invalid field.
    value : Any, optional
        The invalid value (redacted by default for security).
    constraint : str, default ""
        The validation constraint that failed.
    recoverable : bool, default True
        Whether processing can continue (user can fix and retry).
    **context : Any
        Additional context for logging. Set ``log_value=True`` to log value.
    """

    default_code = ErrorCode.CFG_GENERIC

    def __init__(
        self,
        message: str,
        code: str | ErrorCode | None = None,
        field: str = "",
        value: Any = None,
        constraint: str = "",
        recoverable: bool = True,  # User can fix and retry
        **context: Any,
    ) -> None:
        # Don't log potentially sensitive values unless explicitly allowed
        safe_value = value if context.get("log_value", False) else "<redacted>"
        super().__init__(
            message,
            code=code,
            pipeline_stage="validation",
            recoverable=recoverable,
            field=field,
            value=safe_value,
            constraint=constraint,
            **context,
        )


class MissingParameterError(ValidationError):
    """Required parameter is missing (CFG002)."""

    default_code = ErrorCode.CFG_MISSING_PARAMETER

    def __init__(
        self,
        parameter: str,
        **context: Any,
    ) -> None:
        message = f"Missing required parameter: {parameter}"
        super().__init__(
            message,
            field=parameter,
            constraint="required",
            **context,
        )


class InvalidPathError(ValidationError):
    """File or directory path is invalid or doesn't exist (CFG004)."""

    default_code = ErrorCode.CFG_INVALID_PATH

    def __init__(
        self,
        path: str,
        path_type: str = "path",  # "file", "directory", "path"
        reason: str = "",
        **context: Any,
    ) -> None:
        message = f"Invalid {path_type}: {path}"
        if reason:
            message += f" ({reason})"
        super().__init__(
            message,
            field=path_type,
            value=path,
            log_value=True,  # Paths are safe to log
            reason=reason,
            **context,
        )


# =============================================================================
# Filtering / Quality Errors
# =============================================================================


class DataQualityFilterError(PipelineError):
    """
    Error during data quality filtering (FIL001).

    Raised when data fails quality checks (e.g. RFI saturation, bad metadata)
    and should be discarded immediately without retry.

    Parameters
    ----------
    message : str
        Human-readable error message.
    code : str or ErrorCode, optional
        Error code (default: FIL_GENERIC).
    filter_name : str, default ""
        Name of the filter that failed.
    metric : str, default ""
        Metric being checked.
    value : Any, optional
        The failing value.
    threshold : Any, optional
        The threshold that was violated.
    **context : Any
        Additional context for logging.
    """

    default_code = ErrorCode.FIL_GENERIC

    def __init__(
        self,
        message: str,
        code: str | ErrorCode | None = None,
        filter_name: str = "",
        metric: str = "",
        value: Any = None,
        threshold: Any = None,
        **context: Any,
    ) -> None:
        super().__init__(
            message,
            code=code,
            pipeline_stage="filtering",
            recoverable=False,  # Never retry quality failures
            filter_name=filter_name,
            metric=metric,
            value=value,
            threshold=threshold,
            **context,
        )


# =============================================================================
# Exception helpers
# =============================================================================


def wrap_exception(
    exc: BaseException,
    wrapper_class: type[PipelineError] = PipelineError,
    message: str | None = None,
    **context: Any,
) -> PipelineError:
    """
    Wrap a standard exception in a pipeline-specific exception.

    Preserves the original exception and its traceback.

    Parameters
    ----------
    exc : BaseException
        The original exception to wrap.
    wrapper_class : type[PipelineError], default PipelineError
        The pipeline exception class to use.
    message : str, optional
        Override message (defaults to str(exc)).
    **context : Any
        Additional context for the exception.

    Returns
    -------
    PipelineError
        A pipeline exception wrapping the original.

    Examples
    --------
    >>> try:  # doctest: +SKIP
    ...     h5py.File(path, 'r')
    ... except OSError as e:
    ...     raise wrap_exception(e, UVH5ReadError, file_path=path)
    """
    # Use the base PipelineError if wrapper has incompatible signature
    try:
        return wrapper_class(
            message or str(exc),
            original_exception=exc,
            **context,
        )
    except TypeError:
        # Fall back to base PipelineError for incompatible signatures
        return PipelineError(
            message or str(exc),
            original_exception=exc,
            **context,
        )


def is_recoverable(exc: BaseException) -> bool:
    """
    Check if an exception indicates a recoverable error.

    Parameters
    ----------
    exc : BaseException
        The exception to check.

    Returns
    -------
    bool
        True if processing can continue, False if it should halt.

    Examples
    --------
    >>> from dsa110_continuum.utils.exceptions import (
    ...     PipelineError, is_recoverable
    ... )
    >>> is_recoverable(FileNotFoundError("missing"))
    True
    >>> is_recoverable(PipelineError("fatal", recoverable=False))
    False
    """
    if isinstance(exc, PipelineError):
        return exc.recoverable

    # Standard exceptions that are typically recoverable
    recoverable_types = (
        FileNotFoundError,  # Can skip missing files
        PermissionError,  # Can retry with different permissions
        TimeoutError,  # Can retry
    )
    return isinstance(exc, recoverable_types)
