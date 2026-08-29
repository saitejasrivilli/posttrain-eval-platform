import logging
import time
import uuid

from pythonjsonlogger import jsonlogger
from starlette.middleware.base import BaseHTTPMiddleware

from app.config import settings


def configure_logging() -> logging.Logger:
    logger = logging.getLogger("app")
    logger.setLevel(settings.log_level)
    handler = logging.StreamHandler()
    handler.setFormatter(jsonlogger.JsonFormatter("%(asctime)s %(name)s %(levelname)s %(message)s"))
    logger.handlers = [handler]
    logger.propagate = False
    return logger


logger = configure_logging()


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        request_id = str(uuid.uuid4())
        start = time.perf_counter()
        response = await call_next(request)
        latency_ms = round((time.perf_counter() - start) * 1000, 2)
        logger.info(
            "request",
            extra={
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
                "latency_ms": latency_ms,
            },
        )
        response.headers["X-Request-ID"] = request_id
        return response
