from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env")

    database_url: str = "postgresql+psycopg2://postgres:postgres@localhost:5432/postgres"
    log_level: str = "INFO"
    port: int = 8000
    kafka_bootstrap_servers: str = "localhost:9092"
    outbox_poll_interval_ms: int = 500
    worker_id: str = "worker-local"

    # V0.3 -- see ARCHITECTURE_V0.3.md "Config additions"
    lease_duration_seconds: int = 30
    heartbeat_interval_seconds: int = 5
    recovery_poll_interval_ms: int = 1000
    max_attempts: int = 3
    base_delay_seconds: float = 2
    max_delay_seconds: float = 60
    jitter_ratio: float = 0.2


settings = Settings()
