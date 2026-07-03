# Vendored from dsa110-contimg @ /data/dsa110-contimg/backend/src (H17), 2026-07-03,
# workflow/pipeline/registry.py, as part of the contimg-import-retirement migration
# (docs/rse/specs/plan-contimg-import-retirement.md, Phase 4).
"""
Pipeline and job registry: Type lookup and discovery.

This module provides:
1. Job type registry for Dagster asset/op routing
2. Pipeline discovery for scheduler registration

Notes
-----
Classes provided:

- ``JobRegistry``: Registry for job types
- ``PipelineRegistry``: Registry for pipeline classes
"""

from __future__ import annotations

import logging
from typing import TypeVar

from .base import Job, Pipeline

_JobT = TypeVar("_JobT", bound=Job)
_PipelineT = TypeVar("_PipelineT", bound=Pipeline)

logger = logging.getLogger(__name__)


# =============================================================================
# Job Registry
# =============================================================================


class JobRegistry:
    """Registry for job types.

        Maps job_type strings to Job classes for Dagster asset/op routing.

        Example
    -------
        registry = JobRegistry()
        registry.register(MosaicPlanningJob)
        registry.register(MosaicBuildJob)

        job_class = registry.get("mosaic_planning")
        job = job_class(**params)
        result = job.execute()

    Parameters
    ----------
        None

    Returns
    -------
        None
    """

    def __init__(self):
        """Initialize empty registry."""
        self._jobs: dict[str, type[Job]] = {}

    def register(self, job_class: type[Job]) -> None:
        """Register a job class.

        Parameters
        ----------
        job_class :
            Job subclass to register
        job_class: Type[Job] :


        Raises
        ------
        ValueError
            If job_type already registered

        """
        job_type = job_class.job_type
        if job_type in self._jobs:
            existing = self._jobs[job_type].__name__
            raise ValueError(f"Job type '{job_type}' already registered by {existing}")

        self._jobs[job_type] = job_class
        logger.debug(f"Registered job type: {job_type} -> {job_class.__name__}")

    def unregister(self, job_type: str) -> None:
        """Unregister a job type.

        Parameters
        ----------
        job_type :
            Job type to remove
        """
        if job_type in self._jobs:
            del self._jobs[job_type]

    def get(self, job_type: str) -> type[Job] | None:
        """Get job class by type.

        Parameters
        ----------
        job_type :
            Job type string

        Returns
        -------
            Job class or None if not found

        """
        return self._jobs.get(job_type)

    def get_or_raise(self, job_type: str) -> type[Job]:
        """Get job class by type or raise.

        Parameters
        ----------
        job_type :
            Job type string

        Returns
        -------
            Job class

        Raises
        ------
        ValueError
            If job type not registered

        """
        job_class = self.get(job_type)
        if job_class is None:
            available = ", ".join(sorted(self._jobs.keys()))
            raise ValueError(f"Unknown job type: '{job_type}'. Available: {available or '(none)'}")
        return job_class

    def list_types(self) -> list[str]:
        """List all registered job types.

        Returns
        -------
            List of job type strings

        """
        return sorted(self._jobs.keys())

    def __contains__(self, job_type: str) -> bool:
        """Check if job type is registered."""
        return job_type in self._jobs

    def __len__(self) -> int:
        """Return number of registered job types."""
        return len(self._jobs)


# =============================================================================
# Pipeline Registry
# =============================================================================


