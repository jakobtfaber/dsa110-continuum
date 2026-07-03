"""
DSA-110 Continuum Imaging Pipeline - Conversion Module.

This module provides functionality for converting UVH5 subband files to
Measurement Sets (MS).

Entry Points:
    Batch conversion:
        from dsa110_continuum.conversion import convert_subband_groups_to_ms

Writers:
    DirectSubbandWriter - Main MS writer for production use

    For explicit file-list conversion (bypassing auto-discovery), use
    DirectSubbandWriter directly::

        from dsa110_continuum.conversion.writers import get_writer
        import pyuvdata

        writer_cls = get_writer("direct-subband")
        uvdata = pyuvdata.UVData()  # Empty - DirectSubbandWriter reads files directly
        writer = writer_cls(uvdata, ms_path, file_list=file_list, max_workers=4)
        writer.write()
"""

from __future__ import annotations

from importlib import import_module
from typing import Any

_LAZY_EXPORTS = {
    # Submodules
    "helpers_coordinates": ".helpers_coordinates",
    # Batch conversion
    "convert_subband_groups_to_ms": ".conversion_orchestrator",
    # Normalization
    "build_subband_filename": ".normalize",
    "normalize_directory": ".normalize",
    "normalize_subband_on_ingest": ".normalize",
    "normalize_subband_path": ".normalize",
    # Writers
    "MSWriter": ".writers",
    "DirectSubbandWriter": ".writers",
    "get_writer": ".writers",
    # Downsampling
    "downsample_uvh5": ".downsample_uvh5",
    "get_downsampling_info": ".downsample_uvh5",
    # Calibrator MS generation
    "CalibratorMSGenerator": ".calibrator_ms_generator",
    "CalibratorMSResult": ".calibrator_ms_generator",
    "CalibratorInfo": ".calibrator_ms_generator",
    "TransitInfo": ".calibrator_ms_generator",
}


def __getattr__(name: str) -> Any:
    """Lazily load conversion exports so package import stays cloud-collectable."""
    module_name = _LAZY_EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    module = import_module(module_name, __name__)
    value = module if name == "helpers_coordinates" else getattr(module, name)
    globals()[name] = value
    return value


__all__ = [
    # Submodules
    "helpers_coordinates",
    # Batch conversion
    "convert_subband_groups_to_ms",
    # Normalization
    "build_subband_filename",
    "normalize_directory",
    "normalize_subband_on_ingest",
    "normalize_subband_path",
    # Writers
    "MSWriter",
    "DirectSubbandWriter",
    "get_writer",
    # Downsampling
    "downsample_uvh5",
    "get_downsampling_info",
    # Calibrator MS generation
    "CalibratorMSGenerator",
    "CalibratorMSResult",
    "CalibratorInfo",
    "TransitInfo",
]
