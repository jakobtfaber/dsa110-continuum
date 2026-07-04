# Handoff: Caskade simulation-control layer (fold caskade into simulation framework)

---
**Date:** 2026-07-03 03:16
**Author:** AI Assistant (Claude Code, session on jakob-mbp)
**Status:** Handoff
**Branch:** `main` (up to date with `origin/main`)
**Commit:** `82e3d96` (all new work is uncommitted on top of this)

---

## Task(s)

User request: "Fold https://github.com/ConnorStoneAstro/caskade into the simulation control framework for the imaging pipeline."

| Task | Status | Notes |
|------|--------|-------|
| Design + implement caskade control layer (`dsa110_continuum/simulation/control.py`) | ✅ Complete | Variability modules, gain/noise knobs, `SimulationControl` DAG |
| Legacy interop (duck-type `evaluate`/`to_dict`, `from_legacy`/`to_legacy` bridges) | ✅ Complete | Bit-identical to legacy on numpy backend (verified by adversarial review sweep) |
| Test suite (`tests/test_simulation_control.py`) | ✅ Complete | 44 passed, 2 env-skips locally |
| Adversarial review + fix round | ✅ Complete | Independent reviewer subagent found 1 BLOCKER + 3 MAJOR; all fixed and re-tested |
| Import-time float32-backend warning | ✅ Complete | User-requested follow-up; verified live under torch and numpy |
| Exports (`simulation/__init__.py`) + pyproject extra (`caskade = ["caskade>=1.1"]`) | ✅ Complete | Soft-import pattern; package imports cleanly without caskade |
| Commit the work | 📋 Planned | User was asked "commit as one change?" — no answer yet |
| H17 deployment (`pip install caskade` into casa6 env) | 📋 Planned | One-time step before the module is usable on H17 |
| Fix latent `visibility_models.py` import bug | 📋 Planned (follow-up, not started) | Pre-existing; see Learnings |

**Current Workflow Phase:** Implement → Validate (implementation complete and validated locally; remaining work is commit/deploy/follow-ups)

## Workflow Artifacts

None pre-existing (`docs/rse/specs/` created by this handoff; no research/plan docs were produced — the work went design-in-session → implement → adversarial-verify).

## Critical References

- `dsa110_continuum/simulation/control.py` — the entire new layer. Module docstring documents design, interop contract, backend caveats, and usage examples.
- `tests/test_simulation_control.py` — encodes every verified invariant (legacy equivalence, dict cross-compat, dynamic fill, sanitized source-ID registry, degenerate params, delegation forwarding, float32 guard).
- `dsa110_continuum/simulation/variability_models.py` — the legacy dataclasses the caskade modules mirror field-for-field. Do not change field names in either file without updating both.

## Recent Changes

All uncommitted, on top of `82e3d96`:

- `dsa110_continuum/simulation/control.py` (new, ~590 lines) — caskade layer:
  - `VariabilityModule` base (`evaluate`/`lightcurve`/`to_dict`/`from_dict`, `_require_all_static` guard) + `ConstantFluxModule`, `FlareModule`, `ESEScatteringModule`, `PeriodicVariationModule` with vectorized `@forward flux(mjd, ...)`.
  - `GainCorruptionModule.apply` → `gain_corruption.corrupt_uvh5`; `ThermalNoiseModule.apply` → `visibility_models.add_thermal_noise` (forwards `frequency_hz` only when given — the wrapped param is non-Optional).
  - `SimulationControl` — sanitized-key registry (`_source_keys` id→graph-key; caskade link keys must be Python identifiers, real IDs like `NVSS_J123456+420000` are not), `fluxes(mjd)` stacking, `variability_models()` for `generate_multi_epoch_uvh5`, `to_dict()` config snapshot.
  - Import-time `_float32_backend()` probe + `RuntimeWarning` (torch/jax default float32 → MJD-scale precision loss).
