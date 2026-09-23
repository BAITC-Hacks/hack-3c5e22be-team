import asyncio
import secrets
import time
from dataclasses import dataclass, field

from app.ai import Interpreter
from app.catalog import Catalog
from app.models import ChatResponse, Product
from app.terms import PurchaseTerms


@dataclass
class Session:
    expires_at: float
    history: list[dict] = field(default_factory=list)
    product_ids: list[int] = field(default_factory=list)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class Sessions:
    def __init__(self, ttl: int, limit: int):
        self.ttl, self.limit = ttl, limit
        self.items: dict[str, Session] = {}

    def create(self) -> str:
        self.items = {k: v for k, v in self.items.items() if v.expires_at > time.monotonic()}
        if len(self.items) >= self.limit:
            raise OverflowError("Session capacity reached")
        token = secrets.token_urlsafe(32)
        self.items[token] = Session(time.monotonic() + self.ttl)
        return token

    def get(self, token: str) -> Session | None:
        session = self.items.get(token)
        if session and session.expires_at > time.monotonic():
            return session
        self.items.pop(token, None)
        return None


def describe(product: Product) -> str:
    lines = [f"{product.name} — артикул {product.article}."]
    if product.price is not None:
        lines.append(f"Цена по снимку каталога: {product.price}; валюта в API не указана.")
    if product.quantity is None:
        lines.append("Остаток неизвестен: подробная карточка ещё не загружена.")
    else:
        lines.append(
            f"Общий остаток по снимку: {product.quantity}. Возможность отгрузки нужно уточнить."
        )
    labels = {
        "current": "Ток",
        "voltage": "Напряжение",
        "poles": "Полюса",
        "breaking_capacity": "Отключающая способность",
        "mounting": "Установка",
        "brand": "Бренд",
    }
    conflicting_current = any(
        issue.code == "conflicting_current" for issue in product.quality_issues
    )
    for key, value in product.attributes.items():
        if key == "current" and conflicting_current:
            lines.append("Ток: противоречивые данные; значение требует уточнения.")
        else:
            lines.append(f"{labels[key]}: {value}")
    lines.extend(issue.message for issue in product.quality_issues)
    lines.append("Данные о сертификате не подключены; это не означает, что сертификата нет.")
    lines.append(f"Данные получены: {product.observed_at.isoformat()}.")
    if product.stale:
        lines.append("Снимок устарел; цену и наличие необходимо обновить.")
    if product.source == "synthetic":
        lines.insert(0, "Тестовые синтетические данные — не предложение ekt.kz.")
    return "\n".join(lines)


class ChatService:
    def __init__(self, catalog: Catalog, interpreter: Interpreter, terms: PurchaseTerms):
        self.catalog, self.interpreter, self.terms = catalog, interpreter, terms

    async def reply(self, session: Session, message: str) -> ChatResponse:
        intent, mode, warning = await self.interpreter.resolve(
            message,
            session.history,
            session.product_ids,
        )
        response = ChatResponse(message="", mode=mode, warnings=[warning] if warning else [])
        if intent.intent == "cart":
            response.message = (
                "Корзина пока не подключена. Товары не добавлены. "
                "Для добавления потребуются выбор товара, количества и явное подтверждение."
            )
            response.cart_action = "integration_required"
        elif intent.intent == "terms":
            response.message = self.terms.answer(intent.topic)
        elif intent.intent == "clarify":
            response.message = "Укажите артикул, бренд или характеристики нужного электротовара."
        else:
            selected = self.catalog.get(intent.product_id) if intent.product_id else None
            products = [selected] if selected else self.catalog.search(intent.query)
            response.products = products
            if not products:
                response.message = (
                    "Товар не найден в загруженной выборке. Уточните артикул или название. "
                    "Это не означает, что товара нет в полном каталоге."
                )
            elif len(products) > 1:
                response.message = (
                    "Найдено несколько товаров. Выберите позицию или уточните артикул."
                )
            else:
                product = products[0]
                response.message = describe(product)
                if intent.intent == "alternatives" or product.quantity == 0:
                    alternatives = self.catalog.alternatives(product.id)
                    response.alternatives = alternatives.items
                    response.warnings.extend(alternatives.warnings)
                    response.message += (
                        "\nНайдены кандидаты для сравнения."
                        if alternatives.items
                        else "\nПроверенные аналоги не найдены."
                    )
            if products:
                session.product_ids = [p.id for p in products]
        session.history = [
            *session.history,
            {"role": "user", "content": message},
            {"role": "assistant", "content": response.message[:2000]},
        ][-6:]
        return response
