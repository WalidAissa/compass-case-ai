import asyncio

from app.models.job import Job


class InMemoryJobStore:
    """Dict-backed job store, safe for concurrent async access via asyncio.Lock.

    Satisfies JobStore structurally — no explicit inheritance needed.

    Production swap: replace with a class that wraps Redis or Azure Cosmos DB
    and register it in the FastAPI lifespan.  The rest of the application never
    imports this class directly; it only depends on the JobStore Protocol.
    """

    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        # A single lock serialises all mutations.  In pure asyncio code the GIL
        # already makes individual dict ops atomic, but multi-step operations
        # (e.g. check-then-insert in create) need an explicit lock to stay
        # consistent when other coroutines are awaited between steps.
        self._lock = asyncio.Lock()

    async def create(self, job: Job) -> None:
        async with self._lock:
            if job.job_id in self._jobs:
                raise ValueError(f"Job {job.job_id!r} already exists")
            self._jobs[job.job_id] = job

    async def get(self, job_id: str) -> Job | None:
        async with self._lock:
            return self._jobs.get(job_id)

    async def update(self, job: Job) -> None:
        async with self._lock:
            if job.job_id not in self._jobs:
                raise KeyError(f"Job {job.job_id!r} not found; cannot update")
            self._jobs[job.job_id] = job

    async def find_by_doc_hash(self, document_hash: str) -> Job | None:
        # We intentionally scan rather than reuse get(document_hash) even though
        # job_id == document_hash in this implementation.  A future store (e.g.
        # Cosmos) may use a surrogate job_id, so the Protocol separates the two
        # concepts and each implementation can optimise as it sees fit.
        async with self._lock:
            for job in self._jobs.values():
                if job.document_hash == document_hash:
                    return job
            return None
