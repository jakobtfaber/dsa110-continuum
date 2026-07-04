"""Tests for the caskade-based simulation control layer.

Verifies numerical equivalence with the legacy ``variability_models``
dataclasses, legacy duck-type compatibility (``evaluate``/``to_dict``),
bidirectional bridging, dynamic-parameter fill, and the composed
``SimulationControl`` DAG.
"""

from __future__ import annotations

import os

import numpy as np
import pytest

# Force the deterministic numpy backend regardless of whether torch/jax are
# installed in the test environment. Must be set before caskade is imported.
os.environ.setdefault("CASKADE_BACKEND", "numpy")

caskade = pytest.importorskip("caskade")
caskade.backend.backend = "numpy"

from dsa110_continuum.simulation.control import (  # noqa: E402
    ConstantFluxModule,
    ESEScatteringModule,
    FlareModule,
    GainCorruptionModule,
    PeriodicVariationModule,
    SimulationControl,
    ThermalNoiseModule,
    VariabilityModule,
    create_variability_module,
    from_legacy,
    to_legacy,
)
from dsa110_continuum.simulation.variability_models import (  # noqa: E402
    ConstantFlux,
    ESEScattering,
    FlareModel,
    PeriodicVariation,
    VariabilityModel,
    compute_flux_at_time,
)

PEAK_MJD = 60000.5

LEGACY_MODELS = [
    ConstantFlux(baseline_flux_jy=2.5),
    FlareModel(
        peak_time_mjd=PEAK_MJD,
        rise_time_hours=1.0,
        decay_time_hours=3.0,
        peak_flux_jy=10.0,
        baseline_flux_jy=2.0,
    ),
    ESEScattering(
        dip_time_mjd=PEAK_MJD,
        dip_duration_days=7.0,
        dip_depth_factor=0.2,
        baseline_flux_jy=3.0,
    ),
    PeriodicVariation(
        period_days=1.0,
        amplitude_jy=0.5,
        phase_offset=0.1,
        baseline_flux_jy=2.0,
    ),
]

# Grid straddling every piecewise boundary of the flare model: pre-flare,
# mid-rise, exact peak, one decay constant out, and far tail.
MJD_GRID = PEAK_MJD + np.array([-2.0, -1.0 / 24, -0.5 / 24, 0.0, 3.0 / 24, 1.0, 10.0])


@pytest.mark.parametrize("legacy", LEGACY_MODELS, ids=lambda m: m.model_type)
class TestLegacyEquivalence:
    def test_pointwise_equivalence(self, legacy: VariabilityModel):
        module = from_legacy(legacy)
        for mjd in MJD_GRID:
            assert module.evaluate(float(mjd)) == pytest.approx(
                legacy.evaluate(float(mjd)), rel=1e-12
            )

    def test_vectorized_lightcurve_matches_scalar_loop(self, legacy: VariabilityModel):
        module = from_legacy(legacy)
        curve = module.lightcurve(MJD_GRID)
        expected = np.array([legacy.evaluate(float(m)) for m in MJD_GRID])
        assert curve.shape == MJD_GRID.shape
        np.testing.assert_allclose(curve, expected, rtol=1e-12)

    def test_round_trip_through_legacy(self, legacy: VariabilityModel):
        module = from_legacy(legacy)
        assert to_legacy(module) == legacy

    def test_dict_cross_compatibility(self, legacy: VariabilityModel):
        module = from_legacy(legacy)
        assert module.to_dict() == legacy.to_dict()
        # Legacy deserializer accepts module-produced dicts and vice versa.
        assert VariabilityModel.from_dict(module.to_dict()) == legacy
        rebuilt = VariabilityModule.from_dict(legacy.to_dict())
        assert rebuilt.to_dict() == legacy.to_dict()

    def test_compute_flux_at_time_duck_typing(self, legacy: VariabilityModel):
        module = from_legacy(legacy)
        mjd = float(MJD_GRID[3])
        assert compute_flux_at_time(1.0, module, mjd) == pytest.approx(
            compute_flux_at_time(1.0, legacy, mjd), rel=1e-12
        )


