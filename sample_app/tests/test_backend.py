"""Pytest suite for the backend service.

Covered cases:
  - POST /cart/total without a discount code returns the correct taxed total (HTTP 200).
  - POST /cart/total with a missing / wrong Authorization header returns HTTP 401.

The discount-code case is intentionally NOT tested here.
"""

import os

import pytest

# Ensure BEARER_TOKEN is set before the app module is imported so that
# os.environ.get("BEARER_TOKEN") resolves to our test value.
os.environ.setdefault("BEARER_TOKEN", "test-secret")

from backend.app import app  # noqa: E402  (import after env setup)


@pytest.fixture()
def client():
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def auth_headers():
    token = os.environ["BEARER_TOKEN"]
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# No-discount case
# ---------------------------------------------------------------------------

def test_cart_total_no_discount(client):
    """Two items at $20 each, 8% tax, no discount -> $43.20."""
    payload = {"items": [{"price": 20.00, "qty": 2}], "discount_code": None}
    resp = client.post("/cart/total", json=payload, headers=auth_headers())
    assert resp.status_code == 200
    data = resp.get_json()
    assert "total" in data
    assert data["total"] == pytest.approx(43.20, abs=0.01)


# ---------------------------------------------------------------------------
# 401 cases
# ---------------------------------------------------------------------------

def test_missing_auth_returns_401(client):
    """No Authorization header -> 401."""
    payload = {"items": [{"price": 10.00, "qty": 1}]}
    resp = client.post("/cart/total", json=payload)
    assert resp.status_code == 401


def test_wrong_token_returns_401(client):
    """Wrong Bearer token -> 401."""
    payload = {"items": [{"price": 10.00, "qty": 1}]}
    resp = client.post(
        "/cart/total",
        json=payload,
        headers={"Authorization": "Bearer wrong-token"},
    )
    assert resp.status_code == 401
