"""Client Web transaction submit + status (Supabase / MySQL via beanthentic_env)."""
from __future__ import annotations

import html
import json
import random
import re
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

import beanthentic_env
from config.client_valid_id import display_url_for_stored, valid_id_from_row

_BASE_DIR = Path(__file__).resolve().parent.parent
_CLIENT_ID_UPLOADS_DIR = _BASE_DIR / "uploads" / "client_ids"


def _insert_returning_id(cur, sql: str, params, id_column: str) -> int:
    if beanthentic_env.is_postgresql():
        base = sql.strip().rstrip(";")
        cur.execute(f"{base} RETURNING {id_column}", params)
        row = cur.fetchone()
        if not row:
            raise RuntimeError("INSERT RETURNING returned no row")
        return int(row[id_column] if isinstance(row, dict) else row[0])
    cur.execute(sql, params)
    return int(cur.lastrowid)


def _customer_tx_columns(cur) -> set[str]:
    if beanthentic_env.is_postgresql():
        cur.execute(
            """
            SELECT column_name FROM information_schema.columns
            WHERE table_schema = CURRENT_SCHEMA() AND table_name = 'customer_transaction'
            """
        )
    else:
        cur.execute(
            """
            SELECT COLUMN_NAME AS column_name FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'customer_transaction'
            """
        )
    out: set[str] = set()
    for row in cur.fetchall() or []:
        name = row.get("column_name") or row.get("COLUMN_NAME") or ""
        if name:
            out.add(str(name))
    return out


_BEAN_FORM_LABELS = {"gcb": "Green Coffee Bean (GCB)", "roasted": "Roasted Beans"}

_ORDER_DETAIL_COLUMNS: dict[str, tuple[str, str]] = {
    "coffee_variety": ("VARCHAR(32)", "VARCHAR(32)"),
    "bean_form": ("VARCHAR(20)", "VARCHAR(20)"),
    "bean_form_label": ("VARCHAR(80)", "VARCHAR(80)"),
    "classification": ("VARCHAR(80)", "VARCHAR(80)"),
    "quantity_pack": ("VARCHAR(20)", "VARCHAR(20)"),
    "quantity_label": ("VARCHAR(40)", "VARCHAR(40)"),
}


def ensure_customer_transaction_order_columns(cur) -> set[str]:
    """Add order-detail columns if missing; return current column names."""
    cols = _customer_tx_columns(cur)
    pg = beanthentic_env.is_postgresql()
    for name, (pg_type, mysql_type) in _ORDER_DETAIL_COLUMNS.items():
        if name in cols:
            continue
        col_type = pg_type if pg else mysql_type
        if pg:
            cur.execute(f"ALTER TABLE customer_transaction ADD COLUMN IF NOT EXISTS {name} {col_type}")
        else:
            cur.execute(f"ALTER TABLE customer_transaction ADD COLUMN {name} {col_type} NULL")
        cols.add(name)
    return cols


