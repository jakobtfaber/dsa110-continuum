**Findings**

- **Medium** [scripts/batch_pipeline.py:154](/data/dsa110-continuum-worktrees/agent-sault-weighted-coadd/scripts/batch_pipeline.py:154): `_archive_epoch_products()` stages both temp copies before replacement, so the prior copy-failure bug is fixed, but the final publish is still not pair-atomic. If `os.replace(mosaic_temp, mosaic_destination)` succeeds and `os.replace(weight_temp, weight_destination)` then fails at line 157, products can contain a new mosaic with an old or missing weight map. Add rollback/backups around the replace phase, or publish the pair through a single transactional directory/manifest state so any replacement failure cannot expose a mixed pair.

**Verified**

- Weight validation now rejects WCS projection/grid mismatches via strict `weight_wcs.wcs.compare(mosaic_wcs.wcs)` at [production.py:420](/data/dsa110-continuum-worktrees/agent-sault-weighted-coadd/dsa110_continuum/mosaic/production.py:420); I found no actionable WCS-validation regression.
- Targeted tests passed: `66 passed, 3 warnings` using `/opt/miniforge/envs/casa6/bin/python -m pytest tests/test_mosaic_sault_coadd.py tests/test_mosaic_production_coadd.py tests/test_provenance.py tests/test_run_report.py -q`.