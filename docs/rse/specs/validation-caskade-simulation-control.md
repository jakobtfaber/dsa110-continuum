# Validation: Caskade simulation-control layer

> Validated against `handoff-2026-07-03-03-16-caskade-simulation-control.md` (no
> separate plan doc exists — the work went design-in-session → implement →
> adversarial-verify, and the handoff is the specification of record) at commit
> `3fb150f` on 2026-07-03. All commands below were re-run fresh in this session;
> no result is carried over from the implementing session's claims.

**Verdict: PASS.** The implementation is complete, the two environment-gated
tests that could not run on the Mac execute and pass on H17, and the full repo
suite shows no interaction regressions.

## Implementation Status

| Handoff item | Status | Evidence (this session) |
|---|---|---|
| `dsa110_continuum/simulation/control.py` caskade layer | ✅ Complete | Committed in `3fb150f`; live smoke test on H17 exercised `SimulationControl.add_source` → `fluxes` → `variability_models()` |
| Legacy interop (duck-type, `from_legacy`/`to_legacy`) | ✅ Complete | Covered by targeted suite (legacy-equivalence tests) — all pass on H17 casa6 |
| Test suite `tests/test_simulation_control.py` | ✅ Complete | 46/46 pass on H17 (see below) |
| Soft import in `simulation/__init__.py` + `caskade` extra in `pyproject.toml` | ✅ Complete | Diff reviewed in `3fb150f`: try/except ImportError block matching the file's existing pattern; `caskade = ["caskade>=1.1"]` under optional-dependencies |
| Commit the work | ✅ Done this session | `3fb150f` — five paths as one change (four code paths + the handoff doc, per user instruction) |
| H17 deployment (`pip install caskade` into casa6) | ✅ Done this session | caskade 1.1.1 installed; pre-checked `numpy>=1.24.0` requirement against casa6's numpy 1.26.4 so pip left numpy untouched |
| Fix latent `visibility_models.py` import bug | 📋 Follow-up (unchanged) | Pre-existing, not in scope; harmless on H17 where `dsa110_contimg` is installed (thermal-noise tests import it and pass) |

## Automated Verification Results

All runs on H17 (`lxd110h17`), casa6 env (`/opt/miniforge/envs/casa6/bin/python`,
Python 3.12.12, numpy 1.26.4), in a temporary worktree of `3fb150f` at
`/tmp/caskade-validate`, with `CASKADE_BACKEND=numpy`.

- ✅ `pip install "caskade>=1.1"` → caskade 1.1.1 importable in casa6.
- ✅ `pytest tests/test_simulation_control.py -v` — **46 passed, 0 skipped, 20.1 s.**
  The 2 tests skipped on the Mac (gain-corruption pyuvdata path, thermal-noise
  against the real `visibility_models`) executed for real and passed.
- ✅ `pytest tests/ -q` (full repo suite) — **1168 passed, 2 skipped, 0 failed,
  3 m 23 s.** The 896 warnings are pre-existing pyuvdata `uvw_array` notices in
  `test_simulated_pipeline.py` / `test_simulation_harness.py`, unrelated to this
  change. Skip identities recorded below.
- ✅ Live import/usage smoke test with `-W error::RuntimeWarning`: package import,
  `SimulationControl` with a real (non-identifier) source ID
  `NVSS_J123456+420000`, flare flux at peak = 3.0 (equals `peak_flux_jy`),
  `variability_models()` mapping keyed by the original ID — and **no**
  float32-backend `RuntimeWarning` on the numpy backend, confirming the guard
  is silent where it should be.

Full-suite skips (identified via a second full run with `-rs`, which also
reproduced the pass counts — 1168 passed, 2 skipped, 195.1 s): both are
pre-existing `--run-slow` gates, unrelated to this change:

- `tests/test_bad_pol_wiring_smoke.py:72` — "slow test (use --run-slow to enable)"
- `tests/test_simulated_pipeline.py` — "slow test (use --run-slow to enable)"

## Code Review Findings

- `simulation/__init__.py` addition follows the file's established soft-import
  pattern exactly (try/except ImportError + `__all__` entries); nothing else in
  the module was touched.
- `pyproject.toml` extra is minimal (`caskade = ["caskade>=1.1"]`) with a
  comment noting the numpy-default/torch-jax-optional backend split.
- Module field names mirror the legacy dataclasses field-for-field (e.g.
  `FlareModule(baseline_flux_jy, peak_time_mjd, rise_time_hours,
  decay_time_hours, peak_flux_jy)`) per the interop contract in the handoff —
  do not rename in either file without updating both.
- No deviations from the handoff spec found.

## Manual Testing Required

None blocking. Optional next phase (handoff item 5): wire `SimulationControl`
into an end-to-end synthetic run via
`generate_multi_epoch_uvh5(variability_models=control.variability_models())`
as an H17 integration test.

## Recommendations

- **Critical:** none.
- **Important:** the branch is unpushed; CI has not seen `3fb150f`. Push/PR when
  ready (repo convention is PR merges to `main`).
- **Follow-Up:** fix the pre-existing `visibility_models.py:134` latent import
  bug (`@stability` applied unconditionally while its import is guarded) — a
  no-op fallback when `dsa110_contimg` is absent. Tracked in the handoff;
  suggested skill: `ai-research-workflows:hardening-research-code`.
- **Nice to Have:** end-to-end synthetic integration test (above).

## Environment / cleanup state

- caskade 1.1.1 now installed in H17 casa6 (intended, persistent — this was
  handoff action item 2).
- Transfer bundle `/tmp/caskade.bundle` on H17 removed after fetch. Temporary
  validation worktree `/tmp/caskade-validate` removed after the final rerun and
  worktree metadata pruned in `/data/dsa110-continuum` (`git worktree list`
  confirms only the production checkout plus pre-existing `.emdash`/`.windsurf`
  worktrees remain — those are separate lanes, untouched).
- The H17 production checkout at `/data/dsa110-continuum` was not modified
  (still at `82e3d96`, clean apart from a pre-existing untracked `.emdash/`
  that was left untouched).

## References

- Spec / handoff: [handoff-2026-07-03-03-16-caskade-simulation-control.md](handoff-2026-07-03-03-16-caskade-simulation-control.md)
- Implementation commit: `3fb150f` ("Add caskade-based simulation control layer")
