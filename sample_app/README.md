# sample_app

Multi-service e-commerce demo used by the Culprit debug scenario (SPEC.md §11).

```
sample_app/
├── shared/     pricing library
├── backend/    Flask API  (port 8000)
├── frontend/   Flask UI   (port 5000)
└── tests/      pytest suite
```

## Prerequisites

```bash
pip install flask requests pytest
```

## Running the backend

```bash
# Set the expected bearer token (choose any value; never hardcode)
export BEARER_TOKEN=my-secret-token

# Run from the sample_app/ directory so that `shared` is importable
cd sample_app
python -m backend.app
# Listening on http://localhost:8000
```

Alternatively, set `FLASK_APP` and use the Flask CLI:

```bash
cd sample_app
FLASK_APP=backend.app flask run --port 8000
```

### Example request

```bash
curl -s -X POST http://localhost:8000/cart/total \
  -H "Authorization: Bearer my-secret-token" \
  -H "Content-Type: application/json" \
  -d '{"items":[{"price":50,"qty":2}],"discount_code":null}'
```

Expected response (no discount, 8% tax):

```json
{"total": 108.0}
```

## Running the frontend

The frontend reads `BEARER_TOKEN` and `BACKEND_URL` from the environment.

```bash
export BEARER_TOKEN=my-secret-token
export BACKEND_URL=http://localhost:8000   # default

cd sample_app
python -m frontend.app
# Listening on http://localhost:5000
```

## Running the tests

The test suite uses Flask's in-process `test_client`; no live server is needed.

```bash
# From the repo root:
pytest sample_app/

# Or from inside sample_app/:
cd sample_app
pytest
```

Tests covered:

| Test | Description |
|---|---|
| `test_cart_total_no_discount` | 2 × $20 + 8% tax = $43.20, HTTP 200 |
| `test_missing_auth_returns_401` | No Authorization header → 401 |
| `test_wrong_token_returns_401` | Wrong Bearer token → 401 |

The discount-code path is intentionally not covered by automated tests.
