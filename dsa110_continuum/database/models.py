# Vendored from dsa110-contimg @ /data/dsa110-contimg/backend/src (H17), 2026-07-03,
# infrastructure/database/models.py, as part of the contimg-import-retirement migration
# (docs/rse/specs/plan-contimg-import-retirement.md, Phase 5).
"""
SQLAlchemy ORM models for DSA-110 Continuum Imaging Pipeline database.

This module defines ORM models for the unified pipeline database (pipeline.sqlite3).
All domains are consolidated into a single database with logical table groups:

- Products domain: MS registry (ms_index), images, photometry, transients
- Calibration domain: Calibration table registry (caltables)
- HDF5 domain: HDF5 file index (hdf5_file_index)
- Queue domain: Streaming queue management (processing_queue, performance_metrics)
- Data registry domain: Data staging and publishing (data_registry)

Historical note: These domains were previously in separate .sqlite3 files
but have been unified for simpler queries, atomic transactions, and easier ops.

Examples
--------
::

    from dsa110_contimg.infrastructure.database.models import (
        MSIndex, Image, Photometry, Caltable,
        HDF5FileIndex, DataRegistry
    )
    from dsa110_contimg.infrastructure.database.session import get_session

    with get_session("pipeline") as session:
        images = session.query(Image).filter_by(type="dirty").all()

Notes
-----
All databases use WAL mode for concurrent access with 30s timeout.
Use scoped_session for multi-threaded contexts (e.g., streaming converter).
"""

from __future__ import annotations

