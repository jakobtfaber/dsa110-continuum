"""
Jobs for the rigorous Science Mosaic workflow.

These jobs bridge the legacy Pipeline/Job architecture with the new
Dagster-based science workflow.
"""

from __future__ import annotations

import contextlib
import json
import logging
import sqlite3
import time
from dataclasses import dataclass

from dsa110_continuum.workflow import Job, JobResult, register_job

from dsa110_continuum.mosaic.jobs import MosaicJobConfig

logger = logging.getLogger(__name__)


@register_job
@dataclass
class SciencePlanningJob(Job):
    """Create a mosaic plan for the Science workflow.

    Unlike MosaicPlanningJob, this does NOT query existing images,
    as the Science workflow generates new images from MS data.
    """

    job_type: str = "science_planning"

    start_time: int = 0
    end_time: int = 0
    tier: str = "science"
    mosaic_name: str = ""
    config: MosaicJobConfig | None = None

    def validate(self) -> tuple[bool, str | None]:
        """Validate job parameters."""
        if not self.config:
            return False, "No configuration provided"
        if not self.mosaic_name:
            return False, "No mosaic name provided"
        return True, None

    def execute(self) -> JobResult:
        """Execute the planning job."""
        from dsa110_continuum.mosaic.schema import ensure_mosaic_tables

        is_valid, error = self.validate()
        if not is_valid:
            return JobResult.fail(error or "Validation failed")

        logger.info(f"Planning Science mosaic '{self.mosaic_name}'")

        try:
            with contextlib.closing(sqlite3.connect(str(self.config.database_path))) as conn:
                conn.row_factory = sqlite3.Row

                ensure_mosaic_tables(conn)

                # Insert plan with empty image_ids
                # The workflow will populate n_images later
                cursor = conn.execute(
                    """
                    INSERT INTO mosaic_plans
                        (name, tier, start_time, end_time, image_ids, n_images,
                         created_at, status)
                    VALUES (?, ?, ?, ?, ?, ?, ?, 'pending')
                    """,
                    (
                        self.mosaic_name,
                        self.tier,
                        self.start_time,
                        self.end_time,
                        json.dumps([]),  # No images yet
                        0,
                        int(time.time()),
                    ),
                )
                plan_id = cursor.lastrowid
                conn.commit()

            logger.info(f"Created pending Science plan {plan_id}")

            return JobResult.ok(
                outputs={
                    "plan_id": plan_id,
                    "image_ids": [],
                    "n_images": 0,
                },
                message="Created pending Science plan",
            )

        except Exception as e:
            logger.exception(f"Science planning failed: {e}")
            return JobResult.fail(str(e))


@register_job
@dataclass
class ScienceMosaicBridgeJob(Job):
    """Bridge job to execute the Dagster Science Mosaic workflow."""

    job_type: str = "science_mosaic_bridge"

    plan_id: int = 0
    config: MosaicJobConfig | None = None

    # Need access to start/end time/name to configure the Dagster job
    # Passed from pipeline params
    start_time: int = 0
    end_time: int = 0
    mosaic_name: str = ""

    def validate(self) -> tuple[bool, str | None]:
        """Validate job parameters."""
        if not self.config:
            return False, "No configuration provided"
        if not self.plan_id:
            return False, "No plan_id provided"
        return True, None

    def execute(self) -> JobResult:
        """Execute the (retired) Dagster workflow."""
        is_valid, error = self.validate()
        if not is_valid:
            return JobResult.fail(error or "Validation failed")

        raise RuntimeError(
            "Science-mosaic Dagster bridge retired with dsa110_contimg; "
            "run the visibility-domain coadd via scripts/batch_pipeline.py "
            "(see docs/skills/mosaicking.md)."
        )

    def _update_status(self, status: str) -> None:
        """Update plan status in database."""
        try:
            with contextlib.closing(sqlite3.connect(str(self.config.database_path))) as conn:
                conn.execute(
                    "UPDATE mosaic_plans SET status = ? WHERE id = ?", (status, self.plan_id)
                )
                conn.commit()
        except Exception:
            pass
