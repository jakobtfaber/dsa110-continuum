from pathlib import Path


def test_mosaic_package_has_no_misleading_legacy_module_cli() -> None:
    entrypoint = Path(__file__).resolve().parents[1] / "dsa110_continuum" / "mosaic" / "__main__.py"
    if not entrypoint.exists():
        return

    source = entrypoint.read_text(encoding="utf-8")
    assert "dsa110_contimg." not in source