class PipelineRegistry:
    """Registry for pipeline classes.

        Maps pipeline_name strings to Pipeline classes for discovery and scheduling.

        Example
    -------
        registry = PipelineRegistry()
        registry.register(OnDemandMosaicPipeline)
        registry.register(HousekeepingPipeline)

        # Get scheduled pipelines
        scheduled = registry.get_scheduled()
        for pipeline_class in scheduled:
        scheduler.register(pipeline_class)

    Parameters
    ----------
        None

    Returns
    -------
        None
    """

    def __init__(self):
        """Initialize empty registry."""
        self._pipelines: dict[str, type[Pipeline]] = {}

    def register(self, pipeline_class: type[Pipeline]) -> None:
        """Register a pipeline class.

        Parameters
        ----------
        pipeline_class :
            Pipeline subclass to register
        pipeline_class: Type[Pipeline] :


        Raises
        ------
        ValueError
            If pipeline_name already registered

        """
        name = pipeline_class.pipeline_name
        if name in self._pipelines:
            existing = self._pipelines[name].__name__
            raise ValueError(f"Pipeline name '{name}' already registered by {existing}")

        self._pipelines[name] = pipeline_class
        logger.debug(f"Registered pipeline: {name} -> {pipeline_class.__name__}")

    def unregister(self, pipeline_name: str) -> None:
        """Unregister a pipeline.

        Parameters
        ----------
        pipeline_name :
            Pipeline name to remove
        """
        if pipeline_name in self._pipelines:
            del self._pipelines[pipeline_name]

    def get(self, pipeline_name: str) -> type[Pipeline] | None:
        """Get pipeline class by name.

        Parameters
        ----------
        pipeline_name :
            Pipeline name string

        Returns
        -------
            Pipeline class or None if not found

        """
        return self._pipelines.get(pipeline_name)

    def get_or_raise(self, pipeline_name: str) -> type[Pipeline]:
        """Get pipeline class by name or raise.

        Parameters
        ----------
        pipeline_name :
            Pipeline name string

        Returns
        -------
            Pipeline class

        Raises
        ------
        ValueError
            If pipeline not registered

        """
        pipeline_class = self.get(pipeline_name)
        if pipeline_class is None:
            available = ", ".join(sorted(self._pipelines.keys()))
            raise ValueError(
                f"Unknown pipeline: '{pipeline_name}'. Available: {available or '(none)'}"
            )
        return pipeline_class

    def get_scheduled(self) -> list[type[Pipeline]]:
        """Get all pipelines with schedules.

        Returns
        -------
            List of pipeline classes that have schedule defined

        """
        return [p for p in self._pipelines.values() if p.schedule is not None]

    def get_on_demand(self) -> list[type[Pipeline]]:
        """Get all pipelines without schedules (on-demand only).

        Returns
        -------
            List of pipeline classes that don't have schedule

        """
        return [p for p in self._pipelines.values() if p.schedule is None]

    def list_names(self) -> list[str]:
        """List all registered pipeline names.

        Returns
        -------
            List of pipeline name strings

        """
        return sorted(self._pipelines.keys())

    def __contains__(self, pipeline_name: str) -> bool:
        """Check if pipeline is registered."""
        return pipeline_name in self._pipelines

    def __len__(self) -> int:
        """Return number of registered pipelines."""
        return len(self._pipelines)


# =============================================================================
# Global Registries
# =============================================================================

# Singleton registries for application-wide use
_job_registry: JobRegistry | None = None
_pipeline_registry: PipelineRegistry | None = None


def get_job_registry() -> JobRegistry:
    """Get the global job registry.

    Returns
    -------
        JobRegistry singleton

    """
    global _job_registry
    if _job_registry is None:
        _job_registry = JobRegistry()
    return _job_registry


def get_pipeline_registry() -> PipelineRegistry:
    """Get the global pipeline registry.

    Returns
    -------
        PipelineRegistry singleton

    """
    global _pipeline_registry
    if _pipeline_registry is None:
        _pipeline_registry = PipelineRegistry()
    return _pipeline_registry


def register_job(job_class: type[_JobT]) -> type[_JobT]:
    """Decorator to register a job class.

        Example
    -------
        @register_job
        @dataclass
        class MyJob(Job):
        job_type = "my_job"
        ...

    Parameters
    ----------
    job_class : Type[Job]
        The job class to register.

    Returns
    -------
        type[_JobT]
            The same job class (preserves type for dataclass fields).
    """
    get_job_registry().register(job_class)
    return job_class


def register_pipeline(pipeline_class: type[_PipelineT]) -> type[_PipelineT]:
    """Decorator to register a pipeline class.

        Example
    -------
        @register_pipeline
        class MyPipeline(Pipeline):
        pipeline_name = "my_pipeline"
        schedule = "0 3 * * *"
        ...

    Parameters
    ----------
    pipeline_class : Type[Pipeline]
        The pipeline class to register.

    Returns
    -------
        type[_PipelineT]
            The same pipeline class (preserves type).
    """
    get_pipeline_registry().register(pipeline_class)
    return pipeline_class


def reset_registries() -> None:
    """Reset global registries (for testing)."""
    global _job_registry, _pipeline_registry
    _job_registry = None
    _pipeline_registry = None
