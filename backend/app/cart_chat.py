"""Chat may prepare an offer; only the separate confirmation endpoint changes items."""

import re

from pydantic import ValidationError

from app.cart import CartError, ProposalRequest


async def cart_reply(cart, session, message, response, validate_session):
    response.cart_action = "proposal_required"
    response.cart_url = "/cart"
    response.message = (
        "Товары не добавлены. Выберите один товар и укажите количество, например «Добавь 2». "
        "Я подготовлю предложение для корзины прототипа; затем подтвердите его кнопкой."
    )
    text = message.strip().casefold()
    if re.fullmatch(r"(?:покажи|открой|моя|где)\s+(?:мою\s+)?корзин[ау][.!?]?", text):
        response.cart_action = "view"
        response.message = (
            "Откройте корзину прототипа по ссылке. Это отдельная корзина, не корзина ekt.kz."
        )
        return response
    explicit = [
        p
        for p in cart.catalog.all()
        if any(
            ident and re.search(r"(?<!\w)" + re.escape(ident) + r"(?!\w)", message, re.I)
            for ident in (str(p.id), p.article, p.supplier_article)
        )
    ]
    product_ids = [p.id for p in explicit] if explicit else session.product_ids
    if len(product_ids) != 1:
        return response
    # Only explicit quantities; product IDs and electrical ratings are not amounts.
    quantities = re.findall(r"(?<![\w.,-])(\d+)\s*(?:шт(?:ук[аи]?)?|ед)(?!\w)", text)
    after_add = re.search(r"\bдобав(?:ь|ить)\s+(\d+)(?![\w.,])", text)
    if after_add and after_add[1] not in {str(p) for p in product_ids}:
        quantities.append(after_add[1])
    words = {"один": 1, "одну": 1, "два": 2, "две": 2, "три": 3, "четыре": 4, "пять": 5}
    word = re.search(r"\bдобав(?:ь|ить)\s+(один|одну|два|две|три|четыре|пять)\b", text)
    if word:
        quantities.append(str(words[word[1]]))
    if not re.search(r"\bдобав", text) or len(set(quantities)) != 1:
        return response
    try:
        body = ProposalRequest(product_id=product_ids[0], quantity=int(quantities[0]))
        proposal = await cart.propose(session.cart, body, validate_session)
    except ValidationError:
        response.cart_error = "VALIDATION_ERROR"
        response.message = "Товары не добавлены. Укажите целое количество от 1 до 10000."
        return response
    except CartError as exc:
        if exc.status_code == 401:
            raise
        response.cart_error = exc.code
        response.message = f"Товары не добавлены. {exc.detail}"
        return response
    response.cart_action = "confirmation_required"
    response.cart_proposal = proposal
    currency = proposal["currency"] or "валюта не указана"
    response.message = (
        f"Товары пока не добавлены. Предложение: {proposal['name']}, "
        f"добавить {proposal['quantity']}; всего в корзине будет {proposal['resulting_quantity']}. "
        f"Цена за единицу: {proposal['unit_price']} ({currency}); "
        f"сумма позиции после изменения: {proposal['resulting_total']}. "
        "Проверьте предложение и нажмите «Подтвердить» в интерфейсе или на странице корзины. "
        + proposal["warning"]
    )
    return response
