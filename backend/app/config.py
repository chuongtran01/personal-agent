from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file="../.env", extra="ignore")

    database_url: str = "postgresql+psycopg://agent:agent@localhost:5432/agent"
    openai_api_key: str = ""
    agent_model: str = "gpt-6-luna"
    judge_model: str = "gpt-6-luna"
    embedding_model: str = "text-embedding-3-small"
    # Must match the vector column in the memories table
    embedding_dimensions: int = 1536

    # Memory search adds `recency_weight * exp(-age_days / recency_half_life_days)` to the fused
    # score. Reciprocal rank fusion scores sit around 0.01 to 0.03, so the weight is small (tuned on dev, docs/PHASE2.md)
    recency_weight: float = 0.0005
    recency_half_life_days: float = 365

    # What the agent recalls before each model call (docs/PHASE3.md). `vector` was the best mode in
    # the phase 2 baseline; the agent eval decides whether to move
    memory_search_mode: Literal["keyword", "vector", "hybrid", "hybrid_recency"] = "vector"
    recall_limit: int = 5
    recall_history_limit: int = 3

    # Who the agent talks to: the name is optional, the time zone decides what "today" is
    owner_name: str = ""
    owner_timezone: str = "UTC"

    # Owner login: one password, kept as an argon2 hash (`python -m app.auth hash`), and a secret
    # that signs the tokens. With either unset, login is closed (docs/PHASE3.md)
    owner_password_hash: str = ""
    jwt_secret: str = ""
    token_ttl_minutes: int = 720

    # Tracing to Langfuse. Both keys empty means no tracing. The base URL is the one for your
    # project's region; empty uses the SDK's default
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_base_url: str = ""

    @field_validator("owner_timezone")
    @classmethod
    def _known_time_zone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (
            ValueError,
            KeyError,
        ) as error:  # a typo fails at start-up, not on the first message
            raise ValueError(f"unknown time zone {value!r}") from error
        return value


settings = Settings()
