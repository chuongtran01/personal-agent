"""Owner login: an argon2 password check and signed tokens.

There is one owner and no users table. The password is kept only as a hash in ``.env``; a correct
password buys a token that says "the owner" and expires. Nothing here touches the database or FastAPI.

    python -m app.auth hash      # prompts for a password and prints OWNER_PASSWORD_HASH
    python -m app.auth secret    # prints a random JWT_SECRET
"""

import getpass
import secrets
import sys
from datetime import UTC, datetime, timedelta

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

MIN_SECRET_LENGTH = 32
ALGORITHM = "HS256"
SUBJECT = "owner"

_hasher = PasswordHasher()
# Checked when no hash is configured, so a wrong password costs the same either way
_DUMMY_HASH = _hasher.hash("not the owner's password")


class InvalidToken(Exception):
    """The token is missing, malformed, expired or not signed with our secret."""


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    """True only for the password the hash was made from. An unset or broken hash is False."""
    try:
        _hasher.verify(password_hash or _DUMMY_HASH, password)
    except (VerificationError, InvalidHashError):
        return False
    return bool(password_hash)


def secret_is_usable(secret: str) -> bool:
    return len(secret) >= MIN_SECRET_LENGTH


def create_token(secret: str, ttl: timedelta, now: datetime | None = None) -> str:
    now = now or datetime.now(UTC)
    claims = {"sub": SUBJECT, "iat": now, "exp": now + ttl}
    return jwt.encode(claims, secret, algorithm=ALGORITHM)


def decode_token(token: str, secret: str) -> None:
    """Returns nothing; raises InvalidToken unless the token is the owner's and still valid."""
    try:
        claims = jwt.decode(
            token,
            secret,
            algorithms=[ALGORITHM],
            options={"require": ["exp", "sub"]},
        )
    except jwt.PyJWTError as error:
        raise InvalidToken(str(error)) from error
    if claims["sub"] != SUBJECT:
        raise InvalidToken("not an owner token")


def main(argv: list[str]) -> int:
    command = argv[0] if argv else ""
    if command == "hash":
        password = getpass.getpass("Owner password: ")
        if password != getpass.getpass("Again: "):
            print("The two passwords differ.", file=sys.stderr)
            return 1
        if not password:
            print("The password is empty.", file=sys.stderr)
            return 1
        print(f"OWNER_PASSWORD_HASH='{hash_password(password)}'")
        return 0
    if command == "secret":
        print(f"JWT_SECRET={secrets.token_urlsafe(48)}")
        return 0
    print("usage: python -m app.auth hash|secret", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
