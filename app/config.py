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

    # V0.4 -- see ARCHITECTURE_V0.4.md "Config additions"
    scheduler_poll_interval_ms: int = 1000
    aging_rate: float = 0.05  # effective-priority points gained per second waited
    priority_ceiling: int = 100
    max_admissions_per_pass: int = 50
    default_cpu: int = 1
    default_memory_mb: int = 512
    default_gpu: int = 0

    # V0.5 -- see ARCHITECTURE_V0.5.md "Config additions"
    minio_endpoint: str = "localhost:9000"
    minio_access_key: str = "minioadmin"
    minio_secret_key: str = "minioadmin"
    minio_bucket: str = "artifacts"
    reconciler_poll_interval_ms: int = 5000
    artifact_pending_grace_period_seconds: int = 300
    upload_lease_duration_seconds: int = 60
    upload_heartbeat_interval_seconds: int = 10


settings = Settings()
