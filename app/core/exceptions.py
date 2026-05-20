"""Exception hierarchy for the extraction pipeline.

Taxonomy
--------
ExtractionError          ← root; catch-all for the whole pipeline
├── TransientError       ← safe to retry; tenacity watches for these
│   ├── LLMTimeoutError
│   ├── LLMRateLimitError
│   └── LLMServerError
└── PermanentError       ← retrying won't help; fail the job immediately
    ├── LLMAuthError
    ├── LLMContextLengthError
    ├── SchemaValidationError
    └── DocumentReadError

The two mid-tier classes are the only thing tenacity needs to import:
    retry=retry_if_exception_type(TransientError)
Everything else is for logging, observability, and clear error messages.
"""


# ---------------------------------------------------------------------------
# Root
# ---------------------------------------------------------------------------


class ExtractionError(Exception):
    """Base class for all extraction pipeline errors."""


# ---------------------------------------------------------------------------
# Mid-tier: retry policy boundary
# ---------------------------------------------------------------------------


class TransientError(ExtractionError):
    """Failure that may resolve on retry (network blip, rate limit, overloaded server).

    Tenacity is configured to retry on exactly this class, so adding a new
    retryable error means subclassing this — nothing else changes.
    """


class PermanentError(ExtractionError):
    """Failure that will not resolve with retry.

    Tenacity is configured to NOT retry on this class.  The job moves
    straight to FAILED status with the error recorded.
    """


# ---------------------------------------------------------------------------
# Transient leaf exceptions
# ---------------------------------------------------------------------------


class LLMTimeoutError(TransientError):
    def __init__(self, timeout_s: float, message: str = "") -> None:
        self.timeout_s = timeout_s
        super().__init__(message or f"LLM call timed out after {timeout_s}s")


class LLMRateLimitError(TransientError):
    def __init__(self, retry_after: float | None = None, message: str = "") -> None:
        # retry_after is the value from the Retry-After header when present.
        self.retry_after = retry_after
        super().__init__(message or "LLM rate limit exceeded")


class LLMServerError(TransientError):
    def __init__(self, status_code: int, message: str = "") -> None:
        self.status_code = status_code
        super().__init__(message or f"LLM server error: HTTP {status_code}")


# ---------------------------------------------------------------------------
# Permanent leaf exceptions
# ---------------------------------------------------------------------------


class LLMAuthError(PermanentError):
    def __init__(self, message: str = "Invalid or missing OpenAI API key") -> None:
        super().__init__(message)


class LLMContextLengthError(PermanentError):
    def __init__(self, token_count: int | None = None, message: str = "") -> None:
        self.token_count = token_count
        detail = f" ({token_count} tokens)" if token_count else ""
        super().__init__(message or f"Document exceeds model context length{detail}")


class SchemaValidationError(PermanentError):
    """Raised when the LLM response cannot be coerced into the target Pydantic model.

    instructor will retry schema failures internally up to its own limit; this
    exception surfaces only after all instructor retries are exhausted.
    """

    def __init__(self, validation_errors: list[dict], message: str = "") -> None:
        self.validation_errors = validation_errors
        n = len(validation_errors)
        super().__init__(message or f"LLM output failed schema validation ({n} error(s))")


class DocumentReadError(PermanentError):
    """Raised when PyMuPDF cannot open or parse the uploaded file."""

    def __init__(self, filename: str, message: str = "") -> None:
        self.filename = filename
        super().__init__(message or f"Failed to read document: {filename}")
