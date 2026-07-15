"""Dagster definitions for read-only H17 continuum observability."""

from __future__ import annotations

from datetime import datetime, timezone

from dagster import (
    AssetSelection,
    AssetSpec,
    DefaultSensorStatus,
    Definitions,
    MaterializeResult,
    MetadataValue,
    RunRequest,
    define_asset_job,
    multi_asset,
    sensor,
)
from dsa110_continuum.observability.hour_state import HourStateConfig, collect_hour_state
from dsa110_continuum.observability.mosaic_preview import (
    mosaic_preview_markdown,
    sync_mosaic_thumb,
)

GROUP = "hour_11_observability"
ASSET_NAMES = (
    "campaign_runtime",
    "measurement_sets_tiles_mosaic",
    "calibration_tables",
    "incoming_hdf5",
    "storage_capacity",
    "hour_11_continuum_summary",
)


def _spec(name: str, description: str) -> AssetSpec:
    return AssetSpec(
        name,
        group_name=GROUP,
        description=description,
        tags={"dsa110/view": "read-only", "dsa110/product": "hourly-epoch"},
    )


def _readiness_markdown(state: dict) -> str:
    labels = {
        "measurement_sets_present": "Measurement Sets",
        "calibration_present": "bandpass and gain calibration",
        "tiles_present": "tile images",
        "mosaic_present": "hourly-epoch mosaic",
        "incoming_hdf5_present": "incoming HDF5",
        "campaign_process_visible": "campaign process",
        "campaign_pid_visible": "recorded campaign PID",
    }
    rows = [
        f"# {state['date']} UTC hour {state['hour']:02d}",
        "",
        f"**Campaign state:** `{state['summary']['campaign_state']}`",
        "",
        "| Check | State |",
        "| --- | --- |",
    ]
    rows.extend(
        f"| {label} | {'present' if state['summary'][key] else 'not visible'} |"
        for key, label in labels.items()
    )
    rows.extend(
        [
            "",
            "This snapshot is read-only; `not visible` does not launch or retry pipeline work.",
        ]
    )
    preview = state.get("mosaic_preview")
    if preview:
        mosaic_path = (state.get("mosaic") or {}).get("path")
        rows.extend(["", mosaic_preview_markdown(preview, mosaic_path=mosaic_path)])
    return "\n".join(rows)


@multi_asset(
    specs=[
        _spec("campaign_runtime", "Campaign processes, recorded PIDs, latest log, and log tail."),
        _spec(
            "measurement_sets_tiles_mosaic",
            "Selected-hour Measurement Sets, tile images, hourly-epoch mosaic, and run records.",
        ),
        _spec("calibration_tables", "Selected-hour bandpass and gain table inventory."),
        _spec("incoming_hdf5", "Selected-hour slow-vis HDF5 inventory under the incoming root."),
        _spec("storage_capacity", "Capacity, use, and free space on configured H17 volumes."),
        _spec(
            "hour_11_continuum_summary",
            "Operator checklist for data, calibration, products, and campaign visibility.",
        ),
    ]
)
def observe_hour_11_continuum():
    """Record external state as Dagster asset metadata without running science code."""
    config = HourStateConfig()
    state = collect_hour_state(config)
    mosaic_path = (state.get("mosaic") or {}).get("path")
    preview = sync_mosaic_thumb(config, fits_path=mosaic_path)
    state["mosaic_preview"] = preview
    campaign = state["campaign"]
    log = campaign["log"]
    mosaic_meta = {
        "mosaic": MetadataValue.json(state["mosaic"]),
        "mosaic_preview": MetadataValue.md(
            mosaic_preview_markdown(preview, mosaic_path=mosaic_path)
        ),
        "qa_thumbnail": MetadataValue.url(preview["qa_thumb_url"]),
        "dagster_mosaic_page": MetadataValue.url(preview["dagster_page_url"]),
    }
    if mosaic_path:
        mosaic_meta["mosaic_fits"] = MetadataValue.path(mosaic_path)
    yield MaterializeResult(
        asset_key="campaign_runtime",
        metadata={
            "date": state["date"],
            "hour": state["hour"],
            "campaign_state": campaign["state"],
            "processes": MetadataValue.json(campaign["processes"]),
            "pid_hints": MetadataValue.json(campaign["pid_hints"]),
            "latest_log": log["path"] if log else "not found",
            "log_tail": MetadataValue.md("```text\n" + "\n".join(campaign["log_tail"]) + "\n```"),
        },
    )
    yield MaterializeResult(
        asset_key="measurement_sets_tiles_mosaic",
        metadata={
            "measurement_set_count": state["measurement_sets"]["count"],
            "measurement_sets": MetadataValue.json(state["measurement_sets"]["paths"]),
            "tile_count": state["tiles"]["count"],
            "latest_tile": MetadataValue.json(state["tiles"]["latest"]),
            "run_products": MetadataValue.json(state["run_products"]),
            **mosaic_meta,
        },
    )
    yield MaterializeResult(
        asset_key="calibration_tables",
        metadata={
            "bandpass_count": state["calibration"]["bandpass_count"],
            "gain_count": state["calibration"]["gain_count"],
            "tables": MetadataValue.json(state["calibration"]),
        },
    )
    yield MaterializeResult(
        asset_key="incoming_hdf5",
        metadata={
            "hdf5_count": state["incoming"]["count"],
            "directory": state["incoming"]["directory"],
            "latest": MetadataValue.json(state["incoming"]["latest"]),
        },
    )
    yield MaterializeResult(
        asset_key="storage_capacity",
        metadata={"volumes": MetadataValue.json(state["disks"])},
    )
    yield MaterializeResult(
        asset_key="hour_11_continuum_summary",
        metadata={
            "observing_date": state["date"],
            "utc_hour": state["hour"],
            "generated_at": state["generated_at"],
            "operator_checklist": MetadataValue.md(_readiness_markdown(state)),
            "readiness": MetadataValue.json(state["summary"]),
            "mosaic_preview": MetadataValue.md(
                mosaic_preview_markdown(preview, mosaic_path=mosaic_path)
            ),
            "qa_thumbnail": MetadataValue.url(preview["qa_thumb_url"]),
            "dagster_mosaic_page": MetadataValue.url(preview["dagster_page_url"]),
        },
    )


refresh_hour_11_observability = define_asset_job(
    "refresh_hour_11_observability",
    selection=AssetSelection.groups(GROUP),
    description="Refresh read-only H17 campaign and product metadata.",
)


@sensor(
    job=refresh_hour_11_observability,
    minimum_interval_seconds=60,
    default_status=DefaultSensorStatus.RUNNING,
)
def refresh_hour_11_observability_sensor():
    """Refresh the operator snapshot once per UTC minute."""
    minute = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    return RunRequest(run_key=minute.isoformat())


defs = Definitions(
    assets=[observe_hour_11_continuum],
    jobs=[refresh_hour_11_observability],
    sensors=[refresh_hour_11_observability_sensor],
)
