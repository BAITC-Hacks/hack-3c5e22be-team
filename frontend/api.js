// Session credentials live only in this module's memory, never in URLs or storage.
const BASE = 'http://127.0.0.1:8000';
export class ApiError extends Error {
  constructor(message, status = 0) { super(message); this.status = status; }
}
export class CatalogApi {
  #token = null;
  async request(path, { method = 'GET', body, authenticated = false } = {}) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 25000);
    try {
      const headers = {};
      if (body !== undefined) headers['Content-Type'] = 'application/json';
      if (authenticated && this.#token) headers.Authorization = `Bearer ${this.#token}`;
      const response = await fetch(`${BASE}${path}`, {
        method, headers, body: body === undefined ? undefined : JSON.stringify(body),
        signal: controller.signal, credentials: 'omit', cache: 'no-store',
      });
      if (!response.ok) {
        const messages = {
          401: 'Сессия завершена. Начните новый диалог и повторно укажите товар.',
          404: 'Товар не найден в загруженной выборке.',
          409: 'Предыдущий запрос ещё обрабатывается. Подождите и повторите.',
          422: 'Проверьте сообщение: от 1 до 2000 символов.',
          502: 'Каталог вернул данные в неизвестном формате.',
          503: 'Сервис временно недоступен. Попробуйте позже.',
        };
        throw new ApiError(messages[response.status] || 'Не удалось получить ответ сервера.', response.status);
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
  async chat(message) {
    if (!this.#token) {
      const session = await this.request('/api/chat/sessions', { method: 'POST' });
      this.#token = session.session_token;
    }
    try {
      return await this.request('/api/chat', { method: 'POST', body: { message }, authenticated: true });
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
