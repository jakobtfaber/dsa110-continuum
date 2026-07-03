# Vendored from dsa110-contimg @ /data/dsa110-contimg/backend/src (H17), 2026-07-03,
# as part of the contimg-import-retirement migration
# (docs/rse/specs/plan-contimg-import-retirement.md, Phases 2 + 5).
"""Database layer for dsa110_continuum (vendored consumed subset).

Package-level names mirror the legacy ``dsa110_contimg.infrastructure.database``
re-exports that this repo consumes. SQLAlchemy-backed ``models``/``session``
are imported directly by their consumers, not re-exported here, to keep the
package import light.
"""

from dsa110_continuum.database.unified import (
    Database,
    ensure_db,
    ensure_pipeline_db,
    get_active_applylist,
    get_db,
    get_pipeline_db_path,
    images_insert,
    init_unified_db,
    ms_index_upsert,
    photometry_insert,
    retire_caltable_set,
)

__all__ = [
    "Database",
    "ensure_db",
    "ensure_pipeline_db",
    "get_active_applylist",
    "get_db",
    "get_pipeline_db_path",
    "images_insert",
    "init_unified_db",
    "ms_index_upsert",
    "photometry_insert",
    "retire_caltable_set",
]
