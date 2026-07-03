# Core domain modules for DSA-110 continuum imaging pipeline.
"""
Core astronomy processing modules:

- calibration: CASA-based calibration with process isolation
- catalog: Source catalog management
- conversion: UVH5 to Measurement Set conversion
- imaging: Image synthesis and deconvolution
- mosaic: Multi-observation mosaic construction
- photometry: Source photometry measurements
- qa: Quality assurance metrics
- rfi: Radio frequency interference detection
- search: Source catalog queries
- selfcal: Self-calibration workflows
- visualization: FITS viewers and diagnostics
"""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("dsa110-continuum")
except PackageNotFoundError:
    # Running from a source checkout (PYTHONPATH) without an installed dist
    __version__ = "0.0.0+unknown"