def _order_details_from_form_json(raw: Any) -> dict:
    if not raw:
        return {}
    try:
        payload = json.loads(raw) if isinstance(raw, str) else dict(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    selections = payload.get("order_selections")
    if not isinstance(selections, dict):
        return {}
    return _normalize_order_selections(selections)


def _normalize_order_selections(raw: dict) -> dict:
    s = dict(raw or {})
    if not s.get("product") and s.get("coffee_type"):
        s["product"] = s["coffee_type"]
    if not s.get("coffee_variety") and s.get("product"):
        s["coffee_variety"] = s["product"]
    if not s.get("bean_form") and s.get("bean_type"):
        s["bean_form"] = s["bean_type"]
    if not s.get("bean_form_label") and s.get("bean_type_label"):
        s["bean_form_label"] = s["bean_type_label"]
    bean_form = str(s.get("bean_form") or "").strip()
    if bean_form and not s.get("bean_form_label"):
        s["bean_form_label"] = _BEAN_FORM_LABELS.get(bean_form, "")
    return {
        "coffee_variety": str(s.get("coffee_variety") or s.get("product") or "").strip() or None,
        "product": str(s.get("product") or s.get("coffee_variety") or "").strip() or None,
        "bean_form": bean_form or None,
        "bean_form_label": str(s.get("bean_form_label") or "").strip() or None,
        "classification": str(s.get("classification") or "").strip() or None,
        "quantity_pack": str(s.get("quantity_pack") or "").strip() or None,
        "quantity_kg": s.get("quantity_kg"),
        "quantity_label": str(s.get("quantity_label") or "").strip() or None,
        "price": s.get("price"),
    }


def _order_row_fields(order: dict) -> dict[str, Any]:
    normalized = _normalize_order_selections(order)
    out: dict[str, Any] = {}
    for key in _ORDER_DETAIL_COLUMNS:
        val = normalized.get(key)
        if val is not None and str(val).strip() != "":
            out[key] = str(val).strip()
    return out


def _order_details_from_row(row: dict) -> dict:
    from_cols = _normalize_order_selections(
        {
            "coffee_variety": row.get("coffee_variety"),
            "product": row.get("coffee_variety"),
            "bean_form": row.get("bean_form"),
            "bean_form_label": row.get("bean_form_label"),
            "classification": row.get("classification"),
            "quantity_pack": row.get("quantity_pack"),
            "quantity_label": row.get("quantity_label"),
            "quantity_kg": row.get("quantity"),
        }
    )
    if from_cols.get("coffee_variety") or from_cols.get("bean_form"):
        return from_cols
    return _normalize_order_selections(_order_details_from_form_json(row.get("client_form_json")))


def _parse_pickup_date(raw: str) -> tuple[str | None, str]:
    s = str(raw or "").strip()
    if not s:
        return None, datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    m = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{4})$", s)
    if m:
        first = int(m.group(1))
        second = int(m.group(2))
        if first > 12:
            day, month = first, second
        elif second > 12:
            month, day = first, second
        else:
            day, month = first, second
        if not 1 <= month <= 12 or not 1 <= day <= 31:
            return None, datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        d = f"{m.group(3)}-{month:02d}-{day:02d}"
        return d, f"{d} 09:00:00"
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", s)
    if m:
        return s, f"{s} 09:00:00"
    return None, datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _new_client_ref() -> str:
    return datetime.now().strftime("CW%Y%m%d%H%M%S") + f"{random.randint(0, 9999):04d}"


def _resolve_farmer_id_for_client_tx(cur, form) -> int:
    try:
        requested = int(str(form.get("farmer_id") or "0"))
    except (TypeError, ValueError):
        requested = 0
    if requested > 0:
        cur.execute("SELECT farmer_id FROM farmers WHERE farmer_id = %s LIMIT 1", (requested,))
        row = cur.fetchone()
        if row and int(row.get("farmer_id") or 0) > 0:
            return int(row["farmer_id"])

    name = " ".join(str(form.get("farmer_name") or "").split())
    if name:
        cur.execute(
            """
            SELECT f.farmer_id
            FROM farmers f
            INNER JOIN personal_information pi ON pi.farmer_id = f.farmer_id
            WHERE LOWER(TRIM(CONCAT(COALESCE(pi.first_name, ''), ' ', COALESCE(pi.last_name, '')))) = LOWER(%s)
            ORDER BY f.farmer_id ASC
            LIMIT 1
            """,
            (name,),
        )
        row = cur.fetchone()
        if row and int(row.get("farmer_id") or 0) > 0:
            return int(row["farmer_id"])
    return 0


def _farmer_sale_is_available(cur, farmer_id: int) -> tuple[bool, str]:
    """Read the admin-controlled farmer sale lock without assuming one schema version."""
    if farmer_id <= 0:
        return False, "Select an available farmer."
    if beanthentic_env.is_postgresql():
        cur.execute(
            """
            SELECT column_name FROM information_schema.columns
            WHERE table_schema = CURRENT_SCHEMA() AND table_name = 'farmers'
            """
        )
    else:
        cur.execute(
            """
            SELECT COLUMN_NAME AS column_name FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'farmers'
            """
        )
    columns = {
        str(row.get("column_name") or row.get("COLUMN_NAME") or "").lower()
        for row in cur.fetchall() or []
    }
    candidates = (
        "self_sale_locked",
        "self_sale_frozen",
        "records_frozen",
        "records_locked",
        "self_sale_status",
        "records_status",
    )
    present = [name for name in candidates if name in columns]
    if not present:
        return True, ""
    cur.execute(
        f"SELECT {', '.join(present)} FROM farmers WHERE farmer_id = %s LIMIT 1",
        (farmer_id,),
    )
    row = cur.fetchone() or {}
    for name in present:
        raw = row.get(name)
        text = str(raw or "").strip().lower()
        if name.endswith("_locked") or name.endswith("_frozen"):
            blocked = raw in (True, 1, "1") or text in ("true", "yes", "locked", "frozen", "blocked")
        else:
            blocked = text in ("locked", "frozen", "blocked", "suspended", "disabled", "inactive", "off", "0")
        if blocked:
            return False, "This farmer's Records are temporarily frozen. Self-sale is unavailable until the farmer is cleared by admin."
    return True, ""


