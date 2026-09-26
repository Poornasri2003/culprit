"""Frontend Flask service — single page that calls the backend and shows the total."""

import os

import requests
from flask import Flask, render_template_string, request

app = Flask(__name__)

BACKEND_URL = os.environ.get("BACKEND_URL", "http://localhost:8000")

_PAGE = """<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><title>Cart</title></head>
<body>
  <h1>Cart Total Demo</h1>
  <form method="post">
    <label>Item price: <input name="price" type="number" step="0.01" value="50.00"></label><br>
    <label>Quantity:   <input name="qty"   type="number" value="2"></label><br>
    <label>Discount code: <input name="discount_code" value=""></label><br>
    <button type="submit">Calculate</button>
  </form>
  {% if total is not none %}
    <p><strong>Total: ${{ "%.2f"|format(total) }}</strong></p>
  {% endif %}
  {% if error %}
    <p style="color:red">Error: {{ error }}</p>
  {% endif %}
</body>
</html>"""


@app.route("/", methods=["GET", "POST"])
def index():
    total = None
    error = None

    if request.method == "POST":
        token = os.environ.get("BEARER_TOKEN", "")
        price = float(request.form.get("price", 0))
        qty = int(request.form.get("qty", 1))
        discount_code = request.form.get("discount_code") or None

        payload = {"items": [{"price": price, "qty": qty}], "discount_code": discount_code}
        headers = {"Authorization": f"Bearer {token}"}

        try:
            resp = requests.post(f"{BACKEND_URL}/cart/total", json=payload, headers=headers, timeout=5)
            if resp.status_code == 200:
                total = resp.json().get("total")
            else:
                error = f"Backend returned HTTP {resp.status_code}"
        except requests.RequestException as exc:
            error = str(exc)

    return render_template_string(_PAGE, total=total, error=error)


if __name__ == "__main__":
    app.run(port=5000)
