"""Pricing utilities for the sample e-commerce app."""

# Known discount codes: code -> fixed dollar amount off
DISCOUNT_CODES = {
    "SAVE10": 10.00,
}


def compute_cart_total(items: list[dict], discount_code: str | None, tax_rate: float) -> float:
    """Return the final cart total (float, rounded to 2 decimal places).

    Business rule:
        1. Sum item prices * quantities to get the subtotal.
        2. Apply tax to the subtotal: taxed_total = subtotal * (1 + tax_rate).
        3. If a valid discount_code is provided, subtract its fixed dollar
           amount from the POST-TAX total.
        4. Return max(result, 0.0) so the total is never negative.

    Discount codes are fixed amounts (e.g. SAVE10 = $10.00 off).
    An unrecognised code is silently ignored.
    """
    subtotal = sum(item["price"] * item["qty"] for item in items)

    discount = DISCOUNT_CODES.get(discount_code or "", 0.0)

    # Apply discount before tax, then add tax on the reduced subtotal.
    discounted_subtotal = subtotal - discount
    total = discounted_subtotal * (1 + tax_rate)

    return round(max(total, 0.0), 2)