def _catalog_amount_for_order(cur, selections: dict, quantity_kg: float) -> float | None:
    cur.execute("SELECT * FROM coffee_pricelist LIMIT 200")
    rows = cur.fetchall() or []
    product = str(selections.get("product") or "").strip().lower()
    bean_form = str(selections.get("bean_form") or "").strip().lower()
    classification = str(selections.get("classification") or "").strip().lower()
    for row in rows:
        normalized = {str(key).lower(): value for key, value in row.items()}
        def pick(*names):
            for name in names:
                if normalized.get(name) not in (None, ""):
                    return normalized[name]
            return ""
        row_product = str(pick("product", "coffee_variety", "variety", "coffee_type", "name") or "").strip().lower()
        row_form = str(pick("bean_form", "bean_type", "form") or "").strip().lower()
        row_classification = str(pick("classification", "grade", "quality") or "").strip().lower()
        if row_product != product or (row_form and row_form != bean_form) or (row_classification and row_classification != classification):
            continue
        try:
            unit_price = float(pick("price", "unit_price", "price_per_kg", "amount"))
        except (TypeError, ValueError):
            continue
        if unit_price <= 0:
            continue
        return round(unit_price * quantity_kg, 2)
    return None


def _save_client_valid_id_file(tx_id: int, upload) -> tuple[str | None, str | None]:
    if not upload or not str(getattr(upload, "filename", "") or "").strip():
        return None, None
    ext = "jpg"
    filename = str(upload.filename)
    if "." in filename:
        guess = filename.rsplit(".", 1)[-1].lower()
        if guess in ("jpg", "jpeg", "png", "webp"):
            ext = "jpg" if guess == "jpeg" else guess
    _CLIENT_ID_UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    fname = f"tx_{tx_id}_{int(datetime.now().timestamp())}.{ext}"
    path = _CLIENT_ID_UPLOADS_DIR / fname
    raw = upload.read()
    if not raw:
        upload.stream.seek(0)
        raw = upload.stream.read()
    if not raw:
        return None, None
    path.write_bytes(raw)
    local_path = f"/uploads/client_ids/{fname}"
    ctype = str(getattr(upload, "mimetype", "") or "").strip()
    if not ctype or ctype == "application/octet-stream":
        ctype = f"image/{ext}" if ext != "jpg" else "image/jpeg"
    return local_path, filename