class TestDynamicParameters:
    def test_dynamic_fill_via_params(self):
        flare = FlareModule(
            baseline_flux_jy=2.0,
            peak_time_mjd=PEAK_MJD,
            rise_time_hours=1.0,
            decay_time_hours=3.0,
            peak_flux_jy=None,
        )
        assert len(flare.dynamic_params) == 1
        result = float(flare.flux(PEAK_MJD, params=[7.0]))
        assert result == pytest.approx(7.0)

    def test_dynamic_sweep_matches_rebuilt_static_models(self):
        flare = FlareModule(
            baseline_flux_jy=2.0,
            peak_time_mjd=PEAK_MJD,
            rise_time_hours=1.0,
            decay_time_hours=3.0,
            peak_flux_jy=None,
        )
        mjd = PEAK_MJD + 3.0 / 24
        for peak in [5.0, 10.0, 20.0]:
            expected = FlareModel(
                peak_time_mjd=PEAK_MJD,
                rise_time_hours=1.0,
                decay_time_hours=3.0,
                peak_flux_jy=peak,
                baseline_flux_jy=2.0,
            ).evaluate(mjd)
            assert float(flare.flux(mjd, params=[peak])) == pytest.approx(expected, rel=1e-12)

    def test_to_dict_fails_loudly_on_dynamic_param(self):
        flare = FlareModule(peak_flux_jy=None)
        with pytest.raises(ValueError, match="dynamic"):
            flare.to_dict()

    def test_to_dict_fails_loudly_on_pointer_param(self):
        a = ConstantFluxModule(baseline_flux_jy=2.0)
        b = ConstantFluxModule(baseline_flux_jy=1.0)
        b.baseline_flux_jy = a.baseline_flux_jy  # becomes a pointer param
        with pytest.raises(ValueError, match="pointer"):
            b.to_dict()

    def test_evaluate_with_dynamic_param_fails_loudly(self):
        flare = FlareModule(peak_flux_jy=None)
        with pytest.raises(ValueError, match="dynamic: peak_flux_jy"):
            flare.evaluate(PEAK_MJD)
        with pytest.raises(ValueError, match="dynamic: peak_flux_jy"):
            flare.lightcurve(MJD_GRID)

    def test_lightcurve_accepts_params(self):
        constant = ConstantFluxModule(baseline_flux_jy=None)
        curve = constant.lightcurve(MJD_GRID, params=[4.2])
        np.testing.assert_allclose(curve, 4.2)


class TestDegenerateParameters:
    def test_zero_rise_time_matches_legacy_step(self):
        # rise_time_hours=0 degenerates to a step: legacy gives baseline
        # before the peak and the decay branch (== peak at t_peak) at/after.
        kwargs = dict(
            peak_time_mjd=PEAK_MJD,
            rise_time_hours=0.0,
            decay_time_hours=3.0,
            peak_flux_jy=10.0,
            baseline_flux_jy=2.0,
        )
        module = FlareModule(**kwargs)
        legacy = FlareModel(**kwargs)
        for mjd in [PEAK_MJD - 0.1, PEAK_MJD, PEAK_MJD + 3.0 / 24, PEAK_MJD + 1.0]:
            got = module.evaluate(mjd)
            assert np.isfinite(got)
            assert got == pytest.approx(legacy.evaluate(mjd), rel=1e-12)

    def test_zero_rise_time_vectorized_has_no_nan(self):
        module = FlareModule(
            peak_time_mjd=PEAK_MJD,
            rise_time_hours=0.0,
            decay_time_hours=3.0,
            peak_flux_jy=10.0,
            baseline_flux_jy=2.0,
        )
        curve = module.lightcurve(MJD_GRID)
        assert np.all(np.isfinite(curve))


class TestFloat32BackendGuard:
    def test_numpy_backend_is_not_float32(self):
        from dsa110_continuum.simulation.control import _float32_backend

        assert _float32_backend() is False

    def test_torch_backend_detected_as_float32(self):
        pytest.importorskip("torch")
        from dsa110_continuum.simulation.control import _float32_backend

        try:
            caskade.backend.backend = "torch"
            assert _float32_backend() is True
        finally:
            caskade.backend.backend = "numpy"


