# Корзина прототипа — Backend 0.3

Реализована в ветке `codex/backend-ai` на основе работы коллеги из `feat/demo-cart`.
Это отдельная корзина приложения. На ekt.kz выполняются только GET-запросы каталога;
товары в корзину магазина не переносятся, заказ и оплата не оформляются.

## Включение и данные

`DEMO_CART_ENABLED=true` включает прототип (название настройки сохранено для совместимости).
Для реальных товаров также нужны `EKT_LIVE_ENABLED=true`, `EKT_USERNAME`, `EKT_PASSWORD`.
Перед предложением и подтверждением добавления/изменения сервер читает подробную карточку EKT.
Если API недоступен, старый снимок не используется для успешного добавления.

Синтетические товары работают без внешних ключей. Для них KZT, шт, минимум 1 и шаг 1 —
условные правила демонстрации. Нужен свежий синтетический снимок. При импорте из файла время
снимка берётся из времени изменения файла; повторный импорт старого файла его не освежает.
Для локального демо можно создать свежую копию **синтетического** примера:

```powershell
# Из backend/, после установки зависимостей. Не применять к реальным снимкам.
New-Item -ItemType Directory -Force data | Out-Null
Get-Content examples/demo_catalog.json -Raw -Encoding utf8 | Set-Content data/demo-catalog.json -Encoding utf8
..\.venv\Scripts\python.exe -m app.import_catalog --files data/demo-catalog.json --source synthetic
```

У реальных товаров `currency` и `unit` остаются `null`. Поддерживаются только целые
количества 1–10000; это ограничение прототипа, а не подтверждённая кратность EKT.
Проверяется общий `quantity` из API с учётом уже добавленного количества. Доступность
складов к продаже, единицы и реальные минимальные партии неизвестны. Сумма справочная.
Реальные и синтетические товары нельзя смешивать в одной корзине.

## Авторизация и работающая ссылка

1. `POST /api/chat/sessions` → `session_token`, `expires_in`.
2. Все `/api/cart*` требуют `Authorization: Bearer <session_token>`. Cookie сама по себе
   не разрешает ни чтение, ни изменение этого API.
3. При создании сессии и успешном подтверждении устанавливается HttpOnly cookie
   `ekt_demo_session`, SameSite=Lax, Path=/cart. Токены не включаются в URL.
4. `cart_url: "/cart"` разрешать относительно **origin Backend**. Страница уже реализована:
   показывает текущую корзину, предлагает добавление по ID, изменение, удаление и очистку.
   Для каждого изменения есть отдельная кнопка подтверждения и отмена.
5. Для браузерного frontend нужны `credentials: "include"`, разрешённый CORS origin и одинаковое
   имя хоста: например, 127.0.0.1:5173 + 127.0.0.1:8000. Не смешивать localhost и 127.0.0.1.
6. Страница использует служебные `/cart/*` с HttpOnly cookie. Изменения дополнительно проверяют
   CSRF-токен и точное совпадение Origin. Внешнему frontend использовать Bearer API выше.
7. За HTTPS-прокси включить `CART_COOKIE_SECURE=true`; на прямом HTTPS Secure устанавливается автоматически.
   Текущая same-site схема не предназначена для произвольного cross-site iframe.

Ссылка сама не предоставляет доступ к чужой корзине. Другой браузер видит свою корзину или 401.
Cookie общая для вкладок/портов одного хоста: использовать одну активную сессию на профиль.
После подтверждения cookie привязывается к подтверждённой Bearer-сессии.
Контекст, корзина и ключи идемпотентности находятся **в памяти одного процесса**: TTL 3600 секунд
по умолчанию, удаление сессии и перезапуск прекращают доступ. Запускать один worker.
Все ответы корзины имеют `Cache-Control: no-store`. Для свежего содержимого страницы —
перезагрузка или «Обновить корзину»; постоянного автообновления нет.

## API

| Метод | Путь | Назначение |
| --- | --- | --- |
| GET | `/api/cart` | Текущая корзина и активное предложение |
| POST | `/api/cart/proposals` | Проверить и создать предложение; позиции не меняются |
| POST | `/api/cart/confirm` | Подтвердить конкретное предложение |
| POST | `/api/cart/cancel` | Отменить предложение; позиции не меняются |
| GET | `/cart` | Готовая страница корзины текущей браузерной сессии |

Варианты тела `POST /api/cart/proposals`:

```json
{"product_id": 900002, "quantity": 2}
{"operation": "set", "product_id": 900002, "quantity": 3}
{"operation": "remove", "product_id": 900002}
{"operation": "clear"}
```

Это четыре отдельных примера запросов. По умолчанию `operation=add`.
`add.quantity` — добавляемое количество, `set.quantity` — новое итоговое количество.
Для `remove` не передавать quantity, для `clear` не передавать ни товар, ни quantity.
Цена из браузера и дополнительные поля не принимаются. Количество — JSON integer, не строка.

Предложение содержит:
- `proposal_id`, `operation`, `product_id`, `name`, `article`;
- `quantity`, `previous_quantity`, `resulting_quantity`, `item_count`;
- `unit_price`, `previous_unit_price`, `previous_total`, `resulting_total`, `total`, `currency`, `unit`;
- `source`, `data_mode`, `available_quantity`, `observed_at`;
- `expires_at`, `cart_version`, `warning`.

Денежные поля и общий остаток — десятичные строки. `total` — цена × quantity запроса;
для подтверждения показывать **resulting_quantity и resulting_total**, учитывающие всю позицию.
Если цена уже добавленной позиции поменялась, новая цена применяется ко всей позиции только
после нового подтверждения. Показывать прежние и новые цену/сумму. При очистке previous_total —
сумма всей корзины, resulting_total — 0; item_count — число удаляемых позиций.

