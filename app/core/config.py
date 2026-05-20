from functools import lru_cache

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # env_file is a fallback; real deployments inject vars directly into the environment.
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # SecretStr prevents the key from appearing in logs, tracebacks, or __repr__.
    # Access the raw value with: settings.openai_api_key.get_secret_value()
    openai_api_key: SecretStr

    openai_model: str = "gpt-4o"
    log_level: str = "INFO"

    # Tenacity will attempt the LLM call up to this many times on TransientErrors.
    llm_max_retries: int = 3

    # Hard timeout passed to the OpenAI client per call (seconds).
    llm_timeout_s: int = 120

    # Controls log renderer: "dev" → pretty console, anything else → JSON.
    app_env: str = "dev"


@lru_cache
def get_settings() -> Settings:
    # Parsed once on first call; subsequent calls return the cached instance.
    return Settings()
