from fastapi import FastAPI

from app.logging_conf import RequestLoggingMiddleware
from app.routers import dlq, health, jobs

app = FastAPI(title="posttrain-eval-platform", version="0.1.0")
app.add_middleware(RequestLoggingMiddleware)
app.include_router(health.router)
app.include_router(jobs.router)
app.include_router(dlq.router)
