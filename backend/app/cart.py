"""Session-bound prototype cart. EKT is read only; no orders or stock reservations."""

import secrets
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.ekt import CatalogUnavailable
from app.errors import ApiError
from app.normalize import normalize


class ProposalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    operation: Literal["add", "set", "remove", "clear"] = "add"
    product_id: int | None = Field(default=None, gt=0)
    quantity: int | None = Field(default=None, gt=0, le=10000)

    @model_validator(mode="after")
    def valid_operation(self):
        if self.operation == "clear":
            if self.product_id is not None or self.quantity is not None:
                raise ValueError("Очистка не принимает товар и количество.")
        elif self.product_id is None:
            raise ValueError("Укажите товар.")
        elif self.operation in ("add", "set") and self.quantity is None:
            raise ValueError("Укажите положительное целое количество.")
        elif self.operation == "remove" and self.quantity is not None:
            raise ValueError("Удаление не принимает количество.")
        return self


class ConfirmRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    proposal_id: str = Field(min_length=1, max_length=100)


class CartError(ApiError):
    def __init__(self, code: str, message: str, status: int = 409):
        super().__init__(status, code, message, retryable=status in (429, 503))


@dataclass
class CartState:
    items: dict = field(default_factory=dict)
    pending: dict | None = None
    receipts: dict = field(default_factory=dict)
    used_proposals: set = field(default_factory=set)
    version: int = 0


