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

# Official GCB prices (Small / Medium / Large are the same). Total = rate × kg.
GCB_PRICE_PER_KG: dict[str, float] = {
    "robusta": 400.0,
    "excelsa": 750.0,
    "liberica": 2000.0,
}

# Official roasted pack prices (ground and whole are the same).
ROASTED_PACK_PRICES: dict[str, dict[str, float]] = {
    "robusta": {"250g": 200.0, "500g": 500.0, "1kg": 750.0},
    "excelsa": {"250g": 250.0, "500g": 500.0, "1kg": 900.0},
    "liberica": {"250g": 750.0, "500g": 1500.0, "1kg": 3000.0},
}

_PACK_TO_KG = {"250g": 0.25, "500g": 0.5, "1kg": 1.0}

_PACK_ALIASES = {
    "250g": "250g",
    "250grams": "250g",
    "250gram": "250g",
    "0.25kg": "250g",
    "0.25": "250g",
    "500g": "500g",
    "500grams": "500g",
    "500gram": "500g",
    "0.5kg": "500g",
    "0.50kg": "500g",
    "0.5": "500g",
    "0.50": "500g",
    "1kg": "1kg",
    "1kl": "1kg",
    "1000g": "1kg",
    "1.0kg": "1kg",
    "1.00kg": "1kg",
}


def _normalize_key(raw: str) -> str:
    return re.sub(r"\s+", " ", str(raw or "").strip().lower())


def normalize_variety(raw: str) -> str:
    text = _normalize_key(raw)
    if text in ("authentic liberica", "authentic_liberica"):
        return "liberica"
    return text


def normalize_pack(raw: str) -> str:
    text = re.sub(r"\s+", "", _normalize_key(raw))
    return _PACK_ALIASES.get(text, text)


def pack_to_kg(pack: str) -> float:
    key = normalize_pack(pack)
    return float(_PACK_TO_KG.get(key) or 0.0)


def lookup_gcb_price_per_kg(variety: str) -> float | None:
    price = GCB_PRICE_PER_KG.get(normalize_variety(variety))
    if price is None:
        return None
    return _money(price)


def lookup_roasted_pack_price(variety: str, pack: str) -> float | None:
    table = ROASTED_PACK_PRICES.get(normalize_variety(variety))
    if not table:
        return None
    price = table.get(normalize_pack(pack))
    if price is None:
        return None
    return _money(price)


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
    variety_key = normalize_variety(variety)
    bean_key = normalize_bean_type(bean_type)
    if bean_key == "gcb":
        official = lookup_gcb_price_per_kg(variety_key)
        if official is not None:
            return official

    rows = prices if prices is not None else fetch_active_prices()
    if not rows:
        return None

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
    quantity_pack: str = "",
) -> dict[str, Any]:
    try:
        qty = float(quantity_kg)
    except (TypeError, ValueError):
        qty = 0.0

    bean_key = normalize_bean_type(bean_type)
    pack_key = normalize_pack(quantity_pack) if quantity_pack else ""
    if bean_key == "roasted" and not pack_key and qty > 0:
        for pack_name, pack_kg in _PACK_TO_KG.items():
            if abs(qty - pack_kg) < 0.0001:
                pack_key = pack_name
                break

    if bean_key == "roasted":
        pack_price = lookup_roasted_pack_price(variety, pack_key) if pack_key else None
        pack_kg = pack_to_kg(pack_key) if pack_key else 0.0
        if pack_price is None or pack_kg <= 0:
            return {
                "ok": False,
                "unit_price_per_kg": None,
                "quantity_kg": pack_kg if pack_kg > 0 else (qty if qty > 0 else None),
                "total_amount": None,
                "pack_price": None,
                "quantity_pack": pack_key or None,
                "pricing_mode": "pack",
            }
        unit = _money(Decimal(str(pack_price)) / Decimal(str(pack_kg)))
        return {
            "ok": True,
            "unit_price_per_kg": unit,
            "quantity_kg": pack_kg,
            "total_amount": pack_price,
            "pack_price": pack_price,
            "quantity_pack": pack_key,
            "pricing_mode": "pack",
        }

    unit = lookup_price_per_kg(variety, bean_type, classification, prices=prices)
    if unit is None or qty <= 0:
        return {
            "ok": False,
            "unit_price_per_kg": None,
            "quantity_kg": qty if qty > 0 else None,
            "total_amount": None,
            "pricing_mode": "per_kg",
        }
    total = _money(Decimal(str(unit)) * Decimal(str(qty)))
    return {
        "ok": True,
        "unit_price_per_kg": unit,
        "quantity_kg": qty,
        "total_amount": total,
        "pricing_mode": "per_kg",
    }


def prices_for_client_api() -> dict[str, Any]:
    rows = fetch_active_prices()
    return {
        "ok": True,
        "prices": rows,
        "gcb_prices": GCB_PRICE_PER_KG,
        "gcb_price_per_kg": GCB_PRICE_PER_KG,
        "roasted_packs": ROASTED_PACK_PRICES,
        "source": "official_pricelist",
    }
