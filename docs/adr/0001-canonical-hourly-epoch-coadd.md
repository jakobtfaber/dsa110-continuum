# Canonical hourly-epoch coadd

Status: accepted

The production hourly-epoch mosaic path and the package Quicklook builder currently
encode different coadd behavior. Production uses `scripts/mosaic_day.py` through
`scripts/batch_pipeline.py`: it partitions disjoint RA strips, blanks low-response
pixels with WSClean beam maps at 20% of peak response, and then coadds the tile
images. The package builder has the RA-wrap-safe WCS behavior and is the importable
package surface, but it uses an analytic Airy-disk beam model and does not blank
low primary-beam pixels during the default coadd.

We will make the package the canonical home for the production hourly-epoch coadd
by absorbing the production `mosaic_day` behavior into `dsa110_continuum.mosaic`,
not by promoting the current package builder behavior unchanged. The migration in
#77 should move or wrap the production coadd behind a package entry point, preserve
the package RA-wrap invariant, and then make `scripts/batch_pipeline.py` call that
package entry point instead of importing `scripts/mosaic_day.py`.

## Considered Options

- **Promote current `builder.build_mosaic` as-is.** This would use the already
  packaged Quicklook API and RA-wrap regression, but would silently drop current
  production behavior around WSClean beam-map blanking and strip partitioning.
- **Absorb production behavior into the package.** This keeps the production
  science behavior stable while moving ownership into the canonical package
  namespace. This is the selected option.

## Per-Axis Decision

- **Primary-beam blanking.** Keep production blanking at `PB_CUTOFF = 0.2` before
  coadd when WSClean `*-beam-0.fits` companions exist. The 10% cutoff in
  `compute_pb_correction_map` is a primary-beam correction floor, not the default
  hourly-epoch coadd blanking policy.
- **Primary-beam model source.** For production batch mosaics, use WSClean's
  per-tile beam model from disk. The analytic Airy-disk map may remain useful for
  Quicklook fallback or simulation paths, but it is not the production beam source
  for #77.
- **Strip partitioning.** Keep 10-degree RA gap strip grouping for day-batch and
  UTC-hour batch inputs so disjoint tile sets do not produce oversized mosaics.
  Sliding-window streaming inputs may bypass strip grouping when the trigger has
  already selected a single contiguous Dec strip window.
- **Glossary.** `CONTEXT.md` should describe Quicklook as the image-domain coadd
  tier, not as a single implementation's current internals. It must not claim
  default 10% blanking for hourly-epoch coadds.

## Consequences

After #77, `scripts/batch_pipeline.py` should stop importing `mosaic_day` for
coadd construction. `scripts/mosaic_day.py` can then either be reduced to a thin
compatibility wrapper around the package entry point or kept only for the legacy
standalone day-batch CLI until its callers are retired. Tests for the production
call path must assert that `batch_pipeline.py` reaches the package entry point and
that the selected primary-beam blanking, beam-map source, strip grouping, and
RA-wrap behavior match this decision.
