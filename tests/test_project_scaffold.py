from aegis.config import AppMode, Settings


def test_project_starts_in_research_only_mode():
    settings = Settings.from_env()
    assert settings.mode == AppMode.RESEARCH_ONLY
