# Vendored from dsa110-contimg @ /data/dsa110-contimg/backend/src (H17), 2026-07-03,
# as part of the contimg-import-retirement migration (docs/rse/specs/plan-contimg-import-retirement.md).
"""
Coordinate and source utilities for DSA-110.

Adapted from dsacalib.utils
"""

import astropy.units as u

# CASA import moved to function level to prevent logs in workspace root
# See: docs/dev-notes/analysis/casa_log_handling_investigation.md
from astropy.coordinates import SkyCoord
from astropy.time import Time

from dsa110_continuum.utils.casa_init import ensure_casa_path


class Direction:
    """
    Class for handling coordinate conversions.

    Parameters
    ----------
    epoch : str
        Coordinate epoch (e.g., 'J2000', 'HADEC')
    lon : astropy.units.Quantity
        Longitude coordinate
    lat : astropy.units.Quantity
        Latitude coordinate
    obstime : astropy.time.Time, optional
        Observation time
    observatory : str
        Observatory name for CASA (default: 'OVRO_MMA')
    """

    def __init__(self, epoch, lon, lat, obstime=None, observatory="OVRO_MMA"):
        self.epoch = epoch
        self.observatory = observatory

        # Handle lon/lat - can be Quantity objects or floats (assumed radians)
        if isinstance(lon, u.Quantity):
            self.lon = lon
        else:
            self.lon = lon * u.rad

        if isinstance(lat, u.Quantity):
            self.lat = lat
        else:
            self.lat = lat * u.rad

        # Handle obstime - can be Time object or float MJD
        if obstime is not None:
            if isinstance(obstime, Time):
                self.obstime = obstime
            else:
                # Assume it's a float MJD
                self.obstime = Time(obstime, format="mjd")
        else:
            self.obstime = None

        # Set up CASA tools
        import casatools as cc

        self.me = cc.measures()
        self.qa = cc.quanta()

        if self.observatory is not None:
            self.me.doframe(self.me.observatory(self.observatory))

        if self.obstime is not None:
            self.me.doframe(self.me.epoch("UTC", self.qa.quantity(self.obstime.mjd, "d")))

    def J2000(self, obstime=None, observatory=None):
        """
        Convert to J2000 coordinates.

        Parameters
        ----------
        obstime : astropy.time.Time, optional
            Observation time (overrides object obstime)
        observatory : str, optional
            Observatory name (overrides object observatory)

        Returns
        -------
        ra : astropy.units.Quantity
            Right Ascension in J2000
        dec : astropy.units.Quantity
            Declination in J2000
        """
        if obstime is not None:
            self.obstime = obstime
        if observatory is not None:
            self.observatory = observatory

        # Update reference frame
        if self.observatory is not None:
            self.me.doframe(self.me.observatory(self.observatory))
        if self.obstime is not None:
            self.me.doframe(self.me.epoch("UTC", self.qa.quantity(self.obstime.mjd, "d")))

        # Convert to J2000
        direction = self.me.direction(
            self.epoch,
            self.qa.quantity(self.lon.to_value(u.rad), "rad"),
            self.qa.quantity(self.lat.to_value(u.rad), "rad"),
        )

        j2000_dir = self.me.measure(direction, "J2000")

        ra = j2000_dir["m0"]["value"] * u.rad
        dec = j2000_dir["m1"]["value"] * u.rad

        return ra, dec

    def hadec(self, obstime=None, observatory=None):
        """
        Convert to Hour Angle-Declination coordinates.

        Parameters
        ----------
        obstime : astropy.time.Time, optional
            Observation time (overrides object obstime)
        observatory : str, optional
            Observatory name (overrides object observatory)

        Returns
        -------
        ha : astropy.units.Quantity
            Hour Angle
        dec : astropy.units.Quantity
            Declination
        """
        if obstime is not None:
            self.obstime = obstime
        if observatory is not None:
            self.observatory = observatory

        # Update reference frame
        if self.observatory is not None:
            self.me.doframe(self.me.observatory(self.observatory))
        if self.obstime is not None:
            self.me.doframe(self.me.epoch("UTC", self.qa.quantity(self.obstime.mjd, "d")))

        # Convert to HADEC
        direction = self.me.direction(
            self.epoch,
            self.qa.quantity(self.lon.to_value(u.rad), "rad"),
            self.qa.quantity(self.lat.to_value(u.rad), "rad"),
        )

        hadec_dir = self.me.measure(direction, "HADEC")

        ha = hadec_dir["m0"]["value"] * u.rad
        dec = hadec_dir["m1"]["value"] * u.rad

        return ha, dec


def generate_calibrator_source(
    name, ra, dec, flux=1.0, epoch="J2000", pa=None, maj_axis=None, min_axis=None
):
    """
    Generate a calibrator source object.

    Parameters
    ----------
    name : str
        Source name
    ra : astropy.units.Quantity
        Right Ascension
    dec : astropy.units.Quantity
        Declination
    flux : float
        Flux in Jy (default: 1.0)
    epoch : str
        Coordinate epoch (default: 'J2000')
    pa : astropy.units.Quantity, optional
        Position angle
    maj_axis : astropy.units.Quantity, optional
        Major axis size
    min_axis : astropy.units.Quantity, optional
        Minor axis size

    Returns
    -------
    source : SimpleNamespace
        Source object with attributes: name, ra, dec, flux, epoch, etc.
    """
    from types import SimpleNamespace

    source = SimpleNamespace()
    source.name = name
    source.ra = ra
    source.dec = dec
    source.flux = flux
    source.epoch = epoch
    source.pa = pa
    source.maj_axis = maj_axis
    source.min_axis = min_axis

    # Create SkyCoord for convenience
    source.coord = SkyCoord(ra=ra, dec=dec, frame="icrs")

    return source


def hms_to_deg(hms: str) -> float:
    """Convert HH:MM:SS to degrees (RA).

    Parameters
    ----------
    hms : str
        Time string in format "HH:MM:SS.sss"

    Returns
    -------
    float
        Right Ascension in degrees
    """
    from dsa110_continuum.adapters.casa import casa_adapter

    ensure_casa_path()
    qa = casa_adapter.quanta()
    
    # casatools.quanta handles parsing and conversion natively
    # It interprets "HH:MM:SS" as time (hours) by default
    # Returns value in degrees
    return qa.convert(qa.quantity(hms), 'deg')['value']


def dms_to_deg(dms: str) -> float:
    """Convert DD:MM:SS to degrees (Dec).

    Parameters
    ----------
    dms : str
        Angle string in format "+DD:MM:SS.sss" or "-DD:MM:SS.sss"

    Returns
    -------
    float
        Declination in degrees
    """
    from dsa110_continuum.adapters.casa import casa_adapter

    ensure_casa_path()
    qa = casa_adapter.quanta()
    
    # casatools.quanta interprets "X:Y:Z" as time (hours) by default.
    # To force angle (degrees) interpretation for Declination, we replace colons with dots
    # which CASA treats as angular degrees/minutes/seconds.
    # e.g., "45:00:00" -> "45.00.00"
    dms_fmt = dms.replace(':', '.')
    
    return qa.convert(qa.quantity(dms_fmt), 'deg')['value']
