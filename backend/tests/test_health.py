from fastapi.testclient import TestClient

from app.config import Settings
from app.db import get_session
from app.main import app, create_app


class FakeSession:
    async def execute(self, _statement):
        return None


async def fake_session():
    yield FakeSession()


def test_health_ok_without_database():
    app.dependency_overrides[get_session] = fake_session
    try:
        response = TestClient(app).get("/health")
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


DOC_PATHS = ["/docs", "/redoc", "/openapi.json"]


def test_the_api_docs_are_closed_unless_the_app_runs_in_dev():
    closed = TestClient(create_app(docs=False))

    assert [closed.get(path).status_code for path in DOC_PATHS] == [404, 404, 404]


def test_the_api_docs_are_served_in_dev():
    opened = TestClient(create_app(docs=True))

    assert [opened.get(path).status_code for path in DOC_PATHS] == [200, 200, 200]


def test_the_docs_are_closed_by_default(monkeypatch):
    monkeypatch.delenv("APP_ENV", raising=False)

    assert Settings(_env_file=None).app_env == "production"
