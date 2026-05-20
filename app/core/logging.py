import logging
import sys

import structlog

from app.core.config import get_settings


def configure_logging() -> None:
    """Call once at application startup (inside the FastAPI lifespan)."""
    settings = get_settings()
    level = getattr(logging, settings.log_level.upper(), logging.INFO)
    is_dev = settings.app_env == "dev"

    processors: list[structlog.types.Processor] = [
        # Merges any key=value pairs bound via structlog.contextvars into every log event.
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        # Renders attached exception info as a string field rather than a raw tuple.
        structlog.processors.format_exc_info,
        structlog.dev.ConsoleRenderer() if is_dev else structlog.processors.JSONRenderer(),
    ]

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.stdlib.BoundLogger,
        logger_factory=structlog.PrintLoggerFactory(),
        # Cache the fully-configured logger on first use — avoids re-running the
        # processor chain setup on every log call.
        cache_logger_on_first_use=True,
    )

    # Also apply the level to stdlib so uvicorn's own log output obeys LOG_LEVEL.
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=level)


def get_logger(name: str = __name__) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)


def bind_job_id(job_id: str) -> structlog.stdlib.BoundLogger:
    """Return a new logger with job_id pre-bound.

    Usage:
        log = bind_job_id(job_id)
        log.info("extraction_started")   # every line carries job_id=<value>

    We use explicit binding (return a new logger) so
    the job_id is always visible in the call site.
    """
    return structlog.get_logger().bind(job_id=job_id)
