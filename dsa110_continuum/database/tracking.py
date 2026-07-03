# Vendored from dsa110-contimg @ /data/dsa110-contimg/backend/src (H17), 2026-07-03,
# infrastructure/tracking/provenance.py, as part of the contimg-import-retirement migration
# (docs/rse/specs/plan-contimg-import-retirement.md, Phase 5).
"""
Provenance tracking for DSA-110 pipeline.

Inspired by Chimbuko's provenance database concept, this module captures
full processing lineage for debugging and reproducibility:

- **What** was processed (inputs, outputs)
- **How** it was processed (configuration, software versions)
- **Why** decisions were made (calibrator selection reason, flagging criteria)
- **When** things happened (timing, stage durations)

Usage:
    from dsa110_contimg.infrastructure.tracking.provenance import (
        ProvenanceTracker,
        record_provenance,
        get_provenance,
    )

    # Create tracker for a job
    tracker = ProvenanceTracker(job_id="2025-12-21-001")

    # Record processing steps
    tracker.add_input("/data/incoming/obs_sb00.hdf5")
    tracker.set_calibrator("3C286", selection_reason="highest_flux_in_fov")
    tracker.add_stage_timing("conversion", duration_s=45.2)
    tracker.add_warning("Missing subband sb07, using placeholder")

    # Persist to database
    tracker.save()

    # Query later
    prov = get_provenance("2025-12-21-001")
    print(prov.calibrator_selection_reason)  # "highest_flux_in_fov"
"""

from __future__ import annotations

import hashlib
import json
import logging
import platform
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Try to get package versions
try:
    import pyuvdata

    PYUVDATA_VERSION = pyuvdata.__version__
except ImportError:
    PYUVDATA_VERSION = "unknown"

try:
    import casatools

    CASATOOLS_VERSION = getattr(casatools, "__version__", "6.x")
except ImportError:
    CASATOOLS_VERSION = "unknown"


# =============================================================================
# Data Classes
# =============================================================================


@dataclass
class StageTiming:
    """Timing information for a pipeline stage."""

    name: str
    duration_s: float
    started_at: float = field(default_factory=time.time)
    success: bool = True
    error: str | None = None
    dependencies: list[str] = field(default_factory=list)


@dataclass
class FlaggingSummary:
    """Summary of data flagging applied."""

    total_fraction: float = 0.0
    by_reason: dict[str, float] = field(default_factory=dict)
    flagged_antennas: list[str] = field(default_factory=list)
    flagged_baselines: list[str] = field(default_factory=list)
    flagged_channels: list[int] = field(default_factory=list)


