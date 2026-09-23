"""Live prototype-cart check: reads EKT, changes only its own local session, calls OpenAI once."""

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import httpx


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--product-id", type=int, default=515292)
    parser.add_argument("--report", type=Path, default=Path("data/cart-live-check.json"))
    args = parser.parse_args()
    results = []

    def check(name, passed):
        result = {"case": name, "passed": bool(passed)}
        results.append(result)
        print(json.dumps(result), flush=True)
        if not passed:
            raise AssertionError(name)

    try:
        with httpx.Client(base_url=args.base_url, timeout=30, trust_env=False) as client:
            health = client.get("/health").json()
            check(
                "cart_and_live_catalog_enabled",
                health["cart_enabled"] and health["live_catalog_enabled"],
            )
            login = client.post("/api/chat/sessions")
            login.raise_for_status()
            token = login.json()["session_token"]
            auth = {"Authorization": f"Bearer {token}"}

            def cart():
                response = client.get("/api/cart", headers=auth)
                response.raise_for_status()
                return response.json()

            def propose(**body):
                response = client.post("/api/cart/proposals", headers=auth, json=body)
                response.raise_for_status()
                return response.json()

            def confirm(offer, key=None):
                response = client.post(
                    "/api/cart/confirm",
                    headers={**auth, "Idempotency-Key": key or str(uuid4())},
                    json={"proposal_id": offer["proposal_id"]},
                )
                response.raise_for_status()
                return response.json()

            try:
                check("empty_session_cart", cart()["items"] == [])
                selected = client.post(
                    "/api/chat", headers=auth, json={"message": str(args.product_id)}
                )
                check(
                    "select_real_product",
                    selected.status_code == 200
                    and selected.json()["products"][0]["id"] == args.product_id,
                )
                chat = client.post(
                    "/api/chat",
                    headers=auth,
                    json={"message": "Добавь 2", "request_id": str(uuid4())},
                )
                body = chat.json()
                check(
                    "chat_prepares_real_offer",
                    chat.status_code == 200 and body.get("cart_action") == "confirmation_required",
                )
                offer = body["cart_proposal"]
                check(
                    "unknown_units_remain_unknown",
                    offer["source"] == "ekt_api"
                    and offer["currency"] is None
                    and offer["unit"] is None,
                )
                check("no_write_before_confirmation", cart()["items"] == [])
                key = str(uuid4())
                applied = confirm(offer, key)
                check("confirmed_add", applied["cart"]["items"][0]["quantity"] == 2)
                replay = confirm(offer, key)
                check(
                    "retry_does_not_duplicate",
                    replay["replayed"] and replay["cart"]["version"] == applied["cart"]["version"],
                )
                page = client.get(applied["cart_url"])
                check(
                    "current_cart_link",
                    page.status_code == 200
                    and str(offer["article"]) in page.text
                    and "no-store" in page.headers["cache-control"],
                )
                revised = propose(operation="set", product_id=args.product_id, quantity=1)
                check("set_waits_for_confirmation", cart()["items"][0]["quantity"] == 2)
                check(
                    "confirmed_quantity_change",
                    confirm(revised)["cart"]["items"][0]["quantity"] == 1,
                )
                removal = propose(operation="remove", product_id=args.product_id)
                cancellation = client.post(
                    "/api/cart/cancel", headers=auth, json={"proposal_id": removal["proposal_id"]}
                )
                check(
                    "cancel_keeps_item",
                    cancellation.status_code == 200 and len(cart()["items"]) == 1,
                )
                removal = propose(operation="remove", product_id=args.product_id)
                check("confirmed_removal", confirm(removal)["cart"]["items"] == [])
                addition = propose(product_id=args.product_id, quantity=1)
                confirm(addition)
                clearing = propose(operation="clear")
                check("confirmed_clear", confirm(clearing)["cart"]["items"] == [])
                invalid = client.post(
                    "/api/cart/proposals",
                    headers=auth,
                    json={"product_id": args.product_id, "quantity": 10001},
                )
                check("invalid_quantity_rejected", invalid.status_code == 422)
            finally:
                client.delete("/api/chat/sessions/current", headers=auth)
            check("revoked_link_is_inaccessible", client.get("/cart").status_code == 401)
    except (AssertionError, httpx.HTTPError, KeyError) as exc:
        # Never print tokens, request headers or provider payloads.
        if not results or results[-1]["passed"]:
            results.append(
                {"case": "unexpected_response", "passed": False, "error_type": type(exc).__name__}
            )
    finally:
        report = {
            "checked_at": datetime.now(UTC).isoformat(),
            "product_id": args.product_id,
            "cases": results,
            "passed": sum(r["passed"] for r in results),
            "total": len(results),
        }
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    raise SystemExit(0 if report["passed"] == report["total"] else 1)


if __name__ == "__main__":
    main()
