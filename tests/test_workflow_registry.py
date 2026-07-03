"""The vendored job/pipeline framework must work without dsa110_contimg.

Phase 4 of the contimg-import-retirement migration
(docs/rse/specs/plan-contimg-import-retirement.md). The vendored registry is
a distinct object from the legacy package's registry, so old and new
packages co-load without the double-registration ValueError documented in
CLAUDE.md (proven on H17 in Phase 8).
"""
import pytest


def test_register_and_duplicate_rejection():
    from dsa110_continuum.workflow import Job, JobResult, job_registry, register_job

    class _T(Job):
        job_type = "test_job_xyz"

        def execute(self) -> JobResult:
            return JobResult.ok()

    register_job(_T)
    try:
        assert job_registry.get("test_job_xyz") is _T
        with pytest.raises(ValueError, match="already registered"):
            register_job(_T)
    finally:
        job_registry.unregister("test_job_xyz")
    assert "test_job_xyz" not in job_registry


def test_job_result_ok_fail():
    from dsa110_continuum.workflow import JobResult

    ok = JobResult.ok({"x": 1}, message="done")
    assert ok.success and ok.outputs == {"x": 1} and ok.error is None
    bad = JobResult.fail("boom")
    assert not bad.success and bad.error == "boom"


def test_retry_policy_delays():
    from dsa110_continuum.workflow import RetryBackoff, RetryPolicy

    p = RetryPolicy(max_retries=3, backoff=RetryBackoff.EXPONENTIAL,
                    initial_delay_seconds=2.0, max_delay_seconds=60.0)
    assert p.get_delay(0) == 0
    assert p.get_delay(1) == 2.0
    assert p.get_delay(2) == 4.0
    fixed = RetryPolicy(backoff=RetryBackoff.FIXED, initial_delay_seconds=5.0)
    assert fixed.get_delay(2) == 5.0


def test_calibration_jobs_register_into_own_registry():
    import dsa110_continuum.calibration.jobs  # noqa: F401  (module-scope @register_job)
    from dsa110_continuum.workflow import job_registry

    assert "calibration_solve" in job_registry
    assert "calibration_apply" in job_registry
    assert "calibration_validate" in job_registry


def test_mosaic_jobs_register_into_own_registry():
    import dsa110_continuum.mosaic.jobs  # noqa: F401
    import dsa110_continuum.mosaic.jobs_wsclean  # noqa: F401
    import dsa110_continuum.mosaic.science_jobs  # noqa: F401
    from dsa110_continuum.workflow import job_registry

    assert len(job_registry) > 0


def test_pipeline_topological_order():
    from dsa110_continuum.workflow import Job, JobResult, Pipeline

    class _A(Job):
        job_type = "order_a"

        def execute(self) -> JobResult:
            return JobResult.ok()

    class _B(Job):
        job_type = "order_b"

        def execute(self) -> JobResult:
            return JobResult.ok()

    class _P(Pipeline):
        pipeline_name = "order_test"

        def build(self):
            self.add_job(_A, job_id="a")
            self.add_job(_B, job_id="b", dependencies=["a"])

    p = _P()
    assert p.get_execution_order() == ["a", "b"]


def test_structured_logging_helpers():
    from dsa110_continuum.workflow.structured_logging import (
        get_logger,
        set_correlation_id,
    )

    cid = set_correlation_id("test-corr-id")
    assert cid == "test-corr-id"
    logger = get_logger("test.workflow")
    logger.info("event_happened", detail=1)  # must not raise


def test_record_ese_detection_callable():
    from dsa110_continuum.workflow.metrics import record_ese_detection

    record_ese_detection(duration=0.5, candidates=2)  # must not raise