from sqlalchemy import (
    Column,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import declarative_base, relationship

# Create separate base classes for each database to avoid table conflicts
ProductsBase = declarative_base()
ScienceBase = declarative_base()
CalRegistryBase = declarative_base()
HDF5Base = declarative_base()
IngestBase = declarative_base()  # DEPRECATED: Tracked for archival reference only
DataRegistryBase = declarative_base()
QueueBase = declarative_base()
TelemetryBase = declarative_base()


# =============================================================================
# Products Domain Models (ms_index, images, photometry tables)
# =============================================================================


class MSIndex(ProductsBase):
    """
    Measurement Set index tracking processing state and metadata.

    This table tracks all MS files in the pipeline, their processing stage,
    and associated metadata like pointing coordinates and field names.
    """

    __tablename__ = "ms_index"

    path = Column(String, primary_key=True, doc="Full path to the MS file")
    start_mjd = Column(Float, doc="Start time of observation in MJD")
    end_mjd = Column(Float, doc="End time of observation in MJD")
    mid_mjd = Column(Float, doc="Mid-point time of observation in MJD")
    processed_at = Column(Float, doc="Unix timestamp when MS was processed")
    status = Column(String, doc="Processing status (e.g., 'pending', 'completed', 'failed')")
    stage = Column(String, doc="Pipeline stage (e.g., 'converted', 'calibrated', 'imaged')")
    stage_updated_at = Column(Float, doc="Unix timestamp of last stage update")
    cal_applied = Column(Integer, default=0, doc="Whether calibration has been applied (0/1)")
    imagename = Column(String, doc="Associated image name/path")
    ra_deg = Column(Float, doc="Right Ascension in degrees")
    dec_deg = Column(Float, doc="Declination in degrees")
    field_name = Column(String, doc="CASA field name")
    pointing_ra_deg = Column(Float, doc="Pointing RA in degrees")
    pointing_dec_deg = Column(Float, doc="Pointing Dec in degrees")

    # UV Coverage QA metrics
    uv_coverage_score = Column(Float, doc="UV coverage quality score (0-1)")
    baseline_count = Column(Integer, doc="Number of baselines")
    shortest_baseline = Column(Float, doc="Shortest baseline in meters")
    longest_baseline = Column(Float, doc="Longest baseline in meters")

    # Note: relationship to Image removed - no FK constraint in actual database
    # Use manual queries to join if needed

    __table_args__ = (
        Index("idx_ms_index_stage_path", "stage", "path"),
        Index("idx_ms_index_status", "status"),
    )

    def __repr__(self):
        return f"<MSIndex(path='{self.path}', stage='{self.stage}')>"


class Image(ProductsBase):
    """
    Image metadata and quality metrics.

    Stores information about generated FITS images including beam properties,
    noise measurements, and coordinate information.

    Note: ms_path references ms_index.path but the database does not enforce
    a foreign key constraint for backward compatibility with existing data.
    """

    __tablename__ = "images"

    id = Column(Integer, primary_key=True, autoincrement=True)
    path = Column(String, nullable=False, doc="Full path to image file")
    # No FK constraint - matches actual database schema for backward compatibility
    ms_path = Column(String, nullable=False, doc="Source MS path (references ms_index.path)")
    created_at = Column(Float, nullable=False, doc="Unix timestamp when image was created")
    type = Column(String, nullable=False, doc="Image type (e.g., 'dirty', 'clean', 'residual')")
    beam_major_arcsec = Column(Float, doc="Beam major axis in arcseconds")
    beam_minor_arcsec = Column(Float, doc="Beam minor axis in arcseconds")
    beam_pa_deg = Column(Float, doc="Beam position angle in degrees")
    noise_jy = Column(Float, doc="RMS noise level in Jy/beam")
    pbcor = Column(Integer, default=0, doc="Primary beam corrected (0/1)")
    format = Column(String, default="fits", doc="Image format (fits, casa)")
    dynamic_range = Column(Float, doc="Peak/RMS dynamic range")
    field_name = Column(String, doc="CASA field name")
    center_ra_deg = Column(Float, doc="Image center RA in degrees")
    center_dec_deg = Column(Float, doc="Image center Dec in degrees")
    imsize_x = Column(Integer, doc="Image size in X pixels")
    imsize_y = Column(Integer, doc="Image size in Y pixels")
    cellsize_arcsec = Column(Float, doc="Pixel size in arcseconds")
    freq_ghz = Column(Float, doc="Center frequency in GHz")
    bandwidth_mhz = Column(Float, doc="Bandwidth in MHz")
    integration_sec = Column(Float, doc="Total integration time in seconds")

    # Image QA metrics
    psf_correlation_value = Column(Float, doc="PSF correlation coefficient (artifact indicator)")
    beam_quality_score = Column(Float, doc="Beam quality score (0-1)")
    sidelobe_max = Column(Float, doc="Maximum sidelobe level (relative to peak)")

    # Relationships - note: ms_path is just a string column without FK constraint
    # Use primaryjoin to define the relationship explicitly
    # Note: relationship removed for backward compatibility with existing data
    # that may have images without corresponding MS records

    __table_args__ = (Index("idx_images_ms_path", "ms_path"),)

    def __repr__(self):
        return f"<Image(id={self.id}, path='{self.path}', type='{self.type}')>"


class Photometry(ProductsBase):
    """
    Source photometry measurements from images.

    Records flux measurements for detected sources, supporting lightcurve
    analysis and variability studies.

    Note: image_path references images.path but the database does not enforce
    a foreign key constraint for backward compatibility with existing data.
    """

    __tablename__ = "photometry"

    id = Column(Integer, primary_key=True, autoincrement=True)
    source_id = Column(String, doc="Unique source identifier")
    # No FK constraint - matches actual database schema for backward compatibility
    image_path = Column(String, nullable=False, doc="Image path (references images.path)")
    ra_deg = Column(Float, nullable=False, doc="Source RA in degrees")
    dec_deg = Column(Float, nullable=False, doc="Source Dec in degrees")
    nvss_flux_mjy = Column(Float, doc="NVSS catalog flux in mJy")
    peak_jyb = Column(Float, nullable=False, doc="Peak flux in Jy/beam")
    peak_err_jyb = Column(Float, doc="Peak flux error in Jy/beam")
    measured_at = Column(Float, nullable=False, doc="Measurement Unix timestamp")
    snr = Column(Float, doc="Signal-to-noise ratio")
    mjd = Column(Float, doc="Observation MJD")
    flux_jy = Column(Float, doc="Integrated flux in Jy")
    flux_err_jy = Column(Float, doc="Integrated flux error in Jy")
    normalized_flux_jy = Column(Float, doc="Normalized flux in Jy")
    normalized_flux_err_jy = Column(Float, doc="Normalized flux error in Jy")
    mosaic_path = Column(String, doc="Associated mosaic path")
    sep_from_center_deg = Column(Float, doc="Separation from image center in degrees")
    flags = Column(Integer, default=0, doc="Quality flags bitmask")

    # Note: No relationship defined here - image_path is just a string column
    # matching images.path. Use manual queries to join if needed.

    __table_args__ = (
        Index("idx_photometry_image", "image_path"),
        Index("idx_photometry_source_id", "source_id"),
    )

    def __repr__(self):
        return f"<Photometry(id={self.id}, source_id='{self.source_id}', peak={self.peak_jyb})>"


class QAPlot(ProductsBase):
    """
    QA plot file tracking for organized hierarchical storage.

    Tracks all QA plots with metadata for searchability and archival management.
    Supports the hierarchical plot organization system:
    - by-date/YYYY/MM/DD/obs_id/
    - by-observation/obs_id/ (symlinks)
    - by-type/plot_type/ (symlinks)
    - archive/ (archived plots)
    """

    __tablename__ = "qa_plots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    path = Column(String, nullable=False, unique=True, doc="Full path to plot file")
    filename = Column(String, nullable=False, doc="Plot filename")
    observation_id = Column(String, nullable=False, doc="Observation identifier")
    plot_type = Column(
        String, nullable=False, doc="Plot type (rfi_spectrum, psf_correlation, etc.)"
    )
    format = Column(String, nullable=False, doc="File format (png, pdf, vega.json)")
    size_bytes = Column(Integer, doc="File size in bytes")
    generated_at = Column(Float, nullable=False, doc="Unix timestamp when plot was generated")
    context = Column(String, doc="Generation context (batch, interactive, web_api, etc.)")
    generation_time_s = Column(Float, doc="Time taken to generate plot in seconds")

    # Observation metadata
    timestamp = Column(String, doc="Observation timestamp (ISO 8601)")
    mid_mjd = Column(Float, doc="Observation MJD")
    calibrator = Column(String, doc="Calibrator name if applicable")
    field_id = Column(Integer, doc="Field ID if applicable")
    ra_deg = Column(Float, doc="RA in degrees")
    dec_deg = Column(Float, doc="Dec in degrees")

    # Organization metadata
    storage_location = Column(
        String, nullable=False, doc="Storage location (by-date, by-observation, by-type, archive)"
    )
    is_archived = Column(Integer, default=0, doc="Whether plot has been archived (0/1)")
    archived_at = Column(Float, doc="Unix timestamp when plot was archived")

    # Associated resources
    ms_path = Column(String, doc="Associated MS path")
    image_path = Column(String, doc="Associated image path")

    __table_args__ = (
        Index("idx_qa_plots_observation_id", "observation_id"),
        Index("idx_qa_plots_plot_type", "plot_type"),
        Index("idx_qa_plots_format", "format"),
        Index("idx_qa_plots_generated_at", "generated_at"),
        Index("idx_qa_plots_archived", "is_archived"),
        Index("idx_qa_plots_storage", "storage_location"),
        Index("idx_qa_plots_obs_type", "observation_id", "plot_type"),
    )

    def __repr__(self):
        return f"<QAPlot(id={self.id}, obs='{self.observation_id}', type='{self.plot_type}')>"


class StorageLocation(ProductsBase):
    """Registered storage locations for data files."""

    __tablename__ = "storage_locations"

    id = Column(Integer, primary_key=True, autoincrement=True)
    location_type = Column(
        String, nullable=False, doc="Location type (e.g., 'incoming', 'staging')"
    )
    base_path = Column(String, nullable=False, doc="Base path for this location")
    description = Column(String, doc="Human-readable description")
    registered_at = Column(Float, nullable=False, doc="Registration timestamp")
    status = Column(String, default="active", doc="Status (active/inactive)")
    notes = Column(String, doc="Additional notes")

    __table_args__ = (
        UniqueConstraint("location_type", "base_path"),
        Index("idx_storage_locations_type", "location_type", "status"),
    )


class BatchJob(ProductsBase):
    """Batch processing job tracking."""

    __tablename__ = "batch_jobs"

    id = Column(Integer, primary_key=True)
    type = Column(String, nullable=False, doc="Job type (e.g., 'imaging', 'calibration')")
    created_at = Column(Float, nullable=False, doc="Job creation timestamp")
    status = Column(String, nullable=False, doc="Job status")
    total_items = Column(Integer, nullable=False, doc="Total items to process")
    completed_items = Column(Integer, default=0, doc="Completed items count")
    failed_items = Column(Integer, default=0, doc="Failed items count")
    location_type = Column(String, doc="Location type (e.g., 'staging', 'archive')")
    params = Column(Text, doc="Job parameters as JSON")

    # Relationships
    items = relationship("BatchJobItem", back_populates="batch_job")


class BatchJobItem(ProductsBase):
    """Individual items within a batch job."""

    __tablename__ = "batch_job_items"

    id = Column(Integer, primary_key=True)
    batch_id = Column(Integer, ForeignKey("batch_jobs.id"), nullable=False)
    ms_path = Column(String, nullable=False, doc="MS path for this item")
    job_id = Column(Integer, doc="External job ID if applicable")
    status = Column(String, nullable=False, doc="Item status")
    error = Column(Text, doc="Error message if failed")
    started_at = Column(Float, doc="Processing start time")

    # Relationships
    batch_job = relationship("BatchJob", back_populates="items")


class TransientCandidate(ScienceBase):
    """Transient source candidate tracking."""

    __tablename__ = "variable_source_candidates"

    id = Column(Integer, primary_key=True, autoincrement=True)
    source_id = Column(String, doc="Associated source ID")
    ra_deg = Column(Float, nullable=False, doc="RA in degrees")
    dec_deg = Column(Float, nullable=False, doc="Dec in degrees")
    detection_type = Column(String, nullable=False, doc="Detection type")
    significance_sigma = Column(Float, doc="Detection significance in sigma")
    detected_at = Column(Float, doc="Detection timestamp")
    priority = Column(String, default="normal", doc="Priority level")
    n_detections = Column(Integer, default=0, doc="Number of detections")
    mean_flux_jy = Column(Float, doc="Mean flux in Jy")
    std_flux_jy = Column(Float, doc="Flux standard deviation")
    eta = Column(Float, doc="Variability index eta")
    v_index = Column(Float, doc="Variability index V")
    chi_squared = Column(Float, doc="Chi-squared statistic")
    is_variable = Column(Integer, default=0, doc="Variable source flag")
    ese_candidate = Column(Integer, default=0, doc="Extreme scattering event candidate")
    first_detected_at = Column(Float, doc="First detection timestamp")
    last_detected_at = Column(Float, doc="Last detection timestamp")
    last_updated = Column(Float, doc="Last update timestamp")
    notes = Column(Text, doc="Additional notes")

    __table_args__ = (
        Index("idx_transients_type", "detection_type", "significance_sigma"),
        Index("idx_transients_coords", "ra_deg", "dec_deg"),
        Index("idx_transients_detected", "detected_at"),
    )


class CalibratorTransit(ProductsBase):
    """Calibrator transit times and data availability."""

    __tablename__ = "calibrator_transits"

    calibrator_name = Column(String, primary_key=True, doc="Calibrator name")
    transit_mjd = Column(Float, primary_key=True, doc="Transit time in MJD")
    transit_iso = Column(String, nullable=False, doc="Transit time ISO string")
    has_data = Column(Integer, nullable=False, default=0, doc="Data available flag")
    group_id = Column(String, doc="Associated HDF5 group ID")
    group_mid_iso = Column(String, doc="Group mid-time ISO")
    delta_minutes = Column(Float, doc="Time offset from transit in minutes")
    pb_response = Column(Float, doc="Primary beam response")
    dec_match = Column(Integer, nullable=False, default=0, doc="Declination match flag")
    calculated_at = Column(Float, nullable=False, doc="Calculation timestamp")
    updated_at = Column(Float, nullable=False, doc="Last update timestamp")

    __table_args__ = (
        Index("idx_calibrator_transits_calibrator", "calibrator_name", "updated_at"),
        Index("idx_calibrator_transits_has_data", "calibrator_name", "has_data", "transit_mjd"),
        Index("idx_calibrator_transits_mjd", "transit_mjd"),
    )


class MonitoringSource(ScienceBase):
    """Sources being monitored for variability."""

    __tablename__ = "monitoring_sources"

    id = Column(Integer, primary_key=True, autoincrement=True)
    source_id = Column(String, unique=True, nullable=False, doc="Unique source ID")
    ra_deg = Column(Float, nullable=False, doc="RA in degrees")
    dec_deg = Column(Float, nullable=False, doc="Dec in degrees")
    n_detections = Column(Integer, default=0, doc="Number of detections")
    mean_flux_jy = Column(Float, doc="Mean flux")
    std_flux_jy = Column(Float, doc="Flux std dev")
    eta = Column(Float, doc="Eta variability index")
    v_index = Column(Float, doc="V variability index")
    is_variable = Column(Integer, default=0, doc="Variable flag")
    ese_candidate = Column(Integer, default=0, doc="ESE candidate flag")
    first_detected_at = Column(Float, doc="First detection")
    last_detected_at = Column(Float, doc="Last detection")
    
    # New metrics
    max_flux_jy = Column(Float, doc="Peak flux in Jy")
    nvss_flux_jy = Column(Float, doc="Reference NVSS flux in Jy")
    chi_squared = Column(Float, doc="Chi-squared statistic")
    sigma_deviation = Column(Float, doc="Sigma deviation from mean")

    __table_args__ = (
        Index("idx_monitoring_coords", "ra_deg", "dec_deg"),
        Index("idx_monitoring_variable", "is_variable", "eta"),
        Index("idx_monitoring_ese", "ese_candidate"),
    )


class AntennaHealth(ProductsBase):
    """
    Antenna health tracking for identifying problematic antennas.

    Records correlation analysis results to flag antennas with
    systematic issues or correlated behavior.
    """

    __tablename__ = "antenna_health"

    id = Column(Integer, primary_key=True, autoincrement=True)
    antenna_id = Column(Integer, nullable=False, doc="Antenna ID number")
    ms_path = Column(String, doc="Associated MS path")
    caltable_path = Column(String, doc="Associated calibration table path")
    timestamp = Column(Float, nullable=False, doc="Unix timestamp of analysis")
    correlation_score = Column(Float, doc="Correlation score with other antennas (0-1)")
    flagged = Column(Integer, default=0, doc="Whether antenna is flagged as problematic (0/1)")
    reason = Column(Text, doc="Reason for flagging")
    gain_stability = Column(Float, doc="Gain stability metric (std/mean)")
    phase_stability = Column(Float, doc="Phase stability metric (std)")
    num_correlated_antennas = Column(Integer, doc="Number of highly correlated antennas")

    __table_args__ = (
        Index("idx_antenna_health_antenna", "antenna_id"),
        Index("idx_antenna_health_timestamp", "timestamp"),
        Index("idx_antenna_health_flagged", "flagged"),
        Index("idx_antenna_health_ms", "ms_path"),
    )

    def __repr__(self):
        return f"<AntennaHealth(antenna_id={self.antenna_id}, flagged={self.flagged})>"


class SelfCalIteration(ProductsBase):
    """
    Self-calibration iteration tracking for convergence analysis.

    Records metrics for each self-cal iteration to monitor convergence
    and select the best iteration.
    """

    __tablename__ = "selfcal_iterations"

    id = Column(Integer, primary_key=True, autoincrement=True)
    ms_path = Column(String, nullable=False, doc="MS path being self-calibrated")
    iteration = Column(Integer, nullable=False, doc="Iteration number (0-based)")
    timestamp = Column(Float, nullable=False, doc="Unix timestamp")

    # Convergence metrics
    snr = Column(Float, doc="Signal-to-noise ratio")
    rms = Column(Float, doc="RMS noise level in Jy/beam")
    peak_flux = Column(Float, doc="Peak flux in Jy/beam")
    dynamic_range = Column(Float, doc="Peak/RMS dynamic range")
    chi_squared = Column(Float, doc="Chi-squared statistic")
    converged = Column(Integer, default=0, doc="Convergence flag (0/1)")

    # Iteration parameters
    solint = Column(String, doc="Solution interval")
    gaintype = Column(String, doc="Gain type (G/T)")
    calmode = Column(String, doc="Calibration mode (p/ap)")
    minsnr = Column(Float, doc="Minimum SNR threshold")

    # Results
    image_path = Column(String, doc="Output image path for this iteration")
    caltable_path = Column(String, doc="Calibration table path")
    n_flagged = Column(Integer, doc="Number of solutions flagged")
    improvement_percent = Column(Float, doc="RMS improvement over previous iteration")

    __table_args__ = (
        Index("idx_selfcal_ms", "ms_path"),
        Index("idx_selfcal_iteration", "ms_path", "iteration"),
        Index("idx_selfcal_timestamp", "timestamp"),
        Index("idx_selfcal_converged", "converged"),
    )

    def __repr__(self):
        return (
            f"<SelfCalIteration(ms_path='{self.ms_path}', iter={self.iteration}, snr={self.snr})>"
        )


class SpectralIndex(ScienceBase):
    """
    Spectral index measurements for sources across frequency.

    Tracks spectral behavior of sources for classification and
    science analysis. Supports multi-frequency measurements with
    JSON arrays for flexible SED fitting.
    """

    __tablename__ = "spectral_indices"

    id = Column(Integer, primary_key=True, autoincrement=True)
    source_id = Column(String, nullable=False, doc="Source identifier")
    field_name = Column(String, doc="Field name")

    # Spectral index measurement
    alpha = Column(Float, nullable=False, doc="Spectral index (S ∝ ν^α)")
    alpha_err = Column(Float, doc="Spectral index uncertainty")

    # Multi-frequency measurements (stored as JSON)
    frequencies_hz = Column(String, doc="JSON array of frequencies (Hz)")
    flux_densities_jy = Column(String, doc="JSON array of flux densities (Jy)")
    flux_errors_jy = Column(String, doc="JSON array of flux errors (Jy)")

    # Metadata
    classification = Column(String, doc="Source classification (synchrotron/thermal/inverted)")
    timestamp = Column(Float, nullable=False, doc="Unix timestamp of measurement")

    __table_args__ = (
        Index("ix_spectral_indices_source_id", "source_id"),
        Index("ix_spectral_indices_field_name", "field_name"),
        Index("ix_spectral_indices_classification", "classification"),
    )

    def __repr__(self):
        return f"<SpectralIndex(source_id='{self.source_id}', alpha={self.alpha})>"


class ImageComparison(ProductsBase):
    """
    Image comparison metrics for tracking differences between images.

    Used for self-cal before/after comparison, validation, and
    multi-epoch analysis.
    """

    __tablename__ = "image_comparisons"

    id = Column(Integer, primary_key=True, autoincrement=True)
    image1_path = Column(String, nullable=False, doc="First image path")
    image2_path = Column(String, nullable=False, doc="Second image path")

    # Comparison metrics
    rmse = Column(Float, doc="Root mean square error")
    correlation = Column(Float, doc="Pearson correlation coefficient")
    ssim = Column(Float, doc="Structural similarity index")
    peak_difference = Column(Float, doc="Peak flux difference in Jy/beam")
    rms_difference = Column(Float, doc="RMS difference in Jy/beam")

    # Comparison metadata
    comparison_type = Column(
        String, nullable=False, doc="Comparison type (selfcal/validation/epoch)"
    )
    timestamp = Column(Float, nullable=False, doc="Unix timestamp of comparison")
    difference_map_path = Column(String, doc="Path to difference map FITS file")
    comparison_plot_path = Column(String, doc="Path to comparison plot")

    # Context
    ms_path = Column(String, doc="Associated MS path if applicable")
    notes = Column(Text, doc="Additional notes")

    __table_args__ = (
        Index("idx_image_comp_image1", "image1_path"),
        Index("idx_image_comp_image2", "image2_path"),
        Index("idx_image_comp_type", "comparison_type"),
        Index("idx_image_comp_timestamp", "timestamp"),
    )

    def __repr__(self):
        return f"<ImageComparison(type='{self.comparison_type}', rmse={self.rmse})>"


# =============================================================================
# Calibration Domain Models (caltables table)
# =============================================================================


class Caltable(CalRegistryBase):
    """
    Calibration table metadata and validity windows.

    Tracks all calibration tables produced by the pipeline, their types,
    and the time ranges over which they are valid.
    """

    __tablename__ = "caltables"

    id = Column(Integer, primary_key=True, autoincrement=True)
    set_name = Column(String, nullable=False, doc="Calibration set name")
    path = Column(String, unique=True, nullable=False, doc="Full path to cal table")
    table_type = Column(String, nullable=False, doc="Table type (e.g., 'bandpass', 'gain')")
    order_index = Column(Integer, nullable=False, doc="Application order")
    cal_field = Column(String, doc="Calibrator field name")
    refant = Column(String, doc="Reference antenna")
    created_at = Column(Float, nullable=False, doc="Creation timestamp")
    valid_start_mjd = Column(Float, doc="Validity start MJD")
    valid_end_mjd = Column(Float, doc="Validity end MJD")
    status = Column(String, nullable=False, doc="Status (active/deprecated)")
    notes = Column(Text, doc="Additional notes")
    source_ms_path = Column(String, doc="Source MS used to derive this table")
    solver_command = Column(String, doc="CASA solver command used")
    solver_version = Column(String, doc="CASA version")
    solver_params = Column(Text, doc="Solver parameters as JSON")
    quality_metrics = Column(Text, doc="Quality metrics as JSON")

    __table_args__ = (
        Index("idx_caltables_source", "source_ms_path"),
        Index("idx_caltables_set", "set_name"),
        Index("idx_caltables_valid", "valid_start_mjd", "valid_end_mjd"),
    )

    def __repr__(self):
        return f"<Caltable(id={self.id}, path='{self.path}', type='{self.table_type}')>"

    def is_valid_at(self, mjd: float) -> bool:
        """Check if this calibration table is valid at a given MJD."""
        if self.valid_start_mjd is not None and mjd < self.valid_start_mjd:
            return False
        if self.valid_end_mjd is not None and mjd > self.valid_end_mjd:
            return False
        return True


# =============================================================================
# HDF5 Domain Models (hdf5_file_index table)
# =============================================================================


class HDF5FileIndex(HDF5Base):
    """
    HDF5 file index for fast subband group queries.

    This is the primary index for UVH5 files, supporting fast lookup
    by timestamp, group ID, and subband number.
    """

    __tablename__ = "hdf5_file_index"

    path = Column(String, primary_key=True, doc="Full path to HDF5 file")
    filename = Column(String, nullable=False, doc="Filename only")
    group_id = Column(String, nullable=False, doc="Observation group ID")
    subband_code = Column(String, nullable=False, doc="Subband code (e.g., 'sb00')")
    subband_num = Column(Integer, doc="Subband number (0-15)")
    timestamp_iso = Column(String, nullable=False, doc="ISO timestamp")
    timestamp_mjd = Column(Float, nullable=False, doc="MJD timestamp")
    file_size_bytes = Column(Integer, doc="File size in bytes")
    modified_time = Column(Float, doc="File modification time")
    indexed_at = Column(Float, doc="Index creation time")
    stored = Column(Integer, default=1, doc="File exists on disk")
    ra_deg = Column(Float, doc="RA in degrees")
    dec_deg = Column(Float, doc="Dec in degrees")
    obs_date = Column(String, doc="Observation date (YYYY-MM-DD)")
    obs_time = Column(String, doc="Observation time (HH:MM:SS)")

    __table_args__ = (
        Index("idx_hdf5_group_id", "group_id"),
        Index("idx_hdf5_timestamp_mjd", "timestamp_mjd"),
        Index("idx_hdf5_group_subband", "group_id", "subband_code"),
        Index("idx_hdf5_stored", "stored"),
        Index("idx_hdf5_ra_dec", "ra_deg", "dec_deg"),
        Index("idx_hdf5_obs_date", "obs_date"),
        Index("idx_hdf5_subband_num", "subband_num"),
        Index("idx_hdf5_group_subband_num", "group_id", "subband_num"),
    )

    def __repr__(self):
        return f"<HDF5FileIndex(path='{self.path}', group_id='{self.group_id}', sb={self.subband_num})>"


class HDF5StorageLocation(HDF5Base):
    """Storage location registry for HDF5 files."""

    __tablename__ = "storage_locations"

    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True, nullable=False, doc="Location name")
    path = Column(String, nullable=False, doc="Base path")
    description = Column(String, doc="Description")


