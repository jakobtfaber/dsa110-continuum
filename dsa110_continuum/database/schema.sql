-- Primary table for tracking HDF5 subband files from the correlator.
-- Each row represents one physical file on disk.
CREATE TABLE IF NOT EXISTS hdf5_files (
    path TEXT PRIMARY KEY,       -- Full absolute path to the file.
    filename TEXT,               -- Basename of the file.
    group_id TEXT,               -- Canonical group ID (timestamp of the first file in the group).
    subband_code TEXT,           -- String representation (e.g., 'sb01').
    subband_num INTEGER,         -- Zero-indexed subband number (0-15).
    timestamp_iso TEXT,          -- ISO8601 timestamp from the filename.
    timestamp_mjd REAL,          -- Extracted MJD from HDF5 metadata.
    file_size_bytes INTEGER,
    modified_time REAL,          -- OS modified time.
    indexed_at REAL,             -- Unix timestamp when indexed.
    stored INTEGER DEFAULT 1,    -- Flag indicating if file is currently on disk.
    ra_deg REAL,                 -- Extracted metadata: Right Ascension.
    dec_deg REAL,                -- Extracted metadata: Declination.
    jd_start REAL,               -- Julian Date of first integration (time_array[0]).
    obs_date TEXT,               -- YYYY-MM-DD for efficient partitioning.
    obs_time TEXT,               -- HH:MM:SS for efficient partitioning.
    processed INTEGER DEFAULT 0  -- Flag for downstream conversion status.
);

-- ENFORCE INTEGRITY: A group cannot have more than one file for the same subband.
-- This index automatically handles "jitter" duplicates by rejecting redundant inserts.
CREATE UNIQUE INDEX IF NOT EXISTS idx_hdf5_group_sb ON hdf5_files(group_id, subband_num);

-- PERFORMANCE: Speed up group lookups for conversion triggers.
CREATE INDEX IF NOT EXISTS idx_hdf5_group ON hdf5_files(group_id);

-- PERFORMANCE: Speed up chronological queries and maintenance.
CREATE INDEX IF NOT EXISTS idx_hdf5_timestamp ON hdf5_files(timestamp_iso);
CREATE INDEX IF NOT EXISTS idx_hdf5_date ON hdf5_files(obs_date);

-- PERFORMANCE: Speed up exact-match grouping by time_array[0].
CREATE INDEX IF NOT EXISTS idx_hdf5_jd_start ON hdf5_files(jd_start);

CREATE TABLE IF NOT EXISTS ms_index (
    path TEXT PRIMARY KEY,
    start_mjd REAL,
    end_mjd REAL,
    mid_mjd REAL,
    processed_at REAL,
    status TEXT,
    stage TEXT,
    stage_updated_at REAL,
    cal_applied INTEGER DEFAULT 0,
    imagename TEXT,
    ra_deg REAL,
    dec_deg REAL,
    field_name TEXT,
    pointing_ra_deg REAL,
    pointing_dec_deg REAL,
    group_id TEXT,
    created_at REAL
);

CREATE TABLE IF NOT EXISTS images (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    path TEXT NOT NULL UNIQUE,
    ms_path TEXT NOT NULL,
    created_at REAL NOT NULL,
    type TEXT NOT NULL,
    format TEXT DEFAULT 'fits',
    beam_major_arcsec REAL,
    beam_minor_arcsec REAL,
    beam_pa_deg REAL,
    noise_jy REAL,
    dynamic_range REAL,
    pbcor INTEGER DEFAULT 0,
    field_name TEXT,
    center_ra_deg REAL,
    center_dec_deg REAL,
    imsize_x INTEGER,
    imsize_y INTEGER,
    cellsize_arcsec REAL,
    freq_ghz REAL,
    bandwidth_mhz REAL,
    integration_sec REAL
);

CREATE TABLE IF NOT EXISTS image_qa (
    ms_path TEXT PRIMARY KEY,
    overall_quality TEXT,
    created_at REAL
);

CREATE TABLE IF NOT EXISTS photometry (
    id INTEGER,
    source_id TEXT,
    image_path TEXT NOT NULL,
    ra_deg REAL NOT NULL,
    dec_deg REAL NOT NULL,
    nvss_flux_mjy REAL,
    peak_jyb REAL,
    peak_err_jyb REAL,
    measured_at REAL,
    snr REAL,
    mjd REAL,
    flux_jy REAL,
    flux_err_jy REAL,
    normalized_flux_jy REAL,
    normalized_flux_err_jy REAL,
    mosaic_path TEXT,
    sep_from_center_deg REAL,
    local_rms REAL,
    rms_jy REAL,
    peak_flux_jy REAL,
    quality_flag TEXT,
    flags INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER,
    type TEXT,
    status TEXT,
    ms_path TEXT,
    created_at REAL
);

