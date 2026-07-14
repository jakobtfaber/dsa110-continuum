"""
Pipeline framework: Declarative job orchestration for Dagster.

This module provides the base classes for building declarative pipelines
that integrate with Dagster's asset-based orchestration.

Notes
-----
Classes provided:

- ``JobResult``: Result of job execution with outputs
- ``Job``: Abstract base class for pipeline jobs
- ``JobConfig``: Configuration for a job in a pipeline
- ``RetryPolicy``: Retry behavior for failed jobs
- ``Pipeline``: Abstract base class for declarative pipelines
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


# =============================================================================
# Job Result
# =============================================================================


@dataclass
class JobResult:
    """Result of job execution."""

    success: bool
    outputs: dict[str, Any] = field(default_factory=dict)
    message: str = ""
    error: str | None = None

    @classmethod
    def ok(cls, outputs: dict[str, Any] | None = None, message: str = "") -> JobResult:
        """Create a successful result.

        Parameters
        ----------
        outputs : dict[str, Any] or None
            Output data to pass to downstream jobs
            (Default value = None)
        message : str
            Human-readable success message
            (Default value = "")

        """
        return cls(success=True, outputs=outputs or {}, message=message)

    @classmethod
    def fail(cls, error: str) -> JobResult:
        """Create a failed result.

        Parameters
        ----------
        error : str
            Error message describing the failure

        """
        return cls(success=False, error=error, message=error)


# =============================================================================
# Retry Policy
# =============================================================================


class RetryBackoff(str, Enum):
    """Retry backoff strategy."""

    NONE = "none"  # Don't retry
    FIXED = "fixed"  # Fixed delay between retries
    EXPONENTIAL = "exponential"  # Exponential backoff


@dataclass
class RetryPolicy:
    """Configuration for job retry behavior."""

    max_retries: int = 2
    backoff: RetryBackoff = RetryBackoff.EXPONENTIAL
    initial_delay_seconds: float = 2.0
    max_delay_seconds: float = 60.0

    def get_delay(self, attempt: int) -> float:
        """Calculate delay for given attempt number (0-indexed).

        Parameters
        ----------
        attempt : int
            Attempt number (0 = first attempt, no delay)

        """
        if attempt == 0 or self.backoff == RetryBackoff.NONE:
            return 0

        if self.backoff == RetryBackoff.FIXED:
            delay = self.initial_delay_seconds
        else:  # EXPONENTIAL
            delay = self.initial_delay_seconds * (2 ** (attempt - 1))

        return min(delay, self.max_delay_seconds)


# =============================================================================
# Job Base Class
# =============================================================================


class Job(ABC):
    """Abstract base class for pipeline jobs.

        Each job becomes a Dagster op or asset when executed via Pipeline.
        Subclasses implement execute() with their business logic.

        Class Attributes
    ----------------
    job_type : str
        Unique identifier for this job type (used in registry)

    Examples
    --------
        @dataclass
        class MyJob(Job):
        job_type = "my_job"

    input_path: str
    threshold: float = 0.5

        def execute(self) -> JobResult:
        result = process(self.input_path, self.threshold)

    Note: Retry logic is handled by Dagster retry policies at the asset level.
    """

    job_type: str = "base_job"

    @abstractmethod
    def execute(self) -> JobResult:
        """Execute job logic."""
        ...

    def validate(self) -> tuple[bool, str | None]:
        """Validate job parameters before execution.

        Override to add custom validation logic.

        """
        return True, None


# =============================================================================
# Job Configuration
# =============================================================================


@dataclass
class JobConfig:
    """Configuration for a job in a pipeline."""

    job_class: type
    job_id: str
    params: dict[str, Any] = field(default_factory=dict)
    dependencies: list[str] = field(default_factory=list)
    priority: int = 0
    timeout_seconds: int | None = None


# =============================================================================
# Notification Configuration
# =============================================================================


@dataclass
class NotificationConfig:
    """Configuration for pipeline notifications."""

    job_id: str
    channels: list[str] = field(default_factory=lambda: ["email"])
    recipients: list[str] = field(default_factory=list)
    on_failure: bool = True
    on_success: bool = False


# =============================================================================
# Pipeline Base Class
# =============================================================================


class Pipeline(ABC):
    """Abstract base class for declarative pipelines.

        Subclasses define job graphs that integrate with Dagster orchestration.
        Each pipeline has a unique name and optional cron schedule.

        Class Attributes
    ----------------
    pipeline_name : str
        Unique name for this pipeline type
    schedule : Optional[str]
        Optional cron syntax schedule (e.g., "0 3 * * *")

        Example
    -------
        class OnDemandMosaicPipeline(Pipeline):
        pipeline_name = "on_demand_mosaic"
        schedule = None  # On-demand, not scheduled

        def build(self):
        self.add_job(
        MosaicPlanningJob,
        job_id='plan',
        params={'tier': self.tier, ...}
        )
        self.add_job(
        MosaicBuildJob,
        job_id='build',

    Parameters
    ----------
        dependencies :
        plan

    """

    # Class-level configuration (override in subclasses)
    pipeline_name: str = "base_pipeline"
    schedule: str | None = None  # Cron syntax

    def __init__(self, config: Any | None = None):
        """Initialize pipeline.

        Parameters
        ----------
            config :
            Pipeline configuration (application-specific)
        """
        self.config = config
        self.jobs: list[JobConfig] = []
        self._retry_policy = RetryPolicy()
        self._notifications: list[NotificationConfig] = []

        # Build the job graph
        self.build()

    @abstractmethod
    def build(self) -> None:
        """Define the job graph.

        Called during __init__ to construct the pipeline.
        Subclasses should call add_job() here to define jobs and dependencies.

        """
        ...

    def add_job(
        self,
        job_class: type,
        job_id: str,
        params: dict[str, Any] | None = None,
        dependencies: list[str] | None = None,
        priority: int = 0,
        timeout_seconds: int | None = None,
    ) -> None:
        """Add a job to the pipeline.

        Parameters
        ----------
        job_class : type
            The Job subclass to instantiate
        job_id : str
            Unique ID within this pipeline
        params : dict[str, Any] or None
            Parameters for the job
            (Default value = None)
        dependencies : list[str] or None
            List of job_ids this job depends on
            (Default value = None)
        priority : int
            Higher priority
            (Default value = 0)
        timeout_seconds : int or None
            Optional execution timeout
            (Default value = None)

        """
        # Validate dependencies exist
        dep_list = dependencies or []
        for dep in dep_list:
            existing_ids = [j.job_id for j in self.jobs]
            if dep not in existing_ids:
                raise ValueError(
                    f"Job '{job_id}' depends on '{dep}' which hasn't been added yet. "
                    f"Add dependencies before dependents."
                )

        self.jobs.append(
            JobConfig(
                job_class=job_class,
                job_id=job_id,
                params=params or {},
                dependencies=dep_list,
                priority=priority,
                timeout_seconds=timeout_seconds,
            )
        )

    def set_retry_policy(
        self,
        max_retries: int = 2,
        backoff: str = "exponential",
        initial_delay: float = 2.0,
        max_delay: float = 60.0,
    ) -> None:
        """Configure retry behavior for all jobs in this pipeline.

        Parameters
        ----------
        max_retries :
            Maximum retry attempts
        backoff :
            Backoff strategy ("none", "fixed", "exponential")
        initial_delay :
            Initial delay in seconds
        max_delay :
            Maximum delay in seconds
        max_retries : int :
            (Default value = 2)
        backoff : str :
            (Default value = "exponential")
        initial_delay : float :
            (Default value = 2.0)
        max_delay : float :
            (Default value = 60.0)
        max_retries : int :
            (Default value = 2)
        backoff : str :
            (Default value = "exponential")
        initial_delay : float :
            (Default value = 2.0)
        max_delay : float :
            (Default value = 60.0)
        max_retries : int :
            (Default value = 2)
        backoff : str :
            (Default value = "exponential")
        initial_delay : float :
            (Default value = 2.0)
        max_delay : float :
            (Default value = 60.0)
        """
        self._retry_policy = RetryPolicy(
            max_retries=max_retries,
            backoff=RetryBackoff(backoff),
            initial_delay_seconds=initial_delay,
            max_delay_seconds=max_delay,
        )

    def add_notification(
        self,
        on_failure: str,
        channels: list[str],
        recipients: list[str],
        on_success: bool = False,
    ) -> None:
        """Add notification on job events.

        Parameters
        ----------
        on_failure : str
            Job ID to watch for failure
        channels : list[str]
            Notification channels (email, slack, webhook)
        recipients : list[str]
            List of recipients
        on_success : bool
            Also notify on success
            (Default value = False)

        """
        self._notifications.append(
            NotificationConfig(
                job_id=on_failure,
                channels=channels,
                recipients=recipients,
                on_failure=True,
                on_success=on_success,
            )
        )

    @property
    def retry_policy(self) -> RetryPolicy:
        """Get the retry policy for this pipeline."""
        return self._retry_policy

    @property
    def notifications(self) -> list[NotificationConfig]:
        """Get notification configurations for this pipeline."""
        return self._notifications

    def get_job(self, job_id: str) -> JobConfig | None:
        """Get a job by ID.

        Parameters
        ----------
        job_id : str
            The job ID to look up

        """
        for job in self.jobs:
            if job.job_id == job_id:
                return job
        return None

    def get_execution_order(self) -> list[str]:
        """Compute topological order for job execution.

        Uses Kahn's algorithm to compute execution order respecting dependencies.

        """
        # Build adjacency and in-degree
        in_degree = {job.job_id: len(job.dependencies) for job in self.jobs}
        dependents: dict[str, list[str]] = {job.job_id: [] for job in self.jobs}

        for job in self.jobs:
            for dep in job.dependencies:
                dependents[dep].append(job.job_id)

        # Kahn's algorithm
        queue = [job_id for job_id, degree in in_degree.items() if degree == 0]
        order = []

        while queue:
            # Sort by priority (higher first) for tie-breaking
            queue.sort(
                key=lambda jid: next((j.priority for j in self.jobs if j.job_id == jid), 0),
                reverse=True,
            )
            job_id = queue.pop(0)
            order.append(job_id)

            for dependent in dependents[job_id]:
                in_degree[dependent] -= 1
                if in_degree[dependent] == 0:
                    queue.append(dependent)

        if len(order) != len(self.jobs):
            raise ValueError("Circular dependency detected in job graph")

        return order

    def __repr__(self) -> str:
        """String representation."""
        job_ids = [j.job_id for j in self.jobs]
        return f"{self.__class__.__name__}(jobs={job_ids}, schedule={self.schedule})"
