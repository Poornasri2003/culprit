"""Backend Flask service — exposes POST /cart/total on port 8000."""

import os

from flask import Flask, jsonify, request

from shared.pricing import compute_cart_total

app = Flask(__name__)

TAX_RATE = 0.08  # 8 % sales tax


def _check_auth() -> bool:
    """Return True when the request carries a valid Bearer token."""
    expected = os.environ.get("BEARER_TOKEN", "")
    if not expected:
        return False
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return False
    return auth_header[len("Bearer "):] == expected


@app.post("/cart/total")
def cart_total():
    if not _check_auth():
        return jsonify({"error": "Unauthorized"}), 401

    body = request.get_json(silent=True) or {}
    items = body.get("items", [])
    discount_code = body.get("discount_code")

    total = compute_cart_total(items, discount_code, TAX_RATE)
    return jsonify({"total": total}), 200


if __name__ == "__main__":
    app.run(port=8000)
