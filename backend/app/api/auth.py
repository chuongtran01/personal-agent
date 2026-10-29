from datetime import timedelta

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.api.deps import unauthorized
from app.auth import create_token, secret_is_usable, verify_password
from app.config import settings

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    password: str = Field(max_length=1000)


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int


@router.post("/login")
async def login(body: LoginRequest) -> LoginResponse:
    if not secret_is_usable(settings.jwt_secret):
        raise HTTPException(503, "owner login is not configured")
    # A wrong password and an unset hash look the same from outside
    if not verify_password(body.password, settings.owner_password_hash):
        raise unauthorized()
    ttl = timedelta(minutes=settings.token_ttl_minutes)
    return LoginResponse(
        access_token=create_token(settings.jwt_secret, ttl),
        expires_in=int(ttl.total_seconds()),
    )