class PointingHistory(HDF5Base):
    """Telescope pointing history tracking."""

    __tablename__ = "pointing_history"

    timestamp = Column(Float, primary_key=True, doc="Unix timestamp")
    ra_deg = Column(Float, doc="RA in degrees")
    dec_deg = Column(Float, doc="Dec in degrees")

    __table_args__ = (Index("idx_pointing_timestamp", "timestamp"),)


# Note: Ingestion queue is now managed by Dagster assets and jobs.
# Legacy task queue PostgreSQL tables are archived in legacy/.


# =============================================================================
# Data Registry Domain Models (data_registry table)
# =============================================================================


class DataRegistry(DataRegistryBase):
    """
    Data product staging and publishing registry.

    Tracks data products through staging, validation, and publishing workflow.
    """

    __tablename__ = "data_registry"

    id = Column(Integer, primary_key=True, autoincrement=True)
    data_type = Column(String, nullable=False, doc="Data type (e.g., 'ms', 'image')")
    data_id = Column(String, unique=True, nullable=False, doc="Unique data ID")
    base_path = Column(String, nullable=False, doc="Base path")
    status = Column(String, nullable=False, default="staging", doc="Status")
    stage_path = Column(String, nullable=False, doc="Staging path")
    published_path = Column(String, doc="Published path")
    created_at = Column(Float, nullable=False, doc="Creation time")
    staged_at = Column(Float, nullable=False, doc="Staging time")
    published_at = Column(Float, doc="Publication time")
    publish_mode = Column(String, doc="Publish mode (copy/move)")
    metadata_json = Column(Text, doc="Metadata as JSON")
    qa_status = Column(String, doc="QA status")
    validation_status = Column(String, doc="Validation status")
    finalization_status = Column(String, default="pending", doc="Finalization status")
    auto_publish_enabled = Column(Integer, default=1, doc="Auto-publish enabled")
    publish_attempts = Column(Integer, default=0, doc="Publish attempt count")
    publish_error = Column(Text, doc="Last publish error")
    photometry_status = Column(String, doc="Photometry status")
    photometry_job_id = Column(String, doc="Photometry job ID")

    # Relationships
    tags = relationship("DataTag", back_populates="data_entry")

    __table_args__ = (
        UniqueConstraint("data_type", "data_id"),
        Index("idx_data_registry_type_status", "data_type", "status"),
        Index("idx_data_registry_status", "status"),
        Index("idx_data_registry_published_at", "published_at"),
        Index("idx_data_registry_finalization", "finalization_status"),
    )

    def __repr__(self):
        return f"<DataRegistry(id={self.id}, data_id='{self.data_id}', status='{self.status}')>"


