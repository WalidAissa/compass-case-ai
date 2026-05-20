import base64
import logging
from collections.abc import Callable
from typing import Any

import instructor
import openai
from openai import AsyncOpenAI
from pydantic import ValidationError as PydanticValidationError
from tenacity import (
    AsyncRetrying,
    RetryCallState,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

from app.core.config import Settings, get_settings
from app.core.exceptions import (
    ExtractionError,
    LLMAuthError,
    LLMContextLengthError,
    LLMRateLimitError,
    LLMServerError,
    LLMTimeoutError,
    PermanentError,
    SchemaValidationError,
    TransientError,
)
from app.core.logging import bind_job_id
from app.llm.prompts import INVOICE_SYSTEM_PROMPT
from app.models.invoice import ExtractedInvoice

# ---------------------------------------------------------------------------
# Retry helpers
# ---------------------------------------------------------------------------

# tenacity's before_sleep_log requires a stdlib logger.  We keep one here
# for that specific purpose, but the primary per-job logging uses structlog
# via bind_job_id() so that job_id appears on every retry line.


def _before_sleep_log(log: Any) -> Callable[[RetryCallState], None]:
    """Return a before_sleep callback that logs through structlog with job_id.

    We use this instead of tenacity's before_sleep_log(stdlib_logger, ...) so
    that every retry log line carries the job_id correlation field. 
    """
    def _log(retry_state: RetryCallState) -> None:
        exc = retry_state.outcome.exception()
        sleep = getattr(retry_state.next_action, "sleep", None)
        log.warning(
            "llm_retry_scheduled",
            attempt=retry_state.attempt_number,
            wait_s=round(sleep, 2) if sleep is not None else None,
            error_type=type(exc).__name__,
            error=str(exc),
        )

    return _log


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

class LLMClient:
    """instructor-wrapped OpenAI client with tenacity retry and error translation.

    instructor handles: structured outputs, schema validation, schema-level retries.
    tenacity handles:   transient API failures (rate limits, timeouts, 5xx).
    These two retry layers are independent and target different exception types.
    """

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        # instructor patches AsyncOpenAI to add response_model support.
        self._client = instructor.from_openai(
            AsyncOpenAI(
                api_key=self._settings.openai_api_key.get_secret_value(),
                # Hard deadline per call; raises APITimeoutError on breach.
                timeout=float(self._settings.llm_timeout_s),
            )
        )

    # ------------------------------------------------------------------
    # Error translation
    # ------------------------------------------------------------------

    def _translate(self, exc: Exception) -> ExtractionError:
        """Map raw SDK / instructor exceptions onto our taxonomy.

        Called at the boundary of every instructor call so nothing from
        openai or instructor leaks into the service layer.
        """
        # Already ours — shouldn't happen in normal flow but guard anyway.
        if isinstance(exc, ExtractionError):
            return exc

        # instructor exhausted its own schema-retry budget.
        if isinstance(exc, PydanticValidationError):
            return SchemaValidationError(validation_errors=exc.errors())

        try:
            from instructor.exceptions import InstructorRetryException
            if isinstance(exc, InstructorRetryException):
                return SchemaValidationError(validation_errors=[{"msg": str(exc)}])
        except ImportError:
            pass

        match exc:
            case openai.APITimeoutError():
                return LLMTimeoutError(timeout_s=self._settings.llm_timeout_s)
            case openai.RateLimitError():
                return LLMRateLimitError()
            case openai.InternalServerError():
                return LLMServerError(status_code=getattr(exc, "status_code", 500))
            case openai.AuthenticationError():
                return LLMAuthError()
            case openai.BadRequestError() if getattr(exc, "code", None) == "context_length_exceeded":
                return LLMContextLengthError()
            case _:
                # Unrecognised error — treat as permanent to avoid burning retries.
                return PermanentError(str(exc))

    # ------------------------------------------------------------------
    # Core call (shared retry loop)
    # ------------------------------------------------------------------

    async def _call(
        self,
        messages: list[dict[str, Any]],
        log: Any,
    ) -> ExtractedInvoice:
        """Run one instructor call inside a tenacity retry loop.

        stop_after_attempt(N) → N total attempts (1 initial + N-1 retries).
        wait_exponential_jitter → avoids thundering-herd on rate-limit windows.
        retry_if_exception_type(TransientError) → permanent errors fail fast.
        """
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(self._settings.max_llm_retries),
            wait=wait_exponential_jitter(initial=1, max=60),
            retry=retry_if_exception_type(TransientError),
            before_sleep=_before_sleep_log(log),
            reraise=True,
        ):
            with attempt:
                try:
                    return await self._client.chat.completions.create(
                        model=self._settings.openai_model,
                        response_model=ExtractedInvoice,
                        messages=messages,
                        # instructor's own schema-correction retry budget.
                        # Fires on PydanticValidationError, not on API errors.
                        max_retries=2,
                    )
                except Exception as exc:
                    raise self._translate(exc) from exc

        # Unreachable: reraise=True guarantees tenacity raises before we get here.
        # Present only to satisfy the type checker (no implicit None return).
        raise RuntimeError("unreachable")  

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def extract_from_text(self, text: str, job_id: str) -> ExtractedInvoice:
        """Send extracted PDF text to the LLM and return a structured invoice."""
        log = bind_job_id(job_id)
        log.info(
            "llm_extraction_started",
            path="text",
            model=self._settings.openai_model,
            chars=len(text),
        )
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": INVOICE_SYSTEM_PROMPT},
            {"role": "user", "content": text},
        ]
        result = await self._call(messages, log)
        log.info("llm_extraction_completed", path="text")
        return result

    async def extract_from_images(
        self, png_bytes_list: list[bytes], job_id: str
    ) -> ExtractedInvoice:
        """Base64-encode page PNGs and send them to GPT-4o vision."""
        log = bind_job_id(job_id)
        log.info(
            "llm_extraction_started",
            path="vision",
            model=self._settings.openai_model,
            pages=len(png_bytes_list),
        )
        # Build a multimodal content list: text preamble + one image block per page.
        content: list[dict[str, Any]] = [
            {
                "type": "text",
                "text": "Extract the invoice data from the following document pages:",
            }
        ]
        for page_bytes in png_bytes_list:
            b64 = base64.b64encode(page_bytes).decode()
            content.append(
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:image/png;base64,{b64}",
                        # "high" detail: OpenAI tiles the image for fine-print legibility.
                        # Costs more tokens than "low" but necessary for invoice numbers.
                        "detail": "high",
                    },
                }
            )
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": INVOICE_SYSTEM_PROMPT},
            {"role": "user", "content": content},
        ]
        result = await self._call(messages, log)
        log.info("llm_extraction_completed", path="vision")
        return result