CREATE TABLE IF NOT EXISTS caltables (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    set_name TEXT,
    path TEXT,
    source_ms_path TEXT,
    table_type TEXT,
    cal_field TEXT,
    refant TEXT,
    order_index INTEGER,
    created_at REAL,
    status TEXT,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS calibration_applied (
    ms_path TEXT,
    caltable_path TEXT,
    applied_at REAL
);

CREATE TABLE IF NOT EXISTS bandpass_calibrators (
    name TEXT,
    ra_deg REAL,
    dec_deg REAL,
    status TEXT
);

CREATE TABLE IF NOT EXISTS calibrator_transits (
    calibrator_name TEXT,
    transit_mjd REAL
);

CREATE TABLE IF NOT EXISTS processing_queue (
    id INTEGER,
    group_id TEXT,
    status TEXT,
    created_at REAL
);

CREATE TABLE IF NOT EXISTS batch_jobs (
    id INTEGER,
    type TEXT,
    created_at REAL,
    status TEXT,
    total_items INTEGER,
    completed_items INTEGER DEFAULT 0,
    failed_items INTEGER DEFAULT 0,
    params TEXT
);

CREATE TABLE IF NOT EXISTS batch_job_items (
    id INTEGER,
    batch_job_id INTEGER,
    item_path TEXT,
    status TEXT
);

CREATE TABLE IF NOT EXISTS transient_candidates (
    id INTEGER,
    source_id TEXT,
    ra_deg REAL,
    dec_deg REAL
);

CREATE TABLE IF NOT EXISTS monitoring_sources (
    source_id TEXT,
    ra_deg REAL,
    dec_deg REAL
);

CREATE TABLE IF NOT EXISTS sources (
    id INTEGER,
    name TEXT,
    ra_deg REAL,
    dec_deg REAL,
    catalog_match TEXT,
    source_type TEXT,
    first_detected_mjd REAL,
    last_detected_mjd REAL,
    detection_count INTEGER
);

CREATE TABLE IF NOT EXISTS data_registry (
    id INTEGER,
    path TEXT,
    data_type TEXT
);

CREATE TABLE IF NOT EXISTS pipeline_jobs (
    id INTEGER,
    status TEXT,
    job_type TEXT
);

CREATE TABLE IF NOT EXISTS flagging_history (
    id INTEGER,
    ms_path TEXT,
    flag_command TEXT
);

CREATE TABLE IF NOT EXISTS selfcal_iterations (
    id INTEGER,
    ms_path TEXT,
    iteration INTEGER
);

-- Pipeline state table for mosaic calibration scheduler
CREATE TABLE IF NOT EXISTS pipeline_state (
    key TEXT PRIMARY KEY,
    value TEXT,
    value_type TEXT,
    updated_at REAL,
    updated_by TEXT DEFAULT 'system',
    description TEXT
);

CREATE INDEX IF NOT EXISTS idx_pipeline_state_updated_at ON pipeline_state(updated_at);

-- Imaging checkpoints for long-running operations (resume support)
CREATE TABLE IF NOT EXISTS imaging_checkpoints (
    ms_path TEXT PRIMARY KEY,
    output_dir TEXT NOT NULL,
    started_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    iteration INTEGER DEFAULT 0,
    total_iterations INTEGER DEFAULT 0,
    peak_residual REAL,
    model_path TEXT,
    psf_path TEXT,
    residual_path TEXT,
    backend TEXT DEFAULT 'wsclean',
    status TEXT DEFAULT 'in_progress',  -- in_progress, completed, failed
    error_message TEXT
);

CREATE INDEX IF NOT EXISTS idx_imaging_checkpoints_status ON imaging_checkpoints(status);
CREATE INDEX IF NOT EXISTS idx_imaging_checkpoints_updated ON imaging_checkpoints(updated_at);

-- Pipeline health monitoring for alert generation
CREATE TABLE IF NOT EXISTS pipeline_health (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    check_type TEXT NOT NULL,           -- orphan_subbands, stuck_stages, disk_space, stale_jobs
    severity TEXT NOT NULL,             -- info, warning, critical
    message TEXT NOT NULL,
    details TEXT,                       -- JSON blob with additional context
    detected_at REAL NOT NULL,
    resolved_at REAL,
    acknowledged_at REAL,
    acknowledged_by TEXT
);

CREATE INDEX IF NOT EXISTS idx_pipeline_health_type ON pipeline_health(check_type);
CREATE INDEX IF NOT EXISTS idx_pipeline_health_severity ON pipeline_health(severity);
CREATE INDEX IF NOT EXISTS idx_pipeline_health_detected ON pipeline_health(detected_at);
