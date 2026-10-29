from app.config import Settings


def test_defaults_point_at_the_local_database(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)

    settings = Settings(_env_file=None)

    assert settings.database_url.endswith("localhost:5432/agent")
    assert settings.embedding_dimensions == 1536


def test_the_environment_overrides_a_default(monkeypatch):
    monkeypatch.setenv("AGENT_MODEL", "some-other-model")

    assert Settings(_env_file=None).agent_model == "some-other-model"


def test_an_unknown_time_zone_stops_the_settings_from_loading(monkeypatch):
    import pytest
    from pydantic import ValidationError

    monkeypatch.setenv("OWNER_TIMEZONE", "Nope/Nowhere")

    with pytest.raises(ValidationError, match="owner_timezone"):
        Settings(_env_file=None)


def test_recall_defaults(monkeypatch):
    for name in ("MEMORY_SEARCH_MODE", "RECALL_LIMIT", "RECALL_HISTORY_LIMIT"):
        monkeypatch.delenv(name, raising=False)

    settings = Settings(_env_file=None)

    assert (settings.memory_search_mode, settings.recall_limit, settings.recall_history_limit) == (
        "vector",
        5,
        3,
    )
