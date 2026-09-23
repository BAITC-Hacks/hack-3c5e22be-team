# Контракт Backend для команды

Актуально для Backend 0.3. Это описание реализованного API. Черновик участника 2
`frontend-api-contract.md` содержит предложение будущего интерфейса; таблица соответствий ниже.

Базовый адрес разработки: `http://127.0.0.1:8000`. Полная схема: `/openapi.json`,
интерактивная документация: `/docs`. Фронтенд по умолчанию разрешён с localhost:5173.

## Каталог

| Метод | URL | Назначение |
| --- | --- | --- |
| GET | `/health` | Готовность сервера, каталог, AI, `cart_enabled` и `cart_mode` |
| GET | `/api/products?query=200300285_&limit=5` | Поиск в локальной выборке |
| GET | `/api/products/515291` | Карточка из снимка |
| GET | `/api/products/515291?refresh=true` | Проверка через API ekt.kz, если включена настройкой |
| GET | `/api/products/515291/alternatives` | Кандидаты для сравнения и предупреждения |
| GET | `/api/purchase-terms` | Условия, признак `verified` и источник |

Денежные суммы и количества — десятичные строки в JSON. `null` означает неизвестное значение,
а не ноль. `currency: null` нельзя заменять валютой по догадке.
Поля карточки: `id`, `name`, `article`, `supplier_article`, `description`, `price`,
`quantity`, `stores`, `attributes`, `quality_issues`, `url`, `image`, `source`,
`observed_at`, `stale`, `detail_loaded`, `certificate_status`, `certificates`.

`source: synthetic` показывать как тестовые данные. `stale: true` — устаревший снимок,
но даже `stale: false` не резервирует товар. Остатки складов пока не отфильтрованы по правилам продажи.
`certificate_status: unknown` означает, что источник сертификата не подключён.
`quality_issues` показывать рядом с характеристиками, не скрывать.

## Чат

1. `POST /api/chat/sessions` без тела → 201:

```json
{"session_token": "opaque-token", "expires_in": 3600}
```

2. `POST /api/chat` с заголовком `Authorization: Bearer <session_token>`:

```json
{"message": "200300285_", "request_id": "87dcf1ea-9899-4b6b-8aca-8f98a15874b7"}
```

Ответ:

```json
{
  "message_id": "server-generated-uuid",
  "message": "Ответ из данных каталога",
  "mode": "openai",
  "products": [],
  "alternatives": [],
  "warnings": [],
  "cart_action": "none"
}
```

`mode: rules` — упрощённый режим при отсутствии ключа или недоступности OpenAI.
`mode: catalog` — точный артикул/ID или выбор результата по порядку без обращения к модели.
Это штатный быстрый ответ, а не ошибка. `request_id` необязателен; если передаётся, это UUID.
Повтор того же сообщения с тем же ID возвращает сохранённый ответ без нового вызова OpenAI.
Другой текст с прежним ID получает 409 `REQUEST_ID_REUSED`. Кэш ограничен последними 20
запросами с ID внутри сессии; повтор старее этого окна может быть обработан заново.
Карточки выводить из `products`, предупреждения — из `warnings` и `quality_issues`.
`alternatives` содержит `{product, reasons, requires_verification}`.
В текущем этапе LLM извлекает намерение, а текст и карточки сервер формирует по данным;
это уменьшает риск выдуманных цен и наличия.

3. `DELETE /api/chat/sessions/current` с тем же заголовком → 204, удаляет контекст и корзину.

Храните токен только для текущей сессии (например, в памяти интерфейса).
Не добавляйте его в URL. Историю сервер ведёт сам; клиент отправляет только новое сообщение.
При 401 создайте новую сессию. Одновременный второй запрос одной сессии получает 409.
Лимит — 30 новых запросов в минуту на сессию (настраивается); превышение даёт 429.
При завершении сессии во время обработки результат не возвращается старому токену.

## Корзина прототипа

Реализована отдельная корзина приложения: предложения, подтверждения, отмена, изменение
количества, удаление и очистка. API ekt.kz используется только для чтения каталога.
Полный контракт, поля и пример: [cart-api.md](cart-api.md).

