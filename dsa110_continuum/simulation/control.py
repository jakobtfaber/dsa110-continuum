"""Caskade-based simulation control layer.

This module folds `caskade <https://github.com/ConnorStoneAstro/caskade>`_
(Stone & Adam, JOSS 2025) into the simulation framework: simulator knobs
(variability model parameters, gain-corruption scatter, system temperature)
become ``caskade.Param`` nodes in a directed acyclic graph, which buys:

- **Vectorized lightcurves** — ``flux(mjd_array)`` evaluates a whole epoch
  grid in one call (legacy ``VariabilityModel.evaluate`` is scalar-only).
- **Dynamic parameters** — set any ``Param`` to ``None`` and supply values at
  call time (``module.flux(mjd, params=[...])``), enabling parameter sweeps
  and interfacing with samplers/optimizers (emcee, scipy.optimize, dynesty)
  without touching simulator code.
- **One control DAG per run** — :class:`SimulationControl` composes source
  variability, gain corruption, and thermal noise so a full synthetic-run
  configuration is a single inspectable graph object.

Interop with the legacy framework is bidirectional: every
``VariabilityModule`` implements ``evaluate(mjd)`` and ``to_dict()`` with the
exact field names of :mod:`dsa110_continuum.simulation.variability_models`
(values coerced to float; dicts compare equal), so instances are drop-in
replacements anywhere a ``VariabilityModel`` is accepted
(``compute_flux_at_time``, ``generate_multi_epoch_uvh5``
``variability_models=`` dicts, ground-truth JSON serialization).

caskade selects its array backend at import (``CASKADE_BACKEND`` env var,
else torch > jax > numpy by availability). numpy is the validated backend
(bit-identical to the legacy models) and is what the casa6 environment
provides. torch/jax run but default to float32, which quantizes MJD-scale
parameters (~relative 1e-3 flux errors); pin float64 upstream before
relying on them.

Examples
--------
>>> from dsa110_continuum.simulation.control import FlareModule
>>> flare = FlareModule(
...     peak_time_mjd=60000.5, rise_time_hours=1.0, decay_time_hours=3.0,
...     peak_flux_jy=10.0, baseline_flux_jy=2.0,
... )
>>> round(flare.evaluate(60000.5), 9)  # legacy-compatible scalar API
10.0
>>> flare.lightcurve([60000.0, 60000.5, 60001.0])  # vectorized  # doctest: +SKIP
array([ 2.        , 10.        ,  2.14652511])

Sweep a dynamic parameter without rebuilding the module:

>>> flare.peak_flux_jy.to_dynamic()  # or construct with peak_flux_jy=None
>>> flare.flux(60000.5, params=[7.0])
array(7.)
"""

from __future__ import annotations

import keyword
import math
import re
import warnings
from pathlib import Path
from typing import Any, Literal

import numpy as np

try:
    from caskade import Module, Param, backend, forward
except ImportError as exc:  # pragma: no cover - exercised only without caskade
    raise ImportError(
        "caskade is required for dsa110_continuum.simulation.control. "
        "Install it with `pip install caskade` (numpy-only dependency; "
        "torch/jax are optional backends)."
    ) from exc

from dsa110_continuum.simulation.variability_models import (
    HOURS_TO_DAYS,
    VariabilityModel,
)


def _float32_backend() -> bool:
    """Whether the active caskade backend creates sub-64-bit float arrays."""
    return backend.to_numpy(backend.make_array(1.0)).dtype.itemsize < 8


# torch (and jax without x64) default to float32, which quantizes MJD-scale
# parameters (~4-minute peak-time steps, ~1e-3 relative flux errors measured).
# Import-time check only; switching backends later is not re-checked.
if _float32_backend():
    warnings.warn(
        f"caskade selected the '{backend.backend}' backend, which defaults to "
        "float32 — MJD-scale parameters lose precision (~1e-3 relative flux "
        "errors). Set CASKADE_BACKEND=numpy before import, or enable float64 "
        "(torch.set_default_dtype(torch.float64) / jax x64) before using "
        "dsa110_continuum.simulation.control.",
        RuntimeWarning,
        stacklevel=2,
    )

__all__ = [
    "VariabilityModule",
    "ConstantFluxModule",
    "FlareModule",
    "ESEScatteringModule",
    "PeriodicVariationModule",
    "GainCorruptionModule",
    "ThermalNoiseModule",
    "SimulationControl",
    "create_variability_module",
    "from_legacy",
    "to_legacy",
]


