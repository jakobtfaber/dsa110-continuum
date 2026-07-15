"""Canonical forced-photometry CSV contract (issues #133, #134).

One column contract, one writer, one normalizing reader for the per-epoch
``{date}T{HH}00_forced_phot.csv`` products. Three writer schemas exist in the
wild (2026-07 audit of ``/data/dsa110-proc/products/mosaics`` on H17):

1. ``source_id`` + ``flux_jy``/``flux_err_jy``            (canonical, below)
2. ``source_id`` + ``dsa_peak_jyb``/``dsa_peak_err_jyb``  (legacy)
3. ``source_name`` + ``measured_flux_jy``/``flux_err_jy`` (scripts/forced_photometry.py)

``normalize_phot_rows`` maps all of them onto the canonical contract so
consumers can stop alias-sniffing; ``write_forced_phot_csv`` is the single
writer and applies the per-measurement flux sanity gate (#134).

See ``docs/reference/photometry-and-ese.md`` (Forced-photometry CSV contract)
for the rationale and threshold provenance.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)

#: Canonical column contract. Extra columns are preserved after these.
CANONICAL_COLUMNS: list[str] = [
    "source_id",
    "ra_deg",
    "dec_deg",
    "flux_jy",
    "flux_err_jy",
    "nvss_flux_jy",
    "dsa_nvss_ratio",
    "snr",
]

#: Alias -> canonical. Applied only when the canonical column is absent.
COLUMN_ALIASES: dict[str, str] = {
    "source_name": "source_id",
    "measured_flux_jy": "flux_jy",
    "dsa_peak_jyb": "flux_jy",
    "dsa_peak_err_jyb": "flux_err_jy",
    "catalog_flux_jy": "nvss_flux_jy",
    "flux_ratio": "dsa_nvss_ratio",
    "ratio": "dsa_nvss_ratio",
}

#: Columns coerced to numeric during normalization ("" -> NaN).
_NUMERIC_COLUMNS = (
    "ra_deg",
    "dec_deg",
    "flux_jy",
    "flux_err_jy",
    "nvss_flux_jy",
    "dsa_nvss_ratio",
    "snr",
)

#: Per-measurement sanity bound (#134). The brightest source that can appear
#: in a DSA-110 field is Cas A at ~1.7 kJy; anything beyond this is a
#: measurement artifact, not astrophysics.
MAX_ABS_FLUX_JY: float = 5000.0

#: Minimum measurements for an epoch phot CSV to count as a science product
#: (#134). A near-empty CSV means photometry effectively failed even if the
#: process exited cleanly.
MIN_EPOCH_MEASUREMENTS: int = 10


def normalize_phot_rows(rows: Any) -> pd.DataFrame:
    """Return *rows* (records list or DataFrame) under the canonical contract.

    Aliases are renamed only where the canonical column is missing; unknown
    columns are preserved. Numeric canonical columns are coerced (empty
    strings become NaN). Raises ``ValueError`` if no flux column can be found.
    """
    df = rows.copy() if isinstance(rows, pd.DataFrame) else pd.DataFrame(list(rows))
    renames = {
        alias: canon
        for alias, canon in COLUMN_ALIASES.items()
        if alias in df.columns and canon not in df.columns
    }
    df = df.rename(columns=renames)
    if "flux_jy" not in df.columns:
        raise ValueError(
            f"No flux column found (columns={list(df.columns)}); "
            f"expected one of: flux_jy, {', '.join(a for a, c in COLUMN_ALIASES.items() if c == 'flux_jy')}"
        )
    for col in _NUMERIC_COLUMNS:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    ordered = [c for c in CANONICAL_COLUMNS if c in df.columns]
    extras = [c for c in df.columns if c not in ordered]
    return df[ordered + extras]


def apply_flux_sanity_gate(
    df: pd.DataFrame,
    max_abs_flux_jy: float = MAX_ABS_FLUX_JY,
) -> tuple[pd.DataFrame, list[str]]:
    """Drop physically impossible measurements; return ``(clean, reasons)``.

    Rejects rows whose ``flux_jy`` is non-finite or exceeds
    *max_abs_flux_jy* in magnitude. One human-readable reason per rejected
    row (capped at 20) is returned for gate/manifest logging.
    """
    flux = df["flux_jy"]
    finite = np.isfinite(flux)
    in_bounds = finite & (flux.abs() <= max_abs_flux_jy)
    reasons: list[str] = []
    for _, row in df[~in_bounds].head(20).iterrows():
        sid = row.get("source_id", "?")
        val = row["flux_jy"]
        why = (
            "non-finite flux"
            if not np.isfinite(val)
            else (f"|flux| {val:.6g} Jy > {max_abs_flux_jy:.6g} Jy bound")
        )
        reasons.append(f"{sid}: {why}")
    n_dropped = int((~in_bounds).sum())
    if n_dropped > len(reasons):
        reasons.append(f"... and {n_dropped - len(reasons)} more")
    return df[in_bounds].copy(), reasons


def check_min_measurements(
    n_measurements: int,
    minimum: int = MIN_EPOCH_MEASUREMENTS,
) -> tuple[bool, str]:
    """Return ``(ok, reason)`` for the epoch-level minimum-recovery gate."""
    if n_measurements >= minimum:
        return True, ""
    return False, (
        f"only {n_measurements} forced-photometry measurement(s) "
        f"(< {minimum} required for a science product)"
    )


def write_forced_phot_csv(
    rows: Any,
    path: str | Path,
    max_abs_flux_jy: float = MAX_ABS_FLUX_JY,
) -> dict:
    """Normalize, sanity-gate, and write a forced-photometry CSV.

    The single sanctioned writer for ``*_forced_phot.csv`` products.

    Returns
    -------
    dict
        ``n_written``, ``n_rejected``, ``rejected_reasons`` (list[str]),
        ``median_ratio`` (float, NaN when no ratios), ``path``.
    """
    df = normalize_phot_rows(rows)
    clean, reasons = apply_flux_sanity_gate(df, max_abs_flux_jy=max_abs_flux_jy)
    n_rejected = len(df) - len(clean)
    if n_rejected:
        log.warning(
            "Flux sanity gate rejected %d/%d measurement(s) for %s: %s",
            n_rejected,
            len(df),
            path,
            "; ".join(reasons[:5]),
        )
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    clean.to_csv(out, index=False)
    ratios = clean["dsa_nvss_ratio"].dropna() if "dsa_nvss_ratio" in clean.columns else []
    median_ratio = float(np.median(ratios)) if len(ratios) else float("nan")
    return {
        "n_written": int(len(clean)),
        "n_rejected": int(n_rejected),
        "rejected_reasons": reasons,
        "median_ratio": median_ratio,
        "path": str(out),
    }


def read_forced_phot_csv(path: str | Path) -> pd.DataFrame:
    """Read any historical or canonical forced-phot CSV, normalized."""
    return normalize_phot_rows(pd.read_csv(path))
