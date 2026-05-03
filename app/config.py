"""Configuration via pydantic-settings + .env."""
import json
from functools import lru_cache
from typing import Any

from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class DeviceConfig(BaseModel):
    name: str
    ip: str
    port: int = 37777
    username: str = "admin"
    password: str


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # App
    app_name: str = "Dahua Access API"
    debug: bool = True
    log_level: str = "INFO"

    # Auth
    jwt_secret: str = "change-me"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60
    admin_username: str = "admin"
    admin_password_hash: str = ""
    api_key: str = "change-me"

    # CORS / rate-limit
    allowed_origins: list[str] = ["*"]
    rate_limit_default: str = "60/minute"
    rate_limit_auth: str = "10/minute"
    rate_limit_write: str = "20/minute"

    # DB
    database_url: str = "sqlite:///data/jobs.sqlite"

    # Devices
    devices: list[DeviceConfig] = []

    @field_validator("devices", mode="before")
    @classmethod
    def _parse_devices(cls, v: Any):
        if isinstance(v, str):
            return json.loads(v)
        return v

    @field_validator("allowed_origins", mode="before")
    @classmethod
    def _parse_origins(cls, v: Any):
        if isinstance(v, str):
            v = v.strip()
            if v.startswith("["):
                return json.loads(v)
            return [s.strip() for s in v.split(",") if s.strip()]
        return v


@lru_cache
def get_settings() -> Settings:
    return Settings()
