"""Fail if package docs still recommend importing dsa110_contimg."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "dsa110_continuum"
FORBIDDEN = re.compile(
    r"(?:from|import)\s+dsa110_contimg\b|"
    r":(?:class|mod|func|meth):`~?dsa110_contimg\.|"
    r"python -m dsa110_contimg\b"
)


def test_no_recommended_contimg_imports_in_package_docs():
    bad = []
    for path in ROOT.rglob("*.py"):
        text = path.read_text(encoding="utf-8", errors="replace")
        for i, line in enumerate(text.splitlines(), 1):
            if FORBIDDEN.search(line):
                bad.append(f"{path.relative_to(ROOT.parent)}:{i}:{line.strip()}")
    assert not bad, "stale contimg API refs:\n" + "\n".join(bad)
