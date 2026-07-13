"""
Constants for DSA-110 continuum imaging pipeline.

Adapted from dsacalib.constants
"""

# pylint: disable=no-member  # astropy.units dynamic attributes

try:
    import numpy as np

    HAS_NUMPY = True
except ImportError:
    import math as math_fallback

    np = math_fallback
    HAS_NUMPY = False

try:
    import astropy.units as u
    from astropy.coordinates import EarthLocation

    HAS_ASTROPY = True
except ImportError:
    u = None
    EarthLocation = None
    HAS_ASTROPY = False

# Observatory location (DSA-110)
# Coordinates from docs/reference/env.md (authoritative for DSA-110)
if HAS_NUMPY:
    DSA110_LAT = 37.2314 * np.pi / 180  # radians
    DSA110_LON = -118.2817 * np.pi / 180  # radians
else:
    DSA110_LAT = 37.2314 * 3.1415926535 / 180
    DSA110_LON = -118.2817 * 3.1415926535 / 180
DSA110_ALT = 1222.0  # meters

# Create EarthLocation object for DSA-110
if HAS_ASTROPY and HAS_NUMPY:
    DSA110_LOCATION = EarthLocation(
        lat=DSA110_LAT * u.rad, lon=DSA110_LON * u.rad, height=DSA110_ALT * u.m
    )
else:
    DSA110_LOCATION = None

# Observatory coordinates for external use
DSA110_LATITUDE = 37.2314
DSA110_LONGITUDE = -118.2817
