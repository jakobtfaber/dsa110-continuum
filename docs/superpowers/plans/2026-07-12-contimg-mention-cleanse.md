# Contimg Mention Cleanse Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove obsolete `dsa110-contimg` / `dsa110_contimg` residue from the repo without breaking H17, while keeping intentional ban-rails that still must name the retired package.

**Architecture:** Split the work into four tracks with different risk profiles. Track A/B are in-repo text and dead probes (safe). Track C is an ops-gated host-path + env-var rename (requires H17 symlinks and a dual-read window). Track D is docs archival policy + an expanded CI lint so residue cannot creep back. Do **not** do a global string replace.

**Tech Stack:** Python path resolver (`dsa110_continuum/utils/paths/resolver.py`), env helpers (`get_env_path` / `CONTIMG_*`), `scripts/check_import_migration.py`, ruff `banned-api`, pytest regression suite, H17 filesystem roots.

---

## Critical constraint (read first)

On H17 today these directories **exist and are live**:

| Path | Role |
| --- | --- |
| `/data/dsa110-contimg/` | State, catalogs, pipeline DB, config |
| `/stage/dsa110-contimg/` | MS, images, mosaics, figures |
| `/dev/shm/dsa110-contimg/` | Scratch / CASA HOME / tmpfs |

There is **no** `/stage/dsa110-continuum`. Repo checkout lives at `/data/dsa110-continuum`; that is *not* the state/stage root.