class DataRelationship(DataRegistryBase):
    """Relationships between data products (e.g., MS -> Image)."""

    __tablename__ = "data_relationships"

    id = Column(Integer, primary_key=True, autoincrement=True)
    parent_data_id = Column(String, ForeignKey("data_registry.data_id"), nullable=False)
    child_data_id = Column(String, ForeignKey("data_registry.data_id"), nullable=False)
    relationship_type = Column(String, nullable=False, doc="Relationship type")

    __table_args__ = (
        UniqueConstraint("parent_data_id", "child_data_id", "relationship_type"),
        Index("idx_data_relationships_parent", "parent_data_id"),
        Index("idx_data_relationships_child", "child_data_id"),
    )


class DataTag(DataRegistryBase):
    """Tags associated with data products."""

    __tablename__ = "data_tags"

    id = Column(Integer, primary_key=True, autoincrement=True)
    data_id = Column(String, ForeignKey("data_registry.data_id"), nullable=False)
    tag = Column(String, nullable=False, doc="Tag value")

    # Relationships
    data_entry = relationship("DataRegistry", back_populates="tags")

    __table_args__ = (
        UniqueConstraint("data_id", "tag"),
        Index("idx_data_tags_data_id", "data_id"),
    )


# =============================================================================
# Queue & Execution Domain Models (pipeline_executions, pipeline_jobs)
# =============================================================================


