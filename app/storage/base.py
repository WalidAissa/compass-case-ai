from typing import Protocol, runtime_checkable

from app.models.job import Job


@runtime_checkable
class JobStore(Protocol):
    """Structural interface for job persistence.

    Any class that implements these four async methods satisfies the Protocol —
    no inheritance required.  Switch from InMemoryJobStore to a Redis or
    Cosmos DB implementation by pointing the FastAPI dependency at a different
    concrete class; nothing else changes.

    runtime_checkable enables isinstance(obj, JobStore) checks at runtime,
    which is useful in tests and lifespan validation.
    """

    async def create(self, job: Job) -> None:
        """Persist a new job. Raises if job_id already exists."""
        ...

    async def get(self, job_id: str) -> Job | None:
        """Return the job with this id, or None if not found."""
        ...

    async def update(self, job: Job) -> None:
        """Replace the stored job record. Raises KeyError if job_id is unknown."""
        ...

    async def find_by_doc_hash(self, document_hash: str) -> Job | None:
        """Return any existing job whose document_hash matches, or None.

        Used by the idempotency check: if the caller uploads the same PDF
        twice, we return the existing job instead of creating a duplicate.
        """
        ...