Blindly rewriting path defaults in code to `…/dsa110-continuum` would break production. Package-import retirement (PR #93) is already done; this plan cleans *residue*, then optionally renames *host roots*.

### Inventory snapshot (2026-07-12, excluding `outputs/`)

| Area | `dsa110_contimg` | `dsa110-contimg` | Notes |
| --- | --- | --- | --- |
| `dsa110_continuum/` | ~31 files / 53 hits | ~130 files / 356 hits | Mostly path defaults + 71 “Vendored from…” headers |
| `scripts/` | 1 file | 19 files | Path defaults in ops scripts |
| `tests/` | 14 files | 6 files | Mostly intentional ban-rails |
| `docs/` | 36 files | 43 files | Mix of historical + still-active |
| `pyproject.toml` | banned-api msg | old GitHub URLs | Metadata debt |
| `outputs/` | many | many | **Out of scope** (historical artifacts) |

### Taxonomy (do / keep / migrate)

| Class | Examples | Action |
| --- | --- | --- |
| **A. Narrative / provenance** | `# Vendored from dsa110-contimg @ …`, “ported from the older codebase”, stale Sphinx refs to `dsa110_contimg.*` | Scrub or rewrite to current `dsa110_continuum.*` |
| **B. Dead layout probes** | `(root / "src" / "dsa110_contimg").exists()`, `backend/src/dsa110_contimg` path guesses | Delete or retarget to continuum layout |
| **C. Live path defaults** | `"/stage/dsa110-contimg/ms"`, `CONTIMG_BASE_DIR` default `/data/dsa110-contimg` | Dual-read rename behind env + host symlinks |
| **D. Ban rails (keep the name)** | `scripts/check_import_migration.py`, ruff `banned-api`, `tests/test_import_migration_checker.py`, `tests/test_no_compat_layer.py`, retirement `RuntimeError` messages | Keep mentioning `dsa110_contimg` on purpose |
| **E. Historical docs** | `docs/rse/specs/validation-contimg-import-retirement.md`, old plans | Archive with banner; do not rewrite history |
| **F. Derived artifacts** | `outputs/**` | Leave; do not rewrite |

### Target naming (Track C proposal)

| Current | Proposed canonical | Env (new → old alias) |
| --- | --- | --- |
| `/data/dsa110-contimg` | `/data/dsa110-continuum-state` *(or keep data root and only rename leaf — decide in Task C0)* | `DSA110_BASE_DIR` → `CONTIMG_BASE_DIR` |
| `/stage/dsa110-contimg` | `/stage/dsa110-continuum` | `DSA110_STAGING_DIR` / existing `DSA110_MS_DIR`, `DSA110_STAGE_IMAGE_BASE` |
| `/dev/shm/dsa110-contimg` | `/dev/shm/dsa110-continuum` | `DSA110_TMPFS_DIR` → `CONTIMG_TMPFS_DIR` |

**Human decision required before Track C:** confirm the three target directory names. Until then, Tracks A/B/D proceed with path strings untouched.

---

## File map (by track)

### Track A — narrative scrub
- Modify: ~71 files with `# Vendored from dsa110-contimg…` header under `dsa110_continuum/`
- Modify: stale docstrings/comments in e.g. `imaging/__init__.py`, `utils/casa_init.py`, `unified_config.py`, `visualization/*.py`, `database/*.py`, `photometry/{manager,worker}.py`, `mosaic/science_jobs.py`
- Modify: `AGENTS.md`, `CLAUDE.md` (lead with continuum identity; one short historical sentence max)
- Modify: `pyproject.toml` `[project.urls]` → `dsa110/dsa110-continuum`; fix obsolete doctest comment path
- Keep: `pyproject.toml` ruff banned-api entry for `dsa110_contimg`

### Track B — dead probes / wrong package paths
- Modify: `calibration/catalogs.py` (multiple `src/dsa110_contimg` existence probes)
- Modify: `simulation/make_synthetic_uvh5.py`, `simulation/visibility_models.py`
- Modify: `utils/templates.py` (still searches `…/dsa110_contimg/templates`)
- Modify: `database/data_registry.py` Alembic path string; `evidence/hdf5_calibrator_tile_smoke.py` config-owner check
- Modify: any `TYPE_CHECKING` / docstring blocks that show `from dsa110_contimg…` as recommended usage

### Track C — path + env dual-read (ops-gated)
- Modify: `dsa110_continuum/utils/paths/resolver.py` (single source of truth)
- Modify: call sites that hardcode path strings instead of using the resolver (scripts + scattered defaults)
- Modify: env var readers to prefer `DSA110_*` with `CONTIMG_*` fallback
- Host ops (not in git): create symlinks, validate, later cut defaults

### Track D — docs policy + CI gate
- Move completed retirement/validation plans under `docs/archive/contimg-retirement/` with a one-line banner
- Update *active* docs (`docs/skills/`, `docs/reference/`, `docs/agents/`) only after Track C defaults land — or update prose to say “host stage root (default still `/stage/dsa110-contimg` until path migration)”
- Extend or add a lint script for non-allowlisted mentions

---

### Task A0: Freeze allowlist and acceptance criteria

**Files:**
- Create: `docs/superpowers/specs/2026-07-12-contimg-mention-cleanse-design.md` (short design note + allowlist)
- Create: `scripts/check_contimg_mentions.py` (stub that prints classified hits; fail mode off until Track D)

- [ ] **Step 1: Write the allowlist**

Allowlist categories (must keep the banned name):

```text
# Ban rails
scripts/check_import_migration.py
scripts/check_contimg_mentions.py
tests/test_import_migration_checker.py
tests/test_no_compat_layer.py
tests/test_no_latent_nameerror_imports.py
tests/test_batch_e2_hygiene.py
tests/test_init_reexports_new_namespace.py
tests/test_imaging_worker_no_fast_imaging.py
tests/test_workflow_registry.py
tests/test_vendored_database.py
tests/test_simulation_control.py
pyproject.toml  # banned-api rule only

# Historical archive (after move)
docs/archive/contimg-retirement/**

# Until Track C completes: path-default files are Class C, not violations
# (checker reports them as "ops-coupled", does not fail)
```

- [ ] **Step 2: Define exit criteria**

```text
Track A done when:
  - zero "Vendored from dsa110-contimg" headers
  - zero recommended-usage snippets importing dsa110_contimg.*
  - pyproject URLs point at dsa110-continuum
  - AGENTS.md/CLAUDE.md do not lead with the old package as current identity

Track B done when:
  - no filesystem probes for backend/src/dsa110_contimg
  - template/catalog discovery uses continuum paths only

Track C done when:
  - resolver defaults to new roots
  - old roots still work via symlink or env alias for one release
  - batch_pipeline dry-run on H17 finds MS/DB/images

Track D done when:
  - check_contimg_mentions.py --fail exits 0 on allowlisted tree
  - CI runs it alongside check_import_migration.py
```

- [ ] **Step 3: Commit**

```bash
git add docs/superpowers/specs/2026-07-12-contimg-mention-cleanse-design.md \
        scripts/check_contimg_mentions.py
git commit -m "$(cat <<'EOF'
Add contimg-mention cleanse design and mention classifier stub.

EOF
)"
```

---

### Task A1: Strip vendored provenance headers

**Files:**
- Modify: all `dsa110_continuum/**/*.py` whose first lines are `# Vendored from dsa110-contimg @ …`

- [ ] **Step 1: Inventory and replace**

```bash
rg -l '^# Vendored from dsa110-contimg' dsa110_continuum/
```

Replace the two-line header with either nothing (preferred) or a single neutral line:

```python
# Formerly vendored during contimg→continuum migration (2026-07); now first-party.
```

Prefer **deleting** the header entirely unless a file still needs migration provenance for auditors — in that case keep one short continuum-centric note without the old package path.

- [ ] **Step 2: Verify**

```bash
rg -n 'Vendored from dsa110-contimg' dsa110_continuum/ && echo FAIL || echo OK
```

- [ ] **Step 3: Commit**

```bash
git commit -m "$(cat <<'EOF'
Remove obsolete dsa110-contimg vendored provenance headers.

EOF
)"
```

---

### Task A2: Rewrite stale API/docstring references inside the package

**Files (confirmed hits; re-grep before editing):**
- Modify: `dsa110_continuum/imaging/__init__.py` (recommends `from dsa110_contimg.interfaces.public_api import image_ms`)
- Modify: `dsa110_continuum/utils/casa_init.py` (deprecation text imports old `CASAService` path)
- Modify: `dsa110_continuum/unified_config.py`, `utils/gpu_utils.py`, `utils/templates.py`, `utils/error_context.py`, `utils/logging/pipeline.py`
- Modify: `dsa110_continuum/database/*.py` module docstrings that show `from dsa110_contimg.infrastructure…`
- Modify: `dsa110_continuum/visualization/{fits_viewer,fits_plots,calibration_plots,structure,coverage_moc}.py`
- Modify: retirement messages in `photometry/{manager,worker}.py`, `mosaic/science_jobs.py`, `imaging/export.py` — shorten to “legacy batch-photometry / Dagster bridge retired” **without** requiring the old package name (ban-rail tests must still pass)

- [ ] **Step 1: Write/adjust a regression test**

Extend `tests/test_no_compat_layer.py` or add `tests/test_no_stale_contimg_api_refs.py`:

```python
"""Fail if package docs still recommend importing dsa110_contimg."""
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1] / "dsa110_continuum"
FORBIDDEN = re.compile(
    r"(?:from|import)\s+dsa110_contimg\b|"
    r":class:`~dsa110_contimg\.|"
    r"python -m dsa110_contimg\b"
)

