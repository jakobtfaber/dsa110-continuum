# Vendored from dsa110-contimg @ /data/dsa110-contimg/backend/src (H17), 2026-07-03,
# as part of the contimg-import-retirement migration (docs/rse/specs/plan-contimg-import-retirement.md).
"""Local copy of antenna position utilities for the DSA-110 array."""

# pylint: disable=no-member  # astropy.units dynamic attributes

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from importlib import resources

import astropy.units as u
import numpy as np
import pandas as pd
from astropy.coordinates import EarthLocation

__all__ = ["tee_centers", "get_lonlat", "get_itrf"]


DATA_FILENAME = "data/DSA110_Station_Coordinates.csv"
DEFAULT_HEIGHT_M = 1182.6
CSV_HEADER_LINE = 5
STATION_COLUMN = "Station Number"
LAT_COLUMN = "Latitude"
LON_COLUMN = "Longitude"
HEIGHT_COLUMN = "Elevation (meters)"
DX_COLUMN = "dx_m"
DY_COLUMN = "dy_m"
DZ_COLUMN = "dz_m"
X_COLUMN = "x_m"
Y_COLUMN = "y_m"
Z_COLUMN = "z_m"


@dataclass(frozen=True)
class _AntennaCatalog:
    csv_path: str

    @classmethod
    def default(cls) -> _AntennaCatalog:
        csv_resource = resources.files(__package__).joinpath(DATA_FILENAME)
        return cls(str(csv_resource))

    def pandas_frame(self, headerline: int = CSV_HEADER_LINE) -> pd.DataFrame:
        return pd.read_csv(self.csv_path, header=headerline)


def tee_centers() -> tuple[u.Quantity, u.Quantity, u.Quantity]:
    """Return the location of the DSA-110 tee centre in WGS84."""
    # Values from DSA-110 array reference
    tc_longitude = -2.064427799136453 * u.rad
    tc_latitude = 0.6498455107238486 * u.rad
    tc_height = 1188.0519 * u.m
    return tc_latitude, tc_longitude, tc_height


def get_lonlat(
    csvfile: str | None = None,
    headerline: int = CSV_HEADER_LINE,
    defaultheight: float = DEFAULT_HEIGHT_M,
) -> pd.DataFrame:
    """Load the antenna latitude/longitude catalogue as a DataFrame."""
    catalog = _AntennaCatalog(csvfile) if csvfile else _AntennaCatalog.default()

    table = catalog.pandas_frame(headerline=headerline)
    stations = table[STATION_COLUMN]
    latitude = table[LAT_COLUMN]
    longitude = table[LON_COLUMN]
    height = table[HEIGHT_COLUMN]

    df = pd.DataFrame()
    df[STATION_COLUMN] = [int(str(station).split("-")[-1]) for station in stations]
    df[LAT_COLUMN] = latitude
    df[LON_COLUMN] = longitude
    df[HEIGHT_COLUMN] = height
    df[HEIGHT_COLUMN] = np.where(np.isnan(df[HEIGHT_COLUMN]), defaultheight, df[HEIGHT_COLUMN])

    # Drop legacy designations such as 200E/200W if present
    drop_indices = [idx for idx, station in enumerate(stations) if str(station).startswith("200")]
    if drop_indices:
        df.drop(index=drop_indices, inplace=True, errors="ignore")

    df = df.astype({STATION_COLUMN: np.int32})
    df.sort_values(by=[STATION_COLUMN], inplace=True)
    df.set_index(STATION_COLUMN, inplace=True)
    return df


def _select_stations(df: pd.DataFrame, stations: Iterable[int] | None = None) -> pd.DataFrame:
    if stations is None:
        return df
    indices = np.array(list(stations), dtype=int)
    return df.loc[indices]


def get_itrf(
    csvfile: str | None = None,
    latlon_center: tuple[u.Quantity, u.Quantity, u.Quantity] | None = None,
    return_all_stations: bool = True,
    stations: Iterable[int] | None = None,
) -> pd.DataFrame:
    """Return antenna positions as ITRF coordinates."""
    df = get_lonlat(csvfile=csvfile)

    if not return_all_stations and stations is not None:
        df = _select_stations(df, stations)

    if latlon_center is None:
        latcenter, loncenter, heightcenter = tee_centers()
    else:
        latcenter, loncenter, heightcenter = latlon_center

    center = EarthLocation(lat=latcenter, lon=loncenter, height=heightcenter)
    locations = EarthLocation(
        lat=df[LAT_COLUMN].values * u.deg,
        lon=df[LON_COLUMN].values * u.deg,
        height=df[HEIGHT_COLUMN].values * u.m,
    )

    df[X_COLUMN] = locations.x.to_value(u.m)
    df[Y_COLUMN] = locations.y.to_value(u.m)
    df[Z_COLUMN] = locations.z.to_value(u.m)

    df[DX_COLUMN] = (locations.x - center.x).to_value(u.m)
    df[DY_COLUMN] = (locations.y - center.y).to_value(u.m)
    df[DZ_COLUMN] = (locations.z - center.z).to_value(u.m)

    return df
