# Vendored from dsa110-contimg @ /data/dsa110-contimg/backend/src (H17), 2026-07-03,
# as part of the contimg-import-retirement migration (docs/rse/specs/plan-contimg-import-retirement.md).
"""Utilities for identifying DSA-110 antenna types (core vs outrigger)."""


# DSA-110 array configuration
# Based on DSA110_Station_Coordinates.csv analysis:
# - Core antennas: 1-102 (arranged in T-shaped layout)
# - Outrigger antennas: 103-117 (15 antennas at longer baselines)
# Reference: DSA-110 consists of 95-element core + 15 outriggers
# Note: Some antennas may not be present in all observations

# Outrigger antennas (confirmed from station coordinates)
OUTRIGGER_ANTENNAS: set[int] = {
    103,
    104,
    105,
    106,
    107,
    108,
    109,
    110,
    111,
    112,
    113,
    114,
    115,
    116,
    117,
}

# Core antennas (1-102)
CORE_ANTENNAS: set[int] = set(range(1, 103))


def is_outrigger(antenna_id: int) -> bool:
    """
    Check if an antenna is an outrigger.

    Parameters
    ----------
    antenna_id : int
        Antenna ID (1-117).

    Returns
    -------
    bool
        True if antenna is an outrigger, False otherwise.

    Examples
    --------
    >>> is_outrigger(103)
    True
    >>> is_outrigger(50)
    False
    >>> is_outrigger(117)
    True
    """
    return antenna_id in OUTRIGGER_ANTENNAS


def is_core(antenna_id: int) -> bool:
    """
    Check if an antenna is part of the core array.

    Parameters
    ----------
    antenna_id : int
        Antenna ID (1-117).

    Returns
    -------
    bool
        True if antenna is part of the core array, False otherwise.

    Examples
    --------
    >>> is_core(1)
    True
    >>> is_core(50)
    True
    >>> is_core(103)
    False
    """
    return antenna_id in CORE_ANTENNAS


def get_outrigger_antennas(available_antennas: list[int] | None = None) -> list[int]:
    """
    Get list of outrigger antennas, optionally filtered to available antennas.

    Parameters
    ----------
    available_antennas : list of int, optional
        Antenna IDs present in the data. If provided, returns only
        outriggers present in this list.

    Returns
    -------
    list of int
        Sorted list of outrigger antenna IDs.

    Examples
    --------
    >>> get_outrigger_antennas()[:3]
    [103, 104, 105]
    >>> get_outrigger_antennas([1, 50, 103, 110])
    [103, 110]
    """
    if available_antennas is None:
        return sorted(OUTRIGGER_ANTENNAS)

    available_set = set(available_antennas)
    outriggers = sorted([ant for ant in OUTRIGGER_ANTENNAS if ant in available_set])
    return outriggers


def get_core_antennas(available_antennas: list[int] | None = None) -> list[int]:
    """
    Get list of core antennas, optionally filtered to available antennas.

    Parameters
    ----------
    available_antennas : list of int, optional
        Antenna IDs present in the data. If provided, returns only
        core antennas present in this list.

    Returns
    -------
    list of int
        Sorted list of core antenna IDs.

    Examples
    --------
    >>> get_core_antennas()[:3]
    [1, 2, 3]
    >>> get_core_antennas([1, 50, 103, 110])
    [1, 50]
    """
    if available_antennas is None:
        return sorted(CORE_ANTENNAS)

    available_set = set(available_antennas)
    core = sorted([ant for ant in CORE_ANTENNAS if ant in available_set])
    return core


def select_outrigger_refant(
    available_antennas: list[int],
    preferred_refant: int | None = None,
) -> int | None:
    """Select an outrigger antenna as reference antenna.

        Priority:
        1. If preferred_refant is an outrigger and available, use it
        2. Otherwise, select first available outrigger (sorted by ID)

    Parameters
    ----------
    available_antennas : list
        List of antenna IDs present in the data
    preferred_refant : int or None, optional
        Optional preferred reference antenna ID

    Returns
    -------
        int or None
        Selected outrigger antenna ID, or None if no outriggers available
    """
    available_set = set(available_antennas)
    outriggers = get_outrigger_antennas(available_antennas)

    if not outriggers:
        return None

    # If preferred refant is an outrigger and available, use it
    if preferred_refant is not None:
        if preferred_refant in outriggers and preferred_refant in available_set:
            return preferred_refant

    # Otherwise, return first available outrigger
    return outriggers[0]
