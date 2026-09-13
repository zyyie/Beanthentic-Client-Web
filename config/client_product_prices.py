"""Official product prices for client transactions (coffee_pricelist)."""

from __future__ import annotations

import re
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

import beanthentic_env

_CLASSIFICATION_ALIASES = {
    "small": "small_beans",
    "medium": "medium_beans",
    "large": "large_beans",
    "small beans": "small_beans",
    "medium beans": "medium_beans",
    "large beans": "large_beans",
    "ground beans": "ground_beans",
    "whole beans": "whole_beans",
}


def _normalize_key(raw: str) -> str:
    return re.sub(r"\s+", " ", str(raw or "").strip().lower())


def normalize_variety(raw: str) -> str:
    return _normalize_key(raw)


def normalize_bean_type(raw: str) -> str:
    text = _normalize_key(raw)
    if text in ("green coffee bean (gcb)", "green coffee bean", "gcb"):
        return "gcb"
    if text in ("roasted beans", "roasted"):
        return "roasted"
    return text


def normalize_classification(raw: str) -> str:
    text = _normalize_key(raw)
    if not text:
        return ""
    if text in _CLASSIFICATION_ALIASES:
        return _CLASSIFICATION_ALIASES[text]
    return text.replace(" ", "_")


def _money(value: float | Decimal) -> float:
    dec = Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return float(dec)


def fetch_active_prices() -> list[dict[str, Any]]:
    conn = None
    try:
        conn = beanthentic_env.connect()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT variety, bean_type, classification, price_per_kg, currency
            FROM coffee_pricelist
            WHERE COALESCE(is_active, TRUE) = TRUE
            ORDER BY variety, bean_type, classification
            """
        )
        rows = cur.fetchall() or []
        out: list[dict[str, Any]] = []
        for row in rows:
            price = row.get("price_per_kg")
            if price is None:
                continue
            out.append(
                {
                    "variety": normalize_variety(row.get("variety")),
                    "bean_type": normalize_bean_type(row.get("bean_type")),
                    "classification": normalize_classification(row.get("classification")),
                    "price_per_kg": _money(price),
                    "currency": str(row.get("currency") or "PHP").strip() or "PHP",
                }
            )
        return out
    except Exception:
        return []
    finally:
        if conn:
            conn.close()


def lookup_price_per_kg(
    variety: str,
    bean_type: str,
    classification: str = "",
    prices: list[dict[str, Any]] | None = None,
) -> float | None:
    rows = prices if prices is not None else fetch_active_prices()
    if not rows:
        return None

    variety_key = normalize_variety(variety)
    bean_key = normalize_bean_type(bean_type)
    class_key = normalize_classification(classification)
    if not variety_key or not bean_key:
        return None

    exact: float | None = None
    bean_match: float | None = None
    variety_match: float | None = None
    for row in rows:
        row_variety = normalize_variety(row.get("variety"))
        row_bean = normalize_bean_type(row.get("bean_type"))
        row_class = normalize_classification(row.get("classification"))
        price = row.get("price_per_kg")
        if price is None or row_variety != variety_key:
            continue
        variety_match = float(price)
        if row_bean != bean_key:
            continue
        bean_match = float(price)
        if row_class and class_key and row_class != class_key:
            continue
        if row_class == class_key or not row_class or not class_key:
            exact = float(price)
            break

    if exact is not None:
        return exact
    if bean_match is not None:
        return bean_match
    return variety_match


def compute_order_total(
    variety: str,
    bean_type: str,
    classification: str,
    quantity_kg: float,
    prices: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    try:
        qty = float(quantity_kg)
    except (TypeError, ValueError):
        qty = 0.0
    unit = lookup_price_per_kg(variety, bean_type, classification, prices=prices)
    if unit is None or qty <= 0:
        return {
            "ok": False,
            "unit_price_per_kg": None,
            "quantity_kg": qty if qty > 0 else None,
            "total_amount": None,
        }
    total = _money(Decimal(str(unit)) * Decimal(str(qty)))
    return {
        "ok": True,
        "unit_price_per_kg": unit,
        "quantity_kg": qty,
        "total_amount": total,
    }


def prices_for_client_api() -> dict[str, Any]:
    rows = fetch_active_prices()
    return {"ok": True, "prices": rows, "source": "coffee_pricelist"}
