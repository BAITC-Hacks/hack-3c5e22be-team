const $ = id => document.getElementById(id);
const csrf = document.querySelector('meta[name="csrf-token"]').content;
let proposal = null;
let confirmationKey = null;
let lastRequest = null;
let busy = false;
let editProduct = null;

async function request(path, body, key) {
  const headers = { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf };
  if (key) headers['Idempotency-Key'] = key;
  const response = await fetch('/cart/' + path, {
    method: body ? 'POST' : 'GET', credentials: 'same-origin', cache: 'no-store',
    headers, body: body ? JSON.stringify(body) : undefined,
  });
  const result = await response.json();
  if (!response.ok) {
    const error = new Error(result.error?.message || 'Не удалось выполнить запрос.');
    error.code = result.error?.code;
    throw error;
  }
  return result;
}
async function run(action) {
  if (busy) return;
  busy = true;
  const disabled = [...document.querySelectorAll('button')].map(button => [button, button.disabled]);
  disabled.forEach(([button]) => { button.disabled = true; });
  $('error').hidden = true;
  $('status').textContent = 'Проверяем данные…';
  try { await action(); }
  catch (error) {
    $('error').textContent = error.message || 'Нет связи с сервером. Повторите действие.';
    $('error').hidden = false;
  } finally {
    busy = false;
    disabled.forEach(([button, wasDisabled]) => { button.disabled = wasDisabled; });
    $('status').textContent = '';
  }
}
function showProposal(value) {
  proposal = value;
  confirmationKey = crypto.randomUUID();
  const labels = { add: 'Добавление', set: 'Изменение количества', remove: 'Удаление товара', clear: 'Очистка корзины' };
  const unit = value.unit || '(единица не указана)';
  const currency = value.currency || '(валюта не указана)';
  const lines = [
    labels[value.operation],
    value.name + (value.article ? '\nАртикул: ' + value.article : ''),
    value.operation === 'clear' ? 'Будут удалены все позиции: ' + value.item_count :
      'Количество в корзине: ' + value.previous_quantity + ' → ' + value.resulting_quantity + ' ' + unit,
  ];
  if (['add', 'set'].includes(value.operation)) {
    lines.push('Цена за единицу: ' + value.unit_price + ' ' + currency);
    if (value.previous_unit_price != null && value.previous_unit_price !== value.unit_price) {
      lines.push('Прежняя цена: ' + value.previous_unit_price + '. Новая цена применяется ко всему количеству этой позиции.');
    }
  }
  lines.push('Сумма позиции: ' + value.previous_total + ' → ' + value.resulting_total + ' ' + currency);
  lines.push('Действует до: ' + new Date(value.expires_at).toLocaleTimeString('ru-RU'));
  lines.push(value.warning);
  $('proposal-text').textContent = lines.join('\n\n');
  $('proposal').hidden = false;
  $('proposal').scrollIntoView({ behavior: 'smooth', block: 'center' });
}
async function prepare(body) {
  lastRequest = body;
  proposal = null;
  $('proposal').hidden = true;
  showProposal(await request('proposals', body));
}
$('add-form').addEventListener('submit', event => {
  event.preventDefault();
  run(() => prepare({ product_id: Number($('product-id').value), quantity: Number($('quantity').value) }));
});
document.querySelectorAll('[data-set]').forEach(button => {
  button.onclick = () => {
    editProduct = Number(button.dataset.set);
    $('edit-name').textContent = button.closest('tr').querySelector('td').textContent;
    $('edit-quantity').value = button.dataset.quantity;
    $('edit').hidden = false;
    $('edit-quantity').focus();
    $('edit').scrollIntoView({ behavior: 'smooth', block: 'center' });
  };
});
$('edit-close').onclick = () => { $('edit').hidden = true; };
$('edit-form').addEventListener('submit', event => {
  event.preventDefault();
  run(async () => {
    await prepare({ operation: 'set', product_id: editProduct, quantity: Number($('edit-quantity').value) });
    $('edit').hidden = true;
  });
});
document.querySelectorAll('[data-remove]').forEach(button => {
  button.onclick = () => run(() => prepare({ operation: 'remove', product_id: Number(button.dataset.remove) }));
});
$('clear').onclick = () => run(() => prepare({ operation: 'clear' }));
$('cancel').onclick = () => run(async () => {
  if (!proposal) return;
  await request('cancel', { proposal_id: proposal.proposal_id });
  proposal = null;
  $('proposal').hidden = true;
});
$('confirm').onclick = () => run(async () => {
  if (!proposal) return;
  try {
    await request('confirm', { proposal_id: proposal.proposal_id }, confirmationKey);
    window.location.reload();
  } catch (error) {
    if (['PRICE_CHANGED', 'STOCK_CHANGED', 'PROPOSAL_EXPIRED'].includes(error.code) && lastRequest) {
      await prepare(lastRequest);
      throw new Error(error.message + ' Новое предложение показано ниже; подтвердите его отдельно.');
    }
    // The same proposal and key are retained after network failures, so retries cannot duplicate items.
    throw error;
  }
});
run(async () => {
  const state = await request('state');
  if (state.pending_proposal) {
    const p = state.pending_proposal;
    lastRequest = p.operation === 'clear' ? { operation: 'clear' } :
      { operation: p.operation, product_id: p.product_id,
        ...(['add', 'set'].includes(p.operation) ? { quantity: p.quantity } : {}) };
    showProposal(p);
  }
});
