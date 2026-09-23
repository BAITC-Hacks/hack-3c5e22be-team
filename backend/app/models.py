from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class Stock(BaseModel):
    id: int
    name: str
    quantity: Decimal | None = None


class QualityIssue(BaseModel):
    code: str
    message: str


class Product(BaseModel):
    id: int = Field(gt=0)
    name: str
    article: str
    supplier_article: str | None = None
    description: str = ""
    price: Decimal | None = None
    currency: str | None = None  # Not supplied by the observed API; do not infer.
    quantity: Decimal | None = None
    stores: list[Stock] = Field(default_factory=list)
    url: str | None = None
    image: str | None = None
    category: str | None = None
    attributes: dict[str, str] = Field(default_factory=dict)
    certificates: list[str] = Field(default_factory=list)
    certificate_status: Literal["unknown"] = "unknown"
    minimum_quantity_raw: str | None = None
    quality_issues: list[QualityIssue] = Field(default_factory=list)
    source: Literal["ekt_api", "synthetic"]
    observed_at: datetime
    stale: bool
    detail_loaded: bool


class Alternative(BaseModel):
    product: Product
    reasons: list[str]
    requires_verification: bool = True


class AlternativesResult(BaseModel):
    product_id: int
    items: list[Alternative] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000, pattern=r"\S")
    request_id: UUID | None = None


class ChatResponse(BaseModel):
    message_id: str = Field(default_factory=lambda: str(uuid4()))
    message: str
    mode: Literal["openai", "rules", "catalog"]
    products: list[Product] = Field(default_factory=list)
    alternatives: list[Alternative] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    cart_action: Literal["none", "integration_required"] = "none"


class Intent(BaseModel):
    intent: Literal["search", "details", "alternatives", "terms", "cart", "clarify"]
    query: str = Field(max_length=200)
    product_id: int | None
    topic: Literal["payment", "delivery", "minimum", "all"]
