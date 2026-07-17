"""Unit tests for ASKAPSoft cflag-style dynamic amplitude helpers."""

import numpy as np
from dsa110_continuum.calibration.flagging_cflag import (
    IQR_TO_SIGMA,
    _as_nrow_nchan_npol,
    _from_nrow_nchan_npol,
    _row_quantiles,
    dynamic_amplitude_mask,
    flag_rfi_cflag,
    integrate_spectra_mask,
    iqr_sigma,
)


def test_iqr_sigma_matches_askap_constant():
    x = np.linspace(-1.0, 1.0, 1001)
    expected = IQR_TO_SIGMA * (np.percentile(x, 75) - np.percentile(x, 25))
    assert abs(iqr_sigma(x) - expected) < 1e-9


def test_dynamic_amplitude_mask_flags_outliers():
    rng = np.random.default_rng(0)
    amp = rng.normal(0.06, 0.005, size=5000)
    amp[0] = 5.0
    mask = dynamic_amplitude_mask(amp, threshold=4.0)
    assert mask[0]
    assert mask.mean() < 0.01


def test_integrate_spectra_mask_flags_hot_channel():
    amp = np.full((24, 48), 0.06)
    amp[:, 40] = 0.30
    mask_chan = integrate_spectra_mask(amp, threshold=4.0)
    assert mask_chan[40]
    assert int(mask_chan.sum()) == 1


def test_as_nrow_nchan_npol_detects_casatools_layout():
    cube = np.zeros((10, 2, 48))
    ncp, layout = _as_nrow_nchan_npol(cube)
    assert layout == "npol_nchan"
    assert ncp.shape == (10, 48, 2)
    assert _from_nrow_nchan_npol(ncp, layout).shape == (10, 2, 48)


def test_row_quantiles_match_nanpercentile():
    rng = np.random.default_rng(1)
    work = rng.normal(size=(200, 48))
    work[::7, ::3] = np.nan
    med, q25, q75 = _row_quantiles(work, (0.50, 0.25, 0.75))
    assert np.allclose(med, np.nanpercentile(work, 50, axis=1), equal_nan=True, atol=1e-6)
    assert np.allclose(q25, np.nanpercentile(work, 25, axis=1), equal_nan=True, atol=1e-6)
    assert np.allclose(q75, np.nanpercentile(work, 75, axis=1), equal_nan=True, atol=1e-6)


def test_flag_rfi_cflag_runs_mandatory_stages(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "dsa110_continuum.calibration.flagging_cflag.flag_rfi_cflag_pass2",
        lambda *args, **kwargs: calls.append("pass2") or {},
    )
    monkeypatch.setattr(
        "dsa110_continuum.calibration.flagging_cflag.flag_residual_rfi_clip",
        lambda *args, **kwargs: calls.append("stage2") or {},
    )
    monkeypatch.setattr(
        "dsa110_continuum.calibration.flagging_cflag.flag_extend",
        lambda *args, **kwargs: calls.append("stage3"),
    )

    flag_rfi_cflag("fake.ms")

    assert calls == ["pass2", "stage2", "stage3"]
