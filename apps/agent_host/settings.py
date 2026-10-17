from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: Literal["development", "test", "demo", "production"] = "development"
    log_level: str = "INFO"
    database_url: str = "postgresql+psycopg://user:password@localhost:5432/supplymesh"
    asi_one_api_key: SecretStr | None = None
    asi_one_model: str = "asi1"
    agentverse_api_key: SecretStr | None = None
    orchestrator_agent_seed: SecretStr | None = None
    inventory_agent_seed: SecretStr | None = None
    sourcing_agent_seed: SecretStr | None = None
    procurement_agent_seed: SecretStr | None = None
    acknowledgment_agent_seed: SecretStr | None = None
    agent_host_port: int = Field(default=8000, ge=1, le=65535)
    agent_mailbox: bool = True
    publish_agent_details: bool = False
    supplier_simulator_base_url: str = "http://localhost:8001"
    supplier_api_key: SecretStr | None = None
    supplier_admin_api_key: SecretStr | None = None
    fx_provider_url: str = "https://api.frankfurter.dev"
    fx_cache_ttl_seconds: int = Field(default=3600, gt=0)
    http_connect_timeout_seconds: float = Field(default=3, gt=0)
    http_read_timeout_seconds: float = Field(default=10, gt=0)
    http_max_retries: int = Field(default=3, ge=0, le=10)
    min_valid_quotes: int = Field(default=2, ge=1)
    ack_poll_interval_seconds: float = Field(default=2, gt=0)
    ack_poll_timeout_seconds: float = Field(default=45, gt=0)
    sentry_dsn: SecretStr | None = None
    sentry_traces_sample_rate: float = Field(default=1.0, ge=0, le=1)
    sentry_profiles_sample_rate: float = Field(default=0.0, ge=0, le=1)


@lru_cache
def get_settings() -> Settings:
    return Settings()
