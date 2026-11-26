from datetime import UTC, datetime, timedelta

import jwt
import pytest

from app.mcp_client import tokens
from mcp_servers import auth
from mcp_servers.auth import (
    Claims,
    InvalidToken,
    ReplayGuard,
    ScopeError,
    TokenVerifier,
    current_claims,
    require_scope,
)

SECRET = "k" * 40
NOW = datetime(2026, 10, 28, 12, 0, tzinfo=UTC)


def verifier(clock=lambda: NOW, audience="mcp-email", **options):
    return TokenVerifier(SECRET, audience, clock=clock, **options)


def mint(scope="email_search", audience="mcp-email", now=NOW, secret=SECRET):
    return tokens.mint(secret, audience=audience, scope=scope, now=now)


def forge(secret=SECRET, **change):
    """A token made by hand, to break one rule at a time."""
    claims = {
        "iss": "personal-agent",
        "sub": "owner",
        "aud": "mcp-email",
        "scope": "email_search",
        "iat": NOW,
        "exp": NOW + timedelta(seconds=60),
        "jti": "a-token",
    }
    claims = {k: v for k, v in {**claims, **change}.items() if v is not None}
    return jwt.encode(claims, secret, algorithm="HS256")


def refused(token, code, **options):
    with pytest.raises(InvalidToken) as caught:
        verifier(**options).verify(token)
    assert caught.value.code == code


# A good token ------------------------------------------------------------------------------


def test_a_token_the_agent_signed_is_accepted_with_its_claims():
    claims = verifier().verify(mint("email_read"))

    assert claims.subject == "owner" and claims.audience == "mcp-email"
    assert claims.scope == "email_read" and claims.jti
    assert claims.issued_at == NOW and claims.expires_at == NOW + timedelta(seconds=60)


def test_the_two_sides_agree_on_the_constants_they_share():
    assert tokens.ALGORITHM == auth.ALGORITHM and tokens.ISSUER == auth.ISSUER
    assert tokens.SUBJECT == auth.SUBJECT and tokens.SESSION_SCOPE == auth.SESSION_SCOPE
    assert tokens.LIFETIME_SECONDS <= auth.MAX_LIFETIME_SECONDS
    assert tokens.MIN_SECRET_LENGTH == auth.MIN_SECRET_LENGTH


def test_every_token_is_unique_even_for_the_same_call():
    assert mint() != mint()
    assert verifier().verify(mint()).jti != verifier().verify(mint()).jti


def test_a_token_is_valid_until_its_last_second_and_not_after():
    token = mint()

    verifier(clock=lambda: NOW + timedelta(seconds=59)).verify(token)
    refused(token, "expired", clock=lambda: NOW + timedelta(seconds=60))


# What is refused ---------------------------------------------------------------------------


def test_a_token_signed_with_another_secret_is_refused():
    refused(mint(secret="z" * 40), "signature")


def test_text_that_is_not_a_token_is_refused():
    for junk in ("", "abc", "a.b.c", "Bearer x"):
        refused(junk, "malformed")


def test_a_token_that_says_it_needs_no_signature_is_refused():
    unsigned = jwt.encode({"sub": "owner", "aud": "mcp-email"}, None, algorithm="none")

    refused(unsigned, "malformed")


@pytest.mark.filterwarnings("ignore:The HMAC key")
def test_a_token_signed_with_another_algorithm_is_refused():
    other = jwt.encode(
        {
            "iss": "personal-agent",
            "sub": "owner",
            "aud": "mcp-email",
            "scope": "x",
            "jti": "j",
            "iat": NOW,
            "exp": NOW + timedelta(seconds=60),
        },
        SECRET,
        algorithm="HS512",
    )

    refused(other, "malformed")


@pytest.mark.parametrize("missing", ["exp", "iat", "sub", "aud", "iss", "scope", "jti"])
def test_a_token_without_one_of_the_required_claims_is_refused(missing):
    refused(forge(**{missing: None}), "malformed")


def test_a_token_made_for_another_server_is_refused():
    refused(mint(audience="mcp-calendar"), "audience")


def test_a_list_of_audiences_is_not_taken_for_ours():
    refused(forge(aud=["mcp-email", "mcp-calendar"]), "malformed")
    verifier().verify(forge(aud=["mcp-email"], jti="one"))


def test_a_token_for_another_owner_or_issuer_is_refused():
    refused(forge(sub="someone-else"), "subject")
    refused(forge(iss="somebody"), "issuer")


def test_a_token_from_the_future_is_refused_but_a_little_clock_skew_is_not():
    refused(mint(now=NOW + timedelta(seconds=30)), "not_yet_valid")
    verifier().verify(mint(now=NOW + timedelta(seconds=4)))


def test_a_token_that_lives_longer_than_the_agent_would_make_one_is_refused():
    refused(forge(exp=NOW + timedelta(hours=1)), "lifetime")
    verifier().verify(forge(exp=NOW + timedelta(seconds=120), jti="edge"))


def test_a_token_used_twice_is_refused_the_second_time():
    check = verifier()
    token = mint()

    check.verify(token)
    with pytest.raises(InvalidToken) as caught:
        check.verify(token)
    assert caught.value.code == "replayed"


def test_a_refused_token_is_not_remembered_as_used():
    guard = ReplayGuard()
    check = verifier(guard=guard)

    refused(forge(aud="mcp-calendar", jti="x"), "audience", guard=guard)
    check.verify(forge(jti="x"))


def test_a_token_replayed_by_another_verifier_with_its_own_memory_gets_through():
    """The memory is per process, which is why a server runs as one worker (docs/PHASE6.md)."""
    token = mint()

    verifier().verify(token)
    verifier().verify(token)


def test_a_short_secret_is_refused_on_both_sides():
    with pytest.raises(ValueError, match="at least"):
        TokenVerifier("short", "mcp-email")
    with pytest.raises(ValueError, match="at least"):
        tokens.mint("short", audience="mcp-email", scope="x")


# The replay memory -------------------------------------------------------------------------


def test_the_replay_memory_forgets_a_token_once_it_could_no_longer_be_valid():
    guard = ReplayGuard()
    later = NOW + timedelta(seconds=120)

    assert guard.accept("a", NOW + timedelta(seconds=60), NOW)
    assert not guard.accept("a", NOW + timedelta(seconds=60), NOW + timedelta(seconds=61))
    assert guard.accept("a", later, NOW + timedelta(seconds=120))


def test_a_full_replay_memory_refuses_new_tokens_rather_than_forget_old_ones():
    guard = ReplayGuard(capacity=2)
    expires = NOW + timedelta(seconds=60)

    assert guard.accept("a", expires, NOW) and guard.accept("b", expires, NOW)
    assert not guard.accept("c", expires, NOW)
    assert not guard.accept("a", expires, NOW)


# What a tool may do with a token -----------------------------------------------------------


def claims(scope):
    return Claims("owner", "mcp-email", scope, "j", NOW, NOW + timedelta(seconds=60))


def test_a_tool_runs_only_with_a_token_made_for_it():
    marker = auth._claims.set(claims("email_read"))
    try:
        assert require_scope("email_read").scope == "email_read"
        with pytest.raises(ScopeError):
            require_scope("email_search")
    finally:
        auth._claims.reset(marker)


def test_a_session_token_runs_no_tool_and_no_token_runs_no_tool():
    assert current_claims() is None
    with pytest.raises(ScopeError):
        require_scope("email_read")
    marker = auth._claims.set(claims("session"))
    try:
        with pytest.raises(ScopeError):
            require_scope("email_read")
    finally:
        auth._claims.reset(marker)
