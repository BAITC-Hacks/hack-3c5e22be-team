import json
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from app.models import Alternative, AlternativesResult, Product
from app.normalize import normalize, text_key


class Catalog:
    def __init__(self, path: Path, stale_seconds: int = 300):
        self.path = path
        self.stale_seconds = stale_seconds
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS products (
                id INTEGER PRIMARY KEY, raw TEXT NOT NULL,
                observed_at TEXT NOT NULL, source TEXT NOT NULL)""")

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.path)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def upsert(self, raw: dict, observed_at: datetime, source: str = "ekt_api"):
        # Validate before persisting; a list refresh must not relabel old stock as fresh.
        normalize(raw, observed_at, source, self.stale_seconds)
        with self.connect() as db:
            existing = db.execute("SELECT raw FROM products WHERE id=?", (raw["id"],)).fetchone()
            if (
                existing
                and any(key in json.loads(existing[0]) for key in ("properties", "quantity"))
                and not any(key in raw for key in ("properties", "quantity"))
            ):
                return
            db.execute(
                "INSERT OR REPLACE INTO products VALUES (?, ?, ?, ?)",
                (
                    raw["id"],
                    json.dumps(raw, ensure_ascii=False),
                    observed_at.isoformat(),
                    source,
                ),
            )

    def _product(self, row) -> Product:
        return normalize(
            json.loads(row[0]), datetime.fromisoformat(row[1]), row[2], self.stale_seconds
        )

    def get(self, product_id: int) -> Product | None:
        with self.connect() as db:
            row = db.execute(
                "SELECT raw, observed_at, source FROM products WHERE id=?", (product_id,)
            ).fetchone()
        return self._product(row) if row else None

    def all(self) -> list[Product]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT raw, observed_at, source FROM products ORDER BY id"
            ).fetchall()
        return [self._product(row) for row in rows]

    def search(self, query: str, limit: int = 5) -> list[Product]:
        tokens = re.findall(r"[\w.,]+", query.casefold().replace("ё", "е"))
        if not tokens:
            return []
        matches = []
        for product in self.all():
            identifiers = {
                str(product.id),
                product.article.casefold(),
                (product.supplier_article or "").casefold(),
            }
            if query.strip().casefold() in identifiers:
                matches.append((1000, product))
                continue
            haystack = (
                (product.name + " " + product.article + " " + (product.supplier_article or ""))
                .casefold()
                .replace("ё", "е")
            )
            if all(token in haystack for token in tokens):
                matches.append((len(tokens), product))
        return [p for _, p in sorted(matches, key=lambda item: (-item[0], item[1].id))[:limit]]

    def alternatives(self, product_id: int) -> AlternativesResult:
        original = self.get(product_id)
        result = AlternativesResult(product_id=product_id)
        required = ("current", "voltage", "poles", "breaking_capacity", "mounting")
        if not original:
            result.warnings.append("Товар отсутствует в загруженной выборке.")
            return result
        if original.quality_issues:
            result.warnings.append(
                "Подбор заблокирован: характеристики исходного товара противоречивы."
            )
            return result
        # Initial category-specific rules, deliberately conservative.
        if text_key(original.category or "") != text_key("Автоматический выключатель") or any(
            not original.attributes.get(k) for k in required
        ):
            result.warnings.append("Недостаточно проверенных характеристик для подбора аналога.")
            return result
        for candidate in self.all():
            if (
                candidate.id == original.id
                or candidate.quality_issues
                or candidate.source != original.source
                or text_key(candidate.category or "") != text_key(original.category or "")
                or candidate.quantity is None
                or candidate.quantity <= 0
            ):
                continue
            if all(
                text_key(original.attributes[k]) == text_key(candidate.attributes.get(k, ""))
                for k in required
            ):
                result.items.append(
                    Alternative(
                        product=candidate,
                        reasons=[
                            f"Совпадает {label}: {original.attributes[key]}"
                            for key, label in (
                                ("current", "номинальный ток"),
                                ("voltage", "напряжение"),
                                ("poles", "число полюсов"),
                                ("breaking_capacity", "отключающая способность"),
                                ("mounting", "способ установки"),
                            )
                        ],
                    )
                )
        result.items = result.items[:5]
        result.warnings.append(
            "Это кандидаты по доступным характеристикам, а не подтверждение полной совместимости. "
            "Уточните применение и возможность отгрузки; остатки из снимка требуют проверки."
        )
        return result
