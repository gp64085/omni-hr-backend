from typing import List, Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    PROJECT_NAME: str = "OmniHR Enterprise HRMS"
    API_V1_STR: str = "/api/v1"

    # Security & Tokens
    JWT_SECRET: str = "super-secret-omni-hr-key-change-in-production-32-bytes"
    JWT_REFRESH_SECRET: str = "super-secret-refresh-key-change-in-production-32-bytes"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24  # 1 day for dev, 15-60 min for prod
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    # Environment & Transport Guards
    ENVIRONMENT: str = "development"
    EMAIL_TRANSPORT: str = "smtp"  # "smtp", "console", "none"

    # Frontend & Password Reset
    FRONTEND_URL: str = "http://localhost:3000"
    PASSWORD_RESET_TOKEN_EXPIRE_MINUTES: int = 15
    PASSWORD_RESET_MIN_DURATION_SECONDS: float = 0.15

    # SMTP & Email Settings
    SMTP_HOST: Optional[str] = None
    SMTP_PORT: int = 587
    SMTP_USER: Optional[str] = None
    SMTP_PASSWORD: Optional[str] = None
    SMTP_TLS: bool = True
    EMAILS_FROM_EMAIL: str = "noreply@omnihr.internal"
    EMAILS_FROM_NAME: str = "OmniHR System"

    # Database
    DATABASE_URL: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/omni_hr"
    SQL_ECHO: bool = False

    # Redis Cache & PubSub
    REDIS_URL: str = "redis://localhost:6379/0"

    # Scheduler Gate
    RUN_SCHEDULER: bool = True

    # CORS

    BACKEND_CORS_ORIGINS: List[str] = [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ]

    model_config = SettingsConfigDict(
        env_file=["backend/.env", ".env"],
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )


settings = Settings()
