"""Dependencies shared by the endpoints."""

from typing import Annotated

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.auth import InvalidToken, decode_token, secret_is_usable
from app.config import settings

_bearer = HTTPBearer(auto_error=False)


def unauthorized() -> HTTPException:
    return HTTPException(401, "invalid credentials", headers={"WWW-Authenticate": "Bearer"})


async def current_owner(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> None:
    """Lets the request through only with a valid owner token. There is one owner, so it returns nothing."""
    if credentials is None or not secret_is_usable(settings.jwt_secret):
        raise unauthorized()
    try:
        decode_token(credentials.credentials, settings.jwt_secret)
    except InvalidToken:
        raise unauthorized() from None