def _static_float(param: Param) -> float:
    """Return a Param's static value as a plain float, failing loudly if dynamic."""
    value = param.value
    if value is None:
        raise ValueError(
            f"Param '{param.name}' is dynamic (no static value). Set a value "
            "(module.<param> = x) or pass params= at call time; serialization "
            "and legacy bridging require fully static parameters."
        )
    return float(backend.to_numpy(backend.as_array(value)))


class VariabilityModule(Module):
    """Base class for caskade variability modules.

    Subclasses declare ``Param`` children whose names match the corresponding
    legacy dataclass fields in
    :mod:`dsa110_continuum.simulation.variability_models`, and implement a
    ``@forward`` method ``flux(mjd, ...)`` vectorized over ``mjd``.

    The legacy-compatible surface (``evaluate``, ``to_dict``, ``from_dict``)
    makes instances duck-type interchangeable with ``VariabilityModel``.
    """

    model_type: str = "base"

    def _require_all_static(self, method_name: str) -> None:
        # Without this guard caskade's @forward would swallow the mjd argument
        # as the params vector and raise a misleading FillParams error.
        if self.dynamic_params:
            names = ", ".join(p.name for p in self.dynamic_params)
            raise ValueError(
                f"{method_name}() requires all parameters static; dynamic: {names}. "
                "Set values on the params or call flux(mjd, params=...) instead."
            )

    def evaluate(self, mjd: float) -> float:
        """Compute flux density (Jy) at one MJD; legacy ``VariabilityModel`` API."""
        self._require_all_static("evaluate")
        return float(backend.to_numpy(backend.as_array(self.flux(mjd))))

    def lightcurve(self, mjds: Any, params: Any = None) -> np.ndarray:
        """Vectorized flux over an array of MJDs, returned as a numpy array.

        Parameters
        ----------
        mjds : array-like
            Modified Julian Dates to evaluate.
        params : optional
            Values for dynamic parameters, forwarded to the ``@forward``
            machinery (flat array, sequence, or mapping).
        """
        mjds = backend.as_array(np.asarray(mjds, dtype=float))
        if params is None:
            self._require_all_static("lightcurve")
            result = self.flux(mjds)
        else:
            result = self.flux(mjds, params=params)
        return np.asarray(backend.to_numpy(result))

    def to_dict(self) -> dict:
        """Serialize to the legacy dict format (requires plain static parameters)."""
        result: dict[str, Any] = {"model_type": self.model_type}
        for key, child in self.children.items():
            if isinstance(child, Param):
                if child.pointer:
                    raise ValueError(
                        f"Param '{key}' is a pointer (linked/functional); legacy dict "
                        "serialization requires plain static values."
                    )
                result[key] = _static_float(child)
        return result

    @classmethod
    def from_dict(cls, data: dict) -> VariabilityModule:
        """Build a module from a legacy-format dict (``VariabilityModel.to_dict``)."""
        data = dict(data)
        model_type = data.pop("model_type", None)
        try:
            module_cls = _MODULE_TYPES[model_type]
        except KeyError:
            raise ValueError(f"Unknown model_type: {model_type}") from None
        return module_cls(**data)


class ConstantFluxModule(VariabilityModule):
    """Constant flux (caskade counterpart of ``ConstantFlux``)."""

    model_type = "constant"

    def __init__(self, baseline_flux_jy: float = 0.0, name: str | None = None):
        super().__init__(name=name)
        self.baseline_flux_jy = Param("baseline_flux_jy", baseline_flux_jy, units="Jy")

    @forward
    def flux(self, mjd, baseline_flux_jy=None):
        """Return the constant baseline flux broadcast to the shape of ``mjd``."""
        # mjd * 0.0 broadcasts the constant to the shape of mjd (scalar or array)
        return baseline_flux_jy + backend.as_array(mjd) * 0.0


