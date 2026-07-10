"""Durable service-level contracts for the static monitor Pages workflow."""

from __future__ import annotations

from pathlib import Path

import yaml

WORKFLOW_PATH = Path(__file__).parent.parent / ".github" / "workflows" / "docs.yml"


def _workflow() -> dict:
    parsed = yaml.safe_load(WORKFLOW_PATH.read_text())
    assert isinstance(parsed, dict)
    return parsed


def _triggers(workflow: dict) -> dict:
    # PyYAML 1.1 interprets the unquoted key `on` as boolean true.
    return workflow.get("on", workflow.get(True, {}))


def _step_script(workflow: dict, job: str, step_name: str) -> str:
    for step in workflow["jobs"][job]["steps"]:
        if step.get("name") == step_name:
            return step.get("run", "")
    raise AssertionError(f"missing {job!r} step {step_name!r}")


def test_monitor_schedule_and_concurrency_recovery_contract() -> None:
    workflow = _workflow()
    triggers = _triggers(workflow)
    assert {row["cron"] for row in triggers["schedule"]} == {"*/15 * * * *"}
    assert workflow["concurrency"]["cancel-in-progress"] is True
    fast_recovery = triggers["workflow_dispatch"]["inputs"]["fast_recovery"]
    assert fast_recovery["type"] == "boolean"
    assert fast_recovery["default"] is True


def test_monitor_host_scans_are_stat_free_bounded_and_timeout() -> None:
    workflow = _workflow()
    job = workflow["jobs"]["build_monitors"]
    assert job["timeout-minutes"] == 10
    script = _step_script(workflow, "build_monitors", "Build monitor output")
    assert "--no-stat" in script
    assert "--no-hdf5-metadata" in script
    assert "MONITOR_POINTING_METADATA_ENABLED" in WORKFLOW_PATH.read_text()
    assert "--metadata-cache" in script
    assert "--metadata-update-limit 100" in script
    assert "--metadata-retry-seconds 3600" in script
    assert "fast_recovery" in WORKFLOW_PATH.read_text()


def test_scanner_changes_trigger_pr_validation() -> None:
    workflow = _workflow()
    triggers = _triggers(workflow)
    assert "tools/dsacamera-monitor/**" in triggers["push"]["paths"]
    assert "tools/dsacamera-monitor/**" in triggers["pull_request"]["paths"]
    script = _step_script(workflow, "pr_render", "Test monitor scanner")
    assert "pytest tools/dsacamera-monitor/tests" in script


def test_monitor_artifact_validator_accepts_stat_free_manifests() -> None:
    workflow = _workflow()
    script = _step_script(workflow, "build_monitors", "Validate monitor artifact contract")
    assert 'get("no_stat") is not True' in script
    assert 'get("hdf5_metadata")' in script
    assert '"metadata_cache"' in script
    assert "must be false" not in script


def test_post_deploy_smoke_requires_fresh_root_and_both_manifests() -> None:
    workflow = _workflow()
    smoke = workflow["jobs"]["smoke_pages"]
    assert smoke["needs"] == "deploy"
    assert smoke["timeout-minutes"] == 10
    script = _step_script(workflow, "smoke_pages", "Verify deployed monitor routes")
    assert "dsacamera-live-monitor/manifest.json" in script
    assert "h17-live-monitor/manifest.json" in script
    assert "1800" in script
    assert "curl" in script
