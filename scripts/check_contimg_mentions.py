#!/usr/bin/env python3
"""Classify residual dsa110-contimg / dsa110_contimg mentions.

Fail classes (with --fail):
  - vendored provenance headers
  - recommended API imports of dsa110_contimg in package sources
  - dead src/dsa110_contimg layout probes

Ops path defaults are reported as INFO unless --strict-paths.

See docs/superpowers/specs/2026-07-12-contimg-mention-cleanse-design.md.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

ALLOWLIST_EXACT = {
    "scripts/check_import_migration.py",
    "scripts/check_contimg_mentions.py",
    "tests/test_import_migration_checker.py",
    "tests/test_no_compat_layer.py",
    "tests/test_no_latent_nameerror_imports.py",
    "tests/test_batch_e2_hygiene.py",
    "tests/test_init_reexports_new_namespace.py",
    "tests/test_imaging_worker_no_fast_imaging.py",
    "tests/test_workflow_registry.py",
    "tests/test_vendored_database.py",
    "tests/test_simulation_control.py",
    "tests/test_no_stale_contimg_api_refs.py",
    "tests/test_dev_tools.py",
    "pyproject.toml",
    "docs/superpowers/plans/2026-07-12-contimg-mention-cleanse.md",
    "docs/superpowers/specs/2026-07-12-contimg-mention-cleanse-design.md",
    "AGENTS.md",
    "CLAUDE.md",
}

ALLOWLIST_PREFIXES = (
    "docs/archive/contimg-retirement/",
    "outputs/",
)

SCAN_SUFFIXES = {".py", ".md", ".toml", ".yml", ".yaml", ".sh", ".txt", ".rst"}

VENDORED_RE = re.compile(r"^#\s*Vendored from dsa110-contimg", re.M)
API_REF_RE = re.compile(
    r"(?:from|import)\s+dsa110_contimg\b|"
    r":(?:class|mod|func|meth):`~?dsa110_contimg\.|"
    r"python -m dsa110_contimg\b"
)
LAYOUT_PROBE_RE = re.compile(
    r"(?:src[/\\]dsa110_contimg|backend[/\\]src[/\\]dsa110_contimg|"
    r'["\']/dsa110_contimg["\']|'
    r'/\s*"dsa110_contimg"|'
    r'"dsa110_contimg"\s*\)|'
    r"/\s*\"dsa110_contimg\")"
)
# Narrower layout probes used for FAIL classification in .py only
LAYOUT_PROBE_PY_RE = re.compile(
    r'(?:["\']src["\'].*dsa110_contimg|dsa110_contimg["\']|'
    r"backend.*?src.*?dsa110_contimg|"
    r'/\s*"dsa110_contimg"|'
    r'\(.*?/\s*"dsa110_contimg"\s*\)\.exists|'
    r'"dsa110_contimg"\)\.exists|'
    r"/\s*\"dsa110_contimg\"\)\.exists|"
    r'Path\([^)]*dsa110_contimg|/ "dsa110_contimg"|/ \'dsa110_contimg\')'
)
PATH_DEFAULT_RE = re.compile(
    r"/(?:data|stage)|/dev/shm/dsa110-contimg|/data/dsa110-contimg|/stage/dsa110-contimg"
)
ANY_MENTION_RE = re.compile(r"dsa110[_-]contimg")


def _rel(path: Path) -> str:
    return str(path.relative_to(REPO_ROOT)).replace("\\", "/")


def _is_allowlisted(rel: str) -> bool:
    if rel in ALLOWLIST_EXACT:
        return True
    return any(rel.startswith(p) for p in ALLOWLIST_PREFIXES)


def _iter_files() -> list[Path]:
    files: list[Path] = []
    for path in REPO_ROOT.rglob("*"):
        if not path.is_file():
            continue
        rel = _rel(path)
        if rel.startswith(".git/") or "/.git/" in f"/{rel}/":
            continue
        if any(part.startswith(".") and part not in {".github"} for part in path.parts[len(REPO_ROOT.parts) : -1]):
            # skip hidden dirs except .github
            if any(
                part.startswith(".") and part != ".github"
                for part in Path(rel).parts[:-1]
            ):
                continue
        if path.suffix not in SCAN_SUFFIXES and path.name not in {"AGENTS.md", "CLAUDE.md"}:
            continue
        if _is_allowlisted(rel):
            continue
        if rel.startswith("outputs/"):
            continue
        files.append(path)
    return sorted(files)


def classify_file(path: Path) -> list[tuple[str, int, str]]:
    """Return list of (severity, lineno, message)."""
    rel = _rel(path)
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    hits: list[tuple[str, int, str]] = []
    lines = text.splitlines()

    if path.suffix == ".py":
        if VENDORED_RE.search(text):
            for i, line in enumerate(lines, 1):
                if "Vendored from dsa110-contimg" in line:
                    hits.append(("FAIL", i, f"vendored header: {line.strip()[:100]}"))
        for i, line in enumerate(lines, 1):
            if API_REF_RE.search(line):
                hits.append(("FAIL", i, f"stale API ref: {line.strip()[:120]}"))
            elif re.search(
                r'(?:/\s*["\']dsa110_contimg["\']|["\']src["\'].*dsa110_contimg|'
                r'backend.*/src.*/dsa110_contimg|/\s*"dsa110_contimg"\)\.exists)',
                line,
            ) or (
                "dsa110_contimg" in line
                and (".exists()" in line or "/ \"dsa110_contimg\"" in line or "/ 'dsa110_contimg'" in line)
            ):
                hits.append(("FAIL", i, f"layout probe: {line.strip()[:120]}"))
            elif PATH_DEFAULT_RE.search(line) and "dsa110-contimg" in line:
                hits.append(("INFO", i, f"ops path default: {line.strip()[:120]}"))
            elif ANY_MENTION_RE.search(line):
                hits.append(("INFO", i, f"mention: {line.strip()[:120]}"))
    else:
        for i, line in enumerate(lines, 1):
            if not ANY_MENTION_RE.search(line):
                continue
            if PATH_DEFAULT_RE.search(line):
                hits.append(("INFO", i, f"ops path default: {line.strip()[:120]}"))
            else:
                hits.append(("INFO", i, f"mention: {line.strip()[:120]}"))
    return hits


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fail", action="store_true", help="Exit 1 on FAIL-class hits")
    parser.add_argument(
        "--strict-paths",
        action="store_true",
        help="Treat ops path defaults as FAIL (post host-path cutover)",
    )
    args = parser.parse_args(argv)

    fails = 0
    infos = 0
    for path in _iter_files():
        for severity, lineno, msg in classify_file(path):
            if severity == "INFO" and args.strict_paths and "ops path default" in msg:
                severity = "FAIL"
            if severity == "FAIL":
                fails += 1
                print(f"FAIL {_rel(path)}:{lineno}: {msg}")
            else:
                infos += 1
                # Keep INFO quiet unless verbose-ish; print counts at end
                pass

    print(
        f"\ncheck_contimg_mentions: {fails} FAIL, {infos} INFO "
        f"(allowlisted files skipped)"
    )
    if args.fail and fails:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
