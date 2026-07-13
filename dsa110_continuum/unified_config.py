"""
Unified configuration system for DSA-110 continuum imaging pipeline.

This module consolidates configuration from environment variables and YAML/dict
pipeline settings into one continuum-owned config surface.

- Environment variables (CONTIMG_* prefix)
- YAML files (via from_yaml())
- Dictionary (via from_dict())
- Programmatic defaults

Notes
-----
Precedence (highest to lowest):

1. Explicit function arguments
2. YAML file configuration
3. Environment variables
4. Default values

Examples
--------
From environment variables:

>>> import os
>>> os.environ['CONTIMG_CONVERSION_MAX_WORKERS'] = '12'
>>> config = UnifiedPipelineConfig()
>>> print(config.conversion.max_workers)  # 12

From YAML file:

>>> config = UnifiedPipelineConfig.from_yaml('pipeline.yaml')
>>> print(config.conversion.max_workers)  # Value from YAML

Programmatic:

>>> config = UnifiedPipelineConfig(
...     conversion=ConversionConfig(max_workers=8)
... )
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from dsa110_continuum.database.data_config import _resolve_writable_path
from dsa110_continuum.utils.paths import resolve_paths
from dsa110_continuum.utils.yaml_loader import load_yaml_with_env

# Base directory for the pipeline, can be overridden by environment variable
# Defaults to empty string to force explicit configuration or relative paths
CONTIMG_BASE = os.environ.get("CONTIMG_BASE_DIR", "")

# ============================================================================
# Path Configuration
# ============================================================================


class PathsConfig(BaseModel):
    """Path configuration for pipeline execution."""

    input_dir: Path = Field(
        default_factory=lambda: Path(os.environ.get("CONTIMG_INPUT_DIR") or str(resolve_paths().input_dir)),
        description="Input directory for UVH5 files",
    )
    output_dir: Path = Field(
        default_factory=lambda: _resolve_writable_path(
            str(resolve_paths().ms_dir),
            description="output directory",
            warn_on_fallback=False,
        ),
        description="Output directory for MS files (staging area)",
    )
    staging_dir: Path = Field(
        default_factory=lambda: _resolve_writable_path(
            str(resolve_paths().staging_dir),
            description="staging directory",
            warn_on_fallback=False,
        ),
        description="Staging directory for intermediate products (raw/ms, images, etc.)",
    )
    scratch_dir: Path = Field(
        default_factory=lambda: _resolve_writable_path(
            str(resolve_paths().tmpfs_dir),
            description="scratch directory",
            warn_on_fallback=False,
        ),
        description="Scratch directory for temporary files (tmpfs recommended)",
    )
    tmpfs_dir: Path = Field(
        default_factory=lambda: _resolve_writable_path(
            str(resolve_paths().tmpfs_dir),
            description="tmpfs directory",
            warn_on_fallback=False,
        ),
        description="Tmpfs directory for high-I/O staging (MS copy, calibration scratch)",
    )
    state_dir: Path = Field(
        default_factory=lambda: _resolve_writable_path(
            str(resolve_paths().state_dir),
            description="state directory",
            warn_on_fallback=False,
        ),
        description="State directory for databases",
    )
    casa_logs_dir: Path | None = Field(
        default=None,
        description="Directory for CASA log files",
    )
    figures_dir: Path = Field(
        default_factory=lambda: _resolve_writable_path(
            os.environ.get(
                "CONTIMG_PATHS__FIGURES_DIR", str(resolve_paths().staging_dir / "figures")
            ),
            description="figures directory",
            warn_on_fallback=bool(os.environ.get("CONTIMG_PATHS__FIGURES_DIR")),
        ),
        description="Default directory for saved figures and plots (staging area)",
    )

    # Run isolation configuration (for testing/validation)
    run_id: str | None = Field(
        default=None,
        description="Unique run identifier for isolated runs (auto-generated if None when isolate_runs=True)",
    )
    isolate_runs: bool = Field(
        default=False,
        description="Enable run isolation with unique directories (for testing/validation)",
    )
    products_dir: Path | None = Field(
        default_factory=lambda: Path(
            os.environ.get(
                "CONTIMG_PATHS__PRODUCTS_DIR",
                str(resolve_paths().products_dir),
            )
        ),
        description="Finalized products output directory (images, mosaics, photometry, light curves)",
    )

    @property
    def pipeline_db(self) -> Path:
        """Path to unified pipeline database."""
        return self.state_dir / "db" / "pipeline.sqlite3"

    @property
    def products_db(self) -> Path:
        return self.pipeline_db

    @property
    def registry_db(self) -> Path:
        return self.pipeline_db

    @property
    def queue_db(self) -> Path:
        return self.pipeline_db

    def model_post_init(self, __context: Any) -> None:
        if self.casa_logs_dir is None:
            env_logs = os.environ.get("CONTIMG_PATHS__CASA_LOGS_DIR")
            target = env_logs or str(self.state_dir / "logs" / "casa")
            self.casa_logs_dir = _resolve_writable_path(
                target,
                description="CASA logs directory",
                warn_on_fallback=bool(env_logs),
            )

    @property
    def synthetic_images_dir(self) -> Path:
        """Path to synthetic images directory."""
        synthetic_dir = self.state_dir / "synthetic"
        return synthetic_dir / "images"

    @property
    def synthetic_ms_dir(self) -> Path:
        """Path to synthetic MS files directory."""
        synthetic_dir = self.state_dir / "synthetic"
        return synthetic_dir / "ms"

    @property
    def runs_dir(self) -> Path:
        """Path to isolated run directories.

        Each run gets a unique subdirectory under runs/ to prevent
        cross-contamination between runs with different configurations.
        """
        env_tmpfs = os.environ.get(
            "CONTIMG_TMPFS_DIR", os.environ.get("CONTIMG_PATHS__SHM_DIR")
        )
        base = self.scratch_dir or _resolve_writable_path(
            env_tmpfs or "/dev/shm/contimg",
            description="runs directory base",
            warn_on_fallback=bool(env_tmpfs),
        )
        return base / "runs"

    def get_run_directory(self, create_subdirs: bool = True) -> tuple[Path, str]:
        """Get or create an isolated run directory.

        When isolate_runs is True, creates::

            {runs_dir}/{run_id}/
            {runs_dir}/{run_id}/cal_staging/
            {runs_dir}/{run_id}/conversion_staging/
            {runs_dir}/{run_id}/logs/

        When isolate_runs is False, returns the scratch_dir directly with run_id="default".

        Parameters
        ----------
        create_subdirs : bool
            If True, create common subdirectories

        Returns
        -------
        tuple[Path, str]
            Tuple of (run_directory_path, run_id)

        Examples
        --------
        >>> config = PathsConfig(isolate_runs=True)
        >>> run_dir, run_id = config.get_run_directory()
        >>> print(run_dir)
        >>> # Returns path within scratch_dir or /dev/shm/dsa110-contimg
        """
        from dsa110_continuum.utils.run_isolation import create_run_directory

        env_tmpfs = os.environ.get("CONTIMG_TMPFS_DIR")
        base = self.scratch_dir or _resolve_writable_path(
            env_tmpfs or "/dev/shm/dsa110-contimg",
            description="run directory base",
            warn_on_fallback=bool(env_tmpfs),
        )
        return create_run_directory(
            base_dir=base,
            run_id=self.run_id,
            isolate=self.isolate_runs,
            create_subdirs=create_subdirs,
        )


# ============================================================================
# Conversion Configuration (Unified)
# ============================================================================


class ConversionConfig(BaseModel):
    """
    Unified conversion configuration (UVH5 → MS).

    Merges conversion parameters previously split across env and YAML configs.

    Resolved conflicts:
    - max_workers: 8 (compromise between 4 and 16)
    """

    # Writer configuration
    writer: str = Field(
        default="direct-subband",
        description="Writer strategy: 'direct-subband' (production), 'pyuvdata' (test-only)",
    )

    # Parallelization (UNIFIED - resolved conflict)
    max_workers: int = Field(
        default=8,
        ge=1,
        le=32,
        description="Maximum parallel workers (unified: was 4 in config.py, 16 in pipeline/config.py)",
    )
    omp_threads: int = Field(default=4, description="OpenMP threads per worker")
    parallel_loading: bool = Field(
        default=True, description="Enable parallel I/O for subband loading"
    )
    io_max_workers: int = Field(
        default=4, description="Maximum I/O threads for parallel subband loading"
    )

    # Subband configuration
    expected_subbands: int = Field(
        default=16, ge=1, le=32, description="Expected number of subbands per observation"
    )
    chunk_minutes: float = Field(default=5.0, description="Observation chunk duration in minutes")

    # File normalization tolerance (used by Dagster sensors to rename incoming files;
    # NOT used for query-time grouping — that uses exact time_array[0] match).
    cluster_tolerance_s: float = Field(
        default=120.0,
        description="Tolerance for file normalization: groups incoming files by filesystem timestamp before renaming (seconds). Query-time grouping uses exact time_array[0] match instead.",
    )

    # Behavior flags
    skip_incomplete: bool = Field(
        default=True,
        description="Skip groups with fewer than expected_subbands (production behavior)",
    )
    skip_existing: bool = Field(
        default=False, description="Skip groups that already have output MS files"
    )
    rename_calibrator_fields: bool = Field(
        default=True, description="Auto-detect and rename calibrator fields"
    )
    stage_to_tmpfs: bool = Field(default=True, description="Stage files to tmpfs for faster I/O")
    skip_validation_during_conversion: bool = Field(
        default=True,
        description="Skip validation checks during conversion (validate separately)",
    )
    skip_calibration_recommendations: bool = Field(
        default=True,
        description="Skip writing calibration recommendations JSON files",
    )

    # Timeout and retry
    timeout_s: float = Field(default=3600.0, description="Conversion timeout in seconds (1 hour)")
    retry_count: int = Field(default=2, description="Number of retries on failure")

    # Writer-specific configuration
    batch_size: int = Field(default=4, description="Subbands to load per batch (for batch writers)")

    # Advanced / Operational overrides
    remap_input_dir: Path | None = Field(
        default=None,
        description="Alternative directory to read HDF5 files from (for testing/golden datasets)",
    )
    ms_suffix: str | None = Field(
        default=None, description="Suffix to append to output MS names (for testing/versions)"
    )
    scratch_dir: Path | None = Field(
        default=None, description="Explicit scratch directory override"
    )
    execution_mode: str = Field(
        default="auto", description="Execution mode: 'auto', 'inprocess', 'subprocess'"
    )
    memory_limit_mb: int | None = Field(
        default=None, description="Explicit memory limit override in MB"
    )
    timeout_seconds: float = Field(default=3600.0, description="Execution timeout in seconds")


# ============================================================================
# Indexing Configuration
# ============================================================================


class IndexingConfig(BaseModel):
    """Configuration for file indexing behavior."""

    enabled: bool = Field(default=True, description="Enable/disable automatic file indexing")

    include_patterns: list[str] = Field(
        default=["*_sb*.hdf5"], description="Glob patterns for files to index"
    )

    exclude_patterns: list[str] = Field(
        default=[], description="Glob patterns for files to exclude from indexing"
    )

    date_range_start: str | None = Field(
        default=None, description="Only index files from this date onwards (YYYY-MM-DD)"
    )

    date_range_end: str | None = Field(
        default=None, description="Only index files up to this date (YYYY-MM-DD)"
    )

    chunk_size: int = Field(default=1000, description="Number of files to index per sensor tick")

    auto_normalize: bool = Field(
        default=True, description="Automatically normalize filenames before indexing"
    )

    auto_conversion: bool = Field(
        default=False,
        description="Automatically trigger conversion when complete subband groups are detected (default: False for manual backfill)",
    )

    event_driven: bool = Field(
        default=True,
        description="Use filesystem events (inotify) instead of polling for file detection",
    )

    min_subbands_threshold: int = Field(
        default=13,
        ge=1,
        le=16,
        description="Minimum number of subbands to consider a group complete (13-16, allows for missing files)",
    )

    debounce_seconds: float = Field(
        default=5.0,
        ge=0.1,
        description="Wait time after last file write before processing group (seconds)",
    )


# ============================================================================
# Calibration Configuration (Unified)
# ============================================================================


class CalibrationConfig(BaseModel):
    """
    Unified calibration configuration.

    Consolidates parameters from pipeline/config.py CalibrationConfig.
    """

    # Bandpass calibration
    cal_bp_minsnr: float = Field(
        default=3.0,
        ge=1.0,
        le=10.0,
        description="Minimum SNR for bandpass calibration",
    )

    # Gain calibration
    cal_gain_solint: str = Field(default="inf", description="Gain solution interval (CASA format)")

    # Reference antenna
    default_refant: str = Field(default="103", description="Default reference antenna for DSA-110")
    auto_select_refant: bool = Field(
        default=True,
        description="Automatically select reference antenna if default fails",
    )

    # TMPFS staging for faster I/O during calibration
    use_tmpfs_staging: bool = Field(
        default=True,
        description="Copy MS to tmpfs during calibration for 3-5x faster I/O. "
        "MS is copied back to persistence after calibration completes.",
    )

    # Rephase calibrated MS back to meridian for imaging
    rephase_to_meridian_after_apply: bool = Field(
        default=True,
        description=(
            "After applying calibration on a calibrator-phaseshifted MS, rephase back "
            "to the median meridian position for imaging."
        ),
    )

    # Quick flux scale sanity check (MODEL_DATA vs CORRECTED_DATA) after applycal
    flux_scale_check_enabled: bool = Field(
        default=True,
        description="Run a lightweight MODEL_DATA vs CORRECTED_DATA flux scale check after applycal.",
    )
    flux_scale_warn_factor: float = Field(
        default=1.5,
        ge=1.0,
        description="Warn if flux scale factor exceeds this value.",
    )
    flux_scale_fail_factor: float = Field(
        default=5.0,
        ge=1.0,
        description="Fail if flux scale factor exceeds this value.",
    )
    flux_scale_sample_rows: int = Field(
        default=500,
        ge=10,
        description="Number of MS rows to sample for the flux scale check.",
    )
    flux_scale_channel_fraction: float = Field(
        default=0.5,
        gt=0.0,
        le=1.0,
        description="Fraction of central channels to sample for the flux scale check.",
    )


# ============================================================================
# Imaging Configuration (Unified)
# ============================================================================


class ImagingConfig(BaseModel):
    """
    Unified imaging configuration.

    Consolidates parameters from pipeline/config.py ImagingConfig.
    """

    # Quality tier preset (optional, can be set programmatically)
    quality_tier: str | None = Field(
        default=None,
        description="Imaging quality tier: 'fast', 'standard', 'high'. If set, overrides niter/imsize.",
    )

    # Basic imaging parameters
    imsize: int = Field(
        default=2048,
        ge=64,
        description="Image size in pixels (must be even)",
    )
    cell: float = Field(
        default=0.2,
        gt=0.0,
        description="Pixel size in arcseconds",
    )
    niter: int = Field(
        default=5000,
        ge=0,
        description="Maximum number of cleaning iterations",
    )
    robust: float = Field(
        default=0.0,
        ge=-2.0,
        le=2.0,
        description="Briggs robust parameter",
    )
    threshold: str = Field(
        default="0.1mJy",
        description="Cleaning threshold (e.g., '0.1mJy')",
    )

    # Field selection
    field: str | None = Field(None, description="Field name or coordinates to image")
    refant: str | None = Field(default="103", description="Reference antenna")

    # Gridding configuration
    gridder: str = Field(
        default="idg",
        description="Gridding algorithm: 'idg' (best w-term), 'wgridder', 'wstacking'",
    )
    wprojplanes: int = Field(default=-1, description="W-projection planes (-1 for auto-select)")

    # Masking configuration
    use_unicat_mask: bool = Field(
        default=True,
        description="Use unified catalog mask for imaging (2-4x faster clean)",
    )
    mask_radius_arcsec: float = Field(
        default=60.0,
        ge=10.0,
        le=300.0,
        description="Mask radius around catalog sources (arcsec)",
    )

    # Galvin adaptive clip (artifact suppression during self-cal)
    use_galvin_clip: bool = Field(
        default=True,
        description="Enable Galvin adaptive minimum absolute clip for artifact suppression",
    )
    galvin_box_size: int = Field(
        default=100,
        ge=10,
        le=500,
        description="Box size for adaptive clip sliding window (pixels)",
    )
    galvin_adaptive_depth: int = Field(
        default=3,
        ge=1,
        le=10,
        description="Max iterations for adaptive box subdivision",
    )

    # GPU acceleration
    gpu_enabled: bool = Field(
        default=True,
        description="Enable GPU acceleration if available (auto-detects GPU)",
    )
    gpu_idg_mode: str = Field(
        default="gpu",
        description="IDG gridder mode: 'cpu', 'gpu', or 'hybrid'",
    )
    gpu_device_ids: list[int] | None = Field(
        default=None,
        description="GPU device IDs to use (None = all available GPUs)",
    )

    # Validation
    run_catalog_validation: bool = Field(
        default=True,
        description="Run catalog-based flux scale validation after imaging",
    )
    catalog_validation_catalog: str = Field(
        default="unicat",
        description="Catalog to use for validation: 'nvss', 'vlass', 'unicat'",
    )

    # Field of view / extent handling
    fixed_extent_deg: float = Field(
        default=3.5,
        description="Fixed FoV extent (degrees) used when derivation is disabled",
    )
    derive_extent_from_telescope: bool = Field(
        default=False,
        description="Derive FoV from telescope spec instead of using fixed extent",
    )
    primary_beam_kappa: float = Field(
        default=1.02,
        description="Scale factor for primary beam FWHM (lambda/D) in FoV derivation",
    )
    fov_padding_factor: float = Field(
        default=1.3,
        description="Padding factor applied to FWHM when deriving FoV extent",
    )
    min_extent_deg: float = Field(
        default=1.0,
        description="Minimum FoV extent clamp (degrees)",
    )
    max_extent_deg: float = Field(
        default=8.0,
        description="Maximum FoV extent clamp (degrees)",
    )
    telescope_yaml_path: str | None = Field(
        default=None,
        description="Optional path to telescope.yaml for FoV derivation",
    )


# ============================================================================
# GPU Configuration
# ============================================================================


class GPUSettings(BaseSettings):
    """GPU acceleration configuration."""

    model_config = SettingsConfigDict(
        env_prefix="PIPELINE_GPU_",
        extra="ignore",
    )

    enabled: bool = Field(default=True, description="Enable GPU acceleration")
    prefer_gpu: bool = Field(
        default=True,
        description="Prefer GPU execution when available (can be disabled to force CPU)",
    )
    devices: str = Field(default="", description="Comma-separated GPU device IDs (empty = all)")
    gridder: str = Field(
        default="idg", description="WSClean gridder: idg (GPU), wgridder (CPU), wstacking (CPU)"
    )
    idg_mode: str = Field(default="gpu", description="IDG mode: cpu, gpu, or hybrid")
    memory_fraction: float = Field(
        default=0.9, ge=0.0, le=1.0, description="Max fraction of GPU memory to use"
    )
    min_array_size: int = Field(
        default=1_000_000,
        ge=1,
        description="Minimum array elements before opting into GPU to avoid launch overhead",
    )


# ============================================================================
# Validation Configuration
# ============================================================================


class ValidationConfig(BaseModel):
    """Configuration for validation stage."""

    enabled: bool = Field(default=True, description="Enable validation stage")

    catalog: str = Field(
        default="unicat",
        description="Catalog to use for validation: 'nvss', 'vlass', 'unicat'",
    )

    validation_types: list[str] = Field(
        default=["astrometry", "flux_scale", "source_counts"],
        description="Types of validation to run",
    )

    generate_html_report: bool = Field(default=True, description="Generate HTML validation report")

    min_snr: float = Field(default=5.0, description="Minimum SNR for source detection")

    search_radius_arcsec: float = Field(
        default=10.0, description="Search radius for source matching (arcsec)"
    )

    catalog_radius_deg: float = Field(default=2.0, description="Catalog search radius (degrees)")

    max_astrometry_rms_arcsec: float = Field(
        default=5.0, description="Maximum astrometry RMS to pass validation (arcsec)"
    )

    archive_to_hdd: bool = Field(
        default=True,
        description="Archive MS files to HDD (/data/stage/) after successful validation",
    )


# ============================================================================
# Cross-Match Configuration
# ============================================================================


class CrossMatchConfig(BaseModel):
    """Configuration for catalog cross-matching stage."""

    enabled: bool = Field(default=True, description="Enable cross-matching stage")
    catalogs: list[str] = Field(
        default=["unicat"],
        description="Catalogs to cross-match against",
    )
    catalog_radius_deg: float = Field(
        default=1.5,
        ge=0.1,
        le=10.0,
        description="Catalog query radius around field center (degrees)",
    )
    method: str = Field(
        default="basic",
        description="Matching method: 'basic' (nearest neighbor) or 'advanced' (all matches)",
    )
    store_in_database: bool = Field(
        default=True,
        description="Store cross-match results in database",
    )
    min_separation_arcsec: float = Field(
        default=0.1,
        ge=0.0,
        description="Minimum separation to consider a valid match (arcsec)",
    )
    max_separation_arcsec: float = Field(
        default=60.0,
        ge=1.0,
        description="Maximum separation to consider a valid match (arcsec)",
    )
    calculate_spectral_indices: bool = Field(
        default=True,
        description="Calculate spectral indices from multi-catalog matches",
    )


# ============================================================================
# Variable Source Detection Configuration
# ============================================================================


class VariableSourceDetectionConfig(BaseModel):
    """Configuration for variable source detection stage.
    
    This stage detects flux variability in sources compared to baseline catalogs,
    including ESE (Extreme Scattering Event) candidates.
    """

    enabled: bool = Field(default=False, description="Enable variable source detection stage")
    detection_threshold_sigma: float = Field(
        default=5.0,
        ge=3.0,
        description="Significance threshold for new sources [sigma]",
    )
    variability_threshold_sigma: float = Field(
        default=3.0,
        ge=2.0,
        description="Threshold for flux variability [sigma]",
    )
    match_radius_arcsec: float = Field(
        default=10.0,
        ge=1.0,
        le=30.0,
        description="Matching radius for baseline catalog [arcsec]",
    )
    baseline_catalog: str = Field(
        default="unicat",
        description="Baseline catalog for variability detection: 'nvss', 'first', 'racs', 'unicat'",
    )
    alert_threshold_sigma: float = Field(
        default=7.0,
        ge=5.0,
        description="Minimum significance for generating alerts [sigma]",
    )
    store_lightcurves: bool = Field(
        default=True,
        description="Store flux measurements in lightcurves table",
    )
    min_baseline_flux_mjy: float = Field(
        default=10.0,
        ge=1.0,
        description="Minimum baseline flux for fading source detection [mJy]",
    )


# ============================================================================
# Astrometric Calibration Configuration
# ============================================================================


class AstrometricCalibrationConfig(BaseModel):
    """Configuration for astrometric calibration."""

    enabled: bool = Field(
        default=False,
        description="Enable astrometric refinement in mosaic stage",
    )
    reference_catalog: str = Field(
        default="FIRST",
        description="High-precision reference catalog: 'FIRST'",
    )
    match_radius_arcsec: float = Field(
        default=5.0,
        ge=1.0,
        le=15.0,
        description="Matching radius for reference catalog [arcsec]",
    )
    min_matches: int = Field(
        default=10,
        ge=5,
        description="Minimum number of matches required for solution",
    )
    flux_weight: bool = Field(
        default=True,
        description="Weight astrometric offsets by source flux",
    )
    apply_correction: bool = Field(
        default=True,
        description="Apply WCS correction to output mosaics",
    )
    accuracy_target_mas: float = Field(
        default=1000.0,
        ge=100.0,
        description="Target astrometric accuracy [mas]",
    )


# ============================================================================
# Mosaic Configuration
# ============================================================================


class MosaicConfig(BaseModel):
    """Configuration for mosaic creation stage."""

    enabled: bool = Field(default=True, description="Enable mosaic creation stage")
    min_images: int = Field(
        default=5, ge=1, description="Minimum images required to create a mosaic"
    )
    enable_photometry: bool = Field(
        default=True, description="Run photometry automatically after mosaic creation"
    )
    enable_crossmatch: bool = Field(
        default=True, description="Run cross-matching after mosaic creation"
    )
    output_format: str = Field(default="fits", description="Output format: 'fits' or 'casa'")


# ============================================================================
# Light Curve Configuration
# ============================================================================


class LightCurveConfig(BaseModel):
    """Configuration for light curve computation stage.

    This stage computes variability metrics from photometry measurements,
    enabling automated detection of variable sources and ESE candidates.
    """

    enabled: bool = Field(default=True, description="Enable light curve computation stage")
    min_epochs: int = Field(
        default=3,
        ge=2,
        description="Minimum number of epochs required to compute variability metrics",
    )
    eta_threshold: float = Field(
        default=2.0,
        ge=0.0,
        description="Eta metric threshold for flagging variable sources",
    )
    v_threshold: float = Field(
        default=0.1,
        ge=0.0,
        le=1.0,
        description="V metric (std/mean) threshold for flagging variable sources",
    )
    sigma_threshold: float = Field(
        default=3.0,
        ge=1.0,
        description="Sigma deviation threshold for ESE candidate detection",
    )
    use_normalized_flux: bool = Field(
        default=True,
        description="Use normalized flux values for variability computation",
    )
    update_database: bool = Field(
        default=True,
        description="Update variability_stats table in products database",
    )
    trigger_alerts: bool = Field(
        default=True,
        description="Trigger alerts for sources exceeding variability thresholds",
    )


# ============================================================================
# Photometry Configuration
# ============================================================================


class PhotometryConfig(BaseModel):
    """Configuration for photometry stage."""

    enabled: bool = Field(default=True, description="Enable photometry stage")
    catalog: str = Field(
        default="unicat",
        description="Catalog to use for source selection: 'nvss', 'vlass', 'unicat'",
    )
    catalog_radius_deg: float = Field(
        default=2.0,
        ge=0.1,
        description="Search radius for catalog query [deg]",
    )
    target_snr: float = Field(
        default=10.0,
        ge=3.0,
        description="Target SNR for adaptive binning",
    )
    min_flux_mjy: float = Field(
        default=5.0,
        ge=0.0,
        description="Minimum flux for source selection [mJy]",
    )
    imsize: int = Field(
        default=256,
        ge=64,
        description="Image size for photometry [pixels]",
    )
    max_width: int = Field(
        default=120,
        ge=1,
        description="Maximum bin width in seconds",
    )
    quality_tier: str = Field(
        default="standard",
        description="Imaging quality tier: 'fast', 'standard', 'high'",
    )
    backend: str = Field(
        default="wsclean",
        description="Imaging backend: 'wsclean' or 'casa'",
    )
    parallel: bool = Field(
        default=True,
        description="Run photometry in parallel across sources",
    )
    max_workers: int = Field(
        default=4,
        ge=1,
        description="Maximum number of parallel workers",
    )
    serialize_ms_access: bool = Field(
        default=True,
        description="Serialize access to MS file to avoid locking issues",
    )
    sources: list[dict[str, float]] = Field(
        default_factory=list,
        description="Optional list of manual sources [{'ra': deg, 'dec': deg}]",
    )
    update_database: bool = Field(
        default=True,
        description="Update photometry table in products database",
    )
    generate_html_report: bool = Field(
        default=True,
        description="Generate HTML photometry report",
    )

    aperture_type: str = Field(
        default="adaptive",
        description="Aperture type: 'adaptive', 'fixed', 'psf'",
    )

    fixed_aperture_arcsec: float = Field(
        default=30.0,
        ge=5.0,
        description="Fixed aperture radius [arcsec] (if aperture_type='fixed')",
    )

    # Forced photometry measurement settings
    box_size_pix: int = Field(
        default=5,
        ge=3,
        description="Box size for peak measurement [pixels]",
    )
    annulus_inner_pix: int = Field(
        default=30,
        ge=5,
        description="Inner radius for off-source noise annulus [pixels, ~3 beams]",
    )
    annulus_outer_pix: int = Field(
        default=50,
        ge=10,
        description="Outer radius for off-source noise annulus [pixels, ~5 beams]",
    )

    # SNR-based validation (DSA-110 doesn't absolute flux calibrate)
    snr_detection_threshold: float = Field(
        default=3.0,
        ge=2.0,
        description="Minimum SNR for source detection",
    )
    snr_strong_threshold: float = Field(
        default=5.0,
        ge=3.0,
        description="SNR threshold for strong detection",
    )
    min_detections_pass: int = Field(
        default=3,
        ge=1,
        description="Minimum number of detections for validation pass",
    )


# ============================================================================
# QA Configuration
# ============================================================================


class QAConfig(BaseModel):
    """Configuration for QA plot generation."""

    # Calibration QA
    generate_closure_phase_plots: bool = Field(
        default=True,
        description="Generate closure phase diagnostic plots during calibration",
    )
    generate_antenna_correlation_plots: bool = Field(
        default=True,
        description="Generate antenna correlation analysis plots",
    )

    # Imaging QA
    generate_imaging_qa_plots: bool = Field(
        default=True,
        description="Generate beam analysis and PSF correlation plots",
    )
    generate_uv_coverage_plots: bool = Field(
        default=True,
        description="Generate UV coverage plots before imaging",
    )

    # Self-calibration QA
    generate_selfcal_convergence_plots: bool = Field(
        default=True,
        description="Generate self-calibration convergence plots",
    )
    generate_image_comparison_plots: bool = Field(
        default=True,
        description="Generate image comparison plots (pre vs post selfcal)",
    )

    # Source analysis QA
    generate_lightcurve_plots: bool = Field(
        default=True,
        description="Generate source lightcurve plots",
    )
    generate_photometry_plots: bool = Field(
        default=True,
        description="Generate photometry diagnostic plots",
    )

    # Spectral analysis QA
    generate_spectral_index_plots: bool = Field(
        default=True,
        description="Generate spectral index maps and SEDs",
    )


# ============================================================================
# Timeout Configuration
# ============================================================================


class TimeoutConfig(BaseModel):
    """Configuration for system timeouts."""

    db_connection: float = Field(
        default=30.0,
        description="Database connection timeout in seconds",
    )
    db_busy_timeout_ms: int = Field(
        default=32100,
        description="SQLite busy timeout in milliseconds (PRAGMA busy_timeout)",
    )


# ============================================================================
# Unified Pipeline Configuration
# ============================================================================


class UnifiedPipelineConfig(BaseSettings):
    """Unified pipeline configuration supporting multiple input methods.

    This consolidates the duplicate configuration systems (env vars and YAML)
    into one continuum-owned settings object.

    Notes
    -----
    Input Methods:

    1. Environment variables: ``CONTIMG_CONVERSION_MAX_WORKERS=8``
    2. YAML file: ``UnifiedPipelineConfig.from_yaml('config.yaml')``
    3. Dictionary: ``UnifiedPipelineConfig.from_dict({...})``
    4. Programmatic: ``UnifiedPipelineConfig(conversion=ConversionConfig(...))``

    Precedence (highest to lowest):

    1. Explicit function arguments
    2. YAML file configuration
    3. Environment variables
    4. Default values

    Examples
    --------
    >>> # Load from environment
    >>> config = UnifiedPipelineConfig()

    >>> # Load from YAML
    >>> config = UnifiedPipelineConfig.from_yaml('pipeline.yaml')

    >>> # Programmatic with overrides
    >>> config = UnifiedPipelineConfig(
    ...     paths=PathsConfig(
    ...         input_dir=Path('/data/incoming'),
    ...         output_dir=Path('/data/ms')
    ...     ),
    ...     conversion=ConversionConfig(max_workers=12)
    ... )
    """

    model_config = SettingsConfigDict(
        env_prefix="CONTIMG_",
        env_nested_delimiter="__",
        extra="ignore",
        validate_default=True,
    )

    # Configuration sections
    paths: PathsConfig = Field(default_factory=PathsConfig)
    conversion: ConversionConfig = Field(default_factory=ConversionConfig)
    indexing: IndexingConfig = Field(default_factory=IndexingConfig)
    calibration: CalibrationConfig = Field(default_factory=CalibrationConfig)
    imaging: ImagingConfig = Field(default_factory=ImagingConfig)
    gpu: GPUSettings = Field(default_factory=GPUSettings)
    validation: ValidationConfig = Field(default_factory=ValidationConfig)
    qa: QAConfig = Field(default_factory=QAConfig)
    photometry: PhotometryConfig = Field(default_factory=PhotometryConfig)
    crossmatch: CrossMatchConfig = Field(default_factory=CrossMatchConfig)
    variable_source_detection: VariableSourceDetectionConfig = Field(default_factory=VariableSourceDetectionConfig)
    astrometric_calibration: AstrometricCalibrationConfig = Field(
        default_factory=AstrometricCalibrationConfig
    )
    mosaic: MosaicConfig = Field(default_factory=MosaicConfig)
    light_curve: LightCurveConfig = Field(default_factory=LightCurveConfig)
    timeouts: TimeoutConfig = Field(default_factory=TimeoutConfig)

    def validate_environment(self) -> None:
        """
        Validate critical environment configuration.

        Raises
        ------
        ValueError
            If required directories do not exist or permissions are missing.
        """
        # Validate critical paths if they are set
        if self.paths.input_dir and not self.paths.input_dir.exists():
            logging.getLogger(__name__).warning(
                "Input directory does not exist: %s. Pipeline will not find new data.",
                self.paths.input_dir,
            )

        if self.paths.output_dir:
            try:
                self.paths.output_dir.mkdir(parents=True, exist_ok=True)
                if not os.access(self.paths.output_dir, os.W_OK):
                    raise PermissionError(f"Directory {self.paths.output_dir} is not writable")
            except (PermissionError, OSError) as e:
                # Attempt to fix by resolving again (should have been handled by default factory but
                # this is for when user sets it via other means)
                logging.getLogger(__name__).warning(
                    "Output directory %s is not writable: %s. This might cause issues. Ensure write permissions.",
                    self.paths.output_dir,
                    e,
                )

        # Check CASA configuration alignment
        try:
            import casaconfig
            try:
                from casaconfig import config as casa_config
            except ImportError:
                casa_config = getattr(casaconfig, "config", None)

            if casa_config and casa_config.datapath:
                # Log active CASA data paths for debugging
                logging.getLogger(__name__).debug(
                    "Active CASA datapath: %s", casa_config.datapath
                )
            else:
                logging.getLogger(__name__).warning(
                    "casaconfig.config.datapath is empty. CASA tools may fail to find measures."
                )
        except ImportError:
            logging.getLogger(__name__).debug("casaconfig not available for validation.")

    @classmethod
    def from_yaml(cls, path: str) -> UnifiedPipelineConfig:
        """
        Load configuration from YAML file.

        YAML values take precedence over environment variables.

        Environment variables in YAML files are automatically expanded using
        the syntax ${VAR} or ${VAR:-default}. This prevents literal strings
        like '${CONTIMG_STATE_DIR}' from being used as directory names.

        Parameters
        ----------
        path : str
            Path to YAML configuration file

        Returns
        -------
        UnifiedPipelineConfig
            UnifiedPipelineConfig instance

        Examples
        --------
        >>> config = UnifiedPipelineConfig.from_yaml('pipeline.yaml')

        # In YAML file, environment variables are expanded:
        # paths:
        #   state_dir: ${CONTIMG_STATE_DIR:-/data/dsa110-contimg/state}
        """
        yaml_path = Path(path)
        if not yaml_path.exists():
            raise FileNotFoundError(f"Configuration file not found: {path}")

        # Use advanced YAML loader with environment variable expansion
        # This prevents bugs where literal strings like '${CONTIMG_STATE_DIR}'
        # are used as directory names instead of being expanded
        data = load_yaml_with_env(yaml_path, expand_vars=True)

        if data is None:
            data = {}

        return cls(**data)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> UnifiedPipelineConfig:
        """
        Load configuration from dictionary.

        Dictionary values take precedence over environment variables.

        Parameters
        ----------
        data : Dict[str, Any]
            Configuration dictionary

        Returns
        -------
        UnifiedPipelineConfig
            UnifiedPipelineConfig instance

        Examples
        --------
        >>> config = UnifiedPipelineConfig.from_dict({
        ...     'paths': {'input_dir': '/data', 'output_dir': '/output'},
        ...     'conversion': {'max_workers': 12}
        ... })
        """
        return cls(**data)

    @classmethod
    def from_env(cls) -> UnifiedPipelineConfig:
        """
        Load configuration from environment variables.

        Reads from environment variables with CONTIMG_ prefix.
        This is the same as calling UnifiedPipelineConfig() with no arguments,
        provided as an explicit method for consistency with other config classes.

        Returns
        -------
        UnifiedPipelineConfig
            UnifiedPipelineConfig instance

        Examples
        --------
        >>> import os
        >>> os.environ['CONTIMG_CONVERSION__MAX_WORKERS'] = '12'
        >>> config = UnifiedPipelineConfig.from_env()
        >>> print(config.conversion.max_workers)  # 12
        """
        return cls()

    def to_dict(self) -> dict[str, Any]:
        """
        Export configuration to dictionary.

        Returns
        -------
            Dictionary representation of configuration
        """
        return self.model_dump()

    def to_yaml(self, path: str) -> None:
        """
        Export configuration to YAML file.

        Parameters
        ----------
        path : str
            Output YAML file path

        Examples
        --------
        >>> config.to_yaml('pipeline.yaml')
        """
        yaml_path = Path(path)
        yaml_path.parent.mkdir(parents=True, exist_ok=True)

        data = self.to_dict()
        # Convert Path objects to strings for YAML serialization

        def convert_paths(obj):
            if isinstance(obj, dict):
                return {k: convert_paths(v) for k, v in obj.items()}
            elif isinstance(obj, list):
                return [convert_paths(item) for item in obj]
            elif isinstance(obj, Path):
                return str(obj)
            return obj

        data = convert_paths(data)

        with open(yaml_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(data, f, default_flow_style=False, sort_keys=False)

    def to_hash(self) -> str:
        """Return a stable hash of the current settings for change detection."""
        # Create stable dict representation
        config_dict = {
            "paths": {
                "input_dir": str(self.paths.input_dir),
                "output_dir": str(self.paths.output_dir),
            },
            "conversion": {
                "max_workers": self.conversion.max_workers,
            },
            "calibration": {
                "cal_bp_minsnr": self.calibration.cal_bp_minsnr,
            },
            "imaging": {
                "gridder": self.imaging.gridder,
            },
        }

        # Create stable JSON and hash
        json_str = json.dumps(config_dict, sort_keys=True)
        return hashlib.sha256(json_str.encode()).hexdigest()[:16]


# ============================================================================
# Singleton instance for global access (optional)
# ============================================================================

# Global config instance (can be overridden)
config: UnifiedPipelineConfig | None = None


def initialize_config(
    yaml_path: str | None = None,
    config_dict: dict[str, Any] | None = None,
) -> UnifiedPipelineConfig:
    """
    Initialize global configuration.

    Parameters
    ----------
    yaml_path : Optional[str]
        Optional path to YAML configuration file
    config_dict : Optional[Dict[str, Any]]
        Optional configuration dictionary

    Returns
    -------
    UnifiedPipelineConfig
        Initialized UnifiedPipelineConfig instance

    Examples
    --------
    Initialize from YAML::

        >>> config = initialize_config(yaml_path='pipeline.yaml')

    Initialize from dict::

        >>> config = initialize_config(config_dict={'paths': {...}})

    Initialize from environment (default)::

        >>> config = initialize_config()
    """
    global config

    if yaml_path is not None:
        config = UnifiedPipelineConfig.from_yaml(yaml_path)
    elif config_dict is not None:
        config = UnifiedPipelineConfig.from_dict(config_dict)
    else:
        config = UnifiedPipelineConfig()

    return config


def get_config() -> UnifiedPipelineConfig:
    """
    Get global configuration instance.

    Initializes with default environment variables if not already initialized.

    Returns
    -------
    UnifiedPipelineConfig
        Global UnifiedPipelineConfig instance

    Examples
    --------
    >>> config = get_config()
    >>> print(config.conversion.max_workers)
    """
    global config
    if config is None:
        config = UnifiedPipelineConfig()
    return config


# Backward-compatible alias expected by legacy callers/tests.
def get_settings() -> UnifiedPipelineConfig:
    """Return the global UnifiedPipelineConfig instance."""
    return get_config()

# ============================================================================
# Backward Compatibility - Singleton Instance
# ============================================================================


__all__ = [
    "CONTIMG_BASE",
    "PathsConfig",
    "ConversionConfig",
    "CalibrationConfig",
    "ImagingConfig",
    "GPUSettings",
    "ValidationConfig",
    "PhotometryConfig",
    "CrossMatchConfig",
    "VariableSourceDetectionConfig",
    "AstrometricCalibrationConfig",
    "MosaicConfig",
    "LightCurveConfig",
    "TimeoutConfig",
    "UnifiedPipelineConfig",
    "config",
    "settings",
    "initialize_config",
    "get_config",
    "get_settings",
]

# Global singleton instance (initialized on first access)
_settings: UnifiedPipelineConfig | None = None


# Expose 'settings' as a proxy to get_config() for backward compatibility
# This allows 'from dsa110_continuum.unified_config import settings' to work
class SettingsProxy:
    def __getattr__(self, name):
        return getattr(get_config(), name)


settings = SettingsProxy()