@dataclass
class Provenance:
    """Complete provenance record for a pipeline job."""

    job_id: str
    created_at: float = field(default_factory=time.time)

    # Inputs and outputs
    input_files: list[str] = field(default_factory=list)
    output_ms: str | None = None
    output_images: list[str] = field(default_factory=list)

    # Calibration provenance
    calibrator_name: str | None = None
    calibrator_selection_reason: str | None = None
    calibrator_field_index: int | None = None
    calibrator_flux_jy: float | None = None
    calibration_preset: str | None = None
    caltable_paths: list[str] = field(default_factory=list)

    # Flagging provenance
    flagging: FlaggingSummary = field(default_factory=FlaggingSummary)

    # Configuration state
    config_state: dict[str, Any] = field(default_factory=dict)
    config_hash: str | None = None

    # Software versions
    software_versions: dict[str, str] = field(default_factory=dict)

    # Stage timings (execution DAG)
    stage_timings: list[StageTiming] = field(default_factory=list)

    # Warnings and notes
    warnings: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    # System context
    hostname: str = field(default_factory=platform.node)
    python_version: str = field(default_factory=platform.python_version)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "job_id": self.job_id,
            "created_at": self.created_at,
            "created_at_iso": datetime.fromtimestamp(self.created_at).isoformat(),
            "inputs": {
                "files": self.input_files,
                "file_count": len(self.input_files),
            },
            "outputs": {
                "ms_path": self.output_ms,
                "images": self.output_images,
            },
            "calibration": {
                "calibrator_name": self.calibrator_name,
                "selection_reason": self.calibrator_selection_reason,
                "field_index": self.calibrator_field_index,
                "flux_jy": self.calibrator_flux_jy,
                "preset": self.calibration_preset,
                "caltables": self.caltable_paths,
            },
            "flagging": {
                "total_fraction": self.flagging.total_fraction,
                "by_reason": self.flagging.by_reason,
                "flagged_antennas": self.flagging.flagged_antennas,
            },
            "config": self.config_state,
            "software_versions": self.software_versions,
            "execution_graph": [
                {
                    "stage": t.name,
                    "duration_s": t.duration_s,
                    "success": t.success,
                    "error": t.error,
                    "dependencies": t.dependencies,
                }
                for t in self.stage_timings
            ],
            "warnings": self.warnings,
            "notes": self.notes,
            "system": {
                "hostname": self.hostname,
                "python_version": self.python_version,
            },
        }

    def to_json(self, indent: int = 2) -> str:
        """Convert to JSON string.

        Parameters
        ----------
        indent : int :
            (Default value = 2)
        indent : int :
            (Default value = 2)
        indent : int :
            (Default value = 2)
        """
        return json.dumps(self.to_dict(), indent=indent)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Provenance:
        """Create from dictionary.

        Parameters
        ----------
        data : dict
            Dictionary containing the data

        """
        prov = cls(job_id=data["job_id"])
        prov.created_at = data.get("created_at", time.time())

        # Inputs/outputs
        inputs = data.get("inputs", {})
        prov.input_files = inputs.get("files", [])

        outputs = data.get("outputs", {})
        prov.output_ms = outputs.get("ms_path")
        prov.output_images = outputs.get("images", [])

        # Calibration
        cal = data.get("calibration", {})
        prov.calibrator_name = cal.get("calibrator_name")
        prov.calibrator_selection_reason = cal.get("selection_reason")
        prov.calibrator_field_index = cal.get("field_index")
        prov.calibrator_flux_jy = cal.get("flux_jy")
        prov.calibration_preset = cal.get("preset")
        prov.caltable_paths = cal.get("caltables", [])

        # Flagging
        flag_data = data.get("flagging", {})
        prov.flagging = FlaggingSummary(
            total_fraction=flag_data.get("total_fraction", 0.0),
            by_reason=flag_data.get("by_reason", {}),
            flagged_antennas=flag_data.get("flagged_antennas", []),
        )

        # Config and versions
        prov.config_state = data.get("config", {})
        prov.software_versions = data.get("software_versions", {})

        # Execution graph
        for stage_data in data.get("execution_graph", []):
            prov.stage_timings.append(
                StageTiming(
                    name=stage_data["stage"],
                    duration_s=stage_data["duration_s"],
                    success=stage_data.get("success", True),
                    error=stage_data.get("error"),
                    dependencies=stage_data.get("dependencies", []),
                )
            )

        # Warnings and notes
        prov.warnings = data.get("warnings", [])
        prov.notes = data.get("notes", [])

        # System
        system = data.get("system", {})
        prov.hostname = system.get("hostname", platform.node())
        prov.python_version = system.get("python_version", platform.python_version())

        return prov


# =============================================================================
# Provenance Tracker
# =============================================================================