class FlareModule(VariabilityModule):
    """Fast-rise / exponential-decay flare (counterpart of ``FlareModel``)."""

    model_type = "flare"

    def __init__(
        self,
        baseline_flux_jy: float = 0.0,
        peak_time_mjd: float = 0.0,
        rise_time_hours: float = 1.0,
        decay_time_hours: float = 2.0,
        peak_flux_jy: float = 5.0,
        name: str | None = None,
    ):
        super().__init__(name=name)
        self.baseline_flux_jy = Param("baseline_flux_jy", baseline_flux_jy, units="Jy")
        self.peak_time_mjd = Param("peak_time_mjd", peak_time_mjd, units="MJD")
        self.rise_time_hours = Param("rise_time_hours", rise_time_hours, units="h")
        self.decay_time_hours = Param("decay_time_hours", decay_time_hours, units="h")
        self.peak_flux_jy = Param("peak_flux_jy", peak_flux_jy, units="Jy")

    @forward
    def flux(
        self,
        mjd,
        baseline_flux_jy=None,
        peak_time_mjd=None,
        rise_time_hours=None,
        decay_time_hours=None,
        peak_flux_jy=None,
    ):
        """Flux (Jy) at ``mjd``: linear rise to peak, exponential decay after."""
        B = backend.module
        mjd = backend.as_array(mjd)
        rise_days = rise_time_hours * HOURS_TO_DAYS
        decay_days = decay_time_hours * HOURS_TO_DAYS
        amplitude = peak_flux_jy - baseline_flux_jy
        # clip folds the pre-flare branch (fraction 0) and the peak (fraction 1)
        # into the linear rise, matching the legacy piecewise definition.
        # rise_time_hours == 0 degenerates to a step at the peak (the legacy
        # branch structure gives baseline before, peak at/after); the safe
        # denominator avoids a 0/0 NaN leaking through the where() below.
        dt_rise = mjd - (peak_time_mjd - rise_days)
        safe_rise_days = B.where(rise_days > 0, rise_days, 1.0)
        fraction = B.clip(dt_rise / safe_rise_days, 0.0, 1.0)
        fraction = B.where(rise_days > 0, fraction, B.where(dt_rise >= 0, 1.0, 0.0))
        rising = baseline_flux_jy + amplitude * fraction
        # clip keeps exp() bounded for mjd < peak; those entries are discarded
        # by the where() below but would otherwise overflow for early times.
        dt_decay = B.clip(mjd - peak_time_mjd, 0.0, None)
        decay = baseline_flux_jy + amplitude * B.exp(-dt_decay / decay_days)
        return B.where(mjd <= peak_time_mjd, rising, decay)


class ESEScatteringModule(VariabilityModule):
    """Gaussian-dip extreme scattering event (counterpart of ``ESEScattering``)."""

    model_type = "ese"

    def __init__(
        self,
        baseline_flux_jy: float = 0.0,
        dip_time_mjd: float = 0.0,
        dip_duration_days: float = 7.0,
        dip_depth_factor: float = 0.1,
        name: str | None = None,
    ):
        super().__init__(name=name)
        self.baseline_flux_jy = Param("baseline_flux_jy", baseline_flux_jy, units="Jy")
        self.dip_time_mjd = Param("dip_time_mjd", dip_time_mjd, units="MJD")
        self.dip_duration_days = Param("dip_duration_days", dip_duration_days, units="d")
        self.dip_depth_factor = Param("dip_depth_factor", dip_depth_factor, valid=(0.0, 1.0))

    @forward
    def flux(
        self,
        mjd,
        baseline_flux_jy=None,
        dip_time_mjd=None,
        dip_duration_days=None,
        dip_depth_factor=None,
    ):
        """Flux (Jy) at ``mjd``: baseline scaled by a Gaussian dip profile."""
        B = backend.module
        # FWHM -> sigma (2*sqrt(2*ln2) ~= 2.355), same relation as the legacy model
        sigma_days = dip_duration_days / 2.355
        dt = backend.as_array(mjd) - dip_time_mjd
        gaussian = B.exp(-0.5 * (dt / sigma_days) ** 2)
        return baseline_flux_jy * (1.0 - (1.0 - dip_depth_factor) * gaussian)


class PeriodicVariationModule(VariabilityModule):
    """Sinusoidal periodic variation (counterpart of ``PeriodicVariation``)."""

    model_type = "periodic"

    def __init__(
        self,
        baseline_flux_jy: float = 0.0,
        period_days: float = 1.0,
        amplitude_jy: float = 0.5,
        phase_offset: float = 0.0,
        name: str | None = None,
    ):
        super().__init__(name=name)
        self.baseline_flux_jy = Param("baseline_flux_jy", baseline_flux_jy, units="Jy")
        self.period_days = Param("period_days", period_days, units="d")
        self.amplitude_jy = Param("amplitude_jy", amplitude_jy, units="Jy")
        self.phase_offset = Param("phase_offset", phase_offset, cyclic=True, valid=(0.0, 1.0))

    @forward
    def flux(
        self,
        mjd,
        baseline_flux_jy=None,
        period_days=None,
        amplitude_jy=None,
        phase_offset=None,
    ):
        """Flux (Jy) at ``mjd``: sinusoidal modulation about the baseline."""
        B = backend.module
        phase = (backend.as_array(mjd) / period_days + phase_offset) % 1.0
        return baseline_flux_jy + 0.5 * amplitude_jy * B.sin(2.0 * math.pi * phase)


