import math
import re
from collections import Counter

import fitz  # PyMuPDF — installed as "pymupdf", imported as "fitz"

from app.core.exceptions import DocumentReadError

# ---------------------------------------------------------------------------
# Quality-score internals
# ---------------------------------------------------------------------------

# Presence of these terms strongly suggests a real invoice text layer.
_INVOICE_KEYWORDS: frozenset[str] = frozenset({
    "invoice", "total", "date", "amount", "tax",
    "subtotal", "bill", "vendor", "due", "$", "€", "£",
})

# Matching this many distinct keywords earns a full keyword sub-score.
# Keeps the score meaningful even if only a handful of terms are on the page.
_KEYWORD_SATURATION = 4

# Word-like tokens: letters and digits (invoice numbers, amounts, dates all qualify).
_WORD_RE = re.compile(r"[A-Za-z0-9]+")


def _keyword_score(text: str) -> float:
    """0–1: fraction of invoice keywords found, saturating at _KEYWORD_SATURATION."""
    lower = text.lower()
    matched = sum(1 for kw in _INVOICE_KEYWORDS if kw in lower)
    return min(matched / _KEYWORD_SATURATION, 1.0)


def _word_ratio(text: str) -> float:
    """0–1: proportion of characters that belong to word-like tokens.

    Garbled extraction from image-only PDFs tends to produce sparse,
    non-alphanumeric output — whitespace, stray symbols — so this ratio falls.
    """
    if not text:
        return 0.0
    word_chars = sum(len(m) for m in _WORD_RE.findall(text))
    return word_chars / len(text)


def _entropy_score(text: str) -> float:
    """0–1: normalised Shannon character entropy.

    English prose lands around 3.5–4.5 bits; we normalise against 4.0 so
    real invoice text scores ≈ 0.9–1.0.  Repetitive noise (lots of spaces
    or a single repeated char) scores low; actual text scores high.
    """
    if not text:
        return 0.0
    n = len(text)
    counts = Counter(text)
    entropy = -sum((c / n) * math.log2(c / n) for c in counts.values())
    return min(entropy / 4.0, 1.0)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def extract_text(pdf_bytes: bytes) -> tuple[str, float]:
    """Open a PDF and return (full_text, quality_score).

    quality_score ∈ [0.0, 1.0] — weighted combination of:
      0.4 × keyword presence   (invoice vocabulary present?)
      0.4 × word-like ratio    (chars belong to real words?)
      0.2 × character entropy  (text varied enough to be prose?)

    Raises DocumentReadError if PyMuPDF cannot parse the bytes.
    """
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception as exc:
        raise DocumentReadError(filename="<uploaded>", message=str(exc)) from exc

    try:
        text = "\n".join(page.get_text() for page in doc)
    finally:
        doc.close()

    if not text.strip():
        return text, 0.0

    score = (
        0.4 * _keyword_score(text)
        + 0.4 * _word_ratio(text)
        + 0.2 * _entropy_score(text)
    )
    return text, round(score, 4)


def render_pages_to_pngs(pdf_bytes: bytes, dpi: int = 150) -> list[bytes]:
    """Render every page to a PNG and return a list of raw PNG bytes.

    dpi=150 gives roughly 1240×1754 px for A4 — enough for GPT-4o vision to
    read fine print without producing an oversized payload.

    Raises DocumentReadError if the PDF cannot be opened.
    """
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception as exc:
        raise DocumentReadError(filename="<uploaded>", message=str(exc)) from exc

    # fitz's native resolution is 72 DPI; the Matrix scales up from that.
    zoom = dpi / 72
    matrix = fitz.Matrix(zoom, zoom)

    try:
        return [page.get_pixmap(matrix=matrix).tobytes("png") for page in doc]
    finally:
        doc.close()


def is_image_only(pdf_bytes: bytes) -> bool:
    """True if the PDF yields fewer than 20 characters of extractable text.

    Reuses extract_text so we never open the same PDF twice in the normal
    extraction flow.  The 20-char threshold is intentionally conservative:
    even a trivial text layer (a page number, a watermark) keeps us on the
    cheaper text path, but a completely blank text layer — as produced by a
    scanner with no OCR — correctly routes to vision.
    """
    text, _ = extract_text(pdf_bytes)
    return len(text.strip()) < 20
