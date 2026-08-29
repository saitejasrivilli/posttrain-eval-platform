from fastapi import FastAPI

from app.logging_conf import RequestLoggingMiddleware
from app.routers import artifacts, capacity, datasets, dlq, health, jobs, models, training_runs

app = FastAPI(title="posttrain-eval-platform", version="0.1.0")
app.add_middleware(RequestLoggingMiddleware)
app.include_router(health.router)
app.include_router(jobs.router)
app.include_router(dlq.router)
app.include_router(capacity.router)
app.include_router(datasets.router)
app.include_router(models.router)
app.include_router(training_runs.router)
app.include_router(artifacts.router)
