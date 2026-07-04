"""The vendored settings singleton must work without H17 paths or env vars."""


def test_settings_singleton_paths():
    from dsa110_continuum.unified_config import get_settings, settings

    # settings is a lazy proxy over the get_settings() singleton
    assert get_settings() is get_settings()
    assert settings.paths.pipeline_db == get_settings().paths.pipeline_db
    # attribute used at module scope by the four Category-A consumers
    assert settings.paths.pipeline_db


def test_get_config_alias():
    from dsa110_continuum.unified_config import get_config

    cfg = get_config()
    assert cfg is not None
