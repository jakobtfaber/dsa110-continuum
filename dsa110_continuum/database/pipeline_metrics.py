# Vendored from dsa110-contimg @ /data/dsa110-contimg/backend/src (H17), 2026-07-03,
# infrastructure/monitoring/pipeline_metrics.py, as part of the contimg-import-retirement migration
# (docs/rse/specs/plan-contimg-import-retirement.md, Phase 5).
"""
Pipeline Metrics Module for DSA-110 Continuum Imaging.

Provides comprehensive metrics collection for pipeline operations:
- GPU utilization per pipeline stage
- Processing time breakdown (CPU vs GPU)
- Memory high-water marks per job
- Throughput metrics (MS/hour)

Usage:
    from dsa110_contimg.infrastructure.monitoring.pipeline_metrics import (
        PipelineMetrics, StageMetrics, get_metrics_collector
    )

    # Record stage execution
    with metrics.stage_context("imaging", ms_path) as stage:
        stage.record_gpu_time(2.5)
        stage.record_cpu_time(1.0)
        # ... processing ...

    # Get metrics summary
    summary = metrics.get_summary()
"""

from __future__ import annotations

import logging
import sqlite3
import time
import statistics
from collections import defaultdict, deque
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from threading import Lock
from typing import Any

logger = logging.getLogger(__name__)


# =============================================================================
# Metric Types
# =============================================================================


class PipelineStage(str, Enum):
    """Pipeline processing stages."""

    CONVERSION = "conversion"
    RFI_FLAGGING = "rfi_flagging"
    CALIBRATION_SOLVE = "calibration_solve"
    CALIBRATION_APPLY = "calibration_apply"
    IMAGING = "imaging"
    QA = "qa"
    TOTAL = "total"


class ProcessingMode(str, Enum):
    """Processing mode for timing breakdown."""

    CPU = "cpu"
    GPU = "gpu"
    IO = "io"
    IDLE = "idle"


# =============================================================================
# Metric Data Classes
# =============================================================================


@dataclass
class StageTimingMetrics:
    """Timing metrics for a pipeline stage."""

    stage: PipelineStage
    total_time_s: float = 0.0
    cpu_time_s: float = 0.0
    gpu_time_s: float = 0.0
    io_time_s: float = 0.0
    idle_time_s: float = 0.0

    @property
    def gpu_fraction(self) -> float:
        """Fraction of time spent on GPU (0.0-1.0)."""
        if self.total_time_s <= 0:
            return 0.0
        return self.gpu_time_s / self.total_time_s

    @property
    def speedup_ratio(self) -> float:
        """Speedup ratio (CPU+GPU time / total time)."""
        compute_time = self.cpu_time_s + self.gpu_time_s
        if self.total_time_s <= 0 or compute_time <= 0:
            return 1.0
        return compute_time / self.total_time_s

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "stage": self.stage.value,
            "total_time_s": round(self.total_time_s, 3),
            "cpu_time_s": round(self.cpu_time_s, 3),
            "gpu_time_s": round(self.gpu_time_s, 3),
            "io_time_s": round(self.io_time_s, 3),
            "idle_time_s": round(self.idle_time_s, 3),
            "gpu_fraction": round(self.gpu_fraction, 3),
        }


@dataclass
class MemoryMetrics:
    """Memory metrics for a job."""

    peak_ram_gb: float = 0.0
    peak_gpu_mem_gb: float = 0.0
    average_ram_gb: float = 0.0
    average_gpu_mem_gb: float = 0.0
    samples: int = 0

    def update(self, ram_gb: float, gpu_mem_gb: float = 0.0) -> None:
        """Update metrics with new sample.

        Parameters
        ----------
        ram_gb : float
            Current RAM usage in GB
        gpu_mem_gb : float, optional
            Current GPU memory usage in GB (default is 0.0)
        """
        self.peak_ram_gb = max(self.peak_ram_gb, ram_gb)
        self.peak_gpu_mem_gb = max(self.peak_gpu_mem_gb, gpu_mem_gb)

        # Running average
        self.samples += 1
        alpha = 1.0 / self.samples
        self.average_ram_gb = (1 - alpha) * self.average_ram_gb + alpha * ram_gb
        self.average_gpu_mem_gb = (1 - alpha) * self.average_gpu_mem_gb + alpha * gpu_mem_gb

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "peak_ram_gb": round(self.peak_ram_gb, 3),
            "peak_gpu_mem_gb": round(self.peak_gpu_mem_gb, 3),
            "average_ram_gb": round(self.average_ram_gb, 3),
            "average_gpu_mem_gb": round(self.average_gpu_mem_gb, 3),
        }


