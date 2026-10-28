from app.config import Settings


def test_defaults_point_at_the_local_database(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)

    settings = Settings(_env_file=None)

    assert settings.database_url.endswith("localhost:5432/agent")
    assert settings.embedding_dimensions == 1536


def test_the_environment_overrides_a_default(monkeypatch):
    monkeypatch.setenv("AGENT_MODEL", "some-other-model")

    assert Settings(_env_file=None).agent_model == "some-other-model"
