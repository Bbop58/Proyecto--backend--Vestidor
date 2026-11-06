import os
from typing import List, Union, Literal
from pydantic import AnyHttpUrl, field_validator, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", ".env.local"),
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore"
    )

    RUN_STARTUP_MIGRATIONS: bool = False
    RUN_DEMO_SEED: bool = False
    # Optional automatic backups: absent storage never disables manual export.
    BACKUP_S3_ENDPOINT: str = ''
    BACKUP_S3_BUCKET: str = ''
    BACKUP_S3_REGION: str = 'auto'
    BACKUP_S3_ADDRESSING_STYLE: Literal['virtual', 'path'] = 'virtual'
    BACKUP_S3_ACCESS_KEY_ID: SecretStr | None = None
    BACKUP_S3_SECRET_ACCESS_KEY: SecretStr | None = None
    BACKUP_ENVIRONMENT: str = ''
    PLATFORM_ADMIN_USER_IDS: List[str] = []
    PAYMENT_CREDENTIALS_KEY: SecretStr | None = None
    WALLET_SIGNING_KEY: str | None = Field(None, min_length=32)

    @field_validator('WALLET_SIGNING_KEY')
    @classmethod
    def require_private_wallet_key(cls, value):
        if value and value.startswith(('FASHIONSTORE_WALLET_', 'REPLACE_', 'CHANGE_ME')):
            raise ValueError('Configure a private wallet signing key; public examples are forbidden')
        return value

    @field_validator('SECRET_KEY')
    @classmethod
    def require_private_signing_key(cls,value):
        if value.startswith(("REPLACE_WITH_","CHANGE_ME")) or value in {'supersecretkey-change-this-in-production-long-random-string','your-secret-key-change-in-production'}:
            raise ValueError('Configure a private SECRET_KEY; the public default is forbidden')
        return value

    PROJECT_NAME: str = "Sistema de Autenticacion"
    API_V1_STR: str = "/api/v1"
    SECRET_KEY: str = Field(..., min_length=32)
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    DATABASE_URL: str = ""
    DATABASE_PUBLIC_URL: str = ""
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
        "https://proyecto-frontend-vestidor.vercel.app",
        "https://proyecto-frontend-vestidor-ten.vercel.app",
        "http://127.0.0.1:54205"
    ]

    # PayPal Configuration
    PAYPAL_CLIENT_ID: str = ""
    PAYPAL_CLIENT_SECRET: str = ""
    PAYPAL_MODE: str = "sandbox"
    PAYPAL_WEBHOOK_ID: str = ""
    SUBSCRIPTION_WEB_URL: str = "https://proyecto-frontend-vestidor-ten.vercel.app/empresa/suscripcion"
    PAYPAL_EXCHANGE_RATE: float = 6.96

    # Google Gemini AI Configuration
    GEMINI_API_KEY: str = ""
    VOICE_GEMINI_API_KEY: str = ""
    CHATBOT_GEMINI_API_KEY: str = ""
    # Comma-separated stable model IDs; at most two attempts per request.
    GEMINI_VOICE_MODELS: str = "gemini-3.6-flash,gemini-3.5-flash-lite"
    GEMINI_CHAT_MODELS: str = "gemini-3.6-flash,gemini-3.5-flash-lite"
    GEMINI_IMAGE_MODEL: str = "gemini-3.1-flash-image"

    # Hugging Face Configuration (User Access Token for IDM-VTON)
    HF_TOKEN: str = ""

    # Optional dedicated VTON endpoint (e.g. Google Colab Gradio Live URL: https://xxxx.gradio.live)
    VTON_API_URL: str = ""


    @property
    def PAYPAL_API_BASE_URL(self) -> str:
        if self.PAYPAL_MODE.lower() == "live":
            return "https://api-m.paypal.com"
        return "https://api-m.sandbox.paypal.com"

    @property
    def SQLALCHEMY_DATABASE_URI(self) -> str:
        db_url = self.DATABASE_URL or self.DATABASE_PUBLIC_URL or os.getenv("DATABASE_URL", "") or os.getenv("DATABASE_PUBLIC_URL", "")
        if db_url:
            # Fix Railway postgres:// and postgresql:// schema to explicitly use psycopg2
            if db_url.startswith("postgres://"):
                return db_url.replace("postgres://", "postgresql+psycopg2://", 1)
            elif db_url.startswith("postgresql://"):
                return db_url.replace("postgresql://", "postgresql+psycopg2://", 1)
            return db_url
        return f"postgresql+psycopg2://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}@{self.POSTGRES_SERVER}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"


settings = Settings()