def test_no_recommended_contimg_imports_in_package_docs():
    bad = []
    for path in ROOT.rglob("*.py"):
        text = path.read_text(encoding="utf-8", errors="replace")
        for i, line in enumerate(text.splitlines(), 1):
            if FORBIDDEN.search(line):
                bad.append(f"{path}:{i}:{line.strip()}")
    assert not bad, "stale contimg API refs:\n" + "\n".join(bad)
```

- [ ] **Step 2: Run test (expect fail), then scrub, then pass**

```bash
PYTHONPATH=/data/dsa110-continuum /opt/miniforge/envs/casa6/bin/python -m pytest \
  tests/test_no_stale_contimg_api_refs.py -q
```

- [ ] **Step 3: Retarget deprecations to continuum symbols**

Example for `get_casa_task`: point at the real continuum successor (verify symbol exists before citing it; if none, say “use casatasks directly / continuum calibration runner” rather than a dead old path).

- [ ] **Step 4: Commit**

```bash
git commit -m "$(cat <<'EOF'
Scrub stale dsa110_contimg API references from package docs.

EOF
)"
```

---

### Task A3: Metadata and agent docs

**Files:**
- Modify: `pyproject.toml` (`[project.urls]`, obsolete `# Run with: pytest --doctest-modules src/dsa110_contimg/…` comment)
- Modify: `AGENTS.md`, `CLAUDE.md`
- Keep: ruff `"dsa110_contimg".msg = "…"` banned-api rule