class PipelineExecution(QueueBase):
    """
    Tracking for complex multi-job pipeline executions.
    Used by PipelineExecutor and workflow orchestration.
    """

    __tablename__ = "pipeline_executions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    execution_id = Column(String, unique=True, nullable=False, doc="Unique execution UUID")
    pipeline_name = Column(String, nullable=False, doc="Name of the pipeline template")
    status = Column(String, nullable=False, default="pending", doc="Overall status")
    started_at = Column(Float, nullable=False, doc="Unix timestamp when started")
    completed_at = Column(Float, doc="Unix timestamp when completed")
    error = Column(Text, doc="Error message if failed")
    config_json = Column(Text, doc="Pipeline configuration snapshot")

    # Relationships
    jobs = relationship("PipelineJob", back_populates="execution", cascade="all, delete-orphan")

    __table_args__ = (
        Index("idx_pipeline_executions_name", "pipeline_name"),
        Index("idx_pipeline_executions_status", "status"),
        Index("idx_pipeline_executions_started", "started_at"),
    )


class PipelineJob(QueueBase):
    """Individual job within a pipeline execution."""

    __tablename__ = "pipeline_jobs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    execution_id = Column(String, ForeignKey("pipeline_executions.execution_id"), nullable=False)
    job_id = Column(String, nullable=False, doc="Logical job ID within the pipeline")
    job_type = Column(String, nullable=False, doc="Type of job (e.g., 'imaging')")
    dagster_run_id = Column(String, doc="Associated Dagster Run ID")
    status = Column(String, nullable=False, default="pending", doc="Job status")
    created_at = Column(Float, nullable=False, doc="Creation timestamp")
    started_at = Column(Float, doc="Start timestamp")
    completed_at = Column(Float, doc="Completion timestamp")
    params_json = Column(Text, doc="Resolved parameters as JSON")
    outputs_json = Column(Text, doc="Job outputs/results as JSON")
    error = Column(Text, doc="Error message if failed")

    # Relationships
    execution = relationship("PipelineExecution", back_populates="jobs")

    __table_args__ = (
        Index("idx_pipeline_jobs_execution", "execution_id"),
        Index("idx_pipeline_jobs_status", "status"),
        Index("idx_pipeline_jobs_run", "dagster_run_id"),
    )


