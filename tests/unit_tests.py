"""Smoke tests for the extraction pipeline.

Two test groups:
  1. End-to-end: POST a PDF → poll /jobs/{id} → assert COMPLETED result.
     The LLM is mocked; the PDF handler and strategy run for real.
  2. Quality scorer unit tests: no PDF bytes, no LLM, just plain strings.
"""

import asyncio
import hashlib
from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock

import fitz
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.routes import router
from app.extractors.pdf_handler import _entropy_score, _keyword_score, _word_ratio
from app.extractors.strategy import ExtractionStrategy, choose_strategy
from app.llm.client import LLMClient
from app.models.invoice import ExtractedInvoice, InvoiceHeader, LineItem
from app.services.extraction_service import ExtractionService
from app.storage.memory import InMemoryJobStore


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def fixed_invoice() -> ExtractedInvoice:
    """The value the mocked LLM always returns."""
    return ExtractedInvoice(
        header=InvoiceHeader(
            vendor="Acme Corp",
            invoice_id="INV-001",
            invoice_date=date(2024, 1, 15),
            currency="USD",
            subtotal=Decimal("90.00"),
            total=Decimal("100.00"),
        ),
        line_items=[
            LineItem(description="Consulting services", amount=Decimal("100.00"))
        ],
    )


@pytest.fixture
def fake_pdf_bytes() -> bytes:
    """A real, minimal PDF containing invoice-like text.

    PyMuPDF creates it in-memory so the PDF handler runs against
    actual bytes — only the LLM call is mocked.
    """
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text(
        (50, 50),
        "Invoice #INV-001\nVendor: Acme Corp\nDate: 2024-01-15\nTotal: $100.00",
    )
    pdf_bytes = doc.tobytes()
    doc.close()
    return pdf_bytes


@pytest.fixture
async def client(fixed_invoice: ExtractedInvoice) -> AsyncClient:
    """AsyncClient wired to a minimal FastAPI app with a mocked LLM.

    We build a fresh FastAPI instance (not the real app) to avoid
    triggering the lifespan, which would try to instantiate a real
    LLMClient and require OPENAI_API_KEY in the test environment.
    """
    mock_llm = AsyncMock(spec=LLMClient)
    mock_llm.extract_from_text.return_value = fixed_invoice
    mock_llm.extract_from_images.return_value = fixed_invoice

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.state.job_store = InMemoryJobStore()
    test_app.state.extraction_service = ExtractionService(llm=mock_llm)

    async with AsyncClient(
        transport=ASGITransport(app=test_app), base_url="http://test"
    ) as ac:
        yield ac


# ---------------------------------------------------------------------------
# End-to-end smoke tests
# ---------------------------------------------------------------------------

async def test_extract_and_poll(
    client: AsyncClient,
    fake_pdf_bytes: bytes,
    fixed_invoice: ExtractedInvoice,
) -> None:
    """Full pipeline: submit PDF → poll until done → assert result."""
    # --- submit ---
    r = await client.post(
        "/extract",
        files={"file": ("invoice.pdf", fake_pdf_bytes, "application/pdf")},
    )
    assert r.status_code == 202
    body = r.json()
    job_id = body["job_id"]

    # job_id must be the SHA-256 of the bytes — the idempotency contract
    assert job_id == hashlib.sha256(fake_pdf_bytes).hexdigest()

    # --- poll ---
    # Background tasks complete synchronously in the ASGI test transport, so
    # the first GET already returns COMPLETED in practice.
    job_data: dict = {}
    for _ in range(10):
        r = await client.get(f"/jobs/{job_id}")
        assert r.status_code == 200
        job_data = r.json()
        if job_data["status"] not in ("pending", "processing"):
            break
        await asyncio.sleep(0.05)

    # --- assert ---
    assert job_data["status"] == "completed"
    assert job_data["error"] is None

    header = job_data["result"]["header"]
    assert header["vendor"] == fixed_invoice.header.vendor
    assert header["invoice_id"] == fixed_invoice.header.invoice_id
    assert header["currency"] == fixed_invoice.header.currency

    items = job_data["result"]["line_items"]
    assert len(items) == 1
    assert items[0]["description"] == fixed_invoice.line_items[0].description


async def test_extract_idempotent(
    client: AsyncClient, fake_pdf_bytes: bytes
) -> None:
    """Submitting the same PDF twice returns the same job — no duplicate created."""
    r1 = await client.post(
        "/extract",
        files={"file": ("invoice.pdf", fake_pdf_bytes, "application/pdf")},
    )
    r2 = await client.post(
        "/extract",
        files={"file": ("invoice.pdf", fake_pdf_bytes, "application/pdf")},
    )
    assert r1.status_code == 202  # new submission
    assert r2.status_code == 200  # existing job — nothing re-queued
    assert r1.json()["job_id"] == r2.json()["job_id"]


async def test_get_job_not_found(client: AsyncClient) -> None:
    r = await client.get("/jobs/does-not-exist")
    assert r.status_code == 404


async def test_health(client: AsyncClient) -> None:
    r = await client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


# ---------------------------------------------------------------------------
# PDF quality scorer — pure string tests, no PDF bytes, no LLM
# ---------------------------------------------------------------------------

class TestKeywordScore:
    def test_known_good(self) -> None:
        # Hits invoice, total, $, tax, subtotal — exceeds the saturation threshold
        text = "Invoice #001  Vendor: Acme  Total: $500.00  Tax: $50.00  Subtotal: $450.00"
        assert _keyword_score(text) == 1.0

    def test_known_bad(self) -> None:
        assert _keyword_score("hello world foo bar baz qux") == 0.0

    def test_empty(self) -> None:
        assert _keyword_score("") == 0.0


class TestWordRatio:
    def test_known_good(self) -> None:
        # All alphanumeric — ratio should be high (spaces are ~17 % of chars)
        score = _word_ratio("Invoice Vendor Total Amount Subtotal Tax")
        assert score > 0.8

    def test_known_bad(self) -> None:
        # Pure whitespace — zero word characters
        assert _word_ratio("   \n\n\t   ") == 0.0

    def test_empty(self) -> None:
        assert _word_ratio("") == 0.0


class TestEntropyScore:
    def test_known_good(self) -> None:
        # Varied invoice prose — entropy should be well above 0.7 normalised
        text = "Invoice Total Due Date Vendor Amount Tax Subtotal Consulting Services"
        assert _entropy_score(text) > 0.7

    def test_known_bad_repetitive(self) -> None:
        # Single unique character — Shannon entropy is 0
        assert _entropy_score("aaaaaaaaaa") == 0.0

    def test_empty(self) -> None:
        assert _entropy_score("") == 0.0


class TestChooseStrategy:
    def test_text_path(self) -> None:
        text = "Invoice #001 Vendor: Acme Corp Total: $100.00 Date: 2024-01-15"
        assert choose_strategy(text, quality_score=0.8) is ExtractionStrategy.TEXT

    def test_vision_on_empty_text(self) -> None:
        assert choose_strategy("", quality_score=0.0) is ExtractionStrategy.VISION

    def test_vision_on_low_quality_score(self) -> None:
        # Long enough text but quality below default threshold of 0.5
        assert choose_strategy("x" * 50, quality_score=0.2) is ExtractionStrategy.VISION

    def test_vision_on_short_text(self) -> None:
        # Only 10 chars — below the 20-char image-only threshold
        assert choose_strategy("hi invoice", quality_score=0.9) is ExtractionStrategy.VISION
