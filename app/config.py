from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    database_url: str = "postgresql+asyncpg://affinitylog:affinitylog@localhost:5432/affinitylog"
    debug: bool = False
    cors_origins: list[str] = ["*"]
    app_title: str = "AffinityLog"
    app_version: str = "0.1.0"


settings = Settings()