Чат дополнительно возвращает `cart_proposal`, `cart_url`, `cart_error` (по умолчанию null).
`cart_action`: none / integration_required (выключена) / proposal_required (уточнить)
 / confirmation_required (показать предложение) / view (показать ссылку).
Только отдельный POST /api/cart/confirm после кнопки пользователя изменяет позиции.

Сессию и API вызывать с `credentials: "include"`: /cart использует HttpOnly cookie,
а /api/cart — Bearer. Ссылка не содержит токен, открывается на origin Backend.
Неизвестные валюта, единица и правила реальных товаров не подставляются по догадке.

## Ошибки и проверка без ключей

Ошибки сохраняют поле `detail` и дополнительно возвращают:

```json
{"error":{"code":"CATALOG_UNAVAILABLE","message":"Каталог недоступен","retryable":true}}
```

Коды: `PRODUCT_NOT_FOUND`, `CATALOG_DISABLED`, `CATALOG_UNAVAILABLE`,
`INVALID_CATALOG_RESPONSE`, `SESSION_REQUIRED`, `SESSION_EXPIRED`, `SESSION_CAPACITY`,
`REQUEST_IN_PROGRESS`, `REQUEST_ID_REUSED`, `RATE_LIMITED`, `VALIDATION_ERROR`.
Повторяйте только подходящие для повтора запросы; не создавайте новые запросы в бесконечном цикле.

- 404: товара нет в локальной выборке; это не ответ о полном каталоге.
- 422: неверный формат запроса.
- 502: неизвестная структура карточки от партнёра.
- 503: живой каталог недоступен/отключён либо исчерпан лимит сессий.

После импорта `examples/demo_catalog.json` проверьте:

1. `query=DEMO-001`: товар с нулевым остатком.
2. `/api/products/900001/alternatives`: кандидат 900002.
3. `/api/products/900003/alternatives`: подбор блокируется конфликтом 16/25 А.
4. В чате `DEMO-002`, затем `А какие характеристики?`: сохраняется контекст позиции.
5. При DEMO_CART_ENABLED=true: выберите DEMO-002, затем `Добавь 2`; предложение не меняет корзину до отдельного подтверждения.

## Соответствие черновику участника 2

| Черновик Frontend | Реализованный Backend 0.3 |
| --- | --- |
| `text` | `message` |
| `message_id` | Реализовано |
| `request_id` | Необязательный UUID с ограниченной защитой повторов |
| Строковый `product.id` | Числовой ID партнёра; фронтенд может хранить как строку, отправляет число |
| `sku` | `article`; дополнительно `supplier_article` |
| `price.amount` | `price`, десятичная строка или `null` |
| `price.currency` | `currency`; сейчас `null`, нельзя подставлять KZT автоматически |
| `unit`, `quantity_rules` | Пока неизвестны; `minimum_quantity_raw` не является готовым правилом |
| `available_quantity` | `quantity` — общий остаток снимка, не гарантированный доступный к продаже |
| `availability` | При неизвестном/устаревшем остатке показывать unknown; доступность отгрузки не подтверждена |
| Массив `attributes` | Словарь ключ → значение; учитывать `quality_issues` |
| `image_url`, `product_url`, `checked_at` | `image`, `url`, `observed_at` плюс `stale` |
| `data_mode` | `source` у каждой карточки: `synthetic`/`ekt_api`; это не признак свежести |
| `reason` аналога | Массив `reasons` и `requires_verification: true` |
| Cookie-сессия | API: Bearer; страница /cart: HttpOnly cookie + CSRF; см. cart-api.md |
| `error.code/message/retryable` | Реализовано |
| `/api/cart/*` | Реализовано; см. cart-api.md |

Пустой список `certificates` при `certificate_status: unknown` показывать как
«Нет подтверждённых данных о сертификате», а не как доказательство его отсутствия.
Все новые поля добавлены без удаления прежних; текущие клиенты могут продолжать работу.