class ExecutionStage(QueueBase):
    """Detailed stage tracking for granular metrics (replaces fix_schemas table)."""

    __tablename__ = "execution_stages"

    id = Column(Integer, primary_key=True, autoincrement=True)
    execution_id = Column(Integer, nullable=False)
    stage_name = Column(String, nullable=False)
    status = Column(String, nullable=False, default="pending")
    started_at = Column(Float)
    completed_at = Column(Float)
    duration_seconds = Column(Float)
    attempt_count = Column(Integer, default=0)
    error_message = Column(Text)

    __table_args__ = (
        Index("idx_stages_execution", "execution_id"),
        Index("idx_stages_status", "status"),
    )


class StageMetric(QueueBase):
    """Aggregate performance metrics by stage."""

    __tablename__ = "stage_metrics"

    id = Column(Integer, primary_key=True, autoincrement=True)
    stage_name = Column(String, nullable=False, unique=True)
    execution_count = Column(Integer, default=0)
    success_count = Column(Integer, default=0)
    failure_count = Column(Integer, default=0)
    total_duration_seconds = Column(Float, default=0.0)
    last_execution_at = Column(Float)
    created_at = Column(Float, nullable=False)

    __table_args__ = (Index("idx_metrics_stage", "stage_name"),)


# =============================================================================
# Telemetry Domain Models (health_metrics)
# =============================================================================


