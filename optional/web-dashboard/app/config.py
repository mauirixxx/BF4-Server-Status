from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "BF4 Status Web Dashboard"
    app_env: str = "development"
    database_url: str = Field(..., alias="DATABASE_URL")
    db_statement_timeout_ms: int = 5000
    snapshot_fresh_seconds: int = 900
    snapshot_adaptive_seconds: int = 3972
    presence_healthy_coverage_pct: float = 90.0
    worker_healthy_seconds: int = 60
    worker_warning_seconds: int = 180
    public_operator_panel: bool = False
    public_infrastructure_panel: bool = False
    database_nodes: str = ""
    dns_nodes: str = ""
    infrastructure_probe_timeout_seconds: float = 2.0

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