_MODULE_TYPES: dict[str, type[VariabilityModule]] = {
    "constant": ConstantFluxModule,
    "flare": FlareModule,
    "ese": ESEScatteringModule,
    "periodic": PeriodicVariationModule,
}


def create_variability_module(
    model_type: Literal["constant", "flare", "ese", "periodic"],
    baseline_flux_jy: float,
    **params: Any,
) -> VariabilityModule:
    """Create a caskade module; mirrors the legacy ``create_variability_model`` factory.

    Unlike the legacy factory, unknown ``params`` raise ``TypeError`` for every
    model type (the legacy factory silently drops them for ``constant``).
    """
    try:
        module_cls = _MODULE_TYPES[model_type]
    except KeyError:
        raise ValueError(f"Unknown model_type: {model_type}") from None
    return module_cls(baseline_flux_jy=baseline_flux_jy, **params)


def from_legacy(model: VariabilityModel) -> VariabilityModule:
    """Convert a legacy ``VariabilityModel`` dataclass into a caskade module."""
    return VariabilityModule.from_dict(model.to_dict())


def to_legacy(module: VariabilityModule) -> VariabilityModel:
    """Convert a caskade module back into a legacy ``VariabilityModel`` dataclass.

    Requires all parameters to be static (dynamic parameters have no single
    value to freeze into a dataclass).
    """
    return VariabilityModel.from_dict(module.to_dict())


class GainCorruptionModule(Module):
    """Per-antenna gain corruption knobs as caskade parameters.

    Wraps :func:`dsa110_continuum.simulation.gain_corruption.corrupt_uvh5`;
    the scatter amplitudes live in the control DAG so a corruption level is
    part of the recorded simulation configuration.
    """

    def __init__(
        self,
        amp_scatter: float = 0.05,
        phase_scatter_deg: float = 5.0,
        name: str | None = None,
    ):
        super().__init__(name=name)
        self.amp_scatter = Param("amp_scatter", amp_scatter, valid=(0.0, None))
        self.phase_scatter_deg = Param("phase_scatter_deg", phase_scatter_deg, units="deg")

    def apply(
        self,
        uvh5_path: Path | str,
        *,
        seed: int = 0,
        output_path: Path | str | None = None,
    ) -> Path:
        """Corrupt a UVH5 file using the current (static) parameter values."""
        from dsa110_continuum.simulation.gain_corruption import corrupt_uvh5

        return corrupt_uvh5(
            uvh5_path,
            amp_scatter=_static_float(self.amp_scatter),
            phase_scatter_deg=_static_float(self.phase_scatter_deg),
            seed=seed,
            output_path=output_path,
        )


class ThermalNoiseModule(Module):
    """System-temperature noise knob as a caskade parameter.

    Wraps :func:`dsa110_continuum.simulation.visibility_models.add_thermal_noise`.
    """

    def __init__(self, system_temp_k: float = 50.0, name: str | None = None):
        super().__init__(name=name)
        self.system_temp_k = Param("system_temp_k", system_temp_k, units="K", valid=(0.0, None))

    def apply(
        self,
        data: np.ndarray,
        integration_time_sec: float,
        channel_width_hz: float,
        *,
        frequency_hz: float | None = None,
        rng: np.random.Generator | None = None,
    ) -> np.ndarray:
        """Add thermal noise to visibilities using the current system temperature."""
        from dsa110_continuum.simulation.visibility_models import add_thermal_noise

        # add_thermal_noise's frequency_hz is not Optional (default 1.4e9);
        # forward it only when given so the wrapped default applies.
        kwargs: dict[str, Any] = {}
        if frequency_hz is not None:
            kwargs["frequency_hz"] = frequency_hz
        return add_thermal_noise(
            data,
            integration_time_sec,
            channel_width_hz,
            system_temperature_k=_static_float(self.system_temp_k),
            rng=rng,
            **kwargs,
        )


