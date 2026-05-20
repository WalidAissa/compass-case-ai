from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models.invoice import ExtractedInvoice
from app.models.job import JobStatus


class SubmitResponse(BaseModel):
    """Returned by POST /extract — tells the client their job_id to poll."""
    job_id: str
    status: JobStatus


class JobResponse(BaseModel):
    """Returned by GET /jobs/{job_id} — full job state including result."""

    # from_attributes lets us call JobResponse.model_validate(job_instance)
    # directly instead of going through .model_dump() first.
    model_config = ConfigDict(from_attributes=True)

    job_id: str
    status: JobStatus
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    attempts: int
    document_hash: str
    document_size_bytes: int
    result: ExtractedInvoice | None
    error: str | None
