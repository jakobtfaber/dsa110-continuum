"""Simulation utilities for generating synthetic DSA-110 datasets.

This package provides tools for:
- Generating synthetic UVH5 visibility data
- Injecting time-variable sources (flares, ESE, periodic)
- Ground truth tracking for validation
- Validation of pipeline outputs
- Multi-epoch time-domain simulation

Main modules:
    - time_domain: Multi-epoch generation with variability
    - variability_models: Time-varying flux models
    - ground_truth: Track injected sources for validation
    - validation: Validate pipeline outputs against ground truth
    - metrics: Quantitative validation metrics
    - pyuvsim_adapter: Integration with pyuvsim for accurate visibility simulation

pyuvsim Integration
-------------------
The simulation toolkit can use pyuvsim (Lanman et al., 2019) for high-precision
visibility simulation. pyuvsim provides:
- Validated visibility calculations (tested against analytic solutions)
- Full polarization and beam model support
- MPI parallelization for large simulations

To use pyuvsim for visibility generation:
    >>> from dsa110_continuum.simulation import simulate_visibilities
    >>> uvdata_sim = simulate_visibilities(uvdata, sources)

"""

# Time-domain simulation
# Ground truth tracking
try:
    from dsa110_continuum.simulation.ground_truth import (
        GroundTruthRegistry,
        GroundTruthSource,
    )
except ImportError:
    pass  # optional deps of the target module absent (cloud/test env)

# Metrics
try:
    from dsa110_continuum.simulation.metrics import (
        astrometric_offset,
        compute_variability_metrics,
        detection_completeness,
        false_positive_rate,
        flux_recovery_error,
        match_sources_by_position,
        mean_absolute_percentage_error,
        rms_flux_error,
    )
    from dsa110_continuum.simulation.time_domain import (
        EpochData,
        MultiEpochResult,
        generate_multi_epoch_uvh5,
    )
except ImportError:
    pass  # optional deps of the target module absent (cloud/test env)

# Validation
try:
    from dsa110_continuum.simulation.validation import (
        ValidationReport,
        validate_all,
        validate_ese_detection,
        validate_lightcurve,
        validate_photometry,
    )
except ImportError:
    pass  # optional deps of the target module absent (cloud/test env)

# Variability models
try:
    from dsa110_continuum.simulation.variability_models import (
        ConstantFlux,
        ESEScattering,
        FlareModel,
        PeriodicVariation,
        VariabilityModel,
        compute_flux_at_time,
        create_variability_model,
    )
except ImportError:
    pass  # optional deps of the target module absent (cloud/test env)

# pyuvsim integration for accurate visibility simulation (genuinely optional:
# requires pyuvdata/pyuvsim/pyradiosky; __all__ gains these names only when
# the stack is installed)
_PYUVSIM_EXPORTS = [
    "check_mpi_available",
    "check_pyuvsim_available",
    "create_dsa110_beam",
    "simulate_visibilities",
    "sources_to_skymodel",
]
try:
    # noqa comments: names join __all__ only via the conditional append below,
    # which ruff cannot see statically
    from dsa110_continuum.simulation.pyuvsim_adapter import (
        check_mpi_available,  # noqa: F401
        check_pyuvsim_available,  # noqa: F401
        create_dsa110_beam,  # noqa: F401
        simulate_visibilities,  # noqa: F401
        sources_to_skymodel,  # noqa: F401
    )

    _HAVE_PYUVSIM_ADAPTER = True
except ImportError:
    _HAVE_PYUVSIM_ADAPTER = False

# Caskade-based simulation control DAG (optional; requires `pip install caskade`)
from dsa110_continuum.simulation.control import (
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

# Configuration and constants
try:
    from dsa110_continuum.simulation.simulation_config import (
        DSA110_CHANNEL_WIDTH_HZ,
        DSA110_CHANNELS_PER_SUBBAND,
        DSA110_FREQ_MAX_HZ,
        DSA110_FREQ_MIN_HZ,
        DSA110_INTEGRATION_TIME_SEC,
        DSA110_NUM_ANTENNAS,
        DSA110_NUM_ANTENNAS_TEST,
        DSA110_NUM_INTEGRATIONS,
        DSA110_NUM_POLARIZATIONS,
        DSA110_NUM_SUBBANDS,
        DSA110_REFERENCE_FREQ_HZ,
        DSA110_TOTAL_BANDWIDTH_HZ,
        DSA110_TOTAL_CHANNELS,
        SimulationConfig,
        get_config,
        get_test_config,
    )
except ImportError:
    pass  # optional deps of the target module absent (cloud/test env)

__all__ = [
    # Configuration
    "SimulationConfig",
    "get_config",
    "get_test_config",
    # Constants
    "DSA110_NUM_ANTENNAS",
    "DSA110_NUM_ANTENNAS_TEST",
    "DSA110_INTEGRATION_TIME_SEC",
    "DSA110_NUM_INTEGRATIONS",
    "DSA110_NUM_SUBBANDS",
    "DSA110_CHANNELS_PER_SUBBAND",
    "DSA110_TOTAL_CHANNELS",
    "DSA110_CHANNEL_WIDTH_HZ",
    "DSA110_TOTAL_BANDWIDTH_HZ",
    "DSA110_FREQ_MIN_HZ",
    "DSA110_FREQ_MAX_HZ",
    "DSA110_REFERENCE_FREQ_HZ",
    "DSA110_NUM_POLARIZATIONS",
    # Time-domain
    "generate_multi_epoch_uvh5",
    "EpochData",
    "MultiEpochResult",
    # Variability models
    "VariabilityModel",
    "ConstantFlux",
    "FlareModel",
    "ESEScattering",
    "PeriodicVariation",
    "compute_flux_at_time",
    "create_variability_model",
    # Caskade control DAG (available when caskade is installed)
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
    # Ground truth
    "GroundTruthSource",
    "GroundTruthRegistry",
    # Validation
    "ValidationReport",
    "validate_photometry",
    "validate_lightcurve",
    "validate_ese_detection",
    "validate_all",
    # Metrics
    "flux_recovery_error",
    "astrometric_offset",
    "detection_completeness",
    "false_positive_rate",
    "rms_flux_error",
    "mean_absolute_percentage_error",
    "compute_variability_metrics",
    "match_sources_by_position",
]

# pyuvsim integration (only when the optional stack imported above)
if _HAVE_PYUVSIM_ADAPTER:
    __all__ += _PYUVSIM_EXPORTS
