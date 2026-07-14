"""Synthetic tests for the pre-coadd tile MAD gate."""

import sys
from pathlib import Path
from unittest.mock import Mock

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from mosaic_day import tile_mad_gate


def test_745_mjy_tile_is_rejected_before_coadd():
    amplitude_jy = 0.0745 / 1.4826
    tile = np.tile(np.array([-amplitude_jy, amplitude_jy]), 10_000)
    coadd_helper = Mock()

    accepted, reason = tile_mad_gate(tile)
    if accepted:
        coadd_helper(["bad-tile.fits"])

    assert not accepted
    assert "74.5 mJy" in reason
    coadd_helper.assert_not_called()


def test_quiet_tile_passes_gate():
    amplitude_jy = 0.005 / 1.4826
    tile = np.tile(np.array([-amplitude_jy, amplitude_jy]), 10_000)

    accepted, _ = tile_mad_gate(tile)

    assert accepted


def test_diagnostic_override_is_separate_and_explicit():
    amplitude_jy = 0.0745 / 1.4826
    tile = np.tile(np.array([-amplitude_jy, amplitude_jy]), 10_000)

    accepted, reason = tile_mad_gate(tile, include_qa_failed_tiles=True)

    assert accepted
    assert "diagnostic override" in reason
