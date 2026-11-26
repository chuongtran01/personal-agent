"""Checking the token every request to an MCP server carries (docs/PHASE6.md).

The agent signs a token for each request: who (``sub``), for which server (``aud``), for what
(``scope``, a tool name, or ``session`` for everything else a connection says), valid for a minute
(``exp``) and unique (``jti``). A server refuses a token that is badly signed, expired, issued in
the future, for another server or another owner, lives longer than it should, or that it has seen
before. The token says nothing about the provider: the server holds those credentials itself.

``TokenAuthMiddleware`` wraps a server's ASGI app and answers 401 without detail; the reason goes to
the log as a short code, never the token. A tool then asks ``require_scope`` for its own name, so a
token made for one tool cannot run another.
"""

import contextvars
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt

log = logging.getLogger(__name__)

ALGORITHM = "HS256"
ISSUER = "personal-agent"
SUBJECT = "owner"
SESSION_SCOPE = "session"
MIN_SECRET_LENGTH = 32
LIFETIME_SECONDS = 60
# A longer-lived token than the agent would ever sign is refused, so one that leaks cannot be kept
MAX_LIFETIME_SECONDS = 120
# How far a clock may differ between the agent and a server
LEEWAY_SECONDS = 5
REPLAY_CAPACITY = 100_000


class InvalidToken(Exception):
    """The token was refused. ``code`` is a short reason for the log, not for the caller."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class ScopeError(Exception):
    """The token is valid, but not for what was asked."""


@dataclass(frozen=True)
class Claims:
    subject: str
    audience: str
    scope: str
    jti: str
    issued_at: datetime
    expires_at: datetime


def utcnow() -> datetime:
    return datetime.now(UTC)


class ReplayGuard:
    """Remembers the ``jti`` of every accepted token until it would have expired anyway, so a token
    seen once is refused the second time. One process only: servers run as one worker."""

    def __init__(self, capacity: int = REPLAY_CAPACITY):
        self._capacity = capacity
        self._seen: dict[str, datetime] = {}

    def accept(self, jti: str, expires_at: datetime, now: datetime) -> bool:
        """False when ``jti`` was seen (or there is no room to remember it), else remember it."""
        self._seen = {k: v for k, v in self._seen.items() if v > now}
        if jti in self._seen or len(self._seen) >= self._capacity:
            return False
        self._seen[jti] = expires_at + timedelta(seconds=LEEWAY_SECONDS)
        return True


class TokenVerifier:
    def __init__(
        self,
        secret: str,
        audience: str,
        *,
        clock: Callable[[], datetime] = utcnow,
        guard: ReplayGuard | None = None,
    ):
        if len(secret) < MIN_SECRET_LENGTH:
            raise ValueError(f"the signing secret must be at least {MIN_SECRET_LENGTH} characters")
        self._secret = secret
        self._audience = audience
        self._clock = clock
        self._guard = guard or ReplayGuard()

    def verify(self, token: str) -> Claims:
        """The claims of a token that is valid now and has not been used; raises InvalidToken."""
        try:
            raw = jwt.decode(
                token,
                self._secret,
                algorithms=[ALGORITHM],
                options={
                    "require": ["exp", "iat", "sub", "aud", "iss", "scope", "jti"],
                    # Checked below, against the verifier's clock
                    "verify_exp": False,
                    "verify_iat": False,
                    "verify_nbf": False,
                    "verify_aud": False,
                    "verify_iss": False,
                },
            )
        except jwt.InvalidSignatureError as error:
            raise InvalidToken("signature") from error
        except jwt.PyJWTError as error:
            raise InvalidToken("malformed") from error
        try:
            claims = Claims(
                subject=str(raw["sub"]),
                audience=_one(raw["aud"]),
                scope=str(raw["scope"]),
                jti=str(raw["jti"]),
                issued_at=datetime.fromtimestamp(int(raw["iat"]), UTC),
                expires_at=datetime.fromtimestamp(int(raw["exp"]), UTC),
            )
        except (TypeError, ValueError, OverflowError) as error:
            raise InvalidToken("malformed") from error
        now = self._clock()
        if raw["iss"] != ISSUER:
            raise InvalidToken("issuer")
        if claims.audience != self._audience:
            raise InvalidToken("audience")
        if claims.subject != SUBJECT:
            raise InvalidToken("subject")
        if claims.issued_at > now + timedelta(seconds=LEEWAY_SECONDS):
            raise InvalidToken("not_yet_valid")
        if claims.expires_at <= now:
            raise InvalidToken("expired")
        if (claims.expires_at - claims.issued_at).total_seconds() > MAX_LIFETIME_SECONDS:
            raise InvalidToken("lifetime")
        if not claims.jti or not claims.scope:
            raise InvalidToken("malformed")
        if not self._guard.accept(claims.jti, claims.expires_at, now):
            raise InvalidToken("replayed")
        return claims


def _one(audience: object) -> str:
    """``aud`` may be a string or a one-item list; anything else is not ours."""
    if isinstance(audience, str):
        return audience
    if isinstance(audience, list) and len(audience) == 1 and isinstance(audience[0], str):
        return audience[0]
    raise ValueError("audience")


_claims: contextvars.ContextVar[Claims | None] = contextvars.ContextVar("claims", default=None)


def current_claims() -> Claims | None:
    """The claims of the request being handled, or None outside a request."""
    return _claims.get()


def require_scope(tool: str) -> Claims:
    """The claims, provided the token was made for exactly this tool."""
    claims = _claims.get()
    if claims is None or claims.scope != tool:
        raise ScopeError(tool)
    return claims


class TokenAuthMiddleware:
    """An ASGI wrapper: no request reaches the server without a valid token."""

    def __init__(self, app, verifier: TokenVerifier):
        self._app = app
        self._verifier = verifier

    def lifespan(self):
        """The wrapped Starlette app's lifespan, for a caller that does not run one (a test over an
        ASGI transport). Under uvicorn the lifespan scope is passed through and this is not needed."""
        return self._app.router.lifespan_context(self._app)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":  # lifespan, and whatever else is not a request
            await self._app(scope, receive, send)
            return
        header = dict(scope["headers"]).get(b"authorization", b"").decode("latin-1")
        kind, _, token = header.partition(" ")
        try:
            if kind.lower() != "bearer" or not token:
                raise InvalidToken("missing")
            claims = self._verifier.verify(token.strip())
        except InvalidToken as refused:
            log.warning("request refused: %s", refused.code)
            await _refuse(send)
            return
        marker = _claims.set(claims)
        try:
            await self._app(scope, receive, send)
        finally:
            _claims.reset(marker)


async def _refuse(send) -> None:
    body = json.dumps({"error": "invalid_token"}).encode()
    headers = [
        (b"content-type", b"application/json"),
        (b"content-length", str(len(body)).encode()),
        (b"www-authenticate", b"Bearer"),
    ]
    await send({"type": "http.response.start", "status": 401, "headers": headers})
    await send({"type": "http.response.body", "body": body})
