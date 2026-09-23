// Session credentials live only in this module's memory, never in URLs or storage.
// Local two-port development; hosted frontend and API share one HTTPS origin.
export const API_BASE = ['127.0.0.1', 'localhost'].includes(location.hostname) && location.port === '5173'
  ? `${location.protocol}//${location.hostname}:8000` : location.origin;
export class ApiError extends Error {
  constructor(message, status = 0, code = 'NETWORK_ERROR', retryable = true) {
    super(message);
    this.status = status;
    this.code = code;
    this.retryable = retryable;
  }
}
export class CatalogApi {
  #token = null;
  #creating = null;
  async request(path, { method = 'GET', body, authenticated = false, extraHeaders = {} } = {}) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 25000);
    try {
      const headers = { ...extraHeaders };
      if (body !== undefined) headers['Content-Type'] = 'application/json';
      if (authenticated && this.#token) headers.Authorization = `Bearer ${this.#token}`;
      const response = await fetch(`${API_BASE}${path}`, {
        method, headers, body: body === undefined ? undefined : JSON.stringify(body),
        signal: controller.signal, credentials: 'include', cache: 'no-store',
      });
      if (!response.ok) {
        const payload = await response.json().catch(() => null);
        const serverError = payload?.error;
        const messages = {
          401: 'Сессия завершена. Начните новый диалог и повторно укажите товар.',
          404: 'Товар не найден в загруженной выборке.',
          409: 'Предыдущий запрос ещё обрабатывается. Подождите и повторите.',
          422: 'Проверьте сообщение: от 1 до 2000 символов.',
          429: 'Слишком много запросов. Повторите через минуту.',
          502: 'Каталог вернул данные в неизвестном формате.',
          503: 'Сервис временно недоступен. Попробуйте позже.',
        };
        const code = typeof serverError?.code === 'string' ? serverError.code : `HTTP_${response.status}`;
        let description = typeof serverError?.message === 'string'
          ? serverError.message : messages[response.status] || 'Не удалось получить ответ сервера.';
        if (response.status === 401) description = messages[401];
        if (code === 'REQUEST_ID_REUSED') description = 'Не удалось повторить сообщение: его ID уже занят. Отправьте сообщение заново.';
        const retryable = typeof serverError?.retryable === 'boolean'
          ? serverError.retryable : [408, 429, 500, 502, 503, 504].includes(response.status);
        if (response.status === 401) this.#token = null;
        throw new ApiError(description, response.status, code, retryable && response.status !== 401);
      }
      return response.status === 204 ? null : await response.json();
    } catch (error) {
      if (error instanceof ApiError) throw error;
      throw new ApiError(error.name === 'AbortError'
        ? 'Ответ занимает слишком много времени. Запрос мог дойти до сервера; повторите позже.'
        : 'Нет связи с сервером. Проверьте, запущен ли backend на порту 8000.');
    } finally { clearTimeout(timer); }
  }
  health() { return this.request('/health'); }
  async session() {
    if (this.#token) return;
    if (!this.#creating) this.#creating = this.request('/api/chat/sessions', { method: 'POST' })
      .then(session => { this.#token = session.session_token; })
      .finally(() => { this.#creating = null; });
    return this.#creating;
  }
  async authed(path, options = {}) {
    await this.session();
    return this.request(path, { ...options, authenticated: true });
  }
  cart() { return this.authed('/api/cart'); }
  propose(body) { return this.authed('/api/cart/proposals', { method: 'POST', body }); }
  confirm(proposalId, key) {
    return this.authed('/api/cart/confirm', {
      method: 'POST', body: { proposal_id: proposalId }, extraHeaders: { 'Idempotency-Key': key },
    });
  }
  cancel(proposalId) { return this.authed('/api/cart/cancel', { method: 'POST', body: { proposal_id: proposalId } }); }
  search(query) { return this.request(`/api/products?query=${encodeURIComponent(query)}&limit=8`); }
  async chat(message, requestId) {
    await this.session();
    try {
      return await this.request('/api/chat', {
        method: 'POST', body: { message, request_id: requestId }, authenticated: true,
      });
    } catch (error) {
      if (error.status === 401) {
        this.#token = null;
        // Do not silently replay a contextual question into a different conversation.
      }
      throw error;
    }
  }
  alternatives(id) { return this.request(`/api/products/${encodeURIComponent(id)}/alternatives`); }
  async reset() {
    if (this.#token) {
      try { await this.request('/api/chat/sessions/current', { method: 'DELETE', authenticated: true }); }
      catch (error) { if (error.status !== 401) throw error; }
    }
    this.#token = null;
  }
}