class TestFactory:
    def test_create_variability_module_types(self):
        for model_type, cls in [
            ("constant", ConstantFluxModule),
            ("flare", FlareModule),
            ("ese", ESEScatteringModule),
            ("periodic", PeriodicVariationModule),
        ]:
            module = create_variability_module(model_type, baseline_flux_jy=1.5)
            assert isinstance(module, cls)
            assert module.evaluate(60000.0) > 0.0

    def test_create_variability_module_unknown_type(self):
        with pytest.raises(ValueError, match="Unknown model_type"):
            create_variability_module("nova", baseline_flux_jy=1.0)

    def test_from_dict_unknown_type(self):
        with pytest.raises(ValueError, match="Unknown model_type"):
            VariabilityModule.from_dict({"model_type": "nova"})


class TestSimulationControl:
    # Realistic IDs: NVSS catalog convention and the time_domain.py fallback
    # (`source_{ra:.4f}_{dec:.4f}`) — neither is a valid Python identifier,
    # exercising the sanitized-key registry.
    FLARE_ID = "NVSS_J123456+420000"
    CONST_ID = "source_188.0000_42.0000"

    def _control(self) -> SimulationControl:
        return SimulationControl(
            sources={
                self.FLARE_ID: FlareModule(
                    baseline_flux_jy=1.0,
                    peak_time_mjd=PEAK_MJD,
                    peak_flux_jy=5.0,
                ),
                self.CONST_ID: ConstantFluxModule(baseline_flux_jy=2.0),
            },
            gain_corruption=GainCorruptionModule(amp_scatter=0.07, phase_scatter_deg=4.0),
            thermal_noise=ThermalNoiseModule(system_temp_k=55.0),
        )

    def test_fluxes_scalar_mjd(self):
        control = self._control()
        fluxes = np.asarray(control.fluxes(PEAK_MJD))
        # rtol 1e-9: catastrophic cancellation in (mjd - t_start) at MJD ~6e4
        # limits the rise-branch value at the exact peak to ~1e-10 absolute;
        # the legacy scalar model has the identical rounding.
        np.testing.assert_allclose(fluxes, [5.0, 2.0], rtol=1e-9)

    def test_fluxes_vector_mjd(self):
        control = self._control()
        fluxes = np.asarray(control.fluxes(MJD_GRID))
        assert fluxes.shape == (2, len(MJD_GRID))
        np.testing.assert_allclose(fluxes[1], 2.0)

    def test_variability_models_mapping_is_legacy_consumable(self):
        control = self._control()
        models = control.variability_models()
        assert set(models) == {self.FLARE_ID, self.CONST_ID}
        # The exact duck-type surface generate_multi_epoch_uvh5 touches:
        for model in models.values():
            assert isinstance(model.evaluate(PEAK_MJD), float)
            assert "model_type" in model.to_dict()

    def test_add_source_extends_graph(self):
        control = self._control()
        control.add_source("NVSS_J123500-420100", ESEScatteringModule(baseline_flux_jy=3.0))
        assert control.source_ids == (self.FLARE_ID, self.CONST_ID, "NVSS_J123500-420100")
        assert np.asarray(control.fluxes(PEAK_MJD)).shape == (3,)

    def test_colliding_sanitized_ids_stay_distinct(self):
        # "a+b" and "a-b" sanitize to the same graph key; registry must disambiguate.
        control = SimulationControl(
            sources={
                "a+b": ConstantFluxModule(baseline_flux_jy=1.0),
                "a-b": ConstantFluxModule(baseline_flux_jy=2.0),
            },
        )
        models = control.variability_models()
        assert models["a+b"].evaluate(PEAK_MJD) == pytest.approx(1.0)
        assert models["a-b"].evaluate(PEAK_MJD) == pytest.approx(2.0)

    def test_add_source_replaces_existing_id(self):
        control = self._control()
        control.add_source(self.CONST_ID, ConstantFluxModule(baseline_flux_jy=9.0))
        assert len(control.source_ids) == 2
        assert control.sources[self.CONST_ID].evaluate(PEAK_MJD) == pytest.approx(9.0)

    def test_fluxes_with_no_sources_fails_loudly(self):
        with pytest.raises(ValueError, match="no sources"):
            SimulationControl().fluxes(PEAK_MJD)

    def test_to_dict_captures_full_configuration(self):
        config = self._control().to_dict()
        assert config["sources"][self.FLARE_ID]["model_type"] == "flare"
        assert config["gain_corruption"]["amp_scatter"] == pytest.approx(0.07)
        assert config["thermal_noise"]["system_temp_k"] == pytest.approx(55.0)

    def test_dynamic_source_param_fills_through_control_dag(self):
        control = SimulationControl(
            sources={
                "a": ConstantFluxModule(baseline_flux_jy=None),
                "b": ConstantFluxModule(baseline_flux_jy=3.0),
            },
        )
        fluxes = np.asarray(control.fluxes(PEAK_MJD, params=[1.5]))
        np.testing.assert_allclose(fluxes, [1.5, 3.0], rtol=1e-12)