- `tests/test_simulation_control.py` (new, ~370 lines) — pins `CASKADE_BACKEND=numpy` before caskade import.
- `dsa110_continuum/simulation/__init__.py` — new try/except import block + `__all__` entries (matches the file's existing soft-import pattern).
- `pyproject.toml` — `caskade = ["caskade>=1.1"]` under `[project.optional-dependencies]` (~line 139).

## Reproducibility & Data State

- **Environments:** local dev/test ran on `~/.conda/envs/py312/bin/python3` (has caskade 1.1.1 in site-packages — installed during this session, user approved keeping it; also has torch+jax, so caskade auto-selects torch there). H17 production env `/opt/miniforge/envs/casa6` does **not** have caskade yet.
- **caskade source reference:** cached at `~/.opensrc/repos/github.com/ConnorStoneAstro/caskade/main` (v1.1.1, numpy-only core dep).
- **Backend invariant:** numpy backend is the validated one (bit-identical to legacy). torch/jax = float32 by default → ~1e-3 relative flux errors, peak times quantized ~4 min. Tests pin numpy; `control.py` warns at import if a float32 backend is active.
- No datasets, seeds, or long-running jobs involved (pure library + unit tests).

## Verification State / Known-Broken

- **Tests:** `tests/test_simulation_control.py` — 44 passed, 2 skipped locally (py312). The 2 skips are environment-only: `pyuvdata` and importable `visibility_models` are absent on the Mac; both should run on H17/CI. Adjacent `tests/test_variability_wiring.py` + `tests/test_variability_metrics.py` re-run green. Full repo suite NOT run locally (needs casa6/H17 or cloud shim).
- **Uncommitted / unpushed:** all four paths above. Nothing pushed.
- **Verification record:** verify-gate entries recorded (adversarial-review, test, cross-check) — reviewer verdict was UNSOUND with 4 findings, all fixed same session and re-tested; post-fix suite green.
- **Known-broken (pre-existing, NOT introduced here):** `dsa110_continuum/simulation/visibility_models.py:134` applies `@stability` unconditionally while its import (from `dsa110_contimg.common.utils.stability`) is inside try/except — the module raises `NameError` at import wherever `dsa110_contimg`/cloud-shim is absent. Confirmed by direct import attempt this session.

## Learnings

- **caskade graph keys must be valid Python identifiers** (`caskade/base.py::is_valid_name`), and linked keys become attributes on the parent module. Real DSA-110 source IDs (`NVSS_J...+...`, `source_<ra>_<dec>` with dots) are not identifiers — hence `SimulationControl._sanitize_key` + registry instead of `NodeDict`. Keys are `src_`-prefixed to dodge method/keyword collisions; identically-sanitizing IDs get numeric suffixes.
- **`module.<param> = None` in caskade makes a static param with no value, NOT a dynamic param** — use constructor `value=None` or `.to_dynamic()`.
- **caskade `@forward` swallows the last positional arg as `params` when dynamic params exist** — that's why `evaluate`/`lightcurve` have the `_require_all_static` guard (otherwise misleading `FillParams` errors).
- **Empty caskade Module never runs `update_graph()`** → `@forward` bookkeeping (`subgraph_kwargs`) unset; `SimulationControl.__init__` calls it explicitly.
- **Flare rise/decay float behavior:** the `clip`+`where(mjd <= peak)` formulation is bit-identical to legacy branch arithmetic (verified over 20001-point + ULP-adjacent sweep); `rise_time_hours=0` needs the explicit step branch (0/0 NaN otherwise); `decay_time_hours=0` intentionally diverges (legacy raises ZeroDivisionError, module returns baseline via `exp(-inf)`).
- **Legacy `int` fields serialize as `2` vs module `2.0`** — dicts compare `==` but not byte-identical JSON. Interop tested via dict equality.
- **One model instance cannot back two sources** (caskade rejects double-linking a child); legacy plain dicts allowed aliasing.
- Review artifact: reviewer subagent ran with caskade probes; its full findings list is in the session transcript (findings 5–12 = MINOR/NIT: torch/jax float32 [now warned], zero-source stack [now guarded], out-of-range `InvalidValueWarning` if `filterwarnings=error` is ever adopted, etc.).

## Action Items & Next Steps

1. [ ] Commit the four paths as one change (user was asked; no answer yet — do not commit unprompted).
2. [ ] On H17: `/opt/miniforge/envs/casa6/bin/python -m pip install caskade`, then run `tests/test_simulation_control.py` there — the 2 local env-skips (gain-corruption pyuvdata stub path, thermal-noise real `visibility_models`) should execute for real.
3. [ ] Run the full repo suite on H17/CI to confirm no interaction regressions (only targeted + adjacent tests were run locally).
4. [ ] Follow-up (separate change): fix `visibility_models.py` latent import bug — no-op fallback for `@stability` when `dsa110_contimg` is absent.
5. [ ] Optional next phase: wire `SimulationControl` into an end-to-end synthetic run (`generate_multi_epoch_uvh5(variability_models=control.variability_models())`) as an integration test on H17.

**Recommended Next Skill:** `ai-research-workflows:validating-implementations` (run remaining H17/CI validation of the completed implementation), then `ai-research-workflows:hardening-research-code` for item 4.

## Other Notes

- ALWAYS use `/opt/miniforge/envs/casa6/bin/python` on H17; on this Mac, `~/.conda/envs/py312/bin/python3` + `PYTHONPATH=<repo>` works for these tests.
- `CASKADE_BACKEND=numpy` is the safe default everywhere; in py312 specifically, torch auto-selection triggers the new import warning by design.
- pyproject extra install form: `pip install -e .[caskade]` (or plain `pip install caskade`).
- The adversarial reviewer agent id was `a9d8ad2874354936b` (same-session only; not resumable in a new session — findings are summarized above and in Learnings).

---

**Handoff created by AI Assistant on 2026-07-03**
