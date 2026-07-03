"""The legacy interop machinery is gone (Phase 7 of contimg-import-retirement).

``dsa110_continuum/_compat.py`` (the cloud-VM stub layer for the old
``dsa110_contimg`` package) is deleted, and no source file references it.
The reference scan uses a word-boundary regex rather than the plan's plain
substring so that legitimate identifiers like ``_validate_strip_compatibility``
(which contain ``_compat`` as a substring) do not false-positive.

Plan: docs/rse/specs/plan-contimg-import-retirement.md (Phase 7).
"""

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE = REPO_ROOT / "dsa110_continuum"

# _compat as a standalone token (module name), not inside e.g. "_compatibility"
_COMPAT_TOKEN = re.compile(r"\b_compat\b")


def test_compat_module_deleted():
    assert not (PACKAGE / "_compat.py").exists()


def test_no_source_references_compat():
    hits = [
        str(p.relative_to(REPO_ROOT))
        for p in PACKAGE.rglob("*.py")
        if _COMPAT_TOKEN.search(p.read_text(encoding="utf-8"))
    ]
    assert hits == [], f"source still references _compat: {hits}"


def test_lazy_init_has_no_legacy_guards():
    text = (PACKAGE / "_lazy_init.py").read_text(encoding="utf-8")
    assert "dsa110_contimg" not in text
    assert "except ImportError" not in text
