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

    # Tracing to Langfuse. Both keys empty means no tracing. The base URL is the one for your
    # project's region; empty uses the SDK's default
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_base_url: str = ""


settings = Settings()
