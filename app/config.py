from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env")

    database_url: str = "postgresql+psycopg2://postgres:postgres@localhost:5432/postgres"
    log_level: str = "INFO"
    port: int = 8000
    kafka_bootstrap_servers: str = "localhost:9092"
    outbox_poll_interval_ms: int = 500
    worker_id: str = "worker-local"


settings = Settings()