class DemoCart:
    def __init__(self, catalog, settings, ekt):
        self.catalog, self.settings, self.ekt = catalog, settings, ekt
        self.proposal_ttl = settings.cart_proposal_ttl_seconds

    async def product(self, product_id, validate_session):
        product = self.catalog.get(product_id)
        if product is None:
            raise CartError("PRODUCT_NOT_FOUND", "Товар не найден.", 404)
        if product.source == "ekt_api":
            if not self.settings.ekt_live_enabled:
                raise CartError(
                    "CATALOG_DISABLED", "Для реального товара включите EKT_LIVE_ENABLED.", 503
                )
            try:
                raw, observed_at = await self.ekt.detail(product_id)
                validate_session()
                if raw.get("id") != product_id:
                    raise ValueError("Wrong product")
                product = normalize(
                    raw, observed_at, "ekt_api", self.settings.catalog_stale_seconds
                )
                self.catalog.upsert(raw, observed_at)
            except CatalogUnavailable as exc:
                raise CartError("CATALOG_UNAVAILABLE", str(exc), 503) from exc
            except (ValueError, KeyError, TypeError) as exc:
                raise CartError(
                    "INVALID_CATALOG_RESPONSE", "Некорректная карточка каталога.", 502
                ) from exc
        if product.quality_issues:
            raise CartError("DATA_CONFLICT", "Характеристики товара противоречивы.")
        if product.source == "synthetic" and product.minimum_quantity_raw not in (None, "1"):
            raise CartError("DATA_CONFLICT", "Правила количества не соответствуют демо-шагу 1 шт.")
        if product.stale or not product.detail_loaded:
            raise CartError("CATALOG_UNAVAILABLE", "Снимок устарел или неполон.", 503)
        values = (product.price, product.quantity)
        if any(value is None or not value.is_finite() or value < 0 for value in values):
            raise CartError("CATALOG_UNAVAILABLE", "Цена или остаток неизвестны.", 503)
        if product.price > Decimal("1000000000000"):
            raise CartError("DATA_CONFLICT", "Цена выходит за допустимый предел прототипа.")
        if product.source == "synthetic" and product.price != product.price.quantize(
            Decimal("0.01")
        ):
            raise CartError("DATA_CONFLICT", "Недопустимая точность демо-цены.")
        return product

    @staticmethod
    def warning(synthetic):
        return (
            "Корзина прототипа, отдельно от ekt.kz. "
            "Остаток не резервируется, заказ не оформляется. "
            + (
                "Тестовые товары; KZT и шт — условные правила демонстрации."
                if synthetic
                else "Для реальных товаров валюта, единица, кратность и доступные к продаже склады "
                "не подтверждены. Сумма справочная; сохранённые цены не являются новой котировкой."
            )
        )

    @staticmethod
    def snapshot(state):
        items = [dict(item) for item in state.items.values()]
        synthetic = bool(items) and all(i["source"] == "synthetic" for i in items)
        return {
            "data_mode": "demo" if synthetic else "prototype",
            "currency": "KZT" if synthetic else None,
            "items": items,
            "total": str(sum((Decimal(i["total"]) for i in items), Decimal("0.00"))),
            "version": state.version,
            "cart_url": "/cart",
            "pending_proposal": dict(state.pending["public"])
            if state.pending and time.monotonic() < state.pending["deadline"]
            else None,
            "warning": DemoCart.warning(synthetic),
        }

    async def propose(self, state, body, validate_session=lambda: None):
        state.pending = None
        if len(state.receipts) >= 1000:
            raise CartError("SESSION_LIMIT", "Достигнут лимит операций сессии.", 429)
        previous = state.items.get(body.product_id)
        existing = previous["quantity"] if previous else 0
        product = None
        if body.operation in ("add", "set"):
            product = await self.product(body.product_id, validate_session)
            if state.items and any(i["source"] != product.source for i in state.items.values()):
                raise CartError(
                    "MIXED_SOURCES", "Реальные и синтетические товары нельзя смешивать."
                )
            if not previous and len(state.items) >= 50:
                raise CartError("CART_CAPACITY", "В корзине допускается до 50 разных товаров.")
            quantity = existing + body.quantity if body.operation == "add" else body.quantity
            if quantity > 10000 or quantity > product.quantity:
                raise CartError(
                    "STOCK_CHANGED",
                    "Количество с учётом корзины превышает остаток или лимит 10000.",
                )
        else:
            if (body.operation == "clear" and not state.items) or (
                body.operation == "remove" and not previous
            ):
                raise CartError("CART_ITEM_NOT_FOUND", "В корзине нет выбранных позиций.", 404)
            quantity = 0
        synthetic = (
            product.source == "synthetic"
            if product
            else bool(previous and previous["source"] == "synthetic")
        )
        if body.operation == "clear":
            synthetic = all(item["source"] == "synthetic" for item in state.items.values())
        price = (
            product.price
            if product
            else Decimal(previous["unit_price"])
            if previous
            else Decimal(0)
        )
        # Repricing an existing line is explicitly displayed before confirmation.
        proposal = {
            "proposal_id": secrets.token_urlsafe(24),
            "operation": body.operation,
            "item_count": len(state.items),
            "product_id": body.product_id,
            "name": product.name if product else previous["name"] if previous else "Все товары",
            "article": product.article if product else previous["article"] if previous else "",
            "quantity": body.quantity if body.quantity is not None else 0,
            "previous_quantity": existing,
            "resulting_quantity": quantity,
            "previous_unit_price": previous["unit_price"] if previous else None,
            "previous_total": self.snapshot(state)["total"]
            if body.operation == "clear"
            else previous["total"]
            if previous
            else "0.00",
            "unit": "шт" if synthetic else None,
            "unit_price": format(price, ".2f") if synthetic else str(price),
            "total": str(price * (body.quantity or 0)),
            "resulting_total": str(price * quantity),
            "currency": "KZT" if synthetic else None,
            "source": product.source if product else previous["source"] if previous else None,
            "available_quantity": str(product.quantity) if product else None,
            "observed_at": product.observed_at.isoformat() if product else None,
            "expires_at": (datetime.now(UTC) + timedelta(seconds=self.proposal_ttl)).isoformat(),
            "data_mode": "demo" if synthetic else "prototype",
            "cart_version": state.version,
            "warning": self.warning(synthetic),
        }
        validate_session()
        state.pending = {"public": proposal, "deadline": time.monotonic() + self.proposal_ttl}
        return proposal

    async def confirm(self, state, proposal_id, key, validate_session=lambda: None):
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
        self.check_deadline(state, pending)
        if len(state.receipts) >= 1000:
            raise CartError("SESSION_LIMIT", "Достигнут лимит операций сессии.", 429)
        proposal = pending["public"]
        product = None
        try:
            if proposal["operation"] in ("add", "set"):
                product = await self.product(proposal["product_id"], validate_session)
                if product.source != proposal["source"]:
                    raise CartError("DATA_CONFLICT", "Источник товара изменился.")
                if product.price != Decimal(proposal["unit_price"]):
                    raise CartError(
                        "PRICE_CHANGED",
                        "Цена изменилась. Создайте новое предложение и подтвердите новую цену.",
                    )
                if (
                    product.quantity != Decimal(proposal["available_quantity"])
                    or proposal["resulting_quantity"] > product.quantity
                ):
                    raise CartError(
                        "STOCK_CHANGED", "Остаток изменился. Нужно новое подтверждение."
                    )
                if product.name != proposal["name"] or product.article != proposal["article"]:
                    raise CartError("DATA_CONFLICT", "Описание товара изменилось.")
            validate_session()
            self.check_deadline(state, pending)
            if state.version != proposal["cart_version"]:
                raise CartError("CART_CHANGED", "Корзина изменилась. Создайте новое предложение.")
        except CartError:
            state.pending = None
            raise
        # Caller holds the session lock; no await between validation and mutation.
        if proposal["operation"] == "clear":
            state.items.clear()
        elif proposal["operation"] == "remove":
            state.items.pop(proposal["product_id"], None)
        else:
            quantity = proposal["resulting_quantity"]
            state.items[product.id] = {
                "product_id": product.id,
                "name": product.name,
                "article": product.article,
                "quantity": quantity,
                "unit": proposal["unit"],
                "unit_price": proposal["unit_price"],
                "total": format(product.price * quantity, ".2f")
                if product.source == "synthetic"
                else str(product.price * quantity),
                "source": product.source,
                "currency": proposal["currency"],
                "checked_at": product.observed_at.isoformat(),
            }
        state.version += 1
        state.receipts[key] = proposal_id
        state.used_proposals.add(proposal_id)
        state.pending = None
        return {"cart": self.snapshot(state), "cart_url": "/cart", "replayed": False}

    @staticmethod
    def check_deadline(state, pending):
        if time.monotonic() >= pending["deadline"]:
            state.pending = None
            raise CartError("PROPOSAL_EXPIRED", "Создайте и подтвердите новое предложение.")

    @staticmethod
    def cancel(state, proposal_id):
        if not state.pending or state.pending["public"]["proposal_id"] != proposal_id:
            raise CartError("PROPOSAL_NOT_FOUND", "Предложение не найдено.", 404)
        state.pending = None
        return {"cancelled": True}