- [ ] **Step 1: Fix URLs**

```toml
Homepage = "https://github.com/dsa110/dsa110-continuum"
Documentation = "https://github.com/dsa110/dsa110-continuum"
Repository = "https://github.com/dsa110/dsa110-continuum"
Issues = "https://github.com/dsa110/dsa110-continuum/issues"
```

- [ ] **Step 2: Rewrite agent-doc lead**

Replace “ported from the older `dsa110-contimg` codebase” as the opening identity with continuum-first wording. Keep at most one historical sentence, and keep the import-ban / checker guidance (those mentions stay Class D).

- [ ] **Step 3: Commit**

```bash
git commit -m "$(cat <<'EOF'
Point packaging metadata and agent docs at dsa110-continuum.

EOF
)"
```

---

### Task B1: Remove dead `src/dsa110_contimg` layout probes

**Files:**
- Modify: `dsa110_continuum/calibration/catalogs.py`
- Modify: `dsa110_continuum/simulation/make_synthetic_uvh5.py`
- Modify: `dsa110_continuum/simulation/visibility_models.py`
- Modify: `dsa110_continuum/utils/templates.py`
- Modify: `dsa110_continuum/database/data_registry.py`
- Modify: `dsa110_continuum/evidence/hdf5_calibrator_tile_smoke.py`
- Modify: `dsa110_continuum/visualization/coverage_moc.py` (path comment)

- [ ] **Step 1: Characterize each probe**

For each hit, decide: delete branch, or retarget to `dsa110_continuum/` / `CONTIMG_BASE_DIR` / resolver.

- [ ] **Step 2: Prefer resolver / env over stringly package trees**

```python
# BAD
if (potential_root / "src" / "dsa110_contimg").exists():
    ...

# GOOD
catalog_root = get_env_path("CONTIMG_BASE_DIR", default="/data/dsa110-contimg") / "state/catalogs"
# (default string stays until Track C; probe no longer names the old *package*)
```

- [ ] **Step 3: Templates search path**

Update `utils/templates.py` so candidate dirs are under `dsa110_continuum/…` (and env overrides), not `dsa110_contimg/templates`.

- [ ] **Step 4: Test**

```bash
PYTHONPATH=/data/dsa110-continuum /opt/miniforge/envs/casa6/bin/python -m pytest \
  tests/test_no_stale_contimg_api_refs.py tests/test_vendored_utils.py \
  tests/test_unified_config.py -q
```

- [ ] **Step 5: Commit**

```bash
git commit -m "$(cat <<'EOF'
Drop dead dsa110_contimg filesystem layout probes.

EOF
)"
```

---

### Task C0: Human decision — host root names

**Files:**
- Modify: design note from A0 with the chosen names

- [ ] **Step 1: Propose options to the operator**

Option 1 (recommended): symlink-friendly rename

```text
/data/dsa110-continuum-state  →  real data; /data/dsa110-contimg symlink
/stage/dsa110-continuum       →  real stage; /stage/dsa110-contimg symlink
/dev/shm/dsa110-continuum     →  real tmpfs; /dev/shm/dsa110-contimg symlink
```

