"""Client Web transaction submit + status (Supabase / MySQL via beanthentic_env)."""
from __future__ import annotations

import html
import json
import random
import re
from datetime import datetime
from pathlib import Path
from typing import Any

import beanthentic_env

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


def _parse_pickup_date(raw: str) -> tuple[str | None, str]:
    s = str(raw or "").strip()
    if not s:
        return None, datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    m = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{4})$", s)
    if m:
        d = f"{m.group(3)}-{int(m.group(2)):02d}-{int(m.group(1)):02d}"
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
    upload.save(path)
    return f"/uploads/client_ids/{fname}", filename


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
        _insert_farmer_pending_notif(farmer_id, rec_msg)
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
    cid = int(row.get("customer_transaction_id") or 0)
    status = str(status or "pending").strip().lower()
    return {
        "ok": True,
        "customer_transaction_id": cid,
        "reference_no": ref_no,
        "status": status,
        "is_pending": status == "pending",
        "is_approved": status == "approved",
        "is_dismissed": status == "dismissed",
        "is_sent_to_client": status == "sent_to_client",
        "buyer_name": buyer_name,
        "product": product_name,
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
                cur.execute(select_full + "customer_transaction_id = %s LIMIT 1", (tx_id,))
            else:
                cur.execute(select_full + "reference_no = %s LIMIT 1", (ref,))
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