def _insert_farmer_pending_notif(farmer_id: int, message: str) -> None:
    """Best-effort notification on a separate connection (must not roll back the main txn)."""
    if farmer_id <= 0:
        return
    conn = None
    try:
        conn = beanthentic_env.connect()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT farmer_id, user_id FROM farmers WHERE farmer_id = %s LIMIT 1",
                (farmer_id,),
            )
            owner = cur.fetchone()
            if not owner or int(owner.get("user_id") or 0) <= 0:
                return
            uid = int(owner["user_id"])
            msg = " ".join(str(message or "").split())[:255] or "Update from Beanthentic"
            is_read = False if beanthentic_env.is_postgresql() else 0
            cur.execute(
                """
                INSERT INTO farmer_notification
                  (farmer_id, user_id, notification_type, message, is_read)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (farmer_id, uid, "record_pending", msg, is_read),
            )
        conn.commit()
    except Exception:
        if conn:
            try:
                conn.rollback()
            except Exception:
                pass
    finally:
        if conn:
            conn.close()


def _parse_order_selections(form) -> dict:
    raw = str(form.get("order_selections_json") or "").strip()
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return parsed
        except (TypeError, ValueError):
            pass

    product = str(form.get("product") or form.get("coffee_type") or "").strip()
    bean_form = str(form.get("bean_form") or form.get("bean_type") or "").strip()
    classification = str(form.get("classification") or "").strip()
    quantity_pack = str(form.get("product_quantity_pack") or form.get("quantity_pack") or "").strip()
    quantity_kg = None
    try:
        qty_kg_raw = form.get("product_quantity_kg")
        if qty_kg_raw is not None and str(qty_kg_raw).strip() != "":
            quantity_kg = float(str(qty_kg_raw))
    except (TypeError, ValueError):
        quantity_kg = None
    price = None
    try:
        price_raw = form.get("payment_amount")
        if price_raw is not None and str(price_raw).strip() != "":
            price = float(str(price_raw))
    except (TypeError, ValueError):
        price = None
    selections = _normalize_order_selections(
        {
            "product": product or None,
            "coffee_variety": product or None,
            "bean_form": bean_form or None,
            "classification": classification or None,
            "quantity_pack": quantity_pack or None,
            "quantity_kg": quantity_kg if quantity_kg and quantity_kg > 0 else None,
            "price": price,
        }
    )
    if not selections.get("quantity_label"):
        if selections.get("quantity_pack"):
            selections["quantity_label"] = selections["quantity_pack"]
        elif selections.get("quantity_kg"):
            try:
                kg = float(selections["quantity_kg"])
                selections["quantity_label"] = f"{kg:g} kg"
            except (TypeError, ValueError):
                pass
    return selections


def submit_client_transaction(form, upload) -> tuple[dict, int]:
    buyer = str(form.get("client_name") or form.get("buyer_name") or "").strip()
    product = str(form.get("product_type") or form.get("product") or "").strip()
    pickup_display = str(form.get("pickup_date") or "").strip()
    transaction_type = str(form.get("transaction_type") or "pickup").strip() or "pickup"
    payment_method = str(form.get("payment_method") or "Cash").strip() or "Cash"
    quantity_unit = str(form.get("quantity_unit") or "KG").strip() or "KG"

    if not buyer or not product:
        return {"ok": False, "error": "Name and product are required."}, 400

    try:
        qty = float(str(form.get("quantity_kg") or "0"))
    except (TypeError, ValueError):
        qty = 0.0
    if qty <= 0:
        return {"ok": False, "error": "Quantity must be greater than zero."}, 400

    try:
        amount = float(str(form.get("payment_amount") or "0"))
    except (TypeError, ValueError):
        amount = 0.0
    if amount <= 0:
        return {"ok": False, "error": "Amount to pay is required."}, 400

    if not upload or not str(getattr(upload, "filename", "") or "").strip():
        return {"ok": False, "error": "Valid ID is required."}, 400

    pickup_date, txn_date = _parse_pickup_date(pickup_display)
    ref = str(form.get("reference_no") or "").strip() or _new_client_ref()
    order_selections = _parse_order_selections(form)

    form_payload = {
        "transaction_type": transaction_type,
        "client_name": buyer,
        "pickup_date": pickup_display,
        "pickup_date_iso": pickup_date,
        "product_type": product,
        "quantity_kg": qty,
        "quantity_unit": quantity_unit,
        "payment_method": payment_method,
        "payment_amount": amount,
        "reference_no": ref,
        "submitted_from": "client_web",
        "order_selections": order_selections,
    }
    form_json = json.dumps(form_payload, ensure_ascii=False)

    conn = None
    try:
        conn = beanthentic_env.connect()
        cur = conn.cursor()
        farmer_id = _resolve_farmer_id_for_client_tx(cur, form)
        if farmer_id <= 0:
            return {
                "ok": False,
                "error": "farmer_id is required. Select a farmer from the dropdown.",
            }, 400
        sale_available, sale_message = _farmer_sale_is_available(cur, farmer_id)
        if not sale_available:
            return {"ok": False, "error": sale_message}, 409
        catalog_amount = _catalog_amount_for_order(cur, order_selections, qty)
        if catalog_amount is None:
            return {"ok": False, "error": "The selected product has no current price in the Beanthentic price list. Please refresh and try again."}, 409
        amount = catalog_amount

        cols = _customer_tx_columns(cur)
        base_row = {
            "farmer_id": farmer_id,
            "buyer_name": buyer,
            "product": product,
            "quantity": qty,
            "amount": amount,
            "payment_amount": amount,
            "payment_method": payment_method,
            "reference_no": ref,
            "transaction_date": txn_date,
        }
        optional = {
            "transaction_type": transaction_type,
            "pickup_date": pickup_date,
            "pickup_date_display": pickup_display,
            "quantity_unit": quantity_unit,
            "submitted_from": "client_web",
            "client_form_json": form_json,
        }
        row = {**base_row}
        for key, val in optional.items():
            if key in cols:
                row[key] = val
        for key, val in _order_row_fields(order_selections).items():
            if key in cols:
                row[key] = val

        cur.execute(
            "SELECT customer_transaction_id FROM customer_transaction WHERE reference_no = %s LIMIT 1",
            (ref,),
        )
        ex = cur.fetchone()
        if ex:
            tx_id = int(ex["customer_transaction_id"])
            sets = [f"{k} = %s" for k in row if k != "reference_no"]
            vals = [row[k] for k in row if k != "reference_no"] + [tx_id]
            cur.execute(
                f"UPDATE customer_transaction SET {', '.join(sets)} WHERE customer_transaction_id = %s",
                vals,
            )
        else:
            fields = list(row.keys())
            tx_id = _insert_returning_id(
                cur,
                f"INSERT INTO customer_transaction ({', '.join(fields)}) VALUES ({', '.join(['%s'] * len(fields))})",
                [row[f] for f in fields],
                "customer_transaction_id",
            )

        valid_path, valid_name = _save_client_valid_id_file(tx_id, upload)
        if valid_path:
            form_payload["valid_id_path"] = valid_path
            form_payload["valid_id_filename"] = valid_name
            upd: dict[str, Any] = {}
            if "valid_id_path" in cols:
                upd["valid_id_path"] = valid_path
            if "valid_id_filename" in cols and valid_name:
                upd["valid_id_filename"] = valid_name
            if "valid_id" in cols:
                upd["valid_id"] = valid_path
            if "client_form_json" in cols:
                upd["client_form_json"] = json.dumps(form_payload, ensure_ascii=False)
            if upd:
                sets = [f"{k} = %s" for k in upd]
                cur.execute(
                    f"UPDATE customer_transaction SET {', '.join(sets)} WHERE customer_transaction_id = %s",
                    list(upd.values()) + [tx_id],
                )

        remarks = f"Client Web {transaction_type}"
        if pickup_display:
            remarks += f"; pickup={pickup_display}"
        if valid_path:
            remarks += f"; valid_id={valid_path}"

        cur.execute(
            """
            INSERT INTO transaction_history
              (customer_transaction_id, status, remarks, changed_by_user_id)
            VALUES (%s, %s, %s, NULL)
            """,
            (tx_id, "pending", remarks[:255]),
        )

        rec_msg = "New pending record"
        if buyer:
            rec_msg += f" from {buyer}"
        if ref:
            rec_msg += f" ({ref})"
        rec_msg += ". Open Records to review."

        conn.commit()
        threading.Thread(
            target=_insert_farmer_pending_notif,
            args=(farmer_id, rec_msg),
            daemon=True,
            name="client-transaction-notification",
        ).start()
        return {
            "ok": True,
            "customer_transaction_id": tx_id,
            "reference_no": ref,
            "status": "pending",
            "farmer_id": farmer_id,
            "saved_fields": {**form_payload, "valid_id_saved": bool(valid_path)},
            "message": "Transaction submitted. Waiting for farmer approval in the app.",
        }, 200
    except Exception as exc:
        if conn:
            try:
                conn.rollback()
            except Exception:
                pass
        return {"ok": False, "error": f"client_transaction_submit failed: {exc}"}, 500
    finally:
        if conn:
            conn.close()


def _status_payload_from_row(row: dict, status: str) -> dict:
    pickup_display = str(row.get("pickup_date_display") or "").strip()
    pickup_raw = str(row.get("pickup_date") or "")
    pickup_label = pickup_display or pickup_raw
    qty = float(row.get("quantity") or 0)
    pay_amt = float(row.get("payment_amount") or 0)
    amt = float(row.get("amount") or 0)
    total = amt if amt > 0 else (pay_amt if pay_amt > 0 else 0)
    change = max(0.0, pay_amt - total)
    unit = str(row.get("quantity_unit") or "KG").strip() or "KG"
    at = str(row.get("transaction_date") or "")
    payment_method = str(row.get("payment_method") or "").strip() or "Cash"
    ref_no = str(row.get("reference_no") or "")
    buyer_name = str(row.get("buyer_name") or "")
    product_name = str(row.get("product") or "")
    farmer_id = int(row.get("farmer_id") or 0)
    farmer_name = " ".join(
        str(row.get("farmer_name") or row.get("farmer_full_name") or "").split()
    ).strip()
    cid = int(row.get("customer_transaction_id") or 0)
    status = str(status or "pending").strip().lower()
    stored_valid_id = valid_id_from_row(row)
    valid_display = display_url_for_stored(stored_valid_id)
    order_details = _order_details_from_row(row)
    order_selections = {
        k: order_details.get(k)
        for k in (
            "product",
            "coffee_variety",
            "bean_form",
            "bean_form_label",
            "classification",
            "quantity_pack",
            "quantity_kg",
            "quantity_label",
        )
    }
    return {
        "ok": True,
        "customer_transaction_id": cid,
        "reference_no": ref_no,
        "farmer_id": farmer_id,
        "farmer_name": farmer_name,
        "valid_id_path": stored_valid_id,
        "valid_id_url": valid_display,
        "valid_id_filename": str(row.get("valid_id_filename") or "").strip(),
        "status": status,
        "is_pending": status == "pending",
        "is_approved": status == "approved",
        "is_dismissed": status == "dismissed",
        "is_sent_to_client": status == "sent_to_client",
        "buyer_name": buyer_name,
        "product": product_name,
        "coffee_variety": order_details.get("coffee_variety") or order_details.get("product"),
        "bean_form": order_details.get("bean_form"),
        "bean_form_label": order_details.get("bean_form_label"),
        "classification": order_details.get("classification"),
        "quantity_pack": order_details.get("quantity_pack"),
        "quantity_label": order_details.get("quantity_label"),
        "order_selections": order_selections,
        "quantity": qty,
        "quantity_kg": qty,
        "amount": amt,
        "payment_amount": pay_amt,
        "payment_method": payment_method,
        "pickup_date": pickup_label,
        "pickup_date_display": pickup_display,
        "quantity_unit": unit,
        "total": total,
        "change": change,
        "transaction_at": at,
        "receipt": {
            "ref": ref_no,
            "reference_no": ref_no,
            "buyer": buyer_name,
            "buyer_name": buyer_name,
            "pickup_date": pickup_label,
            "product": product_name,
            "coffee_variety": order_details.get("coffee_variety") or order_details.get("product"),
            "bean_form": order_details.get("bean_form"),
            "bean_form_label": order_details.get("bean_form_label"),
            "classification": order_details.get("classification"),
            "quantity_pack": order_details.get("quantity_pack"),
            "quantity_label": order_details.get("quantity_label"),
            "order_selections": order_selections,
            "qty": qty,
            "quantity_kg": qty,
            "unit": unit,
            "amount": total,
            "payment": payment_method,
            "payment_method": payment_method,
            "paymentAmount": pay_amt,
            "payment_amount": pay_amt,
            "total": total,
            "change": change,
            "at": at,
        },
    }


def get_client_transaction_status(reference_no: str = "", customer_transaction_id: int = 0) -> tuple[dict, int]:
    ref = str(reference_no or "").strip()
    tx_id = int(customer_transaction_id or 0)
    if not ref and tx_id <= 0:
        return {"ok": False, "error": "reference_no or customer_transaction_id is required."}, 400

    conn = None
    try:
        conn = beanthentic_env.connect()
        cur = conn.cursor()
        select_full = """
            SELECT ct.customer_transaction_id, ct.reference_no, ct.buyer_name, ct.product,
                   ct.quantity, ct.amount, ct.payment_amount, ct.payment_method,
                   ct.transaction_date, ct.pickup_date, ct.pickup_date_display,
                   ct.quantity_unit, ct.farmer_id,
                   ct.coffee_variety, ct.bean_form, ct.bean_form_label,
                   ct.classification, ct.quantity_pack, ct.quantity_label,
                   ct.valid_id_path, ct.valid_id, ct.valid_id_filename, ct.client_form_json,
                   TRIM(CONCAT(COALESCE(pi.first_name, ''), ' ', COALESCE(pi.last_name, '')))
                     AS farmer_name
            FROM customer_transaction ct
            LEFT JOIN personal_information pi ON pi.farmer_id = ct.farmer_id
            WHERE """
        select_no_farmer = """
            SELECT customer_transaction_id, reference_no, buyer_name, product, quantity,
                   amount, payment_amount, payment_method, transaction_date,
                   pickup_date, pickup_date_display, quantity_unit
            FROM customer_transaction WHERE """
        select_base = """
            SELECT customer_transaction_id, reference_no, buyer_name, product, quantity,
                   amount, payment_amount, payment_method, transaction_date
            FROM customer_transaction WHERE """
        row = None
        try:
            if tx_id > 0:
                cur.execute(select_full + "ct.customer_transaction_id = %s LIMIT 1", (tx_id,))
            else:
                cur.execute(select_full + "ct.reference_no = %s LIMIT 1", (ref,))
            row = cur.fetchone()
        except Exception:
            try:
                if tx_id > 0:
                    cur.execute(select_no_farmer + "customer_transaction_id = %s LIMIT 1", (tx_id,))
                else:
                    cur.execute(select_no_farmer + "reference_no = %s LIMIT 1", (ref,))
                row = cur.fetchone()
            except Exception:
                if tx_id > 0:
                    cur.execute(select_base + "customer_transaction_id = %s LIMIT 1", (tx_id,))
                else:
                    cur.execute(select_base + "reference_no = %s LIMIT 1", (ref,))
                row = cur.fetchone()
        if not row:
            return {"ok": False, "error": "Transaction not found."}, 404

        cid = int(row["customer_transaction_id"])
        cur.execute(
            """
            SELECT status FROM transaction_history
            WHERE customer_transaction_id = %s
            ORDER BY transaction_history_id DESC LIMIT 1
            """,
            (cid,),
        )
        h = cur.fetchone()
        status = str((h or {}).get("status") or "pending").strip().lower()
        return _status_payload_from_row(row, status), 200
    except Exception as exc:
        return {"ok": False, "error": f"client_transaction_status failed: {exc}"}, 500
    finally:
        if conn:
            conn.close()


def _money_like(val: Any) -> str:
    try:
        return f"{float(val):,.2f}"
    except (TypeError, ValueError):
        return "0.00"


def _format_receipt_datetime(raw: str) -> tuple[str, str]:
    text = str(raw or "").strip()
    if not text:
        return "-", "-"
    dt = None
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            dt = datetime.strptime(text.replace("Z", "+0000")[:26], fmt)
            break
        except ValueError:
            continue
    if dt is None:
        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return "-", "-"
    date_label = dt.strftime("%B %d, %Y").replace(" 0", " ")
    time_label = dt.strftime("%I:%M %p").lstrip("0")
    return date_label, time_label


def _receipt_logo_data_uri() -> str:
    for name in ("beanthentic-logo.png", "beanthentic-logo.jpg", "beanthentic-logo.webp"):
        path = _BASE_DIR / "static" / "images" / name
        if not path.is_file():
            continue
        try:
            import base64

            mime = "image/png" if name.endswith(".png") else "image/jpeg"
            if name.endswith(".webp"):
                mime = "image/webp"
            encoded = base64.b64encode(path.read_bytes()).decode("ascii")
            return f"data:{mime};base64,{encoded}"
        except OSError:
            continue
    return ""


def _build_receipt_html(receipt: dict, payload: dict | None = None) -> str:
    merged = {**(payload or {}), **(receipt or {})}
    ref = str(merged.get("ref") or merged.get("reference_no") or "").strip()
    buyer = str(merged.get("buyer") or merged.get("buyer_name") or "").strip()
    product = str(merged.get("product") or "").strip()
    qty = merged.get("qty")
    if qty is None:
        qty = merged.get("quantity_kg")
    if qty is None:
        qty = merged.get("quantity")
    unit = str(merged.get("unit") or merged.get("quantity_unit") or "KG").strip() or "KG"
    qty_str = "-" if qty is None or str(qty).strip() == "" else f"{qty}{unit.upper()}"

    amount = float(merged.get("amount") or merged.get("total") or 0)
    total = float(merged.get("total") or merged.get("amount") or 0)
    if total <= 0:
        total = amount
    if amount <= 0:
        amount = total
    pay_amt = float(merged.get("paymentAmount") or merged.get("payment_amount") or amount)
    change = merged.get("change")
    if change is None:
        change = max(0.0, pay_amt - total)
    else:
        change = float(change)
    payment = str(merged.get("payment") or merged.get("payment_method") or "Cash").strip() or "Cash"
    at = str(merged.get("at") or merged.get("transaction_at") or "").strip()
    date_label, time_label = _format_receipt_datetime(at)

    logo = _receipt_logo_data_uri()
    logo_block = (
        f'<img class="logo" src="{logo}" alt="Beanthentic">'
        if logo
        else '<div class="logo-text">Beanthentic</div>'
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Beanthentic Receipt {html.escape(ref)}</title>
<style>
body{{margin:0;padding:24px;background:#f1f5f9;font-family:system-ui,-apple-system,Segoe UI,sans-serif;}}
.card{{max-width:420px;margin:0 auto;background:#fff;border:1px solid #e2e8f0;border-radius:14px;padding:1.35rem 1.15rem;box-shadow:0 2px 12px rgba(15,23,42,.06);}}
.brand{{text-align:center;margin-bottom:.85rem}}
.logo{{width:128px;height:auto;display:block;margin:0 auto .65rem}}
.logo-text{{font-size:1.1rem;font-weight:800;color:#1a5f3f;margin-bottom:.65rem}}
h1{{margin:0 0 .2rem;font-size:1.35rem;color:#1a2b4b}}
.sub{{margin:0;font-size:.82rem;color:#94a3b8;font-weight:600}}
hr{{border:0;margin:.85rem 0}}
.solid{{border-top:1px solid #e2e8f0}}
.dash{{border-top:1px dashed #cbd5e1}}
.section{{margin:0 0 .55rem;font-size:.68rem;font-weight:800;letter-spacing:.08em;text-transform:uppercase;color:#94a3b8}}
.row{{display:flex;justify-content:space-between;gap:.75rem;margin:.42rem 0;font-size:.9rem}}
.label{{color:#64748b;font-weight:600}}
.value{{color:#1a2b4b;font-weight:800;text-align:right}}
.product{{display:flex;justify-content:space-between;font-size:.92rem;font-weight:700;color:#1a2b4b}}
.thanks{{margin-top:.95rem;text-align:center;font-size:.88rem;font-weight:700;color:#64748b}}
</style>
</head>
<body>
<div class="card">
  <div class="brand">{logo_block}<h1>Beanthentic Coffee</h1><p class="sub">Official Transaction Receipt</p></div>
  <hr class="solid">
  <div class="row"><span class="label">Ref. No.</span><span class="value">#{html.escape(ref or "-")}</span></div>
  <div class="row"><span class="label">Date</span><span class="value">{html.escape(date_label)}</span></div>
  <div class="row"><span class="label">Time</span><span class="value">{html.escape(time_label)}</span></div>
  <hr class="dash">
  <p class="section">Buyer Details</p>
  <div class="row"><span class="label">Buyer Name</span><span class="value">{html.escape(buyer or "-")}</span></div>
  <hr class="dash">
  <p class="section">Product Info</p>
  <div class="product"><span>{html.escape(product or "-")}</span><span>{html.escape(qty_str)}</span></div>
  <hr class="dash">
  <p class="section">Payment Details</p>
  <div class="row"><span class="label">Amount</span><span class="value">{html.escape(_money_like(amount))}</span></div>
  <div class="row"><span class="label">Payment</span><span class="value">{html.escape(payment)}</span></div>
  <div class="row"><span class="label">Payment Amt</span><span class="value">{html.escape(_money_like(pay_amt))}</span></div>
  <div class="row"><span class="label">Total</span><span class="value">{html.escape(_money_like(total))}</span></div>
  <div class="row"><span class="label">Change</span><span class="value">{html.escape(_money_like(change))}</span></div>
  <p class="thanks">Thank you for using Beanthentic!</p>
</div>
</body>
</html>"""


def get_receipt_download(reference_no: str) -> tuple[dict, int]:
    ref = str(reference_no or "").strip()
    if not ref:
        return {"ok": False, "error": "reference_no is required."}, 400

    payload, status = get_client_transaction_status(reference_no=ref)
    if status != 200 or not payload.get("ok"):
        return payload, status if status != 200 else 404

    receipt = payload.get("receipt") or {}
    html_doc = _build_receipt_html(receipt, payload)
    ref_safe = re.sub(r"[^\w-]+", "_", ref) or "receipt"
    filename = f"Beanthentic-Receipt-{ref_safe}.html"
    return {"ok": True, "html": html_doc, "filename": filename}, 200