class ProvenanceTracker:
    """Builder class for constructing provenance records."""

    def __init__(
        self,
        job_id: str,
        db_path: str | Path | None = None,
    ):
        """Initialize tracker.

        Parameters
        ----------
        job_id : str
            Unique identifier for this processing job
        db_path : str or Path, optional
            Path to SQLite database for persistence (defaults to pipeline.sqlite3)
        """
        self.provenance = Provenance(job_id=job_id)

        # Set software versions
        self.provenance.software_versions = {
            "pyuvdata": PYUVDATA_VERSION,
            "casatools": CASATOOLS_VERSION,
            "python": platform.python_version(),
        }

        # Database path
        if db_path is None:
            from dsa110_continuum.unified_config import settings

            db_path = settings.paths.pipeline_db
        self.db_path = Path(db_path)

        # Timing for current stage
        self._current_stage_start: float | None = None
        self._current_stage_name: str | None = None

    # -------------------------------------------------------------------------
    # Input/Output tracking
    # -------------------------------------------------------------------------

    def add_input(self, file_path: str | Path) -> ProvenanceTracker:
        """Add an input file to provenance.

        Parameters
        ----------
        file_path : str or Path
            Path to the input file

        """
        self.provenance.input_files.append(str(file_path))
        return self

    def add_inputs(self, file_paths: list[str | Path]) -> ProvenanceTracker:
        """Add multiple input files.

        Parameters
        ----------
        file_paths : list of str or Path
            List of input file paths

        """
        for fp in file_paths:
            self.add_input(fp)
        return self

    def set_output_ms(self, ms_path: str | Path) -> ProvenanceTracker:
        """Set the output measurement set path.

        Parameters
        ----------
        ms_path : str or Path
            Path to the output measurement set

        """
        self.provenance.output_ms = str(ms_path)
        return self

    def add_output_image(self, image_path: str | Path) -> ProvenanceTracker:
        """Add an output image path.

        Parameters
        ----------
        image_path : str or Path
            Path to the output image

        """
        self.provenance.output_images.append(str(image_path))
        return self

    # -------------------------------------------------------------------------
    # Calibration provenance
    # -------------------------------------------------------------------------

    def set_calibrator(
        self,
        name: str,
        selection_reason: str,
        field_index: int | None = None,
        flux_jy: float | None = None,
    ) -> ProvenanceTracker:
        """Record calibrator selection with reasoning.

        Parameters
        ----------
        name : str
            Calibrator name (e.g., "3C286")
        selection_reason : str
            Reason for choosing this calibrator (e.g., "highest_flux_in_fov", "manual_override")
        field_index : int, optional
            Field index in MS where calibrator is located (default is None)
        flux_jy : float, optional
            Expected flux density in Jy (default is None)

        """
        self.provenance.calibrator_name = name
        self.provenance.calibrator_selection_reason = selection_reason
        self.provenance.calibrator_field_index = field_index
        self.provenance.calibrator_flux_jy = flux_jy
        return self

    def set_calibration_preset(self, preset_name: str) -> ProvenanceTracker:
        """Record which calibration preset was used.

        Parameters
        ----------
        preset_name : str
            Name of the calibration preset used

        """
        self.provenance.calibration_preset = preset_name
        return self

    def add_caltable(self, caltable_path: str | Path) -> ProvenanceTracker:
        """Record a generated calibration table.

        Parameters
        ----------
        caltable_path : str or Path
            Path to the calibration table

        """
        self.provenance.caltable_paths.append(str(caltable_path))
        return self

    # -------------------------------------------------------------------------
    # Flagging provenance
    # -------------------------------------------------------------------------

    def set_flagging_summary(
        self,
        total_fraction: float,
        by_reason: dict[str, float] | None = None,
        flagged_antennas: list[str] | None = None,
        flagged_baselines: list[str] | None = None,
    ) -> ProvenanceTracker:
        """Record flagging summary.

        Parameters
        ----------
        total_fraction : float
            Total fraction of data flagged (0-1)
        by_reason : dict of str to float, optional
            Breakdown by reason (e.g., {"rfi": 0.08, "autocorr": 0.04})
        flagged_antennas : list of str, optional
            List of fully flagged antenna names
        flagged_baselines : list of str, optional
            List of flagged baseline pairs

        """
        self.provenance.flagging.total_fraction = total_fraction
        if by_reason:
            self.provenance.flagging.by_reason = by_reason
        if flagged_antennas:
            self.provenance.flagging.flagged_antennas = flagged_antennas
        if flagged_baselines:
            self.provenance.flagging.flagged_baselines = flagged_baselines
        return self

    # -------------------------------------------------------------------------
    # Configuration state
    # -------------------------------------------------------------------------

    def set_config(self, config: dict[str, Any]) -> ProvenanceTracker:
        """Capture the configuration state used.

        Parameters
        ----------
        config : dict
            Configuration dictionary

        """
        self.provenance.config_state = config

        # Compute deterministic hash of the configuration
        try:
            config_json = json.dumps(config, sort_keys=True, default=str)
            self.provenance.config_hash = hashlib.sha256(config_json.encode('utf-8')).hexdigest()
        except Exception:
            self.provenance.config_hash = "hash_computation_failed"

        return self

    def add_config(self, key: str, value: Any) -> ProvenanceTracker:
        """Add a single config key-value.

        Parameters
        ----------
        key : str
            Configuration key
        value : Any
            Configuration value

        """
        self.provenance.config_state[key] = value
        return self

    # -------------------------------------------------------------------------
    # Stage timing (execution DAG)
    # -------------------------------------------------------------------------

    def start_stage(
        self,
        stage_name: str,
        dependencies: list[str] | None = None,
    ) -> ProvenanceTracker:
        """Start timing a stage.

        Parameters
        ----------
        stage_name : str
            Name of the stage
        dependencies : list of str, optional
            List of dependencies (default is None)

        """
        self._current_stage_start = time.perf_counter()
        self._current_stage_name = stage_name
        return self

    def end_stage(
        self,
        success: bool = True,
        error: str | None = None,
    ) -> ProvenanceTracker:
        """End timing current stage and record it.

        Parameters
        ----------
        success : bool :
            (Default value = True)
        error : Optional[str] :
            (Default value = None)
        success : bool :
            (Default value = True)
        error : Optional[str] :
            (Default value = None)
        success : bool :
            (Default value = True)
        error : Optional[str] :
            (Default value = None)
        """
        if self._current_stage_start is None or self._current_stage_name is None:
            logger.warning("end_stage called without start_stage")
            return self

        duration = time.perf_counter() - self._current_stage_start
        self.provenance.stage_timings.append(
            StageTiming(
                name=self._current_stage_name,
                duration_s=duration,
                success=success,
                error=error,
            )
        )
        self._current_stage_start = None
        self._current_stage_name = None
        return self

    def add_stage_timing(
        self,
        stage_name: str,
        duration_s: float,
        success: bool = True,
        error: str | None = None,
        dependencies: list[str] | None = None,
    ) -> ProvenanceTracker:
        """Directly add stage timing (for external timing).

        Parameters
        ----------
        stage_name : str
            Name of the stage.
        duration_s : float
            Duration of the stage in seconds.
        success : bool
            Whether the stage succeeded. (Default value = True)
        error : Optional[str]
            Error message if any. (Default value = None)
        dependencies : Optional[List[str]]
            List of dependent stage names. (Default value = None)

        Returns
        -------
            None
        """
        self.provenance.stage_timings.append(
            StageTiming(
                name=stage_name,
                duration_s=duration_s,
                success=success,
                error=error,
                dependencies=dependencies or [],
            )
        )
        return self

    # -------------------------------------------------------------------------
    # Warnings and notes
    # -------------------------------------------------------------------------

    def add_warning(self, warning: str) -> ProvenanceTracker:
        """Record a non-fatal warning.

        Parameters
        ----------
        warning : str
            Warning message to record.

        Returns
        -------
            None
        """
        self.provenance.warnings.append(warning)
        logger.warning(f"[Provenance] {warning}")
        return self

    def add_note(self, note: str) -> ProvenanceTracker:
        """Add an informational note.

        Parameters
        ----------
        note : str
            Note message to add.

        Returns
        -------
            None
        """
        self.provenance.notes.append(note)
        return self

    # -------------------------------------------------------------------------
    # Persistence
    # -------------------------------------------------------------------------

    def save(self) -> bool:
        """Persist provenance to database."""
        try:
            _ensure_provenance_table(self.db_path)

            conn = sqlite3.connect(str(self.db_path), timeout=30.0)
            conn.execute("PRAGMA journal_mode=WAL")

            conn.execute(
                """
                INSERT OR REPLACE INTO processing_provenance (
                    job_id, ms_path, input_files, calibrator_name,
                    calibrator_selection_reason, flagging_summary,
                    config_state, software_versions, execution_graph,
                    warnings, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    self.provenance.job_id,
                    self.provenance.output_ms,
                    json.dumps(self.provenance.input_files),
                    self.provenance.calibrator_name,
                    self.provenance.calibrator_selection_reason,
                    json.dumps(self.provenance.flagging.__dict__),
                    json.dumps(self.provenance.config_state),
                    json.dumps(self.provenance.software_versions),
                    json.dumps(
                        [
                            {
                                "stage": t.name,
                                "duration_s": t.duration_s,
                                "success": t.success,
                                "error": t.error,
                            }
                            for t in self.provenance.stage_timings
                        ]
                    ),
                    json.dumps(self.provenance.warnings),
                    self.provenance.created_at,
                ),
            )
            conn.commit()
            conn.close()

            logger.info(f"Saved provenance for job {self.provenance.job_id}")
            return True

        except Exception as e:
            logger.error(f"Failed to save provenance: {e}")
            return False

    def get_provenance(self) -> Provenance:
        """Get the provenance record."""
        return self.provenance

    def get_critical_path(self) -> list[str]:
        """Calculate critical path through execution DAG.

        The critical path is the longest path through the DAG, considering
        that stages can run in parallel if they don't depend on each other.
        This identifies the sequence of stages that determines the minimum
        total execution time.

        """
        if not self.provenance.stage_timings:
            return []

        # Build stage lookup
        stages_by_name = {t.name: t for t in self.provenance.stage_timings}

        # Calculate longest path to each stage using dynamic programming
        longest_path_to: dict[str, float] = {}
        predecessor: dict[str, str | None] = {}

        def calculate_longest_path(stage_name: str) -> float:
            """Calculate longest path to this stage recursively with memoization.

            Parameters
            ----------
            stage_name : str
                Name of the stage to calculate the longest path for.

            Returns
            -------
                None
            """
            if stage_name in longest_path_to:
                return longest_path_to[stage_name]

            stage = stages_by_name.get(stage_name)
            if not stage:
                return 0.0

            # If no dependencies, longest path is just this stage's duration
            if not stage.dependencies:
                longest_path_to[stage_name] = stage.duration_s
                predecessor[stage_name] = None
                return stage.duration_s

            # Find the dependency with the longest path
            max_dependency_path = 0.0
            best_predecessor = None

            for dep in stage.dependencies:
                dep_path = calculate_longest_path(dep)
                if dep_path > max_dependency_path:
                    max_dependency_path = dep_path
                    best_predecessor = dep

            # Longest path to this stage is longest dependency path + this stage's duration
            total_path = max_dependency_path + stage.duration_s
            longest_path_to[stage_name] = total_path
            predecessor[stage_name] = best_predecessor

            return total_path

        # Calculate longest paths for all stages
        for stage in self.provenance.stage_timings:
            calculate_longest_path(stage.name)

        # Find the terminal stage (stage with longest total path)
        if not longest_path_to:
            return []

        terminal_stage = max(longest_path_to.keys(), key=lambda s: longest_path_to[s])

        # Reconstruct critical path by following predecessors
        critical_path = []
        current = terminal_stage

        while current is not None:
            critical_path.append(current)
            current = predecessor.get(current)

        # Reverse to get path from start to end
        critical_path.reverse()

        return critical_path

    def get_total_duration(self) -> float:
        """Get total execution time."""
        return sum(t.duration_s for t in self.provenance.stage_timings)

    def get_bottleneck_stage(self) -> str | None:
        """Identify the slowest stage."""
        if not self.provenance.stage_timings:
            return None
        slowest = max(self.provenance.stage_timings, key=lambda t: t.duration_s)
        return slowest.name


# =============================================================================
# Database Schema
# =============================================================================


def _ensure_provenance_table(db_path: Path) -> None:
    """Ensure the provenance table exists.

    Note: Schema is managed by Alembic migrations (revision 006).
    This method now just verifies access or creates for testing.

    Parameters
    ----------
    db_path : Path
        Path to the database file

    """
    conn = sqlite3.connect(str(db_path), timeout=30.0)
    conn.execute("PRAGMA journal_mode=WAL")

    # Check if table exists
    # DEV ONLY: Fallback for non-production environments.
    cursor = conn.cursor()
    cursor.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='processing_provenance'"
    )
    if not cursor.fetchone():
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS processing_provenance (
                job_id TEXT PRIMARY KEY,
                ms_path TEXT,
                input_files TEXT,
                calibrator_name TEXT,
                calibrator_selection_reason TEXT,
                flagging_summary TEXT,
                config_state TEXT,
                software_versions TEXT,
                execution_graph TEXT,
                warnings TEXT,
                created_at REAL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_prov_created ON processing_provenance(created_at)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_prov_calibrator ON processing_provenance(calibrator_name)"
        )
        conn.commit()
    conn.close()


# =============================================================================
# Query Functions
# =============================================================================


def get_provenance(
    job_id: str,
    db_path: str | Path | None = None,
) -> Provenance | None:
    """Retrieve provenance record by job ID.

    Parameters
    ----------
    job_id : str
        Job identifier
    db_path : str or Path, optional
        Database path (defaults to pipeline.sqlite3)

    """
    if db_path is None:
        from dsa110_continuum.unified_config import settings

        db_path = settings.paths.pipeline_db

    try:
        conn = sqlite3.connect(str(db_path), timeout=30.0)
        conn.row_factory = sqlite3.Row
        cursor = conn.execute("SELECT * FROM processing_provenance WHERE job_id = ?", (job_id,))
        row = cursor.fetchone()
        conn.close()

        if row is None:
            return None

        # Reconstruct provenance
        prov = Provenance(job_id=row["job_id"])
        prov.created_at = row["created_at"]
        prov.output_ms = row["ms_path"]
        prov.input_files = json.loads(row["input_files"] or "[]")
        prov.calibrator_name = row["calibrator_name"]
        prov.calibrator_selection_reason = row["calibrator_selection_reason"]
        prov.config_state = json.loads(row["config_state"] or "{}")
        prov.software_versions = json.loads(row["software_versions"] or "{}")
        prov.warnings = json.loads(row["warnings"] or "[]")

        # Flagging
        flag_data = json.loads(row["flagging_summary"] or "{}")
        prov.flagging = FlaggingSummary(**flag_data) if flag_data else FlaggingSummary()

        # Execution graph
        for stage in json.loads(row["execution_graph"] or "[]"):
            prov.stage_timings.append(
                StageTiming(
                    name=stage["stage"],
                    duration_s=stage["duration_s"],
                    success=stage.get("success", True),
                    error=stage.get("error"),
                )
            )

        return prov

    except Exception as e:
        logger.error(f"Failed to retrieve provenance for {job_id}: {e}")
        return None


def list_recent_provenance(
    limit: int = 100,
    db_path: str | Path | None = None,
) -> list[dict[str, Any]]:
    """List recent provenance records (summary only).

    Parameters
    ----------
    limit : int, optional
        Maximum number of records (default is 100)
    db_path : str or Path, optional
        Database path

    """
    if db_path is None:
        from dsa110_continuum.unified_config import settings

        db_path = settings.paths.pipeline_db

    try:
        conn = sqlite3.connect(str(db_path), timeout=30.0)
        conn.row_factory = sqlite3.Row
        cursor = conn.execute(
            """
            SELECT job_id, ms_path, calibrator_name, created_at
            FROM processing_provenance
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (limit,),
        )
        rows = cursor.fetchall()
        conn.close()

        return [
            {
                "job_id": row["job_id"],
                "ms_path": row["ms_path"],
                "calibrator": row["calibrator_name"],
                "created_at": row["created_at"],
                "created_at_iso": datetime.fromtimestamp(row["created_at"]).isoformat()
                if row["created_at"]
                else None,
            }
            for row in rows
        ]

    except Exception as e:
        logger.error(f"Failed to list provenance: {e}")
        return []


# =============================================================================
# Convenience Decorator
# =============================================================================


def record_provenance(job_id: str | None = None):
    """Decorator to automatically record provenance for a function.

    Usage:
        @record_provenance()
        def process_observation(ms_path, calibrator):
            ...

    Parameters
    ----------
    job_id : Optional[str] :
        (Default value = None)
    job_id: Optional[str] :
         (Default value = None)

    """

    def decorator(func):
        import functools

        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            # Generate job ID if not provided
            nonlocal job_id
            actual_job_id = job_id or f"{func.__name__}_{int(time.time())}"

            tracker = ProvenanceTracker(actual_job_id)
            tracker.start_stage(func.__name__)

            try:
                result = func(*args, **kwargs)
                tracker.end_stage(success=True)
                tracker.save()
                return result

            except Exception as e:
                tracker.end_stage(success=False, error=str(e))
                tracker.save()
                raise

        return wrapper

    return decorator


__all__ = [
    "Provenance",
    "ProvenanceTracker",
    "StageTiming",
    "FlaggingSummary",
    "get_provenance",
    "list_recent_provenance",
    "record_provenance",
]
