import re
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.api.deps import current_owner
from app.auth import (
    InvalidToken,
    create_token,
    decode_token,
    hash_password,
    main,
    secret_is_usable,
    verify_password,
)
from app.config import settings
from app.main import app

SECRET = "s" * 40
PASSWORD = "correct horse battery staple"


@pytest.fixture(scope="module")
def password_hash():
    return hash_password(PASSWORD)


@pytest.fixture
def configured(monkeypatch, password_hash):
    monkeypatch.setattr(settings, "owner_password_hash", password_hash)
    monkeypatch.setattr(settings, "jwt_secret", SECRET)
    monkeypatch.setattr(settings, "token_ttl_minutes", 60)


@pytest.fixture
def client():
    return TestClient(app)


def login(client, password=PASSWORD):
    return client.post("/auth/login", json={"password": password})


def test_a_password_matches_only_its_own_hash(password_hash):
    assert verify_password(PASSWORD, password_hash)
    assert not verify_password("another one", password_hash)
    assert not verify_password("", password_hash)


def test_an_unset_or_broken_hash_never_matches():
    assert not verify_password(PASSWORD, "")
    assert not verify_password("not the owner's password", "")
    assert not verify_password(PASSWORD, "not a hash")


def test_the_hash_is_salted(password_hash):
    assert hash_password(PASSWORD) != password_hash
    assert password_hash.startswith("$argon2id$")


def test_a_token_round_trips():
    decode_token(create_token(SECRET, timedelta(minutes=5)), SECRET)


def test_an_expired_token_is_refused():
    past = datetime.now(UTC) - timedelta(hours=2)

    with pytest.raises(InvalidToken):
        decode_token(create_token(SECRET, timedelta(hours=1), now=past), SECRET)


def test_a_token_signed_with_another_secret_is_refused():
    with pytest.raises(InvalidToken):
        decode_token(create_token("t" * 40, timedelta(minutes=5)), SECRET)


def test_a_token_for_someone_else_is_refused():
    claims = {"sub": "visitor", "exp": datetime.now(UTC) + timedelta(minutes=5)}

    with pytest.raises(InvalidToken, match="not an owner token"):
        decode_token(jwt.encode(claims, SECRET, algorithm="HS256"), SECRET)


def test_a_token_without_an_expiry_is_refused():
    with pytest.raises(InvalidToken):
        decode_token(jwt.encode({"sub": "owner"}, SECRET, algorithm="HS256"), SECRET)


def test_an_unsigned_token_is_refused():
    claims = {"sub": "owner", "exp": datetime.now(UTC) + timedelta(minutes=5)}
    token = jwt.encode(claims, key=None, algorithm="none")

    with pytest.raises(InvalidToken):
        decode_token(token, SECRET)


def test_garbage_is_not_a_token():
    with pytest.raises(InvalidToken):
        decode_token("nonsense", SECRET)


def test_a_short_secret_is_unusable():
    assert secret_is_usable(SECRET)
    assert not secret_is_usable("short")
    assert not secret_is_usable("")


def test_login_returns_a_working_token(configured, client):
    response = login(client)

    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["expires_in"] == 3600
    decode_token(body["access_token"], SECRET)


def test_a_wrong_password_and_an_unset_hash_give_the_same_answer(configured, client, monkeypatch):
    wrong = login(client, "wrong")
    monkeypatch.setattr(settings, "owner_password_hash", "")
    unset = login(client, PASSWORD)

    assert wrong.status_code == unset.status_code == 401
    assert wrong.json() == unset.json() == {"detail": "invalid credentials"}


def test_login_is_closed_without_a_usable_secret(configured, client, monkeypatch):
    monkeypatch.setattr(settings, "jwt_secret", "short")

    assert login(client).status_code == 503


def test_login_rejects_extra_fields_and_huge_passwords(configured, client):
    assert client.post("/auth/login", json={"password": "x", "role": "admin"}).status_code == 422
    assert login(client, "x" * 1001).status_code == 422


def protected_app():
    probe = FastAPI()

    @probe.get("/private", dependencies=[Depends(current_owner)])
    async def private():
        return {"ok": True}

    return TestClient(probe)


def test_current_owner_lets_a_valid_token_through(configured):
    token = create_token(SECRET, timedelta(minutes=5))

    response = protected_app().get("/private", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": "Bearer nonsense"},
        {"Authorization": "Basic b3duZXI6cGFzcw=="},
        {"Authorization": "Bearer "},
    ],
)
def test_current_owner_refuses_a_missing_or_bad_token(configured, headers):
    response = protected_app().get("/private", headers=headers)

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_current_owner_refuses_everything_when_the_secret_is_unusable(configured, monkeypatch):
    token = create_token(SECRET, timedelta(minutes=5))
    monkeypatch.setattr(settings, "jwt_secret", "")

    response = protected_app().get("/private", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 401


OPEN_ROUTES = {("POST", "/auth/login"), ("GET", "/health")}


def test_every_route_but_login_and_health_needs_a_token(configured, client):
    """Guards the endpoints added in later steps: a new route that forgets the dependency fails here.

    Routes come from the OpenAPI document, because ``app.routes`` hides included routers.
    """
    served = {
        (method.upper(), path)
        for path, operations in app.openapi()["paths"].items()
        for method in operations
    }
    assert OPEN_ROUTES <= served
    for method, path in sorted(served - OPEN_ROUTES):
        url = re.sub(r"\{[^}]+\}", "00000000-0000-0000-0000-000000000000", path)
        response = client.request(method, url, json={})
        assert response.status_code == 401, f"{method} {path} is open"


def test_the_hash_command_prints_a_quoted_hash(monkeypatch, capsys):
    monkeypatch.setattr("getpass.getpass", lambda _prompt: PASSWORD)

    assert main(["hash"]) == 0
    printed = capsys.readouterr().out.strip()

    assert printed.startswith("OWNER_PASSWORD_HASH='$argon2id$") and printed.endswith("'")
    assert verify_password(PASSWORD, printed.split("=", 1)[1].strip("'"))


def test_the_hash_command_refuses_mismatched_or_empty_passwords(monkeypatch, capsys):
    answers = iter(["one", "two"])
    monkeypatch.setattr("getpass.getpass", lambda _prompt: next(answers))
    assert main(["hash"]) == 1

    monkeypatch.setattr("getpass.getpass", lambda _prompt: "")
    assert main(["hash"]) == 1
    capsys.readouterr()


def test_the_secret_command_prints_a_usable_secret(capsys):
    assert main(["secret"]) == 0
    printed = capsys.readouterr().out.strip()

    assert secret_is_usable(printed.removeprefix("JWT_SECRET="))


def test_an_unknown_command_is_a_usage_error(capsys):
    assert main(["nope"]) == 2
    assert "usage" in capsys.readouterr().err
