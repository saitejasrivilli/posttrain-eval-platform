from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env")

    database_url: str = "postgresql+psycopg2://postgres:postgres@localhost:5432/postgres"
    log_level: str = "INFO"
    port: int = 8000


settings = Settings()
