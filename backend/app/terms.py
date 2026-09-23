import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, ValidationError


class PurchaseTerms(BaseModel):
    verified: bool = False
    source_url: str | None = None
    checked_at: date | None = None
    expires_at: date | None = None
    payment: str | None = Field(default=None, max_length=2000)
    delivery: str | None = Field(default=None, max_length=2000)
    minimum: str | None = Field(default=None, max_length=2000)

    def answer(self, topic: Literal["payment", "delivery", "minimum", "all"]) -> str:
        if self.expires_at and datetime.now(UTC).date() > self.expires_at:
            return (
                "Срок проверки условий покупки истёк. "
                "Уточните актуальные условия у менеджера ekt.kz."
            )
        if not self.verified or not self.source_url:
            return (
                "Подтверждённые условия покупки ещё не подключены. Уточните их у менеджера ekt.kz."
            )
        topics = ["payment", "delivery", "minimum"] if topic == "all" else [topic]
        labels = {"payment": "Оплата", "delivery": "Доставка", "minimum": "Минимальная партия"}
        return (
            "\n".join(
                f"{labels[t]}: {getattr(self, t) or 'Нужно уточнить у менеджера.'}" for t in topics
            )
            + f"\nИсточник: {self.source_url}"
            + (f"\nПроверено: {self.checked_at.isoformat()}." if self.checked_at else "")
        )


def load_terms(path: Path) -> PurchaseTerms:
    if not path.exists():
        return PurchaseTerms()
    try:
        return PurchaseTerms.model_validate(json.loads(path.read_text(encoding="utf-8-sig")))
    except (OSError, ValueError, ValidationError):
        return PurchaseTerms()
