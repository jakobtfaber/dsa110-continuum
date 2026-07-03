# Vendored from dsa110-contimg @ /data/dsa110-contimg/backend/src (H17), 2026-07-03,
# workflow/pipeline/executor.py, as part of the contimg-import-retirement migration
# (docs/rse/specs/plan-contimg-import-retirement.md, Phase 4).
"""
Pipeline executor: Execute pipelines via Dagster orchestration.

This module bridges the Pipeline abstraction to Dagster's asset system,
handling:
- Parameter resolution (including ${job.output} references)
- Stage execution with dependencies
- Status queries (read-only from legacy DB)

Simplified in Phase 6 (January 2026):
- Removed custom circuit breaker logic (use Dagster failure hooks instead)
- Removed custom retry logic for Dagster mode (use Dagster RetryPolicy instead)
- Removed direct database tracking (handled by Dagster hooks)
- Removed custom event emission (handled by Dagster events)
- Removed custom task queue logic (delegate to Dagster)
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# aiosqlite is imported lazily inside the status-query methods so the
# executor (and dsa110_continuum.workflow) imports on hosts without it.

from .base import JobResult, Pipeline, RetryPolicy

logger = logging.getLogger(__name__)


# =============================================================================
# Execution Status
# =============================================================================


@dataclass
class ExecutionStatus:
    """Status of a pipeline execution."""

    execution_id: str
    pipeline_name: str
    status: str
    started_at: float
    completed_at: float | None = None
    error: str | None = None
    jobs: list[dict[str, Any]] = field(default_factory=list)


# =============================================================================
# Pipeline Executor
# =============================================================================


class PipelineExecutor:
    """Executes Pipeline job graphs.

    Note: This class is now a lightweight wrapper. For production usage,
    pipelines should be executed via Dagster directly. This executor
    provides a compatibility layer for existing scripts.
    """

    def __init__(self, db_path: Path):
        """Initialize executor.

        Parameters
        ----------
        db_path : str or Path
            Path to the pipeline database (for status queries)
        """
        self.db_path = db_path

    async def execute(
        self,
        pipeline: Pipeline,
        *,
        execution_id: str | None = None,
        **kwargs,
    ) -> str:
        """Execute a pipeline in-process.

        Parameters
        ----------
        pipeline : Pipeline
            Pipeline instance to execute
        execution_id : optional
            Optional override for execution_id

        Returns
        -------
            execution_id
            Unique ID for this pipeline run
        """
        # Generate execution ID (optionally overridden)
        execution_id = execution_id or f"{pipeline.pipeline_name}_{uuid.uuid4().hex[:12]}"

        logger.info(f"Starting pipeline execution: {execution_id}")

        try:
            await self._execute_jobs_sync(pipeline, execution_id)
            logger.info(f"Pipeline {execution_id} completed successfully")
        except Exception as e:
            logger.exception(f"Pipeline execution failed: {e}")
            raise

        return execution_id


    def _get_max_parallel_jobs(self, pipeline: Pipeline) -> int | None:
        """Determine max parallel jobs from pipeline config if provided."""
        cfg = pipeline.config
        if cfg is None:
            return None

        max_jobs: Any | None = None
        if isinstance(cfg, dict):
            max_jobs = cfg.get("max_parallel_jobs") or cfg.get("max_concurrent_jobs")
        else:
            max_jobs = getattr(cfg, "max_parallel_jobs", None) or getattr(
                cfg, "max_concurrent_jobs", None
            )

        try:
            max_jobs_int = int(max_jobs)
            # 0 or negative implies unlimited, so return None
            return max_jobs_int if max_jobs_int > 0 else None
        except Exception:
            return None

    async def _execute_jobs_sync(
        self,
        pipeline: Pipeline,
        execution_id: str,
    ) -> None:
        """Execute jobs in-process."""
        execution_order = pipeline.get_execution_order()
        jobs_by_id = {job.job_id: job for job in pipeline.jobs}
        in_degree = {job_id: len(cfg.dependencies) for job_id, cfg in jobs_by_id.items()}
        dependents: dict[str, list[str]] = {job_id: [] for job_id in jobs_by_id}

        for job_id, cfg in jobs_by_id.items():
            for dep in cfg.dependencies:
                dependents[dep].append(job_id)

        ready: list[str] = [job_id for job_id, degree in in_degree.items() if degree == 0]
        results: dict[str, JobResult] = {}
        completed: set[str] = set()
        max_parallel = self._get_max_parallel_jobs(pipeline)

        async def run_job(job_id: str) -> tuple[str, JobResult]:
            cfg = jobs_by_id[job_id]
            try:
                resolved_params = self._resolve_params(cfg.params, results)
                result = await self._execute_with_retry(
                    cfg,
                    resolved_params,
                    pipeline.retry_policy,
                    pipeline.config,
                )
            except Exception as e:
                logger.exception(f"Job '{job_id}' raised during execution: {e}")
                result = JobResult.fail(str(e))
            return job_id, result

        running = set()

        while ready or running:
            # Schedule new jobs if slots available
            ready.sort(key=lambda jid: jobs_by_id[jid].priority, reverse=True)

            while ready and (max_parallel is None or len(running) < max_parallel):
                job_id = ready.pop(0)
                task = asyncio.create_task(run_job(job_id))
                running.add(task)

            if not running:
                break

            # Wait for any job to complete
            done, pending = await asyncio.wait(
                running, return_when=asyncio.FIRST_COMPLETED
            )
            running = pending

            for task in done:
                job_id, result = task.result()
                results[job_id] = result

                if result.success:
                    completed.add(job_id)
                    for dependent in dependents.get(job_id, []):
                        in_degree[dependent] -= 1
                        if in_degree[dependent] == 0 and dependent not in completed:
                            ready.append(dependent)
                else:
                    # Cancel pending tasks on failure
                    for t in running:
                        t.cancel()
                    if running:
                        await asyncio.wait(running)
                    raise RuntimeError(f"Job {job_id} failed: {result.error}")

    async def _execute_with_retry(
        self,
        job_config: Any,
        params: dict[str, Any],
        retry_policy: RetryPolicy,
        pipeline_config: Any,
    ) -> JobResult:
        """Execute a job with retry policy."""
        last_result: JobResult | None = None

        for attempt in range(retry_policy.max_retries + 1):
            try:
                delay = retry_policy.get_delay(attempt)
                if delay > 0:
                    logger.info(
                        f"Retrying job '{job_config.job_id}' in {delay:.1f}s "
                        f"(attempt {attempt + 1}/{retry_policy.max_retries + 1})"
                    )
                    await asyncio.sleep(delay)

                # Instantiate job with config
                job_kwargs = dict(params)
                job_kwargs["config"] = pipeline_config
                job = job_config.job_class(**job_kwargs)

                # Validate
                is_valid, error = job.validate()
                if not is_valid:
                    return JobResult.fail(f"Validation failed: {error}")

                # Execute (wrap in thread for blocking jobs)
                execute_future = asyncio.to_thread(job.execute)
                if job_config.timeout_seconds:
                    try:
                        result = await asyncio.wait_for(
                            execute_future,
                            timeout=job_config.timeout_seconds,
                        )
                    except TimeoutError:
                        raise TimeoutError(
                            f"Job '{job_config.job_id}' timed out after "
                            f"{job_config.timeout_seconds}s"
                        )
                else:
                    result = await execute_future

                if result.success:
                    return result

                last_result = result
                logger.warning(f"Job '{job_config.job_id}' failed: {result.error}")

            except Exception as e:
                logger.exception(f"Job '{job_config.job_id}' raised exception: {e}")
                last_result = JobResult.fail(str(e))

        return last_result or JobResult.fail("Unknown error")

    def _resolve_params(
        self,
        params: dict[str, Any],
        results: dict[str, JobResult],
    ) -> dict[str, Any]:
        """Resolve parameter references using completed job results."""
        resolved = {}
        for key, value in params.items():
            if isinstance(value, str) and value.startswith("${") and value.endswith("}"):
                # Parse reference: ${job_id.output_key}
                ref = value[2:-1]
                if "." in ref:
                    job_id, output_key = ref.split(".", 1)
                    if job_id in results and results[job_id].success:
                        resolved[key] = results[job_id].outputs.get(output_key)
                    else:
                        raise ValueError(f"Cannot resolve '{value}': job '{job_id}' not completed")
                else:
                    raise ValueError(f"Invalid reference format: '{value}'")
            else:
                resolved[key] = value
        return resolved

    async def get_status(self, execution_id: str) -> ExecutionStatus:
        """Get pipeline execution status from legacy DB (read-only)."""
        import aiosqlite
        if not self.db_path.exists():
             raise ValueError(f"Database not found: {self.db_path}")

        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row

            # Get execution record
            async with db.execute(
                "SELECT * FROM pipeline_executions WHERE execution_id = ?",
                (execution_id,),
            ) as cursor:
                row = await cursor.fetchone()

            if not row:
                raise ValueError(f"Execution {execution_id} not found")

            # Get job records
            async with db.execute(
                """
                SELECT * FROM pipeline_jobs
                WHERE execution_id = ?
                ORDER BY created_at
                """,
                (execution_id,),
            ) as cursor:
                jobs = [dict(r) for r in await cursor.fetchall()]

            return ExecutionStatus(
                execution_id=row["execution_id"],
                pipeline_name=row["pipeline_name"],
                status=row["status"],
                started_at=row["started_at"],
                completed_at=row["completed_at"],
                error=row["error"],
                jobs=jobs,
            )

    async def list_executions(
        self,
        pipeline_name: str | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> list[ExecutionStatus]:
        """List pipeline executions from legacy DB (read-only)."""
        import aiosqlite
        if not self.db_path.exists():
             return []

        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row

            query = "SELECT * FROM pipeline_executions WHERE 1=1"
            params: list[Any] = []

            if pipeline_name:
                query += " AND pipeline_name = ?"
                params.append(pipeline_name)
            if status:
                query += " AND status = ?"
                params.append(status)

            query += " ORDER BY started_at DESC LIMIT ?"
            params.append(limit)

            async with db.execute(query, params) as cursor:
                rows = await cursor.fetchall()

            return [
                ExecutionStatus(
                    execution_id=row["execution_id"],
                    pipeline_name=row["pipeline_name"],
                    status=row["status"],
                    started_at=row["started_at"],
                    completed_at=row["completed_at"],
                    error=row["error"],
                )
                for row in rows
            ]
