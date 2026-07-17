"""RFI mode and fail-closed chain tests."""

import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from dsa110_continuum.calibration.flagging_rfi import flag_rfi, flag_rfi_aoflagger

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from mosaic_day import RFI_MODES, TileConfig, resolve_rfi_mode


def _config(rfi_mode="full") -> TileConfig:
    return TileConfig(
        date="2026-07-13",
        ms_dir="/ms",
        image_dir="/images",
        mosaic_out="/images/mosaic.fits",
        products_dir="/products",
        bp_table="/ms/test.b",
        g_table="/ms/test.g",
        rfi_mode=rfi_mode,
    )


@pytest.mark.parametrize("mode", RFI_MODES)
def test_tile_config_accepts_explicit_modes(mode):
    cfg = _config(mode)
    assert cfg.rfi_mode == mode


def test_conditional_is_default_and_full_is_opt_in():
    assert resolve_rfi_mode(None, False) == "conditional"
    assert resolve_rfi_mode("full", False) == "full"
    assert resolve_rfi_mode("conditional", False) == "conditional"
    assert resolve_rfi_mode("cflag", False) == "cflag"


def test_execute_rfi_policy_cflag_skips_aoflagger(tmp_path, monkeypatch):
    import mosaic_day
    from dsa110_continuum.calibration import flagging

    calls: list[str] = []

    monkeypatch.setattr(flagging, "flag_zeros", lambda *args, **kwargs: calls.append("zeros"))
    monkeypatch.setattr(
        flagging,
        "flag_autocorrelations",
        lambda *args, **kwargs: calls.append("autocorr"),
    )
    monkeypatch.setattr(
        flagging,
        "flag_clip_amplitude",
        lambda *args, **kwargs: calls.append("clip"),
    )
    monkeypatch.setattr(
        flagging,
        "detect_and_flag_dead_antennas",
        lambda *args, **kwargs: calls.append("dead"),
    )

    def fake_cflag(ms, **kwargs):
        assert kwargs == {}
        calls.append("cflag")
        return {"timings": {}}

    def fake_aoflagger(*args, **kwargs):
        calls.append("aoflagger")
        raise AssertionError("AOFlagger must not run for rfi-mode cflag")

    monkeypatch.setattr(
        "dsa110_continuum.calibration.flagging_cflag.flag_rfi_cflag",
        fake_cflag,
    )
    monkeypatch.setattr(
        "dsa110_continuum.calibration.flagging_rfi.flag_rfi",
        fake_aoflagger,
    )
    mosaic_day._execute_rfi_policy(str(tmp_path / "fake.ms"), "cflag", tag="test")
    assert calls == ["zeros", "autocorr", "clip", "dead", "cflag"]


def test_execute_rfi_policy_off_does_no_work(monkeypatch):
    from dsa110_continuum.calibration import flagging

    monkeypatch.setattr(
        flagging,
        "flag_zeros",
        lambda *args, **kwargs: pytest.fail("Stage 0 must not run in off mode"),
    )
    flagging.execute_rfi_policy("fake.ms", "off", tag="test")


def test_deprecated_no_rfi_alias_means_off_only():
    assert resolve_rfi_mode(None, True) == "off"
    assert resolve_rfi_mode("off", True) == "off"
    with pytest.raises(ValueError, match="deprecated alias"):
        resolve_rfi_mode("conditional", True)


def test_batch_cli_rejects_conflicting_rfi_flags(monkeypatch):
    import batch_pipeline

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "batch_pipeline.py",
            "--rfi-mode",
            "conditional",
            "--no-rfi-flagging",
        ],
    )

    with pytest.raises(SystemExit) as error:
        batch_pipeline.main()

    assert error.value.code == 2


def _run_flag_rfi_with(stage2=None, stage3=None):
    with (
        patch("dsa110_continuum.calibration.flagging_rfi.require_headless"),
        patch("dsa110_continuum.utils.ms_permissions.ensure_ms_writable"),
        patch("dsa110_continuum.calibration.flagging_rfi.flag_rfi_aoflagger"),
        patch(
            "dsa110_continuum.calibration.flagging_rfi.flag_residual_rfi_clip",
            side_effect=stage2,
        ),
        patch(
            "dsa110_continuum.calibration.flagging_rfi.flag_extend",
            side_effect=stage3,
        ),
        patch("dsa110_continuum.calibration.flagging_rfi.time.sleep"),
    ):
        flag_rfi(str(Path("fake.ms")), backend="aoflagger", fail_closed=True)


def test_stage2_failure_is_fatal_for_science_chain():
    with pytest.raises(RuntimeError, match="stage 2"):
        _run_flag_rfi_with(stage2=RuntimeError("stage 2 failed"))


def test_stage3_failure_is_fatal_for_science_chain():
    with pytest.raises(RuntimeError, match="stage 3"):
        _run_flag_rfi_with(stage3=RuntimeError("stage 3 failed"))


def test_aoflagger_uses_tuned_default_strategy_when_unset(monkeypatch):
    monkeypatch.delenv("CONTIMG_AOFLAGGER_STRATEGY", raising=False)
    with (
        patch("dsa110_continuum.calibration.flagging_rfi.require_headless"),
        patch(
            "dsa110_continuum.calibration.flagging_rfi.shutil.which",
            side_effect=lambda name: "/usr/bin/aoflagger" if name == "aoflagger" else None,
        ),
        patch(
            "dsa110_continuum.calibration.flagging_rfi._get_default_aoflagger_strategy",
            return_value="/data/dsa110-contimg/config/dsa110-default.lua",
        ),
        patch("dsa110_continuum.calibration.flagging_rfi.subprocess.run") as run,
    ):
        flag_rfi_aoflagger("fake.ms")

    command = run.call_args.args[0]
    assert command[command.index("-strategy") + 1] == (
        "/data/dsa110-contimg/config/dsa110-default.lua"
    )
