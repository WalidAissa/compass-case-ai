from enum import Enum

# Fewer than this many stripped characters → treat the PDF as image-only.
# Mirrors the threshold in pdf_handler.is_image_only; kept here so the
# strategy is self-contained and testable without opening a real PDF.
_IMAGE_ONLY_CHAR_THRESHOLD = 20


class ExtractionStrategy(str, Enum):
    TEXT = "text"    # extract text with PyMuPDF, send as string to the LLM
    VISION = "vision"  # render pages to PNGs, send as images to GPT-4o vision


def choose_strategy(
    text: str,
    quality_score: float,
    quality_threshold: float = 0.5,
) -> ExtractionStrategy:
    """Decide whether to use the text or vision extraction path.

    This function is deliberately I/O-free.  The caller (extraction_service)
    is responsible for running extract_text first and passing the results in.
    That means:
    - No PDF is opened twice.
    - This function is pure and trivially unit-testable with plain strings.

    Vision is chosen when either condition holds:
      1. The text layer is effectively empty (scanned / image-only PDF).
      2. The quality score is below the threshold (poor or garbled text layer).

    Args:
        text:              Full text returned by pdf_handler.extract_text.
        quality_score:     Score ∈ [0, 1] returned by pdf_handler.extract_text.
        quality_threshold: Minimum acceptable score for the text path.
                           Default 0.5; override in Settings for tuning.
    """
    if len(text.strip()) < _IMAGE_ONLY_CHAR_THRESHOLD:
        return ExtractionStrategy.VISION

    if quality_score < quality_threshold:
        return ExtractionStrategy.VISION

    return ExtractionStrategy.TEXT
