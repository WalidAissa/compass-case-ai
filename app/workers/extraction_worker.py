from dataclasses import dataclass
from datetime import datetime, timezone

from app.core.exceptions import PermanentError, SchemaValidationError, TransientError
from app.core.logging import bind_job_id
from app.models.job import Job, JobStatus
from app.services.extraction_service import ExtractionService
from app.storage.base import JobStore

# Job-level attempt ceiling.  Once a job has been tried this many times and
# each attempt ended in a TransientError, it moves to DEAD_LETTERED.
#
# This is distinct from LLMClient's max_llm_retries, which controls how many
# times tenacity retries a *single* LLM call within one worker attempt.
# Relationship: one worker attempt may internally make up to max_llm_retries
# LLM calls before surfacing a TransientError here.
_DEAD_LETTER_THRESHOLD: int = 3


@dataclass
class WorkerDeps:
    """Container for the dependencies a worker task needs.

    Passed as a single argument so BackgroundTasks.add_task stays clean and
    swapping implementations in tests only requires replacing this object.
    """

    job_store: JobStore
    extraction_service: ExtractionService


async def process_extraction(
    job_id: str,
    doc_bytes: bytes,
    deps: WorkerDeps,
) -> None:
    """Background task entry point for one extraction job.

    State machine:
        PENDING → PROCESSING → COMPLETED            (success)
                             → FAILED               (schema error or other permanent error)
                             → FAILED               (transient error, attempts < threshold)
                             → DEAD_LETTERED        (transient error, attempts >= threshold)

    Error policy:
        SchemaValidationError  permanent; mark FAILED immediately.
        TransientError         the LLM API may recover; allow retries up to
                               _DEAD_LETTER_THRESHOLD before dead-lettering.
        PermanentError         (other subclasses) same as schema: mark FAILED.
        Exception              unexpected; log with full traceback, mark FAILED.

    Never raises an exception.  By the time this runs, FastAPI has already                                                                    
    returned 202 to the client and closed the connection — there is no caller                                                                 
    to propagate to.  An unhandled exception would be swallowed by FastAPI                                                                  
    and the job would stay frozen at PROCESSING forever.  Writing every                                                                       
    failure into the job record is the only way errors reach the API surface.
    """
    log = bind_job_id(job_id)

    # ------------------------------------------------------------------ #
    # Fetch job and transition to PROCESSING                               #
    # ------------------------------------------------------------------ #
    job: Job | None = await deps.job_store.get(job_id)
    if job is None:
        # Should never happen — the route creates the job before enqueuing.
        log.error("worker_job_not_found")
        return

    job.attempts += 1
    job.status = JobStatus.PROCESSING
    job.started_at = datetime.now(timezone.utc)
    await deps.job_store.update(job)
    log.info("job_transition", status=job.status.value, attempt=job.attempts)

    # ------------------------------------------------------------------ #
    # Run extraction                                                       #
    # ------------------------------------------------------------------ #
    try:
        result = await deps.extraction_service.extract(doc_bytes, job_id)

    except SchemaValidationError as exc:
        # The LLM returned output that couldn't be coerced into ExtractedInvoice
        # even after instructor's internal schema-correction retries.  Retrying
        # the whole job won't help — the same document will produce the same
        # bad output.
        _fail(job, str(exc))
        await deps.job_store.update(job)
        log.warning(
            "job_transition",
            status=job.status.value,
            reason="schema_validation_error",
            validation_errors=exc.validation_errors,
        )

    except TransientError as exc:
        # The LLM API was unavailable / rate-limited even after tenacity
        # exhausted its per-call retry budget.  The job may succeed later.
        if job.attempts >= _DEAD_LETTER_THRESHOLD:
            _dead_letter(job, f"Exhausted {job.attempts} attempts. Last error: {exc}")
            await deps.job_store.update(job)
            log.error(
                "job_transition",
                status=job.status.value,
                reason="transient_retries_exhausted",
                attempts=job.attempts,
                error=str(exc),
            )
        else:
            # Mark FAILED (retriable).  In production this is where I would
            # re-enqueue to a durable queue (Redis, Azure Service Bus, etc.).
            # In this BackgroundTasks POC there is no consumer loop, so the job
            # stays FAILED until the client re-submits the document.
            _fail(job, str(exc))
            await deps.job_store.update(job)
            log.warning(
                "job_transition",
                status=job.status.value,
                reason="transient_error_retriable",
                attempt=job.attempts,
                remaining=_DEAD_LETTER_THRESHOLD - job.attempts,
                error=str(exc),
            )

    except PermanentError as exc:
        # DocumentReadError, LLMAuthError, LLMContextLengthError, etc.
        # None of these will resolve with a retry.
        _fail(job, str(exc))
        await deps.job_store.update(job)
        log.warning(
            "job_transition",
            status=job.status.value,
            reason="permanent_error",
            error_type=type(exc).__name__,
            error=str(exc),
        )

    except Exception as exc:
        # Unexpected error — log with full traceback for post-mortem debugging.
        _fail(job, f"{type(exc).__name__}: {exc}")
        await deps.job_store.update(job)
        log.exception(
            "job_transition",
            status=job.status.value,
            reason="unexpected_error",
            error_type=type(exc).__name__,
        )

    else:
        # No exception — extraction succeeded.
        job.status = JobStatus.COMPLETED
        job.result = result
        job.finished_at = datetime.now(timezone.utc)
        job.error = None
        await deps.job_store.update(job)
        log.info("job_transition", status=job.status.value)


# ------------------------------------------------------------------ #
# Helpers                                                              #
# ------------------------------------------------------------------ #

def _fail(job: Job, error: str) -> None:
    job.status = JobStatus.FAILED
    job.error = error
    job.finished_at = datetime.now(timezone.utc)


def _dead_letter(job: Job, error: str) -> None:
    job.status = JobStatus.DEAD_LETTERED
    job.error = error
    job.finished_at = datetime.now(timezone.utc)
