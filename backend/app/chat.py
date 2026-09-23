import asyncio
import re
import secrets
import time
from collections import OrderedDict
from dataclasses import dataclass, field

from app.ai import Interpreter
from app.cart import CartState
from app.catalog import Catalog
from app.models import ChatResponse, Intent, Product
from app.terms import PurchaseTerms


@dataclass
class Session:
    expires_at: float
    history: list[dict] = field(default_factory=list)
    product_ids: list[int] = field(default_factory=list)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    requests: list[float] = field(default_factory=list)
    replies: OrderedDict = field(default_factory=OrderedDict)
    cart: CartState = field(default_factory=CartState)
    csrf_token: str = field(default_factory=lambda: secrets.token_urlsafe(32))
    cart_requests: list[float] = field(default_factory=list)


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
        "trip_curve": "Характеристика срабатывания",
        "device_type": "Тип устройства",
        "residual_current": "Дифференциальный ток",
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
        # Exact identifiers and explicit ordinal selection are deterministic and need no LLM.
        exact = self.catalog.search(message, 1)
        selected = None
        if exact and message.strip().casefold() in {
            str(exact[0].id),
            exact[0].article.casefold(),
            (exact[0].supplier_article or "").casefold(),
        }:
            selected = exact[0].id
        ordinal = re.fullmatch(
            r"(?:выбираю\s+|покажи\s+)?(первый|второй|третий|четвертый|пятый|[1-5])(?:\s+товар)?[.!]?",
            message.strip().casefold().replace("ё", "е"),
        )
        if ordinal:
            order = {"первый": 1, "второй": 2, "третий": 3, "четвертый": 4, "пятый": 5}
            index = order.get(ordinal[1], int(ordinal[1]) if ordinal[1].isdigit() else 0) - 1
            if 0 <= index < len(session.product_ids):
                selected = session.product_ids[index]
        if selected:
            intent = Intent(intent="details", query="", product_id=selected, topic="all")
            mode, warning = "catalog", None
        else:
            intent, mode, warning = await self.interpreter.resolve(
                message,
                session.history,
                session.product_ids,
            )
        guarded = Interpreter.rules(message, session.product_ids)
        if guarded.intent == "cart":
            # Enforce the missing cart integration independently of model classification.
            intent = guarded
        elif guarded.intent == "terms" and guarded.topic == "all":
            intent = guarded  # Never omit a clearly requested second purchase topic.
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
            # An explicit identifier in the new message takes precedence over stale AI context.
            explicit = [
                p
                for p in self.catalog.all()
                if any(
                    ident and re.search(r"(?<!\w)" + re.escape(ident) + r"(?!\w)", message, re.I)
                    for ident in (p.article, p.supplier_article, str(p.id))
                )
            ]
            if explicit:
                products = explicit[:5]
            response.products = products
            if not products:
                session.product_ids = []
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
                session.product_ids.extend(a.product.id for a in response.alternatives)
                session.product_ids = list(dict.fromkeys(session.product_ids))
        context = "; ".join(
            f"ID {p.id}, артикул {p.article}: {p.name}"
            for p in [*response.products, *(a.product for a in response.alternatives)]
        )
        session.history = [
            *session.history,
            {"role": "user", "content": message},
            {"role": "assistant", "content": (response.message[:1500] + "\n" + context)[:3000]},
        ][-6:]
        return response
