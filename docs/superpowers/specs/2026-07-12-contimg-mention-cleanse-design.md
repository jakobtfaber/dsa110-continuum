# Contimg Mention Cleanse — Design

Companion to `docs/superpowers/plans/2026-07-12-contimg-mention-cleanse.md`.

## Decision (Track C)

**Host directory names stay as-is for now** (`/data/dsa110-contimg`,
`/stage/dsa110-contimg`, `/dev/shm/dsa110-contimg`). Code gains `DSA110_*`
env precedence with `CONTIMG_*` fallback; hardcoded defaults remain the
live H17 roots until an explicit ops rename + symlink cutover.

## Allowlist (may still mention `dsa110_contimg` / `dsa110-contimg`)

### Ban rails
- `scripts/check_import_migration.py`
- `scripts/check_contimg_mentions.py`
- `tests/test_import_migration_checker.py`
- `tests/test_no_compat_layer.py`
- `tests/test_no_latent_nameerror_imports.py`
- `tests/test_batch_e2_hygiene.py`
- `tests/test_init_reexports_new_namespace.py`
- `tests/test_imaging_worker_no_fast_imaging.py`
- `tests/test_workflow_registry.py`
- `tests/test_vendored_database.py`
- `tests/test_simulation_control.py`
- `tests/test_no_stale_contimg_api_refs.py`
- `tests/test_dev_tools.py`
- `pyproject.toml` (ruff `banned-api` rule only)

### Historical archive
- `docs/archive/contimg-retirement/**`

### Ops-coupled path defaults (INFO until `--strict-paths`)
- Hardcoded `/data|stage|dev/shm/dsa110-contimg` and `CONTIMG_*` defaults

### Skipped
- `outputs/**`
- `.git/**`

## Fail classes (`check_contimg_mentions.py --fail`)
- Vendored provenance headers naming `dsa110-contimg`
- Recommended `from|import dsa110_contimg` / Sphinx / `python -m dsa110_contimg` in package sources
- Dead `src/dsa110_contimg` or `backend/src/dsa110_contimg` layout probes