Option 2: keep host directory names forever; only cleanse narrative/probes (Tracks A/B/D). Treat path strings as opaque ops constants.

- [ ] **Step 2: Record decision in the design note**

If Option 2: mark Tasks C1–C3 cancelled; update Class C allowlist to permanent “ops path constants”.

---

### Task C1: Dual-read in the path resolver (only if Option 1)

**Files:**
- Modify: `dsa110_continuum/utils/paths/resolver.py`
- Modify: `dsa110_continuum/unified_config.py` (tmpfs default)
- Test: `tests/test_paths_resolver.py` (create if missing)

- [ ] **Step 1: Write failing tests for env precedence**

```python
def test_prefers_dsa110_base_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("DSA110_BASE_DIR", str(tmp_path / "new"))
    monkeypatch.setenv("CONTIMG_BASE_DIR", str(tmp_path / "old"))
    base, src = _resolve_base_dir_with_source()
    assert base == (tmp_path / "new").resolve()
    assert src == "DSA110_BASE_DIR"

def test_falls_back_to_contimg_base_dir(monkeypatch, tmp_path):
    monkeypatch.delenv("DSA110_BASE_DIR", raising=False)
    monkeypatch.setenv("CONTIMG_BASE_DIR", str(tmp_path / "old"))
    base, src = _resolve_base_dir_with_source()
    assert src == "CONTIMG_BASE_DIR"
```

- [ ] **Step 2: Implement precedence**

```text
DSA110_BASE_DIR → CONTIMG_BASE_DIR → default new path if exists → else legacy path
```

Same pattern for staging and tmpfs. Do **not** flip the hardcoded default until H17 symlinks exist.

- [ ] **Step 3: Commit**

```bash
git commit -m "$(cat <<'EOF'
Prefer DSA110_* path env vars with CONTIMG_* fallback.

EOF
)"
```

---

### Task C2: H17 host symlinks (ops, outside git)

- [ ] **Step 1: Create new dirs or rename + symlink (operator)**

```bash
# Example only — execute on H17 with change control
sudo mv /stage/dsa110-contimg /stage/dsa110-continuum
sudo ln -s /stage/dsa110-continuum /stage/dsa110-contimg
# repeat for /data and /dev/shm as decided
```

- [ ] **Step 2: Validate both spellings**

```bash
test -d /stage/dsa110-contimg/ms && test -d /stage/dsa110-continuum/ms
/opt/miniforge/envs/casa6/bin/python scripts/batch_pipeline.py \
  --date 2026-01-25 --start-hour 22 --end-hour 23 --dry-run
```

- [ ] **Step 3: Only then flip code defaults** to the new names, leaving legacy path as secondary fallback for one release.

- [ ] **Step 4: Commit default flip**

```bash
git commit -m "$(cat <<'EOF'
Default stage/state/tmpfs roots to dsa110-continuum host paths.

EOF
)"
```

---

### Task C3: Centralize hardcoded script defaults

**Files:**
- Modify: `scripts/batch_pipeline.py`, `scripts/mosaic_day.py`, `scripts/run_pipeline.py`, `scripts/qa_server.py`, `scripts/monitor_server.py`, `scripts/inventory.py`, `scripts/validate_*.py`, `scripts/canary_history.py`, `scripts/forced_photometry.py`, `scripts/source_finding.py`, etc.
- Prefer importing resolver helpers over duplicating string literals.

- [ ] **Step 1: Introduce a tiny helper used by scripts**

```python
# dsa110_continuum/utils/paths/defaults.py (or export from resolver)
DEFAULT_MS_DIR = ...
DEFAULT_PIPELINE_DB = ...
DEFAULT_STAGE_IMAGE_BASE = ...
```

- [ ] **Step 2: Replace per-script literals**

Keep env overrides (`DSA110_MS_DIR`, `PIPELINE_DB`, …).

- [ ] **Step 3: Dry-run smoke**

```bash
/opt/miniforge/envs/casa6/bin/python scripts/batch_pipeline.py \
  --date 2026-01-25 --start-hour 22 --end-hour 23 --dry-run
```

