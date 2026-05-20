from datetime import date
from decimal import Decimal

from pydantic import BaseModel, Field


class LineItem(BaseModel):
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
            "Total amount for this line excluding tax. "
            "If only a gross amount is shown, use that."
        ),
    )


class InvoiceHeader(BaseModel):
    vendor: str = Field(
        description="Legal name of the company or individual that issued the invoice."
    )
    invoice_id: str | None = Field(
        default=None,
        description=(
            "Invoice number or reference. May be labelled 'Invoice No.', "
            "'Ref', 'Invoice #', or 'PO Number'. Null if absent."
        ),
    )
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
    currency: str = Field(
        default="USD",
        description=(
            "ISO 4217 currency code. "
            "Normalize symbols to codes: $ → USD, € → EUR, £ → GBP, ¥ → JPY."
        ),
    )
    subtotal: Decimal | None = Field(
        default=None,
        description=(
            "Pre-tax subtotal. Null if the invoice does not separate tax "
            "from the total (i.e., only a single total figure is shown)."
        ),
    )
    tax: Decimal | None = Field(
        default=None,
        description=(
            "Tax amount charged (VAT, GST, sales tax, etc.). "
            "Null if no tax is stated or itemized."
        ),
    )
    total: Decimal = Field(
        description=(
            "Final amount due including all taxes and fees. "
            "This is the number the payer must remit."
        ),
    )
    bill_to: str | None = Field(
        default=None,
        description=(
            "Name or address of the invoice recipient ('Bill To' section). "
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
