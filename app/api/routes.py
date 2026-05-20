import hashlib
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response, UploadFile

from app.api.schemas import JobResponse, SubmitResponse
from app.models.job import Job
from app.services.extraction_service import ExtractionService
from app.storage.base import JobStore
from app.workers.extraction_worker import WorkerDeps, process_extraction

router = APIRouter()


# ---------------------------------------------------------------------------
# Dependency-injection helpers
#
# These pull live objects off app.state, which the lifespan populates once
# at startup.  FastAPI calls them per-request via Depends().
# They live here (not in main.py) to avoid a circular import:
#   main → routes → main would fail; keeping DI in routes breaks the cycle.
# ---------------------------------------------------------------------------

def get_job_store(request: Request) -> JobStore:
    return request.app.state.job_store


def get_extraction_service(request: Request) -> ExtractionService:
    return request.app.state.extraction_service


# Annotated aliases keep route signatures readable.
JobStoreDep = Annotated[JobStore, Depends(get_job_store)]
ExtractionServiceDep = Annotated[ExtractionService, Depends(get_extraction_service)]


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.post("/extract", status_code=202, response_model=SubmitResponse)
async def submit_extraction(
    file: UploadFile,
    response: Response,
    background_tasks: BackgroundTasks,
    job_store: JobStoreDep,
    extraction_service: ExtractionServiceDep,
) -> SubmitResponse:
    """Accept a PDF and start an async extraction job.

    Idempotent: uploading the same bytes twice returns the existing job (200)
    instead of creating a duplicate (202).  The job_id is the SHA-256 of the
    PDF content, so identical files always produce the same key.
    """
    doc_bytes = await file.read()
    job_id = hashlib.sha256(doc_bytes).hexdigest()

    # Idempotency check — same bytes → same hash → existing job
    existing = await job_store.find_by_doc_hash(job_id)
    if existing is not None:
        response.status_code = 200  # already accepted, not a new submission
        return SubmitResponse(job_id=existing.job_id, status=existing.status)

    job = Job(
        job_id=job_id,
        document_hash=job_id,
        document_size_bytes=len(doc_bytes),
    )
    await job_store.create(job)

    deps = WorkerDeps(job_store=job_store, extraction_service=extraction_service)
    background_tasks.add_task(process_extraction, job_id, doc_bytes, deps)

    # Default 202 from the decorator — response is already on its way back
    # to the client before process_extraction starts running.
    return SubmitResponse(job_id=job_id, status=job.status)


@router.get("/jobs/{job_id}", response_model=JobResponse)
async def get_job(
    job_id: str,
    job_store: JobStoreDep,
) -> JobResponse:
    """Return the current state of an extraction job."""
    job = await job_store.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job {job_id!r} not found")
    return JobResponse.model_validate(job)


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
