import { CatalogApi } from './api.js';

const api = new CatalogApi();
const $ = id => document.getElementById(id);
let busy = false;
let retryAction = null;
let sessionExpired = false;
const renderedMessages = new Set();
const labels = { current: 'Номинальный ток', voltage: 'Напряжение', poles: 'Полюса', breaking_capacity: 'Отключающая способность', mounting: 'Установка', brand: 'Бренд', trip_curve: 'Характеристика срабатывания', device_type: 'Тип устройства', residual_current: 'Дифференциальный ток' };
function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = String(text);
  return node;
}
function notice(parent, text) { parent.append(el('div', 'notice', text)); }
function safeLink(parent, text, url) {
  try {
    const parsed = new URL(url);
    if (!['https:', 'http:'].includes(parsed.protocol)) return;
    const link = el('a', '', text);
    link.href = parsed.href; link.target = '_blank'; link.rel = 'noopener noreferrer';
    parent.append(link);
  } catch { /* Missing or invalid source URL: no invented link. */ }
}
function message(role, text) {
  $('welcome').hidden = true;
  const node = el('article', `message ${role}`);
  node.append(el('div', 'message-label', role === 'user' ? 'ВЫ' : '✧ ЭКТ ПОМОЩНИК'));
  if (text) node.append(el('p', '', text));
  $('messages').append(node);
  return node;
}
function decimal(value) {
  // Keep server decimal strings intact: no rounding or inferred units/currency.
  return value == null ? null : String(value);
}
function productCard(product, alternative = null) {
  const card = el('article', 'product');
  const badges = el('div', 'badges');
  if (product.source === 'synthetic') badges.append(el('span', 'badge', 'ТЕСТОВЫЕ ДАННЫЕ'));
  if (product.stale) badges.append(el('span', 'badge warning', 'УСТАРЕВШИЙ СНИМОК'));
  if (alternative) badges.append(el('span', 'badge', 'КАНДИДАТ В АНАЛОГИ'));
  card.append(badges, el('h3', '', product.name), el('div', 'sku', `Артикул: ${product.article || 'не указан'}`));
  if (product.supplier_article) card.append(el('div', 'sku', `Артикул поставщика: ${product.supplier_article}`));
  card.append(el('div', 'product-price', product.price == null ? 'Цена неизвестна' : `${decimal(product.price)}${product.currency ? ` ${product.currency}` : ' · валюта не указана'}`));
  card.append(el('div', 'stock', product.quantity == null ? 'Остаток неизвестен' : `Общий остаток по снимку: ${decimal(product.quantity)}`));
  card.append(el('div', 'mode', 'Возможность отгрузки и единицу измерения уточняйте у менеджера.'));
  if (!product.detail_loaded) notice(card, 'Подробная карточка ещё не загружена.');
  for (const issue of product.quality_issues || []) notice(card, issue.message);
  if (alternative) {
    for (const reason of alternative.reasons || []) card.append(el('p', 'description', `✓ ${reason}`));
    if (alternative.requires_verification) notice(card, 'Требуется проверка совместимости специалистом. Это кандидат для сравнения.');
  }
  const details = el('details'); details.append(el('summary', '', 'Характеристики и документы'));
  const list = el('dl');
  for (const [key, value] of Object.entries(product.attributes || {})) {
    const row = el('div');
    const conflict = key === 'current' && (product.quality_issues || []).some(issue => issue.code === 'conflicting_current');
    row.append(el('dt', '', labels[key] || key), el('dd', '', conflict ? 'Конфликт данных — требуется уточнение' : value)); list.append(row);
  }
  details.append(list);
  if (product.description) details.append(el('p', 'description', product.description));
  if (product.certificate_status === 'unknown') details.append(el('p', 'description', 'Источник сертификатов не подключён. Это не означает, что сертификата нет.'));
  for (const [index, url] of (product.certificates || []).entries()) safeLink(details, `Сертификат ${index + 1}`, url);
  if ((product.stores || []).length) {
    details.append(el('p', 'description', 'Остатки складов (не подтверждают доступность к продаже):'));
    for (const store of product.stores) details.append(el('p', 'description', `${store.name}: ${decimal(store.quantity) ?? 'неизвестно'}`));
  }
  const observed = new Date(product.observed_at);
  if (!Number.isNaN(observed.getTime())) details.append(el('div', 'mode', `Снимок: ${observed.toLocaleString('ru-RU')}`));
  if (product.url) safeLink(details, 'Открыть источник ↗', product.url);
  card.append(details);
  const actions = el('div', 'product-actions');
  const select = el('button', '', 'Обсудить товар'); select.type = 'button';
  select.onclick = () => send(String(product.id));
  const alternatives = el('button', '', 'Сравнить кандидатов'); alternatives.type = 'button';
  alternatives.onclick = () => compare(product);
  actions.append(select, alternatives); card.append(actions);
  return card;
}
function renderAlternatives(parent, alternatives) {
  if (!alternatives.length) return;
  parent.append(el('h3', 'section-caption', 'Возможные замены · сравните характеристики'));
  const cards = el('div', 'cards');
  alternatives.forEach(item => cards.append(productCard(item.product, item)));
  parent.append(cards);
}
function setBusy(value) {
  busy = value;
  $('pending').hidden = !value;
  $('messages').setAttribute('aria-busy', String(value));
  document.querySelectorAll('button').forEach(button => { button.disabled = value; });
  $('message').disabled = value;
}
async function perform(action) {
  if (busy) return;
  $('error').hidden = true; setBusy(true);
  try { await action(); retryAction = null; }
  catch (error) {
    $('error-text').textContent = error.message;
    $('error').hidden = false;
    if (error.status === 401) sessionExpired = true;
    retryAction = error.retryable && error.status !== 401 ? action : null;
    $('retry').hidden = !retryAction;
    $('retry').textContent = error.code === 'RATE_LIMITED' ? 'Повторить позже' : 'Повторить';
  } finally {
    setBusy(false);
    $('message').focus({ preventScroll: true });
  }
}
async function send(text) {
  text = text.trim();
  if (busy || !text || text.length > 2000) return;
  if (sessionExpired) {
    $('error-text').textContent = 'Нажмите «Новый диалог»: прежний контекст больше недоступен.';
    $('error').hidden = false; return;
  }
  message('user', text); $('message').value = '';
  // One ID per user action; the retry closure keeps it after a lost response.
  const requestId = crypto.randomUUID();
  const action = async () => {
    const response = await api.chat(text, requestId);
    if (response.message_id && renderedMessages.has(response.message_id)) return;
    const node = message('assistant', response.message);
    const cards = el('div', 'cards');
    (response.products || []).forEach(product => cards.append(productCard(product)));
    if (cards.childElementCount) node.append(cards);
    renderAlternatives(node, response.alternatives || []);
    for (const warning of response.warnings || []) notice(node, warning);
    if (response.cart_action === 'integration_required') notice(node, 'Корзина пока не подключена. Ничего не добавлено, заказ не создан.');
    const modes = {
      rules: 'Базовый режим · поиск и ответы по данным каталога',
      catalog: 'Ответ из каталога · без обращения к ИИ',
      openai: 'ИИ-помощник · факты из каталога',
    };
    node.append(el('div', 'mode', modes[response.mode] || 'Ответ сервера'));
    if (response.message_id) {
      node.dataset.messageId = response.message_id;
      renderedMessages.add(response.message_id);
    }
    node.scrollIntoView({ behavior: 'smooth', block: 'start' });
  };
  await perform(action);
}
async function compare(product) {
  await perform(async () => {
    const response = await api.alternatives(product.id);
    const node = message('assistant', `Кандидаты для «${product.name}»`);
    renderAlternatives(node, response.items || []);
    if (!response.items?.length) notice(node, 'Проверенные кандидаты не найдены в загруженной выборке.');
    for (const warning of response.warnings || []) notice(node, warning);
    node.scrollIntoView({ behavior: 'smooth', block: 'start' });
  });
}
$('chat-form').addEventListener('submit', event => { event.preventDefault(); send($('message').value); });
$('message').addEventListener('keydown', event => {
  if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) { event.preventDefault(); send($('message').value); }
});
document.querySelectorAll('[data-prompt]').forEach(button => { button.onclick = () => send(button.dataset.prompt); });
$('retry').onclick = () => { if (retryAction) perform(retryAction); };
$('new-chat').onclick = () => perform(async () => {
  await api.reset(); sessionExpired = false;
  renderedMessages.clear();
  $('messages').replaceChildren(); $('welcome').hidden = false; $('message').value = '';
});
$('new-chat-mobile').onclick = () => $('new-chat').click();
api.health().then(health => {
  $('connection').textContent = `Сервер подключён · ${health.catalog_count} товаров`;
  if (!health.catalog_count) notice($('welcome'), 'Выборка пуста. Импортируйте демонстрационный каталог по README.');
}).catch(() => { $('connection').textContent = 'Сервер недоступен · запустите backend'; });
