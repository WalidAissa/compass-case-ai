# Architecture

Design decisions and rationale for compass-doc-intel.

---

## Async job model (202 + poll)

The API accepts a PDF and immediately returns `202 Accepted` with a `job_id`. The extraction runs in a FastAPI `BackgroundTask` and the client polls `GET /jobs/{id}` for the result.

The alternative — blocking until extraction completes — ties up an HTTP connection for 2–10 seconds per request, doesn't compose with retries, and gives the client no way to check status if the connection drops. The async model also maps cleanly to a durable queue in production: swapping `BackgroundTasks` for Azure Service Bus changes the worker entry point, not the API contract.

## Idempotency via SHA-256

`job_id` is the SHA-256 of the uploaded PDF bytes. Submitting the same file twice returns `200` with the existing job rather than creating a duplicate. This is a natural key for documents: content-addressable, collision-resistant, and derivable by the client without a server round-trip.

## Text vs vision routing

PDF extraction goes through two possible paths:

1. **Text path** — PyMuPDF extracts the text layer; the raw text is sent to the LLM.
2. **Vision path** — PyMuPDF renders each page to a PNG; the images are sent to GPT-4o with `detail: high`.

`choose_strategy()` in `app/extractors/strategy.py` selects the path based on two signals:
- **Character count** — fewer than 20 stripped characters means the PDF has no usable text layer (scanned or image-only). Vision is chosen unconditionally.
- **Quality score** — a weighted combination of keyword presence (0.4), word-character ratio (0.4), and Shannon entropy (0.2), all in [0, 1]. Scores below 0.5 indicate a garbled or encoded text layer (common in PDFs exported from certain SaaS billing systems) and trigger the vision path.

The function is pure — no I/O, no PDF bytes — so it is cheap to test in isolation with plain strings.

## Two-layer retry

There are two independent retry layers, targeting different failure modes:

| Layer | Library | Triggers on | Budget |
|---|---|---|---|
| Schema correction | instructor (internal) | `PydanticValidationError` — LLM output didn't match the schema | 2 extra attempts per call |
| API resilience | tenacity `AsyncRetrying` | `TransientError` — timeout, rate limit, 5xx | `LLM_MAX_RETRIES` attempts total |

These are composed, not nested: instructor retries happen inside a single tenacity attempt. If instructor exhausts its budget it raises `SchemaValidationError` (a `PermanentError`), which tenacity does not retry.

## Exception taxonomy

```
ExtractionError
├── TransientError       → tenacity retries; job stays retriable
│   ├── LLMTimeoutError
│   ├── LLMRateLimitError
│   └── LLMServerError
└── PermanentError       → tenacity skips; job moves to FAILED immediately
    ├── LLMAuthError
    ├── LLMContextLengthError
    ├── SchemaValidationError
    └── DocumentReadError
```

`retry_if_exception_type(TransientError)` is the only thing tenacity needs to know. Adding a new retryable error means subclassing `TransientError` — nothing else changes.

## Worker error policy

`process_extraction` never raises. Once FastAPI returns `202`, the HTTP connection is closed and there is no caller to receive an exception. An unhandled exception would be silently swallowed by Starlette's background task runner, leaving the job frozen at `PROCESSING` with no visible error. Every failure path — schema error, transient error, permanent error, unexpected exception — writes to the job record so the error surfaces through `GET /jobs/{id}`.

The state machine:
```
PENDING → PROCESSING → COMPLETED
                     → FAILED               (permanent error, or transient with attempts < 3)
                     → DEAD_LETTERED        (transient error, attempts >= 3)
```

`FAILED` is retriable by re-submitting the document. `DEAD_LETTERED` means the LLM API was unavailable for all three worker attempts; manual intervention or a later retry is needed.

## Structured outputs via instructor

`LLMClient` wraps `AsyncOpenAI` with `instructor.from_openai()`. instructor calls `ExtractedInvoice.model_json_schema()` and embeds the result in the API request as a structured-output constraint. This means the `Field(description=...)` annotation on every model field doubles as a field-level prompt instruction — the LLM reads the description to understand what to put there, and the schema constrains the output format. No post-processing or JSON parsing is needed; instructor returns a validated `ExtractedInvoice` instance directly.

## Dependency injection

Live objects (`InMemoryJobStore`, `LLMClient`, `ExtractionService`) are created once in the FastAPI lifespan and stored on `app.state`. Route handlers pull them via `Depends()` functions defined in `routes.py`. The DI functions live in `routes.py` rather than `main.py` to avoid a circular import (`main → routes → main`).

In tests, a fresh `FastAPI` instance is built with a mocked `LLMClient` attached to `app.state` directly, bypassing the lifespan entirely. This is why the test suite requires no `OPENAI_API_KEY`.

## Quality scorer weights

| Signal | Weight | Rationale |
|---|---|---|
| Keyword presence | 0.4 | Invoice-domain terms (`invoice`, `total`, `$`, etc.) are the strongest signal that text extraction worked |
| Word-character ratio | 0.4 | Garbled text layers (base64 artefacts, encoding errors) have low word-character density |
| Shannon entropy | 0.2 | Repetitive or degenerate content scores low; normal prose scores high |

The weights were chosen heuristically against the three invoices in the test dataset and are not tuned. A production system would calibrate these against a labelled corpus.
