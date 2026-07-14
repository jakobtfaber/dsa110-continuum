"""Job/pipeline framework for dsa110_continuum.

This package owns the job and pipeline registries as a first-party continuum
module (distinct from any sibling checkout of the retired contimg package).
"""

from dsa110_continuum.workflow.base import (
    Job,
    JobConfig,
    JobResult,
    NotificationConfig,
    Pipeline,
    RetryBackoff,
    RetryPolicy,
)
from dsa110_continuum.workflow.executor import ExecutionStatus, PipelineExecutor
from dsa110_continuum.workflow.registry import (
    JobRegistry,
    PipelineRegistry,
    get_job_registry,
    get_pipeline_registry,
    register_job,
    register_pipeline,
)

# Module-level singletons (the same objects get_*_registry() return)
job_registry = get_job_registry()
pipeline_registry = get_pipeline_registry()

__all__ = [
    "ExecutionStatus",
    "Job",
    "JobConfig",
    "JobRegistry",
    "JobResult",
    "NotificationConfig",
    "Pipeline",
    "PipelineExecutor",
    "PipelineRegistry",
    "RetryBackoff",
    "RetryPolicy",
    "get_job_registry",
    "get_pipeline_registry",
    "job_registry",
    "pipeline_registry",
    "register_job",
    "register_pipeline",
]
