"""Dependencies shared by the endpoints."""

import logging
from typing import Annotated

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.agent.build import build_agent
from app.agent.loop import AgentLoop
from app.auth import InvalidToken, decode_token, secret_is_usable
from app.config import settings

log = logging.getLogger(__name__)

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


def get_agent(request: Request) -> AgentLoop:
    """The one agent of this process, built when first needed so the server starts without a key.

    A single instance matters: it holds the lock that gives each chat one turn at a time."""
    state = request.app.state
    if state.agent is None:
        try:
            state.agent = build_agent()
        except RuntimeError as error:
            log.error("the agent cannot start: %s", error)
            raise HTTPException(503, "the agent is not configured") from None
    return state.agent
