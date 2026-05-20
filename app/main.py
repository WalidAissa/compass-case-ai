from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.routes import router
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.llm.client import LLMClient
from app.services.extraction_service import ExtractionService
from app.storage.memory import InMemoryJobStore


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Create shared dependencies once at startup; clean up on shutdown.

    Everything hung on app.state here is accessible to route handlers via
    the DI functions in routes.py (request.app.state.<name>).
    """
    configure_logging()
    settings = get_settings()

    app.state.job_store = InMemoryJobStore()
    app.state.llm_client = LLMClient(settings=settings)
    app.state.extraction_service = ExtractionService(llm=app.state.llm_client)

    yield

    # InMemoryJobStore is garbage-collected; nothing to close.
    # If we swap to a real store we would add connection teardown here.


app = FastAPI(
    title="Compass Document Intelligence",
    description="Invoice extraction service",
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(router)
