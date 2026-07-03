"""Oracle tests pinning the vendored dsa110_continuum.utils modules."""
import importlib

import pytest


def test_constants_present_and_sane():
    from dsa110_continuum.utils.constants import (
        DSA110_LATITUDE,
        DSA110_LOCATION,
        DSA110_LONGITUDE,
    )
    from dsa110_continuum.utils.antenna_classification import OUTRIGGER_ANTENNAS

    assert 36.0 < DSA110_LATITUDE < 38.0  # OVRO ~37.23 N
    assert -119.0 < DSA110_LONGITUDE < -117.0  # ~-118.28 E
    assert len(OUTRIGGER_ANTENNAS) > 0
    assert DSA110_LOCATION is not None


def test_time_utils_jd_mjd_oracle():
    from dsa110_continuum.utils.time_utils import jd_to_mjd

    assert jd_to_mjd(2400000.5) == 0.0  # MJD epoch definition


def test_yaml_loader_env_expansion(tmp_path, monkeypatch):
    from dsa110_continuum.utils.yaml_loader import load_yaml_with_env

    monkeypatch.setenv("TVU_TEST_VALUE", "expanded")
    f = tmp_path / "t.yaml"
    f.write_text("key: ${TVU_TEST_VALUE}\n")
    assert load_yaml_with_env(f, expand_vars=True)["key"] == "expanded"


def test_env_helpers_from_package_root(monkeypatch):
    from dsa110_continuum.utils import get_env_int, get_env_list

    monkeypatch.setenv("TVU_INT", "7")
    monkeypatch.setenv("TVU_LIST", "a,b,c")
    assert get_env_int("TVU_INT", 3) == 7
    assert get_env_int("TVU_ABSENT", 3) == 3
    assert get_env_list("TVU_LIST", []) == ["a", "b", "c"]


def test_timed_decorator_passthrough():
    from dsa110_continuum.utils import timed

    @timed
    def f(x):
        return x + 1

    assert f(1) == 2


def test_wrap_phase_deg_oracle():
    from dsa110_continuum.utils.angles import wrap_phase_deg

    assert wrap_phase_deg(370.0) == pytest.approx(10.0)
    assert wrap_phase_deg(-190.0) == pytest.approx(170.0)


def test_render_template_and_css():
    from dsa110_continuum.utils.template_styles import get_shared_css

    css = get_shared_css()
    assert isinstance(css, str) and len(css) > 0


def test_gpu_safety_degrades_without_gpu():
    from dsa110_continuum.utils.gpu_safety import is_gpu_available, register_with_monitor

    assert is_gpu_available() in (True, False)
    assert register_with_monitor() is False  # legacy monitoring retired


def test_all_vendored_modules_import():
    mods = [
        "angles", "antenna_classification", "antpos_local", "casa_init",
        "cli_helpers", "constants", "coordinates", "decorators",
        "error_context", "exceptions", "fast_meta", "fits_utils",
        "gpu_safety", "gpu_utils", "hdf5_io", "logging", "ms_helpers",
        "ms_locking", "ms_permissions", "numba_accel", "paths",
        "performance", "plotting", "progress", "run_isolation",
        "runtime_safeguards", "stability", "temp_manager",
        "template_styles", "templates", "time_utils", "validation",
        "wsclean_utils", "yaml_loader", "env_utils",
    ]
    for m in mods:
        importlib.import_module(f"dsa110_continuum.utils.{m}")