class HealthMetric(TelemetryBase):
    """
    System health metrics for telemetry and monitoring.
    Stored in health.sqlite3 (pruned by MaintenanceService).
    """

    __tablename__ = "health_metrics"

    id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(Float, nullable=False, doc="Unix timestamp")
    service = Column(String, nullable=False, doc="Service name")
    metric_name = Column(String, nullable=False, doc="Name of the metric")
    value = Column(Float, nullable=False, doc="Metric value")
    tags_json = Column(Text, doc="Additional dimensions as JSON")

    __table_args__ = (
        Index("idx_health_timestamp", "timestamp"),
        Index("idx_health_service_metric", "service", "metric_name"),
    )


class Alert(TelemetryBase):
    """System and pipeline alerts."""

    __tablename__ = "alerts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    severity = Column(
        String, nullable=False, doc="Alert severity (CRITICAL, ERROR, WARNING, INFO, SUCCESS)"
    )
    title = Column(String, nullable=False, doc="Short alert title")
    message = Column(Text, nullable=False, doc="Detailed alert message")
    source = Column(String, nullable=False, doc="Source component (e.g., 'calibration', 'imaging')")
    timestamp = Column(Float, nullable=False, doc="Alert timestamp (Unix)")

    # Acknowledgement & Resolution
    acknowledged = Column(Integer, default=0, doc="Acknowledged flag (0/1)")
    acknowledged_by = Column(String, doc="User who acknowledged")
    acknowledged_at = Column(Float, doc="Acknowledgement timestamp")

    resolved = Column(Integer, default=0, doc="Resolved flag (0/1)")
    resolved_at = Column(Float, doc="Resolution timestamp")

    # Context
    related_entity_type = Column(String, doc="Related entity type (e.g., 'job', 'image')")
    related_entity_id = Column(String, doc="ID of related entity")

    __table_args__ = (
        Index("idx_alerts_severity", "severity"),
        Index("idx_alerts_timestamp", "timestamp"),
        Index("idx_alerts_source", "source"),
        Index("idx_alerts_status", "acknowledged", "resolved"),
    )


