"""Token-based matching: 16 A must never match 160 A or 16 kA."""

import re
import unicodedata
from decimal import Decimal

STOPWORDS = {
    "покажи",
    "покажите",
    "найди",
    "найдите",
    "нужен",
    "нужна",
    "нужно",
    "нужны",
    "есть",
    "ли",
    "мне",
    "пожалуйста",
    "на",
    "в",
    "по",
    "для",
    "товар",
    "товара",
    "артикул",
    "артикулу",
    "характеристики",
    "наличие",
    "цена",
    "аналог",
    "аналоги",
    "замена",
    "и",
}
ALIASES = {
    "ав": "автомат",
    "авт": "автомат",
    "автоматы": "автомат",
    "автоматический": "автомат",
    "выключатели": "выключатель",
}
UNITS = {
    "a": "а",
    "ка": "ка",
    "ka": "ка",
    "ma": "ма",
    "v": "в",
    "kv": "кв",
    "w": "вт",
    "вт": "вт",
    "kw": "квт",
    "mm": "мм",
    "ма": "ма",
}


def tokens(text: str) -> set[str]:
    text = unicodedata.normalize("NFKC", text).casefold().replace("ё", "е")
    # Join only known physical units, not arbitrary words following a number.
    text = re.sub(r"(\d)\s+(ка|ka|ма|ma|кв|kv|квт|kw|вт|w|мм|mm|а|a|в|v)(?!\w)", r"\1\2", text)
    parts = re.findall(r"\d+(?:[.,]\d+)?(?:[a-zа-я]+)?|[^\W\d_]+(?:\d+)?", text)
    result = set()
    for part in parts:
        measurement = re.fullmatch(r"(\d+(?:[.,]\d+)?)([a-zа-я]+)?", part)
        if measurement:
            value = format(Decimal(measurement[1].replace(",", ".")).normalize(), "f")
            unit = measurement[2] or ""
            part = value + UNITS.get(unit, unit)
        part = ALIASES.get(part, part)
        if part not in STOPWORDS:
            result.add(part)
    return result
