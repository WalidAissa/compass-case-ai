# compass-doc-intel

Invoice extraction service — upload a PDF, get structured JSON back. Built for a legal firm POC using GPT-4o vision + structured outputs.

## Overview

Accepts PDF invoices via HTTP, extracts header metadata and line items into a typed schema, and returns the result asynchronously. Handles both native-text PDFs and scanned/image-only PDFs by routing to the appropriate extraction path automatically.

**Stack:** FastAPI · GPT-4o (via instructor) · PyMuPDF · Pydantic v2 · tenacity · structlog

See [docs/architecture.md](docs/architecture.md) for design decisions and rationale.

---

## How to run

**Prerequisites:** Python 3.12+, [uv](https://docs.astral.sh/uv/), an OpenAI API key.

```bash
# Create virtual environment and install dependencies
uv venv
uv sync

# Set your API key
cp .env.example .env
# open .env and fill in OPENAI_API_KEY

# Start the server (leave this terminal open — logs stream here)
uv run uvicorn app.main:app --reload
```

### Run the unit tests

Open a second terminal:

```bash
uv run pytest -v
```

### Submit the three invoices and view results

Open a third terminal. Run each block, copy the `job_id` from the first curl, paste it into the second.

**Uber (image-only, CAD)**
```bash
curl -s -X POST http://localhost:8000/extract -F "file=@tests/case_interview_dataset-1.pdf" | jq .
curl -s http://localhost:8000/jobs/<job_id> | jq .
```

**WeWork (garbled text layer → vision, CAD)**
```bash
curl -s -X POST http://localhost:8000/extract -F "file=@tests/case_interview_dataset-2.pdf" | jq .
curl -s http://localhost:8000/jobs/<job_id> | jq .
```

**Cargo (image-only, EUR)**
```bash
curl -s -X POST http://localhost:8000/extract -F "file=@tests/case_interview_dataset-3.pdf" | jq .
curl -s http://localhost:8000/jobs/<job_id> | jq .
```

To confirm which extraction path fired, look in the server terminal for:
```
strategy_selected  strategy=vision  job_id=...
```

---

## Project structure

```
app/
  api/
    routes.py           POST /extract, GET /jobs/{id}, GET /health
    schemas.py          Request/response Pydantic schemas
  core/
    config.py           Settings (pydantic-settings, reads from .env)
    exceptions.py       Exception hierarchy: TransientError / PermanentError
    logging.py          structlog configuration; bind_job_id() helper
  extractors/
    pdf_handler.py      PyMuPDF text extraction + quality scoring + page rendering
    strategy.py         choose_strategy() — text path vs vision path
  llm/
    client.py           LLMClient: instructor-wrapped AsyncOpenAI + tenacity retry
    prompts.py          System prompt for the extraction task
  models/
    invoice.py          ExtractedInvoice, InvoiceHeader, LineItem (Pydantic)
    job.py              Job, JobStatus
  services/
    extraction_service.py   Orchestrates pdf_handler → strategy → LLMClient
  storage/
    base.py             JobStore Protocol (structural typing)
    memory.py           InMemoryJobStore (asyncio.Lock)
  workers/
    extraction_worker.py    Background task; owns the job state machine
  main.py               FastAPI app, lifespan, startup wiring
tests/
  unit_tests.py         Smoke tests (end-to-end with mocked LLM) + scorer unit tests
docs/
  architecture.md       Design decisions and rationale
```

---

## What is not implemented

This is a POC. The following would be required before production:

**Durability**
- Job store is in-memory — all jobs are lost on restart. Production needs a persistent store (PostgreSQL, Redis, Azure Cosmos DB).
- Background tasks run in-process via FastAPI `BackgroundTasks`. On process crash mid-job, the job freezes at `PROCESSING`. Production needs a durable queue (Azure Service Bus, RabbitMQ, Celery) so tasks survive restarts and can be picked up by any worker.

**Retry / reliability**
- Transient failures are marked `FAILED` with a note that the job is retriable, but there is no automatic re-queue. The client must resubmit. A real worker loop would re-enqueue `FAILED` jobs with backoff.
- No circuit breaker around the OpenAI client. Under sustained rate-limiting the service will keep attempting and burning tenacity budget. A circuit breaker (e.g. `pybreaker`) would open the circuit and fast-fail until the API recovers.

**Scale**
- Single-process only. No horizontal scaling, no shared job store, no distributed locking.
- No file size or page count limits enforced at the API boundary. A 500-page PDF would attempt to render every page and send all of them to the vision API.

**Security**
- No authentication or authorisation on any endpoint.
- No file type validation beyond the `application/pdf` content-type hint — a malformed file reaches PyMuPDF before being rejected.

**Observability**
- Logs go to stdout only. No log aggregation, no distributed tracing (OpenTelemetry), no metrics (Prometheus/Grafana).
- No alerting on `DEAD_LETTERED` jobs.

**Deployment**
- No Dockerfile, no container registry, no CI/CD pipeline.
- `InMemoryJobStore` is not safe for multi-worker deployments (each worker has its own state).
