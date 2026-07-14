"""
Enhanced error context utilities.

This module provides utilities for formatting error messages with rich context,
including file metadata, MS characteristics, and actionable suggestions.
"""

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)


def format_error_with_context(
    error: Exception,
    context: dict[str, Any],
    include_metadata: bool = True,
    include_suggestions: bool = True,
) -> str:
    """Format error with rich context including file metadata and suggestions.

        Enhances error messages with:
        - File/MS metadata (size, modification time, characteristics)
        - Suggested command-line fixes
        - Performance hints (if applicable)

    Parameters
    ----------
    error : Exception
        The exception that occurred
    context : dict
        Dictionary with context information:
        - 'ms_path': Path to Measurement Set (adds MS metadata)
        - 'file_path': Path to file (adds file metadata)
        - 'suggestion': Suggested fix or command
        - 'performance_hint': Performance-related hint
        - 'operation': Name of operation that failed
        - 'elapsed_time': Time elapsed before failure (for performance hints)
    include_metadata : bool
        If True, include file/MS metadata
    include_suggestions : bool
        If True, include suggestions and hints

    Returns
    -------
        str
        Formatted error message with context

    Examples
    --------
        ```python
        try:
        validate_ms(ms_path)
        except Exception as e:
        context = {
        'ms_path': ms_path,
        'operation': 'MS validation',
        'suggestion': 'Use --auto-fields to auto-select fields'
        }
        error_msg = format_error_with_context(e, context)
        raise RuntimeError(error_msg) from e
        ```
    """
    lines = [f"Error: {str(error)}"]

    # Add operation context
    if "operation" in context:
        lines.append(f"Operation: {context['operation']}")

    # Add MS metadata
    if include_metadata and "ms_path" in context:
        ms_path = context["ms_path"]
        try:
            # Lazy import to avoid circular dependencies
            from dsa110_continuum.utils.ms_helpers import (  # noqa: F401
                estimate_ms_size,
                get_ms_metadata,
            )

            if os.path.exists(ms_path):
                # Get MS metadata
                try:
                    metadata = get_ms_metadata(ms_path)
                    lines.append("\nMS Metadata:")
                    lines.append(f"  Path: {ms_path}")
                    if "nspw" in metadata:
                        lines.append(f"  Spectral Windows: {metadata['nspw']}")
                    if "nfields" in metadata:
                        lines.append(f"  Fields: {metadata['nfields']}")
                    if "phase_dir" in metadata and len(metadata["phase_dir"]) > 0:
                        lines.append(f"  Phase Centers: {len(metadata['phase_dir'])}")

                    # Get size estimates
                    size_info = estimate_ms_size(ms_path)
                    if size_info:
                        lines.append(f"  Estimated Rows: {size_info.get('n_rows', 'N/A'):,}")
                        if "estimated_memory_gb" in size_info:
                            lines.append(
                                f"  Estimated Memory: {size_info['estimated_memory_gb']:.2f} GB"
                            )
                except Exception as e:
                    logger.debug(f"Failed to get MS metadata: {e}")
                    lines.append(f"\nMS: {ms_path}")
                    if os.path.exists(ms_path):
                        stat = os.stat(ms_path)
                        lines.append(f"  Size: {stat.st_size / (1024**3):.2f} GB")
                        lines.append(f"  Modified: {stat.st_mtime}")
            else:
                lines.append(f"\nMS: {ms_path} (not found)")
        except ImportError:
            # Fallback if ms_helpers not available
            lines.append(f"\nMS: {ms_path}")

    # Add file metadata
    if include_metadata and "file_path" in context:
        file_path = context["file_path"]
        if os.path.exists(file_path):
            stat = os.stat(file_path)
            lines.append(f"\nFile: {file_path}")
            lines.append(f"  Size: {stat.st_size / (1024**2):.2f} MB")
            lines.append(f"  Modified: {stat.st_mtime}")
        else:
            lines.append(f"\nFile: {file_path} (not found)")

    # Add suggestions
    if include_suggestions:
        if "suggestion" in context:
            lines.append(f"\nSuggestion: {context['suggestion']}")

        if "suggestions" in context:
            lines.append("\nSuggestions:")
            for i, suggestion in enumerate(context["suggestions"], 1):
                lines.append(f"  {i}. {suggestion}")

        # Add performance hints
        if "performance_hint" in context:
            lines.append(f"\nPerformance Hint: {context['performance_hint']}")
        elif "elapsed_time" in context and context.get("operation"):
            elapsed = context["elapsed_time"]
            if elapsed > 300:  # 5 minutes
                lines.append(
                    f"\nPerformance Hint: This operation took {elapsed / 60:.1f} minutes. "
                    f"Consider using --fast mode or --preset=fast for faster execution."
                )

    return "\n".join(lines)


def format_ms_error_with_suggestions(
    error: Exception,
    ms_path: str,
    operation: str,
    suggestions: list[str] | None = None,
) -> str:
    """Convenience function to format MS-related errors with suggestions.

    Parameters
    ----------
    error : Exception
        The exception that occurred
    ms_path : str
        Path to Measurement Set
    operation : str
        Name of operation that failed
    suggestions : list
        List of suggested fixes

    Returns
    -------
        str
        Formatted error message with MS context and suggestions

    Examples
    --------
        ```python
        try:
        calibrate_ms(ms_path)
        except Exception as e:
        suggestions = [
        'Use --auto-fields to auto-select fields',
        'Check MS integrity: python -m dsa110_continuum.calibration.cli validate --ms <ms>'
        ]
        error_msg = format_ms_error_with_suggestions(e, ms_path, 'calibration', suggestions)
        raise RuntimeError(error_msg) from e
        ```
    """
    context = {
        "ms_path": ms_path,
        "operation": operation,
    }

    if suggestions:
        context["suggestions"] = suggestions

    return format_error_with_context(error, context)


def format_file_error_with_suggestions(
    error: Exception,
    file_path: str,
    operation: str,
    suggestions: list[str] | None = None,
) -> str:
    """Convenience function to format file-related errors with suggestions.

    Parameters
    ----------
    error : Exception
        The exception that occurred
    file_path : str
        Path to file
    operation : str
        Name of operation that failed
    suggestions : list
        List of suggested fixes

    Returns
    -------
        str
        Formatted error message with file context and suggestions
    """
    context = {
        "file_path": file_path,
        "operation": operation,
    }

    if suggestions:
        context["suggestions"] = suggestions

    return format_error_with_context(error, context)
