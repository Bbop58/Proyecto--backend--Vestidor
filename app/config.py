from typing import List, Union
from pydantic import AnyHttpUrl, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore"
    )

    PROJECT_NAME: str = "Sistema de Autenticacion"
    API_V1_STR: str = "/api/v1"
    SECRET_KEY: str = "supersecretkey-change-this-in-production-long-random-string"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    DATABASE_URL: str = ""
    POSTGRES_SERVER: str = "localhost"
    POSTGRES_PORT: int = 5432
    POSTGRES_USER: str = "postgres"
    POSTGRES_PASSWORD: str = "postgres"
    POSTGRES_DB: str = "probando_1_db"

    BACKEND_CORS_ORIGINS: List[str] = [
        "http://localhost:4200",
        "http://localhost:3000",
        "http://127.0.0.1:4200",
        "http://localhost:8000",
        "http://localhost:8080",
        "http://127.0.0.1:8080",
        "https://*.vercel.app"
    ]

    # PayPal Configuration
    PAYPAL_CLIENT_ID: str = "BAAIlKDZJDPAM82OYRg0wA792WPdU2nHF_j5IEcNBLfwVoWVN1phsaHHDPwFY-BsX2IitDOaADBEKsWM3M"
    PAYPAL_CLIENT_SECRET: str = "EBm9qsZjiTndBEIxD8uNwMn3zQHaqudRd19GYswegmwGfuUsshYhApfUu6IQ2jnX51oqKaUoaq3fvyim"
    PAYPAL_MODE: str = "sandbox"
    PAYPAL_EXCHANGE_RATE: float = 6.96

    # Google Gemini AI Configuration
    GEMINI_API_KEY: str = ""

    # Hugging Face Configuration (User Access Token for IDM-VTON)
    HF_TOKEN: str = ""


    @property
    def PAYPAL_API_BASE_URL(self) -> str:
        if self.PAYPAL_MODE.lower() == "live":
            return "https://api-m.paypal.com"
        return "https://api-m.sandbox.paypal.com"

    @property
    def SQLALCHEMY_DATABASE_URI(self) -> str:
        if self.DATABASE_URL:
            # Fix Railway postgres:// schema for SQLAlchemy 2.0
            if self.DATABASE_URL.startswith("postgres://"):
                return self.DATABASE_URL.replace("postgres://", "postgresql://", 1)
            return self.DATABASE_URL
        return f"postgresql://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}@{self.POSTGRES_SERVER}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"


settings = Settings()
