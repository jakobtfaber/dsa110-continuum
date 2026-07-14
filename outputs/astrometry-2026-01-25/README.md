# Astrometry verification — 2026-01-25T2200

Overall verdict: **PASS** (gate RMS ≤ √(BMAJ×BMIN)/5 ≈ 8.82″).

## Diagnostics

| File | Description |
| --- | --- |
| `summary.json` | Per-survey stats and overall verdict |
| `seeded_offsets_{nvss,first,rax}.csv` | Per-source offsets |
| `offset_scatter.png` | ΔRA vs ΔDec panels |
| `hist_separation.png` | Separation histograms vs gate |
| `quiver_sky.png` | Offset vectors on sky |
| `diagnostic_nvss_bright20_cutouts.png` | 20 brightest NVSS cutouts with catalog markers |
| `nvss_bright20_cutouts.png` | Same figure (working name) |

## Slide deck

- Interactive: [`slides/index.html`](slides/index.html)
- Shareable (images embedded): [`slides/astrometry-verification-2026-01-25T2200-standalone.html`](slides/astrometry-verification-2026-01-25T2200-standalone.html)

## Reproduce

```bash
PYTHONPATH=/data/dsa110-continuum /opt/miniforge/envs/casa6/bin/python \
  scripts/validate_mosaic_astrometry.py \
  --mosaic /stage/dsa110-contimg/images/mosaic_2026-01-25/2026-01-25T2200_mosaic.fits \
  --out-dir /data/dsa110-continuum/outputs/astrometry-2026-01-25 \
  --no-blind
```
