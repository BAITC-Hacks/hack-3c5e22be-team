import { CatalogApi, API_BASE } from './api.js';
import { icon, hydrateIcons } from './icons.js';

const api = new CatalogApi();
const $ = id => document.getElementById(id);
let busy = false, sessionExpired = false, retryAction = null, cartEnabled = false;
let cartState = null, activeProposal = null;
const renderedMessages = new Set(), recentProducts = new Map();
const labels = { current: 'Номинальный ток', voltage: 'Напряжение', poles: 'Полюса', breaking_capacity: 'Отключающая способность', mounting: 'Установка', brand: 'Бренд', trip_curve: 'Характеристика срабатывания', device_type: 'Тип устройства', residual_current: 'Дифференциальный ток' };
const operations = { add: 'Добавление товара', set: 'Изменение количества', remove: 'Удаление товара', clear: 'Очистка корзины' };
hydrateIcons();

function el(tag, className = '', text) {
  const node = document.createElement(tag);
  node.className = className;
  if (text !== undefined) node.textContent = String(text);
  return node;
}
function button(text, callback, className = 'secondary') {
  const node = el('button', className, text); node.type = 'button'; node.onclick = callback; return node;
}
function notice(parent, text, success = false) { const n = el('div', `notice${success ? ' success' : ''}`, text); parent.append(n); return n; }
function money(amount, currency) { return amount == null ? 'Цена неизвестна' : `${amount} ${currency || '· валюта не указана'}`; }
function units(quantity, unit) { return `${quantity ?? 'неизвестно'} ${unit || '· единица не указана'}`; }
function safeUrl(url, base) {
  try { const parsed = new URL(url, base); return ['http:', 'https:'].includes(parsed.protocol) ? parsed.href : null; } catch { return null; }
}
function sourceLink(parent, text, url) {
  const href = safeUrl(url); if (!href) return;
  const a = el('a', '', text); a.href = href; a.target = '_blank'; a.rel = 'noopener noreferrer'; parent.append(a);
}
function setCartUrl(url) {
  const href = safeUrl(url, API_BASE);
  // Cart links must stay on the backend origin; never forward a token in a URL.
  const valid = href && new URL(href).origin === new URL(API_BASE).origin && !new URL(href).search;
  $('cart-link').hidden = !valid;
  if (valid) $('cart-link').href = href;
}
function scrollToEnd() { const s = $('conversation-scroll'); s.scrollTo({ top: s.scrollHeight, behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' }); }
function message(role, text) {
  $('welcome').hidden = true;
  const node = el('article', `message ${role}`), label = el('div', 'message-label');
  label.append(icon(role === 'user' ? 'user' : 'spark'), document.createTextNode(role === 'user' ? 'Вы' : 'EKT Assistant'));
  node.append(label); if (text) node.append(el('p', '', text));
  $('messages').append(node); return node;
}
function setBusy(value) {
  busy = value;
  $('pending').hidden = !value || $('proposal-dialog').open;
  $('dialog-pending').hidden = !value || !$('proposal-dialog').open;
  $('messages').setAttribute('aria-busy', String(value));
  document.querySelectorAll('button').forEach(b => { b.disabled = value || b.dataset.locked === 'true'; });
  $('confirm-proposal').disabled = value || !activeProposal || sessionExpired;
  $('message').disabled = value;
}
function clearErrors() { $('error').hidden = true; $('dialog-error').hidden = true; retryAction = null; }
function showError(error, action) {
  if (error.status === 401) {
    sessionExpired = true; activeProposal = null;
    $('proposal-dialog').close(); $('info-dialog').close(); closePanels();
    $('cart-link').hidden = true;
    $('connection').textContent = 'Сессия завершена'; $('connection-dot').classList.remove('online');
  }
  const dialog = $('proposal-dialog').open;
  const area = dialog ? $('dialog-error') : $('error');
  area.querySelector('span').textContent = error.message;
  area.hidden = false;
  retryAction = error.retryable && !sessionExpired ? (error.recovery || action) : null;
  if (error.code === 'NETWORK_ERROR') {
    $('connection').textContent = 'Сервер недоступен'; $('connection-dot').classList.remove('online');
  }
  const retry = dialog ? $('dialog-retry') : $('retry');
  retry.hidden = !retryAction; retry.textContent = error.code === 'RATE_LIMITED' ? 'Повторить позже' : 'Повторить';
  $('recover-session').hidden = !sessionExpired;
}
async function perform(action, allowExpired = false) {
  if (busy) return;
  if (sessionExpired && !allowExpired) { showError({ status: 401, message: 'Сессия завершена. Начните новый диалог; старая корзина больше недоступна.' }); return; }
  clearErrors(); setBusy(true);
  try {
    await action();
    if (!sessionExpired) {
      if (!$('connection').textContent.startsWith('Подключено')) $('connection').textContent = 'Сервер подключён';
      $('connection-dot').classList.add('online');
    }
  } catch (error) { showError(error, action); }
  finally { setBusy(false); }
}
function quantityInput(initial = 1, labelText = 'Количество') {
  const label = el('label', '', labelText), input = el('input');
  input.type = 'number'; input.min = '1'; input.max = '10000'; input.step = '1'; input.value = String(initial); input.required = true;
  input.inputMode = 'numeric'; input.setAttribute('aria-label', labelText); label.append(input);
  return { label, input };
}
function validatedQuantity(input) {
  if (!input.reportValidity()) return null;
  const n = Number(input.value); return Number.isInteger(n) && n >= 1 && n <= 10000 ? n : null;
}
function remember(product) {
  recentProducts.delete(product.id); recentProducts.set(product.id, product);
  while (recentProducts.size > 5) recentProducts.delete(recentProducts.keys().next().value);
  $('recent-section').hidden = false; $('recent-products').replaceChildren();
  [...recentProducts.values()].reverse().forEach(p => {
    const b = button('', () => { closePanels(); send(String(p.id)); }, 'recent-item');
    b.append(el('strong', '', p.name), el('small', '', `${p.article}${p.source === 'synthetic' ? ' · тестовый товар' : ''}`));
    $('recent-products').append(b);
  });
}
function productCard(p, alternative = null) {
  remember(p);
  const card = el('article', 'product'); card.dataset.productId = p.id;
  const badges = el('div', 'badges');
  if (p.source === 'synthetic') badges.append(el('span', 'badge', 'ТЕСТОВЫЕ ДАННЫЕ'));
  if (p.stale) badges.append(el('span', 'badge warning', 'УСТАРЕВШИЙ СНИМОК'));
  if (alternative) badges.append(el('span', 'badge', 'КАНДИДАТ В АНАЛОГИ'));
  card.append(badges);
  const imageUrl = safeUrl(p.image);
  if (imageUrl) { const img = el('img', 'product-image'); img.src = imageUrl; img.alt = p.name; img.loading = 'lazy'; img.referrerPolicy = 'no-referrer'; img.onerror = () => img.remove(); card.append(img); }
  card.append(el('h3', '', p.name), el('div', 'sku', `Артикул: ${p.article || 'не указан'}`));
  if (p.supplier_article) card.append(el('div', 'sku', `Артикул поставщика: ${p.supplier_article}`));
  card.append(el('div', 'product-price', money(p.price, p.currency)), el('div', 'stock', p.quantity == null ? 'Остаток неизвестен' : `Остаток по снимку: ${p.quantity}`));
  if (p.source !== 'synthetic') card.append(el('div', 'mode', 'Общий остаток не гарантирует отгрузку. Единицы и правила продажи уточняются.'));
  if (p.stale || p.quantity == null) notice(card, 'Текущее наличие неизвестно. Данные требуют проверки.');
  if (!p.detail_loaded) notice(card, 'Подробная карточка ещё не загружена.');
  for (const issue of p.quality_issues || []) notice(card, issue.message);
  if (alternative) {
    (alternative.reasons || []).forEach(reason => card.append(el('p', 'description', `✓ ${reason}`)));
    if (alternative.requires_verification) notice(card, 'Требуется проверка совместимости специалистом.');
  }
  const details = el('details'), dl = el('dl'); details.append(el('summary', '', 'Характеристики и документы'));
  for (const [key, value] of Object.entries(p.attributes || {})) {
    const row = el('div'), conflict = key === 'current' && p.quality_issues?.some(i => i.code === 'conflicting_current');
    row.append(el('dt', '', labels[key] || key), el('dd', '', conflict ? 'Конфликт данных — уточните значение' : value)); dl.append(row);
  }
  details.append(dl);
  if (p.description) details.append(el('p', 'description', p.description));
  if (p.certificate_status === 'unknown') details.append(el('p', 'description', 'Нет подтверждённых данных о сертификате. Это не означает, что сертификата нет.'));
  (p.certificates || []).forEach((url, i) => sourceLink(details, `Сертификат ${i + 1}`, url));
  if (p.stores?.length) {
    details.append(el('p', 'description', 'Склады (доступность к продаже не подтверждена):'));
    p.stores.forEach(s => details.append(el('p', 'description', `${s.name}: ${s.quantity ?? 'неизвестно'}`)));
  }
  const date = new Date(p.observed_at);
  if (p.observed_at && !Number.isNaN(date.getTime())) details.append(el('div', 'mode', `Снимок: ${date.toLocaleString('ru-RU')}`));
  if (p.url) sourceLink(details, 'Карточка в каталоге ↗', p.url);
  card.append(details);
  const actions = el('div', 'product-actions');
  actions.append(button('Обсудить товар', () => send(String(p.id))), button('Сравнить кандидатов', () => compare(p)));
  card.append(actions);
  const form = el('form', 'quantity-form'), { label, input } = quantityInput();
  const add = button('В корзину', null, 'primary'); add.type = 'submit';
  if (!cartEnabled) { add.dataset.locked = 'true'; add.disabled = true; add.textContent = 'Корзина отключена'; }
  form.append(label, add);
  form.onsubmit = event => { event.preventDefault(); const quantity = validatedQuantity(input); if (quantity != null) perform(() => makeProposal({ operation: 'add', product_id: p.id, quantity })); };
  card.append(form, el('div', 'mode', 'Сначала покажем предложение. Добавление — после подтверждения.'));
  return card;
}
function alternatives(parent, items) {
  if (!items?.length) return;
  parent.append(el('h3', 'section-caption', 'Возможные замены'));
  const cards = el('div', 'cards'); items.forEach(a => cards.append(productCard(a.product, a))); parent.append(cards);
}
function proposalBody(p) {
  const body = { operation: p.operation };
  if (p.operation !== 'clear') body.product_id = p.product_id;
  if (['add', 'set'].includes(p.operation)) body.quantity = p.quantity;
  return body;
}
function showProposal(p, body = proposalBody(p), changeMessage = '') {
  // An ID has one key for the entire confirmation attempt, including retries.
  const key = activeProposal?.proposal.proposal_id === p.proposal_id ? activeProposal.key : crypto.randomUUID();
  activeProposal = { proposal: p, body, key };
  $('proposal-title').textContent = operations[p.operation] || 'Подтвердите изменение';
  const content = $('proposal-content'); content.replaceChildren(el('h3', '', p.name));
  if (p.article) content.append(el('p', 'sku', `Артикул: ${p.article}`));
  if (p.source === 'synthetic' || p.data_mode === 'demo') content.append(el('span', 'badge', 'ТЕСТОВЫЕ ДАННЫЕ'));
  if (changeMessage) notice(content, changeMessage);
  const dl = el('dl', 'proposal-dl');
  const rows = p.operation === 'clear'
    ? [['Удаляемых позиций', p.item_count], ['Сумма до очистки', money(p.previous_total, p.currency)], ['Сумма после очистки', money(p.resulting_total, p.currency)]]
    : [['Количество сейчас', units(p.previous_quantity, p.unit)], ['Количество после подтверждения', units(p.resulting_quantity, p.unit)], ['Цена за единицу', money(p.unit_price, p.currency)], ['Сумма позиции сейчас', money(p.previous_total, p.currency)], ['Сумма позиции после', money(p.resulting_total, p.currency)]];
  if (p.previous_unit_price != null) rows.splice(2, 0, ['Прежняя цена', money(p.previous_unit_price, p.currency)]);
  rows.forEach(([key, value]) => { const row = el('div'); row.append(el('dt', '', key), el('dd', '', value)); dl.append(row); }); content.append(dl);
  if (p.available_quantity != null) content.append(el('p', 'muted', `Остаток при проверке: ${p.available_quantity}`));
  if (p.warning) notice(content, p.warning);
  const expiry = new Date(p.expires_at); if (!Number.isNaN(expiry.getTime())) content.append(el('p', 'muted', `Подтвердите до ${expiry.toLocaleTimeString('ru-RU')}. Перед изменением сервер проверит данные снова.`));
  $('confirm-proposal').textContent = p.operation === 'remove' ? 'Подтвердить удаление' : p.operation === 'clear' ? 'Подтвердить очистку' : 'Подтвердить';
  $('dialog-error').hidden = true;
  closePanels(); $('info-dialog').close();
  if (!$('proposal-dialog').open) $('proposal-dialog').showModal();
  setBusy(busy);
}
async function makeProposal(body, reason = '') {
  activeProposal = null; $('cart-pending').replaceChildren();
  try { const p = await api.propose(body); showProposal(p, body, reason); }
  catch (error) {
    $('proposal-dialog').close();
    error.recovery = () => makeProposal(body, reason);
    throw error;
  }
}
async function confirmProposal() {
  const pending = activeProposal; if (!pending || busy) return;
  await perform(async () => {
    try {
      const result = await api.confirm(pending.proposal.proposal_id, pending.key);
      activeProposal = null; $('proposal-dialog').close(); renderCart(result.cart); setCartUrl(result.cart_url);
      const node = message('assistant', 'Корзина прототипа обновлена.');
      notice(node, result.replayed ? 'Повтор подтверждения обработан без повторного изменения корзины.' : 'Изменение выполнено после вашего подтверждения.', true);
      node.append(button('Открыть корзину', () => openCart())); scrollToEnd();
    } catch (error) {
      if (['PRICE_CHANGED', 'STOCK_CHANGED', 'PROPOSAL_EXPIRED', 'CART_CHANGED'].includes(error.code)) {
        const old = pending.proposal;
        const reason = `${error.message} Прежнее предложение: ${units(old.resulting_quantity, old.unit)}, ${money(old.resulting_total, old.currency)}. Проверьте новое предложение и подтвердите ещё раз.`;
        await makeProposal(pending.body, reason);
        return; // Never confirm the replacement automatically.
      }
      if (!error.retryable) {
        activeProposal = null;
        $('confirm-proposal').disabled = true;
      }
      throw error;
    }
  });
}
async function cancelProposal() {
  if (busy) return;
  await perform(async () => {
    if (activeProposal) {
      try { await api.cancel(activeProposal.proposal.proposal_id); }
      catch (error) { if (!['PROPOSAL_NOT_FOUND', 'PROPOSAL_USED', 'PROPOSAL_EXPIRED'].includes(error.code)) throw error; }
    }
    activeProposal = null; $('proposal-dialog').close(); await refreshCart();
  });
}
function renderCart(cart) {
  cartState = cart;
  document.querySelectorAll('[data-cart-count]').forEach(n => { n.textContent = cart.items.length; });
  const content = $('cart-content'); content.replaceChildren();
  if (!cart.items.length) {
    const empty = el('div', 'empty-cart'); empty.append(icon('bag'), el('h3', '', 'Пока ничего не выбрано'), el('p', '', 'Найдите товар в чате. Помощник уточнит количество и подготовит предложение.'), button('Найти товар', focusSearch)); content.append(empty);
  }
  cart.items.forEach(item => {
    const node = el('article', 'cart-item'); node.dataset.productId = item.product_id;
    node.append(el('h3', '', item.name), el('small', '', `${item.article}${item.source === 'synthetic' ? ' · тестовый товар' : ''}`), el('small', '', units(item.quantity, item.unit)), el('div', 'item-price', money(item.total, item.currency)));
    const actions = el('form', 'item-actions'), { input } = quantityInput(item.quantity, 'Новое количество');
    const change = button('Изменить', null); change.type = 'submit';
    actions.append(input, change, button('Удалить', () => perform(() => makeProposal({ operation: 'remove', product_id: item.product_id }))));
    actions.onsubmit = event => { event.preventDefault(); const quantity = validatedQuantity(input); if (quantity != null) perform(() => makeProposal({ operation: 'set', product_id: item.product_id, quantity })); };
    node.append(actions); content.append(node);
  });
  if (cart.items.length && cart.warning) notice(content, cart.warning);
  $('cart-total').textContent = cart.items.length ? money(cart.total, cart.currency) : '—';
  $('clear-cart').hidden = !cart.items.length; setCartUrl(cart.cart_url);
  $('cart-pending').replaceChildren();
  if (cart.pending_proposal) {
    const pending = el('div', 'confirmation-note'); pending.append(el('span', '', 'Ожидает подтверждения'), button('Посмотреть', () => showProposal(cart.pending_proposal))); $('cart-pending').append(pending);
  }
}
async function refreshCart() { const cart = await api.cart(); renderCart(cart); return cart; }
async function send(text) {
  text = text.trim(); if (!text || text.length > 2000 || busy) return;
  if (sessionExpired) { await perform(async () => {}); return; }
  closePanels(); message('user', text); $('message').value = ''; resizeComposer();
  const requestId = crypto.randomUUID();
  await perform(async () => {
    const response = await api.chat(text, requestId);
    if (!renderedMessages.has(response.message_id)) {
      const node = message('assistant', response.message), cards = el('div', 'cards');
      (response.products || []).forEach(p => cards.append(productCard(p))); if (cards.childElementCount) node.append(cards);
      alternatives(node, response.alternatives);
      (response.warnings || []).forEach(w => notice(node, w));
      if (response.cart_error) notice(node, response.cart_error.message || 'Не удалось подготовить предложение. Уточните количество.');
      if (response.cart_action === 'integration_required') notice(node, 'Корзина отключена на сервере. Товары не добавлены.');
      if (response.cart_action === 'proposal_required') notice(node, 'Уточните товар и количество. Корзина не изменилась.');
      const modes = { rules: 'Базовый режим · данные каталога', catalog: 'Ответ из каталога · без обращения к ИИ', openai: 'AI-помощник · факты из каталога' };
      node.append(el('div', 'mode', modes[response.mode] || 'Ответ сервера'));
      if (response.message_id) { node.dataset.messageId = response.message_id; renderedMessages.add(response.message_id); }
      if (response.cart_action === 'view') node.append(button('Открыть корзину', openCart));
    }
    if (response.cart_url) setCartUrl(response.cart_url);
    if (response.cart_action === 'confirmation_required' && response.cart_proposal) {
      // Cached chat replies can refer to a superseded/expired proposal.
      const cart = await refreshCart();
      if (cart.pending_proposal?.proposal_id === response.cart_proposal.proposal_id) showProposal(cart.pending_proposal);
      else notice(message('assistant', ''), 'Это предложение уже использовано, заменено или истекло. Выберите количество заново.');
    }
    scrollToEnd();
  });
}
async function compare(p) {
  await perform(async () => { const result = await api.alternatives(p.id); const node = message('assistant', `Кандидаты для «${p.name}»`); alternatives(node, result.items); if (!result.items.length) notice(node, 'Проверенные кандидаты не найдены в загруженной выборке.'); result.warnings.forEach(w => notice(node, w)); scrollToEnd(); });
}
function closePanels() { $('sidebar').classList.remove('open'); $('cart-panel').classList.remove('open'); $('backdrop').hidden = true; $('menu-toggle').setAttribute('aria-expanded', 'false'); }
function focusSearch() { closePanels(); $('message').focus(); }
async function openCart() {
  closePanels(); $('cart-panel').classList.add('open');
  if (innerWidth <= 950) $('backdrop').hidden = false;
  await perform(refreshCart);
}
function info(title, paragraphs, actions = []) {
  $('info-title').textContent = title; $('info-content').replaceChildren(...paragraphs.map(t => el('p', '', t))); $('info-actions').replaceChildren(...actions);
  closePanels(); if (!$('info-dialog').open) $('info-dialog').showModal();
}
function help() { info('Как работает EKT Assistant', ['Введите артикул или описание товара. Карточки показывают данные каталога, время снимка и предупреждения. Аналоги требуют проверки совместимости.', 'Выберите количество. Помощник покажет предложение с итоговой суммой. Только отдельное нажатие «Подтвердить» изменит корзину.', 'Это корзина прототипа: без оплаты и заказа на ekt.kz. Новый диалог или перезапуск сервера удаляет корзину. Сертификаты и загрузка вложений пока не подключены.']); }
async function resetSession() {
  $('info-dialog').close();
  await perform(async () => {
    await api.reset(); sessionExpired = false; activeProposal = null; cartState = null; renderedMessages.clear(); recentProducts.clear();
    $('messages').replaceChildren(); $('recent-products').replaceChildren(); $('recent-section').hidden = true; $('welcome').hidden = false; $('message').value = ''; resizeComposer();
    await bootstrap();
  }, true);
}
function requestReset() {
  if (sessionExpired) { resetSession(); return; }
  info('Начать новый диалог?', ['История и корзина текущей сессии будут удалены. Продолжить?'], [button('Остаться', () => $('info-dialog').close()), button('Начать новый диалог', resetSession, 'primary')]);
}
async function bootstrap() {
  const health = await api.health(); cartEnabled = health.cart_enabled;
  $('connection').textContent = `Подключено · ${health.catalog_count} товаров`; $('connection-dot').classList.add('online');
  await api.session();
  if (cartEnabled) await refreshCart();
  else { $('cart-content').replaceChildren(el('p', 'muted', 'Корзина отключена на сервере.')); $('cart-link').hidden = true; }
  if (!health.catalog_count) notice($('welcome'), 'Каталог пока пуст. Импортируйте выборку по README.');
}
function resizeComposer() { const t = $('message'); t.style.height = 'auto'; t.style.height = `${Math.min(t.scrollHeight, 120)}px`; $('char-count').textContent = `${t.value.length} / 2 000`; }
function viewport() { document.documentElement.style.setProperty('--viewport-height', `${window.visualViewport?.height || innerHeight}px`); }
window.visualViewport?.addEventListener('resize', viewport); window.addEventListener('resize', viewport); viewport();
$('chat-form').onsubmit = e => { e.preventDefault(); send($('message').value); };
$('message').oninput = resizeComposer;
$('message').onkeydown = e => { if (e.key === 'Enter' && !e.shiftKey && !e.isComposing && innerWidth > 650) { e.preventDefault(); send(e.target.value); } };
document.querySelectorAll('[data-prompt]').forEach(b => { b.onclick = () => send(b.dataset.prompt); });
$('focus-search').onclick = focusSearch;
$('nav-chat').onclick = focusSearch;
$('nav-catalog').onclick = () => { focusSearch(); $('message').placeholder = 'Введите артикул, например DEMO-002, или название…'; };
$('nav-cart').onclick = openCart; $('open-cart').onclick = openCart;
$('refresh-cart').onclick = () => perform(refreshCart); $('close-cart').onclick = closePanels;
$('nav-terms').onclick = () => send('Какие условия оплаты и доставки?');
$('nav-help').onclick = help; $('help').onclick = help;
$('new-chat').onclick = requestReset; $('recover-session').onclick = resetSession;
$('retry').onclick = $('dialog-retry').onclick = () => { const action = retryAction; if (action) perform(action); };
$('confirm-proposal').onclick = confirmProposal; $('cancel-proposal').onclick = cancelProposal;
$('proposal-dialog').addEventListener('cancel', e => { e.preventDefault(); cancelProposal(); });
$('clear-cart').onclick = () => perform(() => makeProposal({ operation: 'clear' }));
$('close-info').onclick = () => $('info-dialog').close();
$('examples').onclick = () => { info('Примеры запросов', ['Эти артикулы относятся к тестовым данным. Для реального товара введите его артикул.']); ['DEMO-002', 'Найди аналоги DEMO-001', 'Какие условия оплаты и доставки?'].forEach(t => $('info-content').append(button(t, () => { $('info-dialog').close(); send(t); }))); };
$('menu-toggle').onclick = () => { const show = !$('sidebar').classList.contains('open'); closePanels(); if (show) { $('sidebar').classList.add('open'); $('backdrop').hidden = false; $('menu-toggle').setAttribute('aria-expanded', 'true'); } };
$('backdrop').onclick = closePanels;
for (const theme of ['light', 'dark']) $('theme-' + theme).onclick = () => { document.documentElement.dataset.theme = theme; for (const t of ['light', 'dark']) { $('theme-' + t).classList.toggle('selected', t === theme); $('theme-' + t).setAttribute('aria-pressed', String(t === theme)); } };
document.addEventListener('keydown', e => { if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); focusSearch(); } if (e.key === 'Escape' && !$('proposal-dialog').open) closePanels(); });
perform(bootstrap);
