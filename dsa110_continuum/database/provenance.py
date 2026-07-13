"""
Calibration provenance tracking and analysis.

This module provides functions for tracking and querying calibration provenance,
including source MS paths, solver parameters, commands, and quality metrics.
"""

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class CalTable:
    """Represents a calibration table with provenance information."""

    id: int
    set_name: str
    path: str
    table_type: str
    order_index: int
    cal_field: str | None
    refant: str | None
    created_at: float
    valid_start_mjd: float | None
    valid_end_mjd: float | None
    status: str
    notes: str | None
    source_ms_path: str | None
    solver_command: str | None
    solver_version: str | None
    solver_params: dict[str, Any] | None
    quality_metrics: dict[str, Any] | None


def track_calibration_provenance(
    registry_db: Path,
    ms_path: str,
    caltable_path: str,
    params: dict[str, Any],
    metrics: dict[str, Any] | None = None,
    solver_command: str | None = None,
    solver_version: str | None = None,
) -> None:
    """Track calibration provenance for a calibration table.

        Updates the provenance fields (source_ms_path, solver_command, solver_version,
        solver_params, quality_metrics) for an existing calibration table entry.

    Parameters
    ----------
    registry_db : str
        Path to calibration registry database
    ms_path : str
        Path to the input MS that generated this caltable
    caltable_path : str
        Path to the calibration table
    params : Dict[str, Any]
        Parameters used in calibration
    metrics : Optional[Dict[str, Any]], optional
        Dictionary of quality metrics, by default None
    solver_command : Optional[str], optional
        Full CASA command executed, by default None
    solver_version : Optional[str], optional
        CASA version used, by default None

    Returns
    -------
        None
    """
    from .unified import ensure_db

    conn = ensure_db(registry_db)

    # Serialize JSON fields
    params_json = json.dumps(params) if params else None
    metrics_json = json.dumps(metrics) if metrics else None

    with conn:
        cursor = conn.execute(
            """
            UPDATE caltables
            SET source_ms_path = ?,
                solver_command = ?,
                solver_version = ?,
                solver_params = ?,
                quality_metrics = ?
            WHERE path = ?
            """,
            (
                ms_path,
                solver_command,
                solver_version,
                params_json,
                metrics_json,
                str(caltable_path),
            ),
        )

        if cursor.rowcount == 0:
            logger.warning(
                f"Calibration table {caltable_path} not found in registry. "
                f"Provenance not updated. Register the table first."
            )
        else:
            logger.info(
                f"Updated provenance for calibration table {caltable_path} (source MS: {ms_path})"
            )

    conn.close()


def query_caltables_by_source(registry_db: Path, ms_path: str) -> list[CalTable]:
    """Query all calibration tables generated from a specific MS.

    Parameters
    ----------
    registry_db :
        Path to calibration registry database
    ms_path :
        Path to the source MS

    Returns
    -------
        List of CalTable objects matching the source MS path

    """
    from .unified import ensure_db

    conn = ensure_db(registry_db)

    cursor = conn.execute(
        """
        SELECT id, set_name, path, table_type, order_index, cal_field, refant,
               created_at, valid_start_mjd, valid_end_mjd, status, notes,
               source_ms_path, solver_command, solver_version, solver_params,
               quality_metrics
        FROM caltables
        WHERE source_ms_path = ?
        ORDER BY order_index ASC, created_at DESC
        """,
        (ms_path,),
    )

    results = []
    for row in cursor.fetchall():
        # Deserialize JSON fields
        solver_params = json.loads(row[15]) if row[15] else None
        quality_metrics = json.loads(row[16]) if row[16] else None

        results.append(
            CalTable(
                id=row[0],
                set_name=row[1],
                path=row[2],
                table_type=row[3],
                order_index=row[4],
                cal_field=row[5],
                refant=row[6],
                created_at=row[7],
                valid_start_mjd=row[8],
                valid_end_mjd=row[9],
                status=row[10],
                notes=row[11],
                source_ms_path=row[12],
                solver_command=row[13],
                solver_version=row[14],
                solver_params=solver_params,
                quality_metrics=quality_metrics,
            )
        )

    conn.close()
    return results


def impact_analysis(registry_db: Path, caltable_paths: list[str]) -> list[str]:
    """Analyze impact of calibration tables on downstream MS processing.

    Given a list of calibration table paths, returns all MS paths that
    were processed using these calibration tables (based on provenance).

    Note: This is a simplified analysis based on source MS paths. A more
    sophisticated analysis would track which MS files actually used which
    calibration tables during applycal operations.

    Parameters
    ----------
    registry_db :
        Path to calibration registry database
    caltable_paths :
        List of calibration table paths to analyze

    Returns
    -------
        List of MS paths that may be affected by changes to these calibration tables

    """
    from .unified import ensure_db

    conn = ensure_db(registry_db)

    # Build query with placeholders
    placeholders = ",".join("?" * len(caltable_paths))

    cursor = conn.execute(
        f"""
        SELECT DISTINCT source_ms_path
        FROM caltables
        WHERE path IN ({placeholders})
          AND source_ms_path IS NOT NULL
        """,
        tuple(caltable_paths),
    )

    affected_ms_paths = [row[0] for row in cursor.fetchall()]

    conn.close()
    return affected_ms_paths


def get_caltable_provenance(registry_db: Path, caltable_path: str) -> CalTable | None:
    """Get full provenance information for a single calibration table.

    Parameters
    ----------
    registry_db :
        Path to calibration registry database
    caltable_path :
        Path to the calibration table

    Returns
    -------
        CalTable object with provenance, or None if not found

    """
    from .unified import ensure_db

    conn = ensure_db(registry_db)

    cursor = conn.execute(
        """
        SELECT id, set_name, path, table_type, order_index, cal_field, refant,
               created_at, valid_start_mjd, valid_end_mjd, status, notes,
               source_ms_path, solver_command, solver_version, solver_params,
               quality_metrics
        FROM caltables
        WHERE path = ?
        LIMIT 1
        """,
        (str(caltable_path),),
    )

    row = cursor.fetchone()
    conn.close()

    if not row:
        return None

    # Deserialize JSON fields
    solver_params = json.loads(row[15]) if row[15] else None
    quality_metrics = json.loads(row[16]) if row[16] else None

    return CalTable(
        id=row[0],
        set_name=row[1],
        path=row[2],
        table_type=row[3],
        order_index=row[4],
        cal_field=row[5],
        refant=row[6],
        created_at=row[7],
        valid_start_mjd=row[8],
        valid_end_mjd=row[9],
        status=row[10],
        notes=row[11],
        source_ms_path=row[12],
        solver_command=row[13],
        solver_version=row[14],
        solver_params=solver_params,
        quality_metrics=quality_metrics,
    )
