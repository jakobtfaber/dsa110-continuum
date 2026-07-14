**Findings**

1. **High: archived mosaic/weight products can become mismatched.**  
   [scripts/batch_pipeline.py](/data/dsa110-continuum-worktrees/agent-sault-weighted-coadd/scripts/batch_pipeline.py:2058) copies the mosaic only when the products-dir FITS is missing or `--force-recal`, but [line 2061](/data/dsa110-continuum-worktrees/agent-sault-weighted-coadd/scripts/batch_pipeline.py:2061) can still copy a newly built weight file when the products mosaic already exists. If the stage mosaic was rebuilt while an older products mosaic remains, the products directory can contain an old science mosaic with a new inverse-variance companion. Gate the weight copy on the same mosaic-copy decision, or verify the products mosaic is byte/header-aligned with the stage mosaic before copying the companion.

2. **Medium: companion validation accepts scientifically unusable weight maps.**  
   [dsa110_continuum/mosaic/production.py](/data/dsa110-continuum-worktrees/agent-sault-weighted-coadd/dsa110_continuum/mosaic/production.py:386) only validates BUNIT, shape, and WCS alignment. An all-zero, all-NaN, negative, or otherwise stale weight plane with matching metadata passes [lines 406-413](/data/dsa110-continuum-worktrees/agent-sault-weighted-coadd/dsa110_continuum/mosaic/production.py:406), causing resume logic to skip rebuilding and downstream users to trust `1/sqrt(weight)`. Add data-level checks such as finite nonnegative weights, at least one positive weight, and positive weight wherever the mosaic has finite science pixels; add a regression test for zero/nonfinite companions.