@dataclass
class GPUUtilizationMetrics:
    """GPU utilization metrics."""

    gpu_id: int
    utilization_pct: float
    memory_utilization_pct: float
    timestamp: float = field(default_factory=time.time)


@dataclass
class ThroughputMetrics:
    """Throughput metrics."""

    ms_processed: int = 0
    ms_per_hour: float = 0.0
    bytes_processed: int = 0
    gb_per_hour: float = 0.0
    time_window_hours: float = 1.0

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "ms_processed": self.ms_processed,
            "ms_per_hour": round(self.ms_per_hour, 2),
            "bytes_processed": self.bytes_processed,
            "gb_per_hour": round(self.gb_per_hour, 2),
            "time_window_hours": self.time_window_hours,
        }


@dataclass
class IngestRateMetrics:
    """Ingest (incoming data) rate metrics.

        Tracks rate of incoming subband groups vs processing rate
        to detect when the pipeline is falling behind.

    Attributes
    ----------
    groups_arrived : int
        Number of subband groups that arrived
    groups_per_hour : float
        Arrival rate (groups per hour)
    groups_processed : int
        Number of groups successfully processed
    processed_per_hour : float
        Processing rate (groups per hour)
    backlog_groups : int
        Current backlog (arrived - processed)
    backlog_growing : bool
        True if backlog is increasing
    time_window_hours : float
        Time window for calculations
    """

    groups_arrived: int = 0
    groups_per_hour: float = 0.0
    groups_processed: int = 0
    processed_per_hour: float = 0.0
    backlog_groups: int = 0
    backlog_growing: bool = False
    time_window_hours: float = 1.0

    @property
    def rate_ratio(self) -> float:
        """Ratio of processing rate to arrival rate."""
        if self.groups_per_hour <= 0:
            return float("inf")  # No incoming data
        return self.processed_per_hour / self.groups_per_hour

    @property
    def is_keeping_up(self) -> bool:
        """Check if pipeline is keeping up with incoming data."""
        return self.rate_ratio >= 1.0

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "groups_arrived": self.groups_arrived,
            "groups_per_hour": round(self.groups_per_hour, 2),
            "groups_processed": self.groups_processed,
            "processed_per_hour": round(self.processed_per_hour, 2),
            "backlog_groups": self.backlog_groups,
            "backlog_growing": self.backlog_growing,
            "rate_ratio": round(self.rate_ratio, 2) if self.rate_ratio != float("inf") else None,
            "is_keeping_up": self.is_keeping_up,
            "time_window_hours": self.time_window_hours,
        }


@dataclass
class JobMetrics:
    """Complete metrics for a single job."""

    ms_path: str
    config_hash: str | None = None
    started_at: float = field(default_factory=time.time)
    ended_at: float | None = None
    stage_timings: dict[PipelineStage, StageTimingMetrics] = field(default_factory=dict)
    memory: MemoryMetrics = field(default_factory=MemoryMetrics)
    success: bool | None = None
    error_message: str | None = None

    @property
    def duration_s(self) -> float:
        """Total job duration in seconds."""
        end = self.ended_at or time.time()
        return end - self.started_at

    @property
    def is_complete(self) -> bool:
        """Whether job has completed."""
        return self.ended_at is not None

    def get_timing(self, stage: PipelineStage) -> StageTimingMetrics:
        """Get or create timing metrics for stage.

        Parameters
        ----------
        stage : PipelineStage
            Pipeline stage to get or create timing metrics for
        """
        if stage not in self.stage_timings:
            self.stage_timings[stage] = StageTimingMetrics(stage=stage)
        return self.stage_timings[stage]

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "ms_path": self.ms_path,
            "config_hash": self.config_hash,
            "started_at": datetime.fromtimestamp(self.started_at).isoformat(),
            "ended_at": (
                datetime.fromtimestamp(self.ended_at).isoformat() if self.ended_at else None
            ),
            "duration_s": round(self.duration_s, 3),
            "success": self.success,
            "error_message": self.error_message,
            "stage_timings": {k.value: v.to_dict() for k, v in self.stage_timings.items()},
            "memory": self.memory.to_dict(),
        }


# =============================================================================
# Stage Context Manager
# =============================================================================


class StageContext:
    """Context manager for tracking stage metrics.

    Usage:
        with metrics.stage_context("imaging", ms_path) as stage:
            stage.record_gpu_time(2.5)
            stage.record_cpu_time(1.0)
            # ... processing ...

    """

    def __init__(
        self,
        stage: PipelineStage,
        job_metrics: JobMetrics,
        collector: PipelineMetricsCollector,
    ):
        self.stage = stage
        self.job_metrics = job_metrics
        self.collector = collector
        self.timing = job_metrics.get_timing(stage)
        self._start_time = 0.0
        self._gpu_samples: list[GPUUtilizationMetrics] = []

    def __enter__(self) -> StageContext:
        self._start_time = time.time()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.timing.total_time_s = time.time() - self._start_time

        # Calculate idle time as remainder
        compute_time = self.timing.cpu_time_s + self.timing.gpu_time_s + self.timing.io_time_s
        self.timing.idle_time_s = max(0, self.timing.total_time_s - compute_time)

        # Record GPU samples
        if self._gpu_samples:
            avg_util = statistics.mean(s.utilization_pct for s in self._gpu_samples)
            self.collector._record_gpu_utilization(self.stage, avg_util)

    def record_cpu_time(self, seconds: float) -> None:
        """Record CPU processing time.

        Parameters
        ----------
        seconds : float
            Time spent in CPU operations
        """
        self.timing.cpu_time_s += seconds

    def record_gpu_time(self, seconds: float) -> None:
        """Record GPU processing time.

        Parameters
        ----------
        seconds : float
            Time spent in GPU operations
        """
        self.timing.gpu_time_s += seconds

    def record_io_time(self, seconds: float) -> None:
        """Record I/O time.

        Parameters
        ----------
        seconds : float
            Time spent in I/O operations
        """
        self.timing.io_time_s += seconds

    def record_memory(self, ram_gb: float, gpu_mem_gb: float = 0.0) -> None:
        """Record memory sample.

        Parameters
        ----------
        ram_gb : float
            Current RAM usage in GB
        gpu_mem_gb : float, optional
            Current GPU memory usage in GB (default is 0.0)
        """
        self.job_metrics.memory.update(ram_gb, gpu_mem_gb)

    def record_gpu_utilization(self, gpu_id: int, util_pct: float, mem_pct: float) -> None:
        """Record GPU utilization sample.

        Parameters
        ----------
        gpu_id : int
            GPU device ID
        util_pct : float
            GPU compute utilization percentage
        mem_pct : float
            GPU memory utilization percentage
        """
        self._gpu_samples.append(
            GPUUtilizationMetrics(
                gpu_id=gpu_id,
                utilization_pct=util_pct,
                memory_utilization_pct=mem_pct,
            )
        )


# =============================================================================
# Metrics Collector
# =============================================================================


