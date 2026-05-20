from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field

from app.models.invoice import ExtractedInvoice


class JobStatus(str, Enum):
    """Lifecycle states of an extraction job.

    PENDING       → created, not yet picked up by the worker
    PROCESSING    → worker is actively extracting
    COMPLETED     → extraction succeeded; result is populated
    FAILED        → extraction failed but retries remain
    DEAD_LETTERED → all retries exhausted; job is terminal
    """

    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    DEAD_LETTERED = "dead_lettered"

    # str, Enum makes FastAPI/pydantic serialize the value ("pending") not the
    # member name ("PENDING"), so the API surface is stable even if we rename members.


class Job(BaseModel):
    # SHA-256 of the uploaded PDF bytes — doubles as the idempotency key.
    # If the client re-uploads the same file, the hash collides and we return
    # the existing job rather than creating a duplicate.
    job_id: str

    status: JobStatus = JobStatus.PENDING

    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    started_at: datetime | None = None
    finished_at: datetime | None = None

    # How many extraction attempts have been made (incremented by the worker).
    attempts: int = 0

    # Stored alongside job_id to make the content-addressing intent explicit.
    # job_id is the external API handle; document_hash names the concept.
    # Both hold the same SHA-256 value.
    document_hash: str
    document_size_bytes: int

    result: ExtractedInvoice | None = None

    # Human-readable error message, set on FAILED / DEAD_LETTERED transitions.
    error: str | None = None
