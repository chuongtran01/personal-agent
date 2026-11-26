"""Signing the token each request to an MCP server carries (docs/PHASE6.md).

The servers check these in ``mcp_servers/auth.py``; a test makes one and verifies it with the other,
so the two cannot drift apart. The agent signs with a secret it shares with the servers and holds no
provider credential: a token proves "this came from the agent, for this tool, just now".
"""

import uuid
from datetime import UTC, datetime, timedelta

import jwt

ALGORITHM = "HS256"
ISSUER = "personal-agent"
SUBJECT = "owner"
LIFETIME_SECONDS = 60
# The scope of a request that is not a tool call: finding out what a server offers
SESSION_SCOPE = "session"
MIN_SECRET_LENGTH = 32


def mint(secret: str, *, audience: str, scope: str, now: datetime | None = None) -> str:
    """A token for one request to ``audience``, for ``scope`` (a tool name or ``session``)."""
    if len(secret) < MIN_SECRET_LENGTH:
        raise ValueError(f"the signing secret must be at least {MIN_SECRET_LENGTH} characters")
    issued = now or datetime.now(UTC)
    claims = {
        "iss": ISSUER,
        "sub": SUBJECT,
        "aud": audience,
        "scope": scope,
        "iat": issued,
        "exp": issued + timedelta(seconds=LIFETIME_SECONDS),
        "jti": uuid.uuid4().hex,
    }
    return jwt.encode(claims, secret, algorithm=ALGORITHM)