Подтверждение выполняется **только из обработчика явной кнопки**:

```http
POST /api/cart/confirm
Authorization: Bearer <session_token>
Idempotency-Key: <UUID>
Content-Type: application/json

{"proposal_id": "..."}
```

Успех: `{"cart": {...}, "cart_url": "/cart", "replayed": false}`.
Повтор той же пары ключ + предложение возвращает **текущую** корзину с `replayed: true`,
не добавляя товар второй раз. При сетевом сбое повторять тот же ключ. Другой ключ для
использованного предложения или прежний ключ с другим предложением дают 409.

Отмена: `POST /api/cart/cancel`, тело `{"proposal_id": "..."}` → `{"cancelled": true}`.

Одновременно активно одно предложение. Новая корректная по схеме попытка его заменяет,
в том числе если бизнес-проверка нового товара завершилась ошибкой. Срок — 300 секунд
(`CART_PROPOSAL_TTL_SECONDS`). На подтверждении заново проверяются сессия, срок, версия,
источник, цена, название/артикул и остаток. Изменившийся остаток, даже больший, требует нового
предложения. Удаление/очистка работают без каталога. Лимиты: 50 позиций, 1000 успешных изменений
за сессию, 60 запросов изменения в минуту; последнее настраивается.

`GET /api/cart`: `{data_mode, currency, items, total, version, cart_url, pending_proposal, warning}`.
Элемент items: `{product_id, name, article, quantity, unit, unit_price, total, source, currency, checked_at}`.
Цена сохранена на момент подтверждения; чтение корзины не запрашивает заново рыночные цены.

## Чат

После выбора одного товара сообщение «Добавь 2» или «Добавь две штуки» готовит предложение.
Ответ: `cart_action: "confirmation_required"`, объект `cart_proposal`, `cart_url: "/cart"`.
Текст «да» или «подтверждаю» сам по себе **не меняет позиции**. Нужна кнопка подтверждения
конкретного серверного предложения. Модель не передаёт цену и не имеет функции записи корзины.

Другие состояния:
- `proposal_required`: уточнить один товар/количество; при ошибке есть `cart_error`.
- `view`: открыть ссылку на корзину.
- `integration_required`: прототип выключен.
- `none`: сообщение не относится к корзине.

Количество распознаётся консервативно: числа после «добавь», число с «шт/ед» и слова один–пять.
Для сложных формулировок используйте форму количества. Изменение/удаление — через API или страницу.
`request_id` чата сохраняет прежний ответ, но не продлевает срок предложения; если оно уже
истекло или заменено, подтверждение отклоняется. Проверять актуальность по API.

## Пример для фронтендера

```js
const base = 'http://127.0.0.1:8000';
async function call(path, body, extraHeaders = {}) {
  const response = await fetch(base + path, {
    method: body ? 'POST' : 'GET', credentials: 'include',
    headers: {Authorization: 'Bearer ' + token, 'Content-Type': 'application/json', ...extraHeaders},
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await response.json();
  if (!response.ok) throw data.error;
  return data;
}
// Сессию создавать с credentials: 'include'; token держать в памяти интерфейса.
const proposal = await call('/api/cart/proposals', {product_id: 900002, quantity: 2});
// Показать товар, previous/resulting_quantity, цену, previous/resulting_total и warning.
const key = crypto.randomUUID();
async function onExplicitConfirm() {
  const result = await call('/api/cart/confirm', {proposal_id: proposal.proposal_id}, {'Idempotency-Key': key});
  return new URL(result.cart_url, base).href;
}
```

В ветках `codex/frontend-chat` и `codex/frontend-backend-v02` на момент интеграции использовалось `credentials: "omit"`
и не было обработчиков корзины. Участнику 2 нужно переключить credentials, добавить кнопку
количества/подтверждения и обработку новых cart_action. Готовая /cart позволяет проверять
сценарий до этих изменений. Старые поля ответа чата сохранены.

## Ошибки

Единый формат: `{detail, error: {code, message, retryable}}`, включая валидацию 422.
`retryable` не означает, что можно молча подтверждать новое предложение.

- 401: SESSION_EXPIRED — завершённая/неизвестная сессия.
- 403: CSRF_INVALID / ORIGIN_FORBIDDEN.
- 404: PRODUCT_NOT_FOUND, CART_ITEM_NOT_FOUND, PROPOSAL_NOT_FOUND (включая чужое/заменённое).
- 409: PRICE_CHANGED, STOCK_CHANGED, DATA_CONFLICT, MIXED_SOURCES, CART_CHANGED,
  PROPOSAL_EXPIRED, PROPOSAL_USED, IDEMPOTENCY_CONFLICT, CART_CAPACITY.
- 422: VALIDATION_ERROR.
- 429: RATE_LIMITED, SESSION_LIMIT.
- 502: INVALID_CATALOG_RESPONSE.
- 503: CART_DISABLED, CATALOG_DISABLED, CATALOG_UNAVAILABLE.

После PRICE_CHANGED / STOCK_CHANGED / PROPOSAL_EXPIRED создать **новое** предложение,
показать его и запросить **новое** подтверждение. Готовая страница выполняет этот переход.
Нет оплаты, резерва остатков между покупателями, подтверждённых правил складов, постоянного
хранилища или интеграции с корзиной ekt.kz. Принятие прототипа по критериям кейса — решение жюри.
