# Демонстрационная корзина — контракт интеграции

Реализация совместима с FastAPI Backend `codex/backend-ai@33f446d` и его `Sessions`,
`Catalog`, `session.lock`. Ответ участника Backend на согласование пока ожидается.
Это технически проверенный контракт, не утверждение о согласовании с партнёром ekt.kz.

## Режим и данные

`DEMO_CART_ENABLED=true` включает корзину. По умолчанию она отключена.
Данные — исключительно `source: synthetic` из `examples/demo_catalog.json`.
KZT, единица «шт», минимум 1, шаг 1 — явно заданные правила этой демонстрации.
Они не выводятся из API ekt.kz. Валюта каталога по-прежнему может быть `null`.
Корзина не резервирует общий остаток, не оформляет заказ и не пишет в магазин.

Пользовательский пример страницы из 20 товаров содержит id, name, article, price,
image, url, url_api_detail и offers; количества, валюты и единиц в нём нет.
`offers: []` не означает нулевой остаток. Такие реальные позиции в демо-корзину не принимаются.

## Авторизация и ссылка

1. `POST /api/chat/sessions` возвращает прежние `session_token`, `expires_in`.
   При включённой демо-корзине выдаётся cookie `ekt_demo_session`, HttpOnly,
   SameSite=Lax, Path=/cart. На HTTPS ставится Secure; за TLS-прокси задайте
   `CART_COOKIE_SECURE=true`. Токен не входит в URL.
2. Все `/api/cart*` требуют `Authorization: Bearer <session_token>`.
   Cookie не авторизует ни чтение API, ни изменения. Фронтенд отправляет изменения
   только после нажатия явной кнопки подтверждения конкретного предложения.
3. `GET /cart` — read-only HTML, определяется cookie текущего браузера. После
   успешного confirm cookie привязывается к подтверждённой Bearer-сессии, включая replay.
   Это предотвращает случай, когда другая вкладка ранее создала новую сессию.
4. `cart_url` всегда `/cart`: разрешать относительно **origin Backend**, не фронтенда.
   Ссылка отображает текущее содержимое при каждом открытии, не сохранённый снимок.
   Ответы имеют `Cache-Control: no-store`. Автообновления уже открытой страницы нет;
   используйте ссылку «Обновить корзину» или перезагрузку.
5. Другая браузерная сессия видит свою корзину или 401, даже по скопированной ссылке.
   Истечение/отзыв сессии и перезапуск сервера возвращают 401, а не создают новую корзину молча.

Cookie общая для вкладок одного браузера; используйте одну активную сессию на профиль.
Для двух независимых покупателей нужны разные профили/инкогнито. SameSite=Lax рассчитан
на один сайт (same-site), не произвольное cross-site встраивание. На localhost используйте
одинаковое имя хоста фронтенда и Backend; не смешивайте localhost и 127.0.0.1.
Запросы с фронтенда выполняются с `credentials: 'include'`; CORS разрешает credentials
и Idempotency-Key только для явно перечисленных origins. Токен хранить в памяти интерфейса.

## Маршруты

| Метод | Путь | Тело / результат |
| --- | --- | --- |
| POST | `/api/cart/proposals` | `{ "product_id": 900002, "quantity": 2 }` → предложение |
| POST | `/api/cart/confirm` | `{ "proposal_id": "..." }` + `Idempotency-Key` → `{cart, cart_url, replayed}` |
| GET | `/api/cart` | Текущее `{data_mode, currency, items, total, version, cart_url, warning}` |
| GET | `/cart` | HTML корзины cookie-сессии |

Предложение содержит `proposal_id`, `product_id`, `name`, `article`, `quantity`, `unit`,
`unit_price`, `total`, `currency`, `expires_at`, `data_mode: demo`. Денежные значения —
строки с двумя знаками, количество — положительное целое, максимум 10000.
Цена из браузера и дополнительные поля не принимаются. Текущая версия поддерживает
одну позицию на предложение. Создание нового предложения заменяет предыдущее.

Позиция корзины: `product_id`, `name`, `article`, `quantity`, `unit`, `unit_price`, `total`.
Цена фиксируется в момент подтверждения. Если цена уже добавленной позиции изменилась,
новое добавление блокируется до новой демо-сессии, чтобы не переоценивать старое количество
без отдельного согласия. Ссылка показывает сохранённую корзину, не новую рыночную котировку.

