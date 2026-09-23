"""Single-process demonstration cart; never writes to the ekt.kz shop."""

import secrets
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class ProposalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    product_id: int = Field(gt=0)
    quantity: int = Field(gt=0, le=10000)


class ConfirmRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    proposal_id: str = Field(min_length=1, max_length=100)


class CartError(Exception):
    def __init__(self, code: str, message: str, status: int = 409):
        self.code, self.message, self.status = code, message, status


@dataclass
class CartState:
    items: dict = field(default_factory=dict)
    pending: dict | None = None
    receipts: dict = field(default_factory=dict)
    used_proposals: set = field(default_factory=set)
    version: int = 0


class DemoCart:
    def __init__(self, catalog, proposal_ttl: int):
        self.catalog, self.proposal_ttl = catalog, proposal_ttl

    def product(self, product_id):
        product = self.catalog.get(product_id)
        if product is None:
            raise CartError("PRODUCT_NOT_FOUND", "Товар не найден.", 404)
        if product.source != "synthetic":
            raise CartError("LIVE_CART_UNAVAILABLE", "Реальная корзина ekt.kz не подключена.")
        if product.quality_issues:
            raise CartError("DATA_CONFLICT", "Характеристики товара противоречивы.")
        if product.minimum_quantity_raw not in (None, "1"):
            raise CartError("DATA_CONFLICT", "Правила количества не соответствуют демо-шагу 1 шт.")
        if product.stale or not product.detail_loaded:
            raise CartError("CATALOG_UNAVAILABLE", "Снимок устарел или неполон.", 503)
        values = (product.price, product.quantity)
        if any(value is None or not value.is_finite() or value < 0 for value in values):
            raise CartError("CATALOG_UNAVAILABLE", "Цена или остаток неизвестны.", 503)
        if product.price != product.price.quantize(Decimal("0.01")):
            raise CartError("DATA_CONFLICT", "Недопустимая точность цены.")
        return product

    @staticmethod
    def snapshot(state):
        items = [dict(item) for item in state.items.values()]
        return {
            "data_mode": "demo",
            "currency": "KZT",
            "items": items,
            "total": format(sum((Decimal(i["total"]) for i in items), Decimal(0)), ".2f"),
            "version": state.version,
            "cart_url": "/cart",
            "warning": "Демо: тестовые данные. Остаток не резервируется, заказ не оформляется.",
        }

    def propose(self, state, body):
        product = self.product(body.product_id)
        existing = state.items.get(product.id, {}).get("quantity", 0)
        previous_price = state.items.get(product.id, {}).get("unit_price")
        if previous_price is not None and Decimal(previous_price) != product.price:
            raise CartError(
                "PRICE_CHANGED", "Цена позиции в корзине изменилась. Начните новую демо-сессию."
            )
        if existing + body.quantity > product.quantity:
            raise CartError("STOCK_CHANGED", "Количество с учётом корзины превышает остаток.")
        proposal = {
            "proposal_id": secrets.token_urlsafe(24),
            "product_id": product.id,
            "name": product.name,
            "article": product.article,
            "quantity": body.quantity,
            "unit": "шт",
            "unit_price": format(product.price, ".2f"),
            "total": format(product.price * body.quantity, ".2f"),
            "currency": "KZT",
            "expires_at": (datetime.now(UTC) + timedelta(seconds=self.proposal_ttl)).isoformat(),
            "data_mode": "demo",
        }
        state.pending = {
            "public": proposal,
            "deadline": time.monotonic() + self.proposal_ttl,
            "stock": product.quantity,
        }
        return proposal

    def confirm(self, state, proposal_id, key):
        if (
            not key
            or not 8 <= len(key) <= 128
            or not key.isascii()
            or not all(ch.isalnum() or ch in "-_" for ch in key)
        ):
            raise CartError(
                "VALIDATION_ERROR", "Нужен Idempotency-Key из 8–128 букв/цифр/-/_ .", 422
            )
        if key in state.receipts:
            if state.receipts[key] != proposal_id:
                raise CartError("IDEMPOTENCY_CONFLICT", "Ключ уже связан с другим предложением.")
            return {"cart": self.snapshot(state), "cart_url": "/cart", "replayed": True}
        if proposal_id in state.used_proposals:
            raise CartError("PROPOSAL_USED", "Предложение уже использовано.")
        pending = state.pending
        if not pending or pending["public"]["proposal_id"] != proposal_id:
            raise CartError("PROPOSAL_NOT_FOUND", "Предложение не найдено в текущей сессии.", 404)
        if time.monotonic() >= pending["deadline"]:
            state.pending = None
            raise CartError("PROPOSAL_EXPIRED", "Создайте и подтвердите новое предложение.")
        if len(state.receipts) >= 1000:
            raise CartError("SESSION_LIMIT", "Достигнут лимит операций демо-сессии.", 429)
        proposal = pending["public"]
        try:
            product = self.product(proposal["product_id"])
            quantity = state.items.get(product.id, {}).get("quantity", 0) + proposal["quantity"]
            if product.price != Decimal(proposal["unit_price"]):
                raise CartError("PRICE_CHANGED", "Цена изменилась. Нужно новое подтверждение.")
            if product.quantity != pending["stock"] or quantity > product.quantity:
                raise CartError("STOCK_CHANGED", "Остаток изменился. Нужно новое подтверждение.")
            if product.name != proposal["name"] or product.article != proposal["article"]:
                raise CartError("DATA_CONFLICT", "Описание товара изменилось.")
        except CartError:
            state.pending = None
            raise
        # No await between validation and mutation; caller holds the session lock.
        state.items[product.id] = {
            "product_id": product.id,
            "name": product.name,
            "article": product.article,
            "quantity": quantity,
            "unit": "шт",
            "unit_price": proposal["unit_price"],
            "total": format(product.price * quantity, ".2f"),
        }
        state.version += 1
        state.receipts[key] = proposal_id
        state.used_proposals.add(proposal_id)
        state.pending = None
        return {"cart": self.snapshot(state), "cart_url": "/cart", "replayed": False}
