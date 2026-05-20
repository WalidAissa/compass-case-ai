from datetime import date
from decimal import Decimal

from pydantic import BaseModel, Field, field_validator


def _collapse_whitespace(v: str | None) -> str | None:
    """Replace any run of whitespace (including \\n, \\t) with a single space."""
    if isinstance(v, str):
        return " ".join(v.split())
    return v


class LineItem(BaseModel):
    @field_validator("description", "unit", mode="before")
    @classmethod
    def normalise_str(cls, v: str | None) -> str | None:
        return _collapse_whitespace(v)

    description: str = Field(
        description=(
            "Name or description of the product or service on this line "
            "(e.g., 'Software license', 'Consulting — 3 hrs')."
        )
    )
    quantity: Decimal | None = Field(
        default=None,
        description=(
            "Number of units billed. Null if not stated "
            "(common on fixed-fee or lump-sum lines)."
        ),
    )
    unit: str | None = Field(
        default=None,
        description="Unit of measure: hours, days, kg, units, etc. Null if not stated.",
    )
    unit_price: Decimal | None = Field(
        default=None,
        description=(
            "Price per single unit before tax. "
            "Null if not broken out (e.g., the line only shows a total)."
        ),
    )
    amount: Decimal = Field(
        description=(
            "Amount shown for this line. For product/service lines this is the "
            "pre-tax amount; for tax or fee lines (GST, HST, TVQ, TPS, etc.) "
            "this is the tax amount itself."
        ),
    )


class InvoiceHeader(BaseModel):
    @field_validator(
        "vendor", "vendor_address", "vendor_email", "vendor_phone",
        "invoice_id", "po_number", "currency",
        "payment_terms", "payment_method",
        "bill_to", "bill_to_address",
        "gst_number", "qst_number",
        mode="before",
    )
    @classmethod
    def normalise_str(cls, v: str | None) -> str | None:
        return _collapse_whitespace(v)

    # --- Vendor ---
    vendor: str = Field(
        description="Legal name of the company or individual that issued the invoice."
    )
    vendor_address: str | None = Field(
        default=None,
        description=(
            "Full address of the vendor as printed on the invoice. "
            "Null if not present."
        ),
    )
    vendor_email: str | None = Field(
        default=None,
        description="Vendor contact email address. Null if not present.",
    )
    vendor_phone: str | None = Field(
        default=None,
        description="Vendor contact phone number. Null if not present.",
    )

    # --- Invoice references ---
    invoice_id: str | None = Field(
        default=None,
        description=(
            "Invoice number or reference. May be labelled 'Invoice No.', "
            "'Ref', or 'Invoice #'. Null if absent."
        ),
    )
    po_number: str | None = Field(
        default=None,
        description=(
            "Purchase order number if explicitly labelled 'PO Number' or 'PO #'. "
            "Null if absent."
        ),
    )

    # --- Dates ---
    invoice_date: date | None = Field(
        default=None,
        description=(
            "Date the invoice was issued. "
            "Return as ISO 8601 (YYYY-MM-DD). Null if not found."
        ),
    )
    due_date: date | None = Field(
        default=None,
        description=(
            "Payment due date, if stated. "
            "Return as ISO 8601 (YYYY-MM-DD). Null if absent."
        ),
    )

    # --- Payment ---
    currency: str = Field(
        default="USD",
        description=(
            "ISO 4217 currency code. "
            "Normalize symbols to codes: $ → USD, € → EUR, £ → GBP, ¥ → JPY."
        ),
    )
    payment_terms: str | None = Field(
        default=None,
        description=(
            "Payment terms as stated (e.g. 'Net 30', 'Due on receipt'). "
            "Null if not present."
        ),
    )
    payment_method: str | None = Field(
        default=None,
        description=(
            "Payment method or instrument (e.g. 'Visa ending in 4242', "
            "'Bank transfer', 'Cash'). Null if not stated."
        ),
    )

    # --- Totals ---
    subtotal: Decimal | None = Field(
        default=None,
        description=(
            "Pre-tax subtotal. Null if the invoice does not separate tax "
            "from the total (i.e., only a single total figure is shown)."
        ),
    )
    total: Decimal = Field(
        description=(
            "Final amount due including all taxes and fees. "
            "This is the number the payer must remit."
        ),
    )

    # --- Canadian tax registration (CAD invoices) ---
    gst_number: str | None = Field(
        default=None,
        description=(
            "Canadian GST/HST registration number, present on CAD invoices. "
            "Typically labelled 'GST #', 'HST #', or 'Business Number (BN)'. "
            "Null if not shown."
        ),
    )
    qst_number: str | None = Field(
        default=None,
        description=(
            "Quebec QST (TVQ) registration number, present on invoices from "
            "Quebec-registered vendors. Typically labelled 'QST #' or 'No TVQ'. "
            "Null if not shown."
        ),
    )

    # --- Recipient ---
    bill_to: str | None = Field(
        default=None,
        description=(
            "Name (and company if shown) of the invoice recipient. "
            "Null if not present."
        ),
    )
    bill_to_address: str | None = Field(
        default=None,
        description=(
            "Mailing or billing address of the recipient. "
            "Null if not present."
        ),
    )


class ExtractedInvoice(BaseModel):
    header: InvoiceHeader = Field(
        description="Top-level invoice metadata: vendor, dates, and monetary totals."
    )
    line_items: list[LineItem] = Field(
        default_factory=list,
        description=(
            "All line items from the invoice body, in the order they appear. "
            "Return an empty list only if the document contains no itemized lines at all."
        ),
    )
