from app.core.logging import bind_job_id
from app.extractors.pdf_handler import extract_text, render_pages_to_pngs
from app.extractors.strategy import ExtractionStrategy, choose_strategy
from app.llm.client import LLMClient
from app.models.invoice import ExtractedInvoice


class ExtractionService:
    """Orchestrates the full extraction pipeline for one document.

    Responsibilities:
      1. Run PDF text extraction and quality scoring (pdf_handler).
      2. Delegate strategy selection (choose_strategy).
      3. Call the appropriate LLM path (text or vision).
      4. Return the structured result.

    This class has no error-handling.  All exceptions propagate to the
    worker, which owns the job lifecycle (status transitions, retry counting,
    dead-lettering).  Keeping error handling out of here means the service
    can be unit-tested without mocking job state machinery.
    """

    def __init__(self, llm: LLMClient) -> None:
        self._llm = llm

    async def extract(self, doc_bytes: bytes, job_id: str) -> ExtractedInvoice:
        log = bind_job_id(job_id)
        log.info("extraction_started", doc_size_bytes=len(doc_bytes))

        # --- Step 1: text extraction + quality score ---
        # extract_text opens the PDF once and returns both the full text and a
        # quality score in [0, 1].  A score of 0 means no usable text layer.
        text, quality_score = extract_text(doc_bytes)
        log.info(
            "text_extracted",
            chars=len(text),
            quality_score=quality_score,
        )

        # --- Step 2: strategy selection ---
        # choose_strategy is I/O-free — it works on the already-computed text
        # and score so the PDF is never opened a second time.
        strategy = choose_strategy(text, quality_score)
        log.info("strategy_selected", strategy=strategy.value)

        # --- Step 3: LLM extraction ---
        if strategy is ExtractionStrategy.TEXT:
            result = await self._llm.extract_from_text(text, job_id)
        else:
            # Vision path: render every page to a PNG and send the images.
            # Logging before render because rendering can be slow for large PDFs.
            log.info("rendering_pages")
            pages = render_pages_to_pngs(doc_bytes)
            log.info("pages_rendered", count=len(pages))
            result = await self._llm.extract_from_images(pages, job_id)

        log.info("extraction_completed", strategy=strategy.value)
        return result