class SimulationControl(Module):
    """Top-level control DAG for a synthetic observation run.

    Composes per-source variability modules with gain-corruption and
    thermal-noise knobs into one caskade graph. The graph is directly
    consumable by the existing generators: :meth:`variability_models` returns
    the ``{source_id: module}`` mapping that
    ``generate_multi_epoch_uvh5(variability_models=...)`` expects, because
    each module implements the legacy ``evaluate``/``to_dict`` API.

    Source IDs may be arbitrary strings (``"NVSS_J123456+420000"``,
    ``"source_188.0000_42.0000"``): caskade graph keys must be Python
    identifiers, so modules are linked under sanitized keys and an internal
    registry maps IDs to graph keys.

    Examples
    --------
    >>> control = SimulationControl(
    ...     sources={
    ...         "NVSS_J123456+420000": FlareModule(
    ...             peak_time_mjd=60300.5, peak_flux_jy=5.0, baseline_flux_jy=1.0
    ...         ),
    ...         "NVSS_J123500+420100": ConstantFluxModule(baseline_flux_jy=2.0),
    ...     },
    ...     gain_corruption=GainCorruptionModule(amp_scatter=0.05),
    ...     thermal_noise=ThermalNoiseModule(system_temp_k=50.0),
    ... )
    >>> control.fluxes(60300.5)  # per-source fluxes, catalog order
    array([5., 2.])
    >>> result = generate_multi_epoch_uvh5(
    ...     epochs=epochs, output_dir=out,
    ...     variability_models=control.variability_models(),
    ... )  # doctest: +SKIP
    """

    def __init__(
        self,
        sources: dict[str, VariabilityModule] | None = None,
        gain_corruption: GainCorruptionModule | None = None,
        thermal_noise: ThermalNoiseModule | None = None,
        name: str | None = None,
    ):
        super().__init__(name=name)
        self._source_keys: dict[str, str] = {}
        for source_id, module in (sources or {}).items():
            self.add_source(source_id, module)
        if gain_corruption is not None:
            self.gain_corruption = gain_corruption
        if thermal_noise is not None:
            self.thermal_noise = thermal_noise
        # With zero children nothing ever triggers update_graph(), leaving
        # @forward bookkeeping (subgraph_kwargs) unset; run it once explicitly
        # so an empty control reaches the fail-loudly guard in fluxes().
        self.update_graph()

    def _sanitize_key(self, source_id: str) -> str:
        # caskade link keys must be identifiers and become attributes on this
        # module, so prefix to dodge method/keyword collisions and
        # disambiguate IDs that sanitize identically ("a+b" vs "a-b").
        key = "src_" + re.sub(r"\W", "_", source_id)
        if keyword.iskeyword(key):
            key += "_"
        base, n = key, 2
        while key in self.children or hasattr(self, key):
            key = f"{base}_{n}"
            n += 1
        return key

    @property
    def source_ids(self) -> tuple[str, ...]:
        """Source identifiers in insertion (stacking) order."""
        return tuple(self._source_keys.keys())

    @property
    def sources(self) -> dict[str, VariabilityModule]:
        """Mapping of source ID to variability module (insertion order)."""
        return {sid: self.children[key] for sid, key in self._source_keys.items()}

    def add_source(self, source_id: str, module: VariabilityModule) -> None:
        """Add or replace a variability module for ``source_id``."""
        if source_id in self._source_keys:
            self.unlink(self._source_keys.pop(source_id))
        key = self._sanitize_key(source_id)
        self.link(key, module)
        self._source_keys[source_id] = key

    @forward
    def fluxes(self, mjd):
        """Stacked per-source fluxes at ``mjd``.

        Returns shape ``(n_sources,)`` for scalar ``mjd`` or
        ``(n_sources, n_times)`` for an array of MJDs, ordered by
        :attr:`source_ids`.
        """
        if not self._source_keys:
            raise ValueError("SimulationControl has no sources; use add_source() first.")
        return backend.module.stack(
            [self.children[key].flux(mjd) for key in self._source_keys.values()]
        )

    def variability_models(self) -> dict[str, VariabilityModule]:
        """Return the mapping ``generate_multi_epoch_uvh5(variability_models=...)`` expects."""
        return self.sources

    def to_dict(self) -> dict:
        """Serialize the full control configuration (requires static parameters)."""
        result: dict[str, Any] = {
            "sources": {sid: module.to_dict() for sid, module in self.sources.items()},
        }
        if "gain_corruption" in self.children:
            result["gain_corruption"] = {
                "amp_scatter": _static_float(self.gain_corruption.amp_scatter),
                "phase_scatter_deg": _static_float(self.gain_corruption.phase_scatter_deg),
            }
        if "thermal_noise" in self.children:
            result["thermal_noise"] = {
                "system_temp_k": _static_float(self.thermal_noise.system_temp_k),
            }
        return result
