"""Tests for scripts/check_import_migration.py (AST-based stale-import gate)."""
import subprocess
import sys
import textwrap
from pathlib import Path

CHECKER = Path(__file__).resolve().parents[1] / "scripts" / "check_import_migration.py"


def _run(tmp_path, source, *flags):
    pkg = tmp_path / "dsa110_continuum"
    pkg.mkdir()
    (pkg / "mod.py").write_text(textwrap.dedent(source))
    return subprocess.run(
        [sys.executable, str(CHECKER), "--root", str(pkg), *flags],
        capture_output=True,
        text=True,
    )


def test_docstring_mention_is_not_stale(tmp_path):
    # bare (non-doctest) docstring line: the line-prefix checker false-positives
    # on this; only an AST implementation passes it
    r = _run(
        tmp_path,
        '''
        """Migration note:
        from dsa110_contimg.core.qa import x
        """
        VALUE = 1
        ''',
        "--fail-on-any",
    )
    assert r.returncode == 0, r.stdout + r.stderr


def test_real_import_fails_gate(tmp_path):
    r = _run(
        tmp_path,
        "from dsa110_contimg.common.utils import get_env_path\n",
        "--fail-on-any",
    )
    assert r.returncode == 1, r.stdout + r.stderr


def test_function_scope_import_is_counted(tmp_path):
    r = _run(
        tmp_path,
        textwrap.dedent(
            """
            def f():
                import dsa110_contimg.common.utils
            """
        ),
        "--fail-on-any",
    )
    assert r.returncode == 1, r.stdout + r.stderr


def test_clean_module_passes(tmp_path):
    r = _run(tmp_path, "from dsa110_continuum.config import get_env_path\n", "--fail-on-any")
    assert r.returncode == 0, r.stdout + r.stderr