class TestCorruptionAndNoiseDelegation:
    def test_gain_corruption_forwards_param_values(self, monkeypatch, tmp_path):
        # gain_corruption imports pyuvdata at module level; stub it when absent
        # (local dev envs) — the fake corrupt_uvh5 below never touches it.
        import sys
        from unittest.mock import MagicMock

        try:
            import pyuvdata  # noqa: F401
        except ImportError:
            monkeypatch.setitem(sys.modules, "pyuvdata", MagicMock())

        import dsa110_continuum.simulation.gain_corruption as gc

        calls = {}

        def fake_corrupt_uvh5(path, *, amp_scatter, phase_scatter_deg, seed, output_path):
            calls.update(
                path=path,
                amp_scatter=amp_scatter,
                phase_scatter_deg=phase_scatter_deg,
                seed=seed,
                output_path=output_path,
            )
            return tmp_path / "out.uvh5"

        monkeypatch.setattr(gc, "corrupt_uvh5", fake_corrupt_uvh5)
        module = GainCorruptionModule(amp_scatter=0.08, phase_scatter_deg=6.0)
        module.apply(tmp_path / "in.uvh5", seed=3)
        assert calls["amp_scatter"] == pytest.approx(0.08)
        assert calls["phase_scatter_deg"] == pytest.approx(6.0)
        assert calls["seed"] == 3

    def test_thermal_noise_forwards_system_temp(self, monkeypatch):
        # visibility_models applies @stability from an optionally-imported
        # dsa110_contimg; without that package (or the cloud shim) the module
        # raises NameError at import. Pre-existing limitation, not under test.
        try:
            import dsa110_continuum.simulation.visibility_models as vm
        except Exception as exc:
            pytest.skip(f"visibility_models not importable in this env: {exc}")

        calls = {}

        def fake_add_thermal_noise(
            data, tint, chw, *, system_temperature_k, frequency_hz=1.4e9, rng=None
        ):
            calls["system_temperature_k"] = system_temperature_k
            calls["frequency_hz"] = frequency_hz
            return data

        monkeypatch.setattr(vm, "add_thermal_noise", fake_add_thermal_noise)
        module = ThermalNoiseModule(system_temp_k=42.0)
        data = np.zeros((2, 2), dtype=complex)
        module.apply(data, 12.88, 244140.625, frequency_hz=1.35e9)
        assert calls["system_temperature_k"] == pytest.approx(42.0)
        assert calls["frequency_hz"] == pytest.approx(1.35e9)

    def test_thermal_noise_omits_frequency_when_not_given(self, monkeypatch):
        # add_thermal_noise's frequency_hz is non-Optional (default 1.4e9);
        # apply() must not forward None into it.
        try:
            import dsa110_continuum.simulation.visibility_models as vm
        except Exception as exc:
            pytest.skip(f"visibility_models not importable in this env: {exc}")

        calls = {}

        def fake_add_thermal_noise(
            data, tint, chw, *, system_temperature_k, frequency_hz=1.4e9, rng=None
        ):
            calls["frequency_hz"] = frequency_hz
            return data

        monkeypatch.setattr(vm, "add_thermal_noise", fake_add_thermal_noise)
        module = ThermalNoiseModule(system_temp_k=42.0)
        module.apply(np.zeros((2, 2), dtype=complex), 12.88, 244140.625)
        assert calls["frequency_hz"] == pytest.approx(1.4e9)

    def test_apply_with_dynamic_param_fails_loudly(self, tmp_path):
        module = GainCorruptionModule(amp_scatter=None)
        with pytest.raises(ValueError, match="dynamic"):
            module.apply(tmp_path / "in.uvh5")