class PipelineMetricsCollector:
    """Collects and aggregates pipeline metrics.

    Thread-safe collector for recording metrics from multiple
    concurrent pipeline jobs.

    """

    def __init__(
        self,
        db_path: str | None = None,
        history_size: int = 1000,
    ):
        """Initialize metrics collector.

        Parameters
        ----------
        db_path : str, optional
            Path for persistent metrics storage
        history_size : int, optional
            Number of completed jobs to keep in memory
        """
        self.db_path = db_path
        self._lock = Lock()
        self._active_jobs: dict[str, JobMetrics] = {}
        self._completed_jobs: deque[JobMetrics] = deque(maxlen=history_size)
        self._stage_gpu_utilization: dict[PipelineStage, list[float]] = defaultdict(list)
        self._throughput_timestamps: deque[tuple[float, int]] = deque(maxlen=1000)
        # Track incoming data arrivals for backlog monitoring
        self._ingest_timestamps: deque[tuple[float, str]] = deque(maxlen=2000)
        self._previous_backlog: int = 0  # For tracking if backlog is growing

        if db_path:
            self._init_db()

    def _init_db(self) -> None:
        """Initialize database for persistent storage.

        Note: Schema is managed by Alembic migrations (revision 006).
        This method now just verifies access or creates for testing.
        """
        db_dir = Path(self.db_path).parent
        db_dir.mkdir(parents=True, exist_ok=True)

        conn = sqlite3.connect(self.db_path)

        # Check if table exists
        # DEV ONLY: Fallback for non-production environments.
        cursor = conn.cursor()
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='pipeline_metrics'"
        )
        if not cursor.fetchone():
            conn.execute("""
                CREATE TABLE IF NOT EXISTS pipeline_metrics (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ms_path TEXT NOT NULL,
                    config_hash TEXT,
                    started_at REAL NOT NULL,
                    ended_at REAL,
                    duration_s REAL,
                    success INTEGER,
                    error_message TEXT,
                    stage_timings_json TEXT,
                    memory_json TEXT,
                    created_at REAL DEFAULT (unixepoch())
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_metrics_ms ON pipeline_metrics(ms_path)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_metrics_started ON pipeline_metrics(started_at)
            """)

        self._ensure_config_hash_column(conn)
        conn.commit()
        conn.close()

    def _ensure_config_hash_column(self, conn: sqlite3.Connection) -> None:
        """Ensure config_hash column exists for existing databases.

        Parameters
        ----------
        conn : sqlite3.Connection
            Database connection
        """
        try:
            columns = {row[1] for row in conn.execute("PRAGMA table_info(pipeline_metrics)")}
            if "config_hash" not in columns:
                conn.execute("ALTER TABLE pipeline_metrics ADD COLUMN config_hash TEXT")
        except Exception as exc:
            logger.debug("Could not ensure config_hash column: %s", exc)

    def start_job(self, ms_path: str, config_hash: str | None = None) -> JobMetrics:
        """Start tracking a new job.

        Parameters
        ----------
        ms_path : str
            Path to MS being processed
        config_hash : str or None, optional
            Configuration hash (default is None)
        """
        with self._lock:
            job = JobMetrics(ms_path=ms_path, config_hash=config_hash)
            self._active_jobs[ms_path] = job
            log_label = ms_path if not config_hash else f"{ms_path} (config_hash={config_hash})"
            logger.debug("Started metrics tracking for %s", log_label)
            return job

    def end_job(
        self,
        ms_path: str,
        success: bool,
        error_message: str | None = None,
        size_bytes: int = 0,
    ) -> JobMetrics | None:
        """End tracking for a job.

        Parameters
        ----------
        ms_path : str
            Path to MS
        success : bool
            Whether job succeeded
        error_message : str or None, optional
            Error message if failed (default is None)
        size_bytes : int, optional
            Size of MS file for throughput calculation (default is 0)
        """
        with self._lock:
            job = self._active_jobs.pop(ms_path, None)
            if not job:
                return None

            job.ended_at = time.time()
            job.success = success
            job.error_message = error_message

            self._completed_jobs.append(job)
            self._throughput_timestamps.append((time.time(), size_bytes))

            logger.debug(
                "Ended metrics for %s: success=%s, duration=%.1fs",
                ms_path,
                success,
                job.duration_s,
            )

            if self.db_path:
                self._persist_job(job)

            return job

    def _persist_job(self, job: JobMetrics) -> None:
        """Persist job metrics to database.

        Parameters
        ----------
        job : JobMetrics
            Job metrics to persist
        """
        import json

        try:
            conn = sqlite3.connect(self.db_path, timeout=10.0)
            self._ensure_config_hash_column(conn)
            conn.execute(
                """
                INSERT INTO pipeline_metrics
                    (ms_path, config_hash, started_at, ended_at, duration_s, success,
                     error_message, stage_timings_json, memory_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job.ms_path,
                    job.config_hash,
                    job.started_at,
                    job.ended_at,
                    job.duration_s,
                    1 if job.success else 0,
                    job.error_message,
                    json.dumps({k.value: v.to_dict() for k, v in job.stage_timings.items()}),
                    json.dumps(job.memory.to_dict()),
                ),
            )
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error("Failed to persist metrics: %s", e)

    def get_job(self, ms_path: str) -> JobMetrics | None:
        """Get metrics for a job (active or completed).

        Parameters
        ----------
        ms_path : str
            Path to MS
        """
        with self._lock:
            if ms_path in self._active_jobs:
                return self._active_jobs[ms_path]

            for job in self._completed_jobs:
                if job.ms_path == ms_path:
                    return job

        return None

    @contextmanager
    def stage_context(
        self, stage: str | PipelineStage, ms_path: str, config_hash: str | None = None
    ) -> Generator[StageContext, None, None]:
        """Context manager for tracking stage execution.

        Parameters
        ----------
        stage : str or PipelineStage
            Pipeline stage name or enum
        ms_path : str
            Path to MS being processed
        config_hash : str or None, optional
            Configuration hash (default is None)

        Yields
        ------
            StageContext
            StageContext for recording metrics
        """
        if isinstance(stage, str):
            stage = PipelineStage(stage)

        job = self.get_job(ms_path)
        if not job:
            job = self.start_job(ms_path, config_hash=config_hash)
        elif config_hash and not job.config_hash:
            job.config_hash = config_hash

        ctx = StageContext(stage, job, self)
        with ctx:
            yield ctx

    def _record_gpu_utilization(self, stage: PipelineStage, utilization_pct: float) -> None:
        """Record GPU utilization sample for stage.

        Parameters
        ----------
        stage : PipelineStage
            Stage of the pipeline.
        utilization_pct : float
            GPU utilization percentage.

        """
        with self._lock:
            samples = self._stage_gpu_utilization[stage]
            samples.append(utilization_pct)
            # Keep last 100 samples per stage
            if len(samples) > 100:
                samples.pop(0)

    def get_throughput(self, hours: float = 1.0) -> ThroughputMetrics:
        """Get throughput metrics for time window.

        Parameters
        ----------
        hours : float
            Time window in hours. (Default value = 1.0)

        """
        with self._lock:
            cutoff = time.time() - (hours * 3600)
            recent = [(ts, size) for ts, size in self._throughput_timestamps if ts >= cutoff]

            ms_count = len(recent)
            total_bytes = sum(size for _, size in recent)

            return ThroughputMetrics(
                ms_processed=ms_count,
                ms_per_hour=ms_count / hours if hours > 0 else 0,
                bytes_processed=total_bytes,
                gb_per_hour=(total_bytes / 1e9) / hours if hours > 0 else 0,
                time_window_hours=hours,
            )

    def record_ingest(self, group_id: str) -> None:
        """Record arrival of a new subband group for ingest rate tracking.

            Call this when a new complete subband group arrives
            in the incoming directory.

        Parameters
        ----------
        group_id : str
            Unique identifier for the subband group.

        """
        with self._lock:
            self._ingest_timestamps.append((time.time(), group_id))

    def get_ingest_rate(self, hours: float = 1.0) -> IngestRateMetrics:
        """Get ingest rate metrics comparing incoming vs processed data.

            This helps detect when the pipeline is falling behind
            the incoming data rate.

        Parameters
        ----------
        hours : float
            Time window in hours. (Default value = 1.0)

        """
        with self._lock:
            cutoff = time.time() - (hours * 3600)

            # Count arrivals in window
            recent_arrivals = [(ts, gid) for ts, gid in self._ingest_timestamps if ts >= cutoff]
            groups_arrived = len(recent_arrivals)
            groups_per_hour = groups_arrived / hours if hours > 0 else 0.0

            # Count processed in window
            recent_processed = [
                (ts, size) for ts, size in self._throughput_timestamps if ts >= cutoff
            ]
            groups_processed = len(recent_processed)
            processed_per_hour = groups_processed / hours if hours > 0 else 0.0

            # Calculate backlog
            # Total arrivals - total processed (since collector start)
            total_arrived = len(self._ingest_timestamps)
            total_processed = len(self._throughput_timestamps)
            backlog = max(0, total_arrived - total_processed)

            # Track if backlog is growing
            backlog_growing = backlog > self._previous_backlog
            self._previous_backlog = backlog

            return IngestRateMetrics(
                groups_arrived=groups_arrived,
                groups_per_hour=groups_per_hour,
                groups_processed=groups_processed,
                processed_per_hour=processed_per_hour,
                backlog_groups=backlog,
                backlog_growing=backlog_growing,
                time_window_hours=hours,
            )

    def get_stage_gpu_utilization(self, stage: PipelineStage | None = None) -> dict[str, float]:
        """Get average GPU utilization by stage.

        Parameters
        ----------
        stage : Optional[PipelineStage]
            Specific stage or None for all. (Default value = None)

        """
        with self._lock:
            if stage:
                samples = self._stage_gpu_utilization.get(stage, [])
                if samples:
                    return {stage.value: statistics.mean(samples)}
                return {stage.value: 0.0}

            return {
                s.value: statistics.mean(samples) if samples else 0.0
                for s, samples in self._stage_gpu_utilization.items()
            }

    def get_stage_timing_summary(self, hours: float = 24.0) -> dict[str, dict[str, float]]:
        """Get aggregated timing breakdown by stage.

        Parameters
        ----------
        hours : float
            Time window in hours. (Default value = 24.0)

        """
        cutoff = time.time() - (hours * 3600)
        stage_totals: dict[PipelineStage, StageTimingMetrics] = {}

        with self._lock:
            for job in self._completed_jobs:
                if job.started_at < cutoff:
                    continue

                for stage, timing in job.stage_timings.items():
                    if stage not in stage_totals:
                        stage_totals[stage] = StageTimingMetrics(stage=stage)

                    stage_totals[stage].total_time_s += timing.total_time_s
                    stage_totals[stage].cpu_time_s += timing.cpu_time_s
                    stage_totals[stage].gpu_time_s += timing.gpu_time_s
                    stage_totals[stage].io_time_s += timing.io_time_s
                    stage_totals[stage].idle_time_s += timing.idle_time_s

        return {s.value: t.to_dict() for s, t in stage_totals.items()}

    def get_memory_high_water_marks(self, hours: float = 24.0) -> dict[str, float]:
        """Get memory high-water marks.

        Parameters
        ----------
        hours : float
            Time window in hours. (Default value = 24.0)

        """
        cutoff = time.time() - (hours * 3600)
        peak_ram = 0.0
        peak_gpu = 0.0

        with self._lock:
            for job in self._completed_jobs:
                if job.started_at < cutoff:
                    continue
                peak_ram = max(peak_ram, job.memory.peak_ram_gb)
                peak_gpu = max(peak_gpu, job.memory.peak_gpu_mem_gb)

        return {
            "peak_ram_gb": round(peak_ram, 3),
            "peak_gpu_mem_gb": round(peak_gpu, 3),
        }

    def get_summary(self, hours: float = 24.0) -> dict[str, Any]:
        """Get comprehensive metrics summary.

        Parameters
        ----------
        hours : float
            Time window in hours. (Default value = 24.0)

        """
        throughput = self.get_throughput(hours)
        gpu_util = self.get_stage_gpu_utilization()
        timing = self.get_stage_timing_summary(hours)
        memory = self.get_memory_high_water_marks(hours)

        # Calculate success rate
        cutoff = time.time() - (hours * 3600)
        with self._lock:
            recent_jobs = [j for j in self._completed_jobs if j.started_at >= cutoff]
            success_count = sum(1 for j in recent_jobs if j.success)
            total_count = len(recent_jobs)

        success_rate = success_count / total_count if total_count > 0 else 0.0

        # Get ingest rate metrics
        ingest_rate = self.get_ingest_rate(hours)

        return {
            "time_window_hours": hours,
            "jobs_completed": total_count,
            "success_rate": round(success_rate, 3),
            "throughput": throughput.to_dict(),
            "ingest_rate": ingest_rate.to_dict(),
            "gpu_utilization_by_stage": gpu_util,
            "timing_by_stage": timing,
            "memory_high_water_marks": memory,
            "active_jobs": len(self._active_jobs),
            "generated_at": datetime.utcnow().isoformat() + "Z",
        }

    def get_active_jobs(self) -> list[dict[str, Any]]:
        """Get list of currently active jobs."""
        with self._lock:
            return [
                {
                    "ms_path": job.ms_path,
                    "started_at": datetime.fromtimestamp(job.started_at).isoformat(),
                    "duration_s": job.duration_s,
                    "current_stage": (
                        list(job.stage_timings.keys())[-1].value
                        if job.stage_timings
                        else "starting"
                    ),
                }
                for job in self._active_jobs.values()
            ]

    def get_recent_jobs(self, limit: int = 50, success_only: bool = False) -> list[dict[str, Any]]:
        """Get recent completed jobs.

        Parameters
        ----------
        limit : int
            Maximum jobs to return. (Default value = 50)
        success_only : bool
            Only return successful jobs. (Default value = False)

        """
        with self._lock:
            jobs = list(self._completed_jobs)

        if success_only:
            jobs = [j for j in jobs if j.success]

        # Most recent first
        jobs = sorted(jobs, key=lambda j: j.ended_at or 0, reverse=True)

        return [j.to_dict() for j in jobs[:limit]]


# =============================================================================
# Singleton Access
# =============================================================================

_metrics_collector: PipelineMetricsCollector | None = None
_metrics_lock = Lock()


def get_metrics_collector(
    db_path: str | None = None,
) -> PipelineMetricsCollector:
    """Get or create singleton metrics collector.

    Parameters
    ----------
    db_path : Optional[str]
        Optional database path for persistence (Default value = None)

    Returns
    -------
        None
    """
    global _metrics_collector

    with _metrics_lock:
        if _metrics_collector is None:
            _metrics_collector = PipelineMetricsCollector(db_path=db_path)
        return _metrics_collector


def close_metrics_collector() -> None:
    """Close singleton metrics collector."""
    global _metrics_collector

    with _metrics_lock:
        _metrics_collector = None


# =============================================================================
# Convenience Functions
# =============================================================================


def record_stage_timing(
    ms_path: str,
    stage: str | PipelineStage,
    cpu_time_s: float = 0.0,
    gpu_time_s: float = 0.0,
    io_time_s: float = 0.0,
    total_time_s: float = 0.0,
) -> None:
    """Record timing for a pipeline stage.

        Convenience function for recording stage timing without context manager.

    Parameters
    ----------
    ms_path : str
        Path to MS
    stage : Union[str, PipelineStage]
        Pipeline stage
    cpu_time_s : float
        CPU time in seconds (Default value = 0.0)
    gpu_time_s : float
        GPU time in seconds (Default value = 0.0)
    io_time_s : float
        I/O time in seconds (Default value = 0.0)
    total_time_s : float
        Total time (calculated if not provided) (Default value = 0.0)

    Returns
    -------
        None
    """
    if isinstance(stage, str):
        stage = PipelineStage(stage)

    collector = get_metrics_collector()
    job = collector.get_job(ms_path)

    if not job:
        job = collector.start_job(ms_path)

    timing = job.get_timing(stage)
    timing.cpu_time_s = cpu_time_s
    timing.gpu_time_s = gpu_time_s
    timing.io_time_s = io_time_s
    timing.total_time_s = total_time_s or (cpu_time_s + gpu_time_s + io_time_s)
    timing.idle_time_s = max(0, timing.total_time_s - cpu_time_s - gpu_time_s - io_time_s)


def record_memory_sample(
    ms_path: str,
    ram_gb: float,
    gpu_mem_gb: float = 0.0,
) -> None:
    """Record memory sample for a job.

    Parameters
    ----------
    ms_path : str
        Path to MS
    ram_gb : float
        Current RAM usage in GB
    gpu_mem_gb : float, optional
        Current GPU memory usage in GB (default is 0.0)
    """
    collector = get_metrics_collector()
    job = collector.get_job(ms_path)

    if job:
        job.memory.update(ram_gb, gpu_mem_gb)


# =============================================================================
# Module Exports
# =============================================================================

__all__ = [
    # Enums
    "PipelineStage",
    "ProcessingMode",
    # Data Classes
    "StageTimingMetrics",
    "MemoryMetrics",
    "GPUUtilizationMetrics",
    "ThroughputMetrics",
    "JobMetrics",
    # Context Manager
    "StageContext",
    # Collector
    "PipelineMetricsCollector",
    "get_metrics_collector",
    "close_metrics_collector",
    # Convenience Functions
    "record_stage_timing",
    "record_memory_sample",
]