Предложение действует `CART_PROPOSAL_TTL_SECONDS` (по умолчанию 300). Подтверждение заново
проверяет свежесть снимка, источник, конфликты, цену, название/артикул и остаток, включая уже
добавленное количество. Изменение остатка даже в большую сторону требует нового предложения.
После конфликта/истечения старое предложение недействительно. Изменения атомарны относительно
сессии; одновременные подтверждения не могут превысить остаток в этой корзине.

`Idempotency-Key`: 8–128 ASCII букв/цифр, `-`, `_`; UUID подходит. Повтор того же ключа
и предложения не пишет второй раз и возвращает **текущую** корзину с `replayed: true`.
Другой ключ для использованного предложения и тот же ключ для другого предложения — 409.
Лимит — 1000 подтверждений на сессию. Не очищайте ключ при сетевом сбое; повторите тот же запрос.

## Пример для фронтенда

```js
const base = 'http://127.0.0.1:8000';
const sessionResponse = await fetch(`${base}/api/chat/sessions`, {
  method: 'POST', credentials: 'include',
});
if (!sessionResponse.ok) throw new Error('Не удалось создать сессию');
const {session_token} = await sessionResponse.json();
const headers = {'Authorization': `Bearer ${session_token}`, 'Content-Type': 'application/json'};
const proposalResponse = await fetch(`${base}/api/cart/proposals`, {
  method: 'POST', credentials: 'include', headers,
  body: JSON.stringify({product_id: 900002, quantity: 2}),
});
if (!proposalResponse.ok) throw new Error('Предложение недоступно');
const proposal = await proposalResponse.json();
// Показать все поля предложения, включая цену, единицу и количество.
// Вызвать следующую функцию ТОЛЬКО из обработчика явного подтверждения пользователя.
const key = crypto.randomUUID();
async function onExplicitConfirmation() {
  const response = await fetch(`${base}/api/cart/confirm`, {
    method: 'POST', credentials: 'include',
    headers: {...headers, 'Idempotency-Key': key},
    body: JSON.stringify({proposal_id: proposal.proposal_id}),
  });
  if (!response.ok) throw new Error('Не добавлено: обработайте ошибку сервера');
  const result = await response.json();
  return new URL(result.cart_url, base).href;
}
```

Видимые значения из каталога отображать как текст, не HTML. Кнопку блокировать на время
запроса; это удобство интерфейса, серверная идемпотентность остаётся обязательной.

## Ошибки и проверка

Бизнес-ошибка: `{ "error": { "code": "PRICE_CHANGED", "message": "...", "retryable": false } }`.
Формат валидации тела пока стандартный FastAPI 422 `{detail: [...]}`; фронтенд обрабатывает оба.

- 401: SESSION_EXPIRED; повторный вход только по решению интерфейса, корзина не переносится.
- 404: PRODUCT_NOT_FOUND, PROPOSAL_NOT_FOUND (также чужое или заменённое предложение).
- 409: PRICE_CHANGED, STOCK_CHANGED, DATA_CONFLICT, LIVE_CART_UNAVAILABLE,
  PROPOSAL_EXPIRED, PROPOSAL_USED, IDEMPOTENCY_CONFLICT.
- 422: неверное количество, лишние поля, отсутствующий/неверный Idempotency-Key.
- 429: SESSION_LIMIT.
- 503: CART_DISABLED, CATALOG_UNAVAILABLE.

Из `backend/`: `python -m pytest -q` и `python -m ruff check .`.
Тесты проверяют отсутствие записи до подтверждения, повтор/параллельные подтверждения,
нехватку и изменение остатка, смену цены, срок предложения, изоляцию сессий, отзыв/истечение,
свежесть ссылки, CORS, cookie-only CSRF, валидацию, блокировку live и экранирование HTML.

До production нужны реальные складские правила и единицы, постоянное транзакционное
хранилище сессий/корзин/идемпотентности, аутентификация, ограничение запросов и реальный API
магазина. Текущий адаптер предназначен для одного процесса и локального командного демо.