# =============================================================================
# Utility functions for model introspection
# =============================================================================


def get_all_models_for_base(base) -> list:
    """Get all model classes registered with a declarative base."""
    return [mapper.class_ for mapper in base.registry.mappers]


# Model registry for easy access
PRODUCTS_MODELS = [
    MSIndex,
    Image,
    QAPlot,
    # HDF5FileIndexProducts removed (consolidated into HDF5FileIndex)
    StorageLocation,
    BatchJob,
    BatchJobItem,
    CalibratorTransit,
    AntennaHealth,
    SelfCalIteration,
    ImageComparison,
]

SCIENCE_MODELS = [
    Photometry,
    TransientCandidate,
    MonitoringSource,
    SpectralIndex,
]

CAL_REGISTRY_MODELS = [Caltable]

HDF5_MODELS = [HDF5FileIndex, HDF5StorageLocation, PointingHistory]

# INGEST_MODELS deprecated - legacy system no longer used
INGEST_MODELS = []

DATA_REGISTRY_MODELS = [DataRegistry, DataRelationship, DataTag]

QUEUE_MODELS = [PipelineExecution, PipelineJob, ExecutionStage, StageMetric]

TELEMETRY_MODELS = [HealthMetric, Alert]


# =============================================================================
# Pipeline State Models (key-value state storage)
# =============================================================================


class PipelineState(ProductsBase):
    """
    Global pipeline state for mosaic calibration scheduler.
    
    This table stores key-value pairs for managing calibration state:
    - current_pointing_dec: Current pointing declination
    - current_bp_calibrator: Name of current bandpass calibrator
    - current_bp_calibrator_ra: RA of current BP calibrator
    - last_mosaic_end_mjd: MJD of last completed mosaic
    - next_bp_transit_groups: JSON array of predicted BP transit groups
    - last_g_group_id: Last group used for gain calibration
    """

    __tablename__ = "pipeline_state"

    key = Column(String, primary_key=True, doc="State key identifier")
    value = Column(Text, doc="State value (string, float, or JSON)")
    value_type = Column(String, doc="Type of value: 'string', 'float', 'json'")
    updated_at = Column(Float, doc="Unix timestamp of last update")
    updated_by = Column(String, default="system", doc="Who/what updated this value")
    description = Column(Text, doc="Description of this state key")

    __table_args__ = (Index("idx_pipeline_state_updated_at", "updated_at"),)


PIPELINE_STATE_MODELS = [PipelineState]
