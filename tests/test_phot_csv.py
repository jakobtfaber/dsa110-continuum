"""Tests for the canonical forced-photometry CSV contract (issues #133, #134).

Pure numpy/pandas + tmp_path; no FITS, no CASA, no telescope paths.
"""

import numpy as np
import pandas as pd
import pytest
from dsa110_continuum.photometry.phot_csv import (
    CANONICAL_COLUMNS,
    MAX_ABS_FLUX_JY,
    MIN_EPOCH_MEASUREMENTS,
    apply_flux_sanity_gate,
    check_min_measurements,
    normalize_phot_rows,
    read_forced_phot_csv,
    write_forced_phot_csv,
)


def test_normalize_canonical_passthrough():
    df = normalize_phot_rows(
        [
            {
                "source_id": "S1",
                "ra_deg": 150.0,
                "dec_deg": 16.1,
                "flux_jy": 0.5,
                "flux_err_jy": 0.01,
                "nvss_flux_jy": 0.5,
                "dsa_nvss_ratio": 1.0,
                "snr": 50.0,
            }
        ]
    )
    assert list(df.columns) == CANONICAL_COLUMNS
    assert df.iloc[0]["flux_jy"] == 0.5


def test_normalize_two_stage_writer_schema():
    # schema 3: scripts/forced_photometry.py two_stage rows ("" for missing)
    df = normalize_phot_rows(
        [
            {
                "source_name": "J150.0+16.1",
                "ra_deg": 150.0,
                "dec_deg": 16.1,
                "catalog_flux_jy": 0.4,
                "measured_flux_jy": 0.42,
                "flux_err_jy": "",
                "flux_ratio": 1.05,
                "snr": 21.0,
                "coarse_snr": 5.0,
                "passed_coarse": True,
            }
        ]
    )
    assert df.iloc[0]["source_id"] == "J150.0+16.1"
    assert df.iloc[0]["flux_jy"] == 0.42
    assert np.isnan(df.iloc[0]["flux_err_jy"])  # "" coerced to NaN
    assert df.iloc[0]["nvss_flux_jy"] == 0.4
    assert df.iloc[0]["dsa_nvss_ratio"] == 1.05
    assert "coarse_snr" in df.columns  # extras preserved
    assert list(df.columns)[: len(CANONICAL_COLUMNS)] == CANONICAL_COLUMNS


def test_normalize_legacy_peak_schema():
    # schema 2: dsa_peak_jyb + source_id (historical products)
    df = normalize_phot_rows(
        pd.DataFrame(
            {
                "source_id": ["1"],
                "ra_deg": [31.2],
                "dec_deg": [15.2],
                "nvss_flux_jy": [4.07],
                "dsa_peak_jyb": [3.9],
                "dsa_peak_err_jyb": [0.05],
                "dsa_nvss_ratio": [0.96],
            }
        )
    )
    assert df.iloc[0]["flux_jy"] == 3.9
    assert df.iloc[0]["flux_err_jy"] == 0.05


def test_normalize_never_clobbers_canonical():
    # if both flux_jy and an alias exist, canonical wins
    df = normalize_phot_rows(
        [
            {
                "source_id": "S1",
                "ra_deg": 1.0,
                "dec_deg": 2.0,
                "flux_jy": 0.7,
                "measured_flux_jy": 99.0,
            }
        ]
    )
    assert df.iloc[0]["flux_jy"] == 0.7
    assert df.iloc[0]["measured_flux_jy"] == 99.0  # kept as extra


def test_normalize_requires_a_flux_column():
    with pytest.raises(ValueError, match="No flux column"):
        normalize_phot_rows([{"source_id": "S1", "ra_deg": 1.0, "dec_deg": 2.0}])


def test_flux_sanity_gate_drops_junk_row():
    # the motivating incident: a 297 kJy artifact (issue #134)
    df = normalize_phot_rows(
        [
            {"source_id": "junk", "ra_deg": 1.0, "dec_deg": 2.0, "flux_jy": 297000.0},
            {"source_id": "casA-scale", "ra_deg": 1.0, "dec_deg": 2.0, "flux_jy": 1700.0},
            {"source_id": "nanrow", "ra_deg": 1.0, "dec_deg": 2.0, "flux_jy": float("nan")},
            {"source_id": "ok", "ra_deg": 1.0, "dec_deg": 2.0, "flux_jy": 0.5},
        ]
    )
    clean, reasons = apply_flux_sanity_gate(df)
    assert list(clean["source_id"]) == ["casA-scale", "ok"]
    assert len(reasons) == 2
    assert any("junk" in r and "bound" in r for r in reasons)
    assert any("nanrow" in r and "non-finite" in r for r in reasons)


def test_min_measurements_gate():
    ok, reason = check_min_measurements(MIN_EPOCH_MEASUREMENTS)
    assert ok and reason == ""
    ok, reason = check_min_measurements(1)
    assert not ok and "1" in reason
    ok, reason = check_min_measurements(1, minimum=1)
    assert ok


def test_writer_roundtrip_and_stats(tmp_path):
    rows = [
        {
            "source_name": f"J{i}",
            "ra_deg": float(i),
            "dec_deg": 16.0,
            "catalog_flux_jy": 0.5,
            "measured_flux_jy": 0.5,
            "flux_err_jy": 0.01,
            "flux_ratio": 1.0,
            "snr": 50.0,
        }
        for i in range(12)
    ]
    rows.append(
        {
            "source_name": "Jjunk",
            "ra_deg": 0.0,
            "dec_deg": 16.0,
            "catalog_flux_jy": 0.5,
            "measured_flux_jy": 297000.0,
            "flux_err_jy": 0.01,
            "flux_ratio": 594000.0,
            "snr": 9e9,
        }
    )
    out = tmp_path / "e_forced_phot.csv"
    stats = write_forced_phot_csv(rows, out)
    assert stats["n_written"] == 12
    assert stats["n_rejected"] == 1
    assert stats["median_ratio"] == pytest.approx(1.0)
    back = read_forced_phot_csv(out)
    assert len(back) == 12
    assert list(back.columns)[: len(CANONICAL_COLUMNS)] == CANONICAL_COLUMNS
    assert "Jjunk" not in set(back["source_id"])


def test_writer_respects_custom_bound(tmp_path):
    rows = [{"source_id": "S", "ra_deg": 0.0, "dec_deg": 0.0, "flux_jy": 10.0}]
    out = tmp_path / "x_forced_phot.csv"
    stats = write_forced_phot_csv(rows, out, max_abs_flux_jy=5.0)
    assert stats["n_written"] == 0 and stats["n_rejected"] == 1
    assert MAX_ABS_FLUX_JY > 5.0  # default untouched