- [ ] **Step 4: Commit**

```bash
git commit -m "$(cat <<'EOF'
Route script path defaults through the continuum path helper.

EOF
)"
```

---

### Task D1: Archive historical retirement docs

**Files:**
- Move: `docs/rse/specs/plan-contimg-import-retirement.md`
- Move: `docs/rse/specs/implement-contimg-import-retirement.md`
- Move: `docs/rse/specs/validation-contimg-import-retirement.md`
- Move: related handoff notes that are purely historical
- Create: `docs/archive/contimg-retirement/README.md` banner

- [ ] **Step 1: Add banner**

```markdown
# Archived: contimg import retirement

Historical record of the 2026-07 package-import migration.
Do not update paths here to match current defaults; see
`docs/superpowers/plans/2026-07-12-contimg-mention-cleanse.md` for the
follow-on mention/path cleanse.
```

- [ ] **Step 2: Leave active docs alone until C2**, or annotate path defaults as “host stage root” without hardcoding the old name more than once (link to resolver).

- [ ] **Step 3: Commit**

```bash
git commit -m "$(cat <<'EOF'
Archive contimg import-retirement specs under docs/archive.

EOF
)"
```

---

### Task D2: Enforce mention policy in CI

**Files:**
- Modify: `scripts/check_contimg_mentions.py` (full classifier)
- Modify: CI workflow that already runs `check_import_migration.py`
- Modify: allowlist from A0

- [ ] **Step 1: Classifier categories**

```text
FAIL:   recommended import / vendored header / dead src/dsa110_contimg probe
INFO:   ops path default (fails only after Track C cutover flag --strict-paths)
ALLOW:  ban-rail files, docs/archive/contimg-retirement/**
SKIP:   outputs/**
```

- [ ] **Step 2: Wire CI**

```yaml
- run: python scripts/check_import_migration.py --fail-on-any
- run: python scripts/check_contimg_mentions.py --fail
```

- [ ] **Step 3: Local verification**

```bash
/opt/miniforge/envs/casa6/bin/python scripts/check_contimg_mentions.py --fail
/opt/miniforge/envs/casa6/bin/python scripts/check_import_migration.py --fail-on-any
PYTHONPATH=/data/dsa110-continuum /opt/miniforge/envs/casa6/bin/python -m pytest \
  tests/test_import_migration_checker.py tests/test_no_compat_layer.py \
  tests/test_no_stale_contimg_api_refs.py -q
```

- [ ] **Step 4: Commit**

```bash
git commit -m "$(cat <<'EOF'
CI-enforce contimg mention policy beyond import AST checks.

EOF
)"
```

---

## Explicit non-goals

- Rewriting `outputs/**` historical run products, JSON, or investigation notes.
- Deleting ban-rail tests or the ruff `banned-api` rule.
- Renaming the sibling checkout `/data/dsa110-contimg` (old repo) — that tree may remain installed on H17; continuum must not import it.
- Bulk-fixing unrelated ruff debt while scrubbing strings.

## Suggested PR sequence

1. **PR1 — Tracks A+B+D1** (no path default changes): headers, docstrings, probes, metadata, archive moves, mention classifier in warn mode.
2. **PR2 — Track C1** (dual-read env only): still defaults to legacy host paths.
3. **Ops change** — host symlinks (Task C2).
4. **PR3 — Tracks C2 defaults + C3 + D2 `--strict-paths`**: flip defaults, centralize scripts, fail CI on leftover path literals if desired.

## Rollback

- PR1/PR2: revert commits; ban rails unchanged.
- Host symlink: keep dual names until all consumers flipped.
- PR3: revert defaults to legacy paths; symlinks can remain harmlessly.

---

## Open question for the operator

Before Track C work starts: **Option 1 (rename host roots + symlink) or Option 2 (keep host directory names forever and only scrub narrative/probes)?**

Everything in Tracks A, B, and D1 can proceed without that answer.
