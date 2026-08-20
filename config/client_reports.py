"""Client Web report helpers — transaction farmers list + misconduct report submit."""
from __future__ import annotations

import json
from typing import Any

import beanthentic_env


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


def _farmer_display_name(row: dict) -> str:
    first = str(row.get("first_name") or "").strip()
    last = str(row.get("last_name") or "").strip()
    full = f"{first} {last}".strip()
    if full:
        return full
    username = str(row.get("username") or "").strip()
    if username:
        return username
    fid = int(row.get("farmer_id") or 0)
    return f"Farmer #{fid}" if fid > 0 else "Farmer"


def get_transaction_farmers(client_name: str) -> tuple[dict, int]:
    """Farmers this client has transacted with (by buyer_name on customer_transaction)."""
    name = str(client_name or "").strip()
    if not name:
        return {"ok": False, "error": "client_name is required.", "farmers": []}, 400

    conn = None
    try:
        conn = beanthentic_env.connect()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT ct.farmer_id,
                   MAX(ct.transaction_date) AS last_transaction_at,
                   COUNT(*) AS tx_count,
                   f.farm_code,
                   u.username,
                   pi.first_name,
                   pi.last_name
            FROM customer_transaction ct
            INNER JOIN farmers f ON f.farmer_id = ct.farmer_id
            LEFT JOIN users u ON u.user_id = f.user_id
            LEFT JOIN personal_information pi ON pi.farmer_id = f.farmer_id
            WHERE LOWER(TRIM(ct.buyer_name)) = LOWER(TRIM(%s))
            GROUP BY ct.farmer_id, f.farm_code, u.username, pi.first_name, pi.last_name
            ORDER BY last_transaction_at DESC, ct.farmer_id DESC
            """,
            (name,),
        )
        farmers: list[dict] = []
        for row in cur.fetchall() or []:
            fid = int(row.get("farmer_id") or 0)
            if fid <= 0:
                continue
            farm_code = str(row.get("farm_code") or "").strip()
            last_at = row.get("last_transaction_at")
            if hasattr(last_at, "isoformat"):
                last_at = last_at.isoformat()
            else:
                last_at = str(last_at or "")
            farmers.append(
                {
                    "farmer_id": fid,
                    "farmer_name": _farmer_display_name(row),
                    "farmer_no": farm_code or str(fid),
                    "tx_count": int(row.get("tx_count") or 0),
                    "last_transaction_at": last_at,
                }
            )
        return {
            "ok": True,
            "client_name": name,
            "farmers": farmers,
            "count": len(farmers),
        }, 200
    except Exception as exc:
        return {"ok": False, "error": str(exc), "farmers": []}, 500
    finally:
        if conn:
            conn.close()


def _resolve_farmer_fields(cur, farmer_id: int, farmer_no: str, farmer_name: str) -> tuple[int, str, str]:
    fid = int(farmer_id or 0)
    fno = str(farmer_no or "").strip()
    fname = str(farmer_name or "").strip()
    if fid <= 0 or fname:
        return fid, fno, fname
    cur.execute(
        """
        SELECT f.farmer_id, f.farm_code, u.username, pi.first_name, pi.last_name
        FROM farmers f
        LEFT JOIN users u ON u.user_id = f.user_id
        LEFT JOIN personal_information pi ON pi.farmer_id = f.farmer_id
        WHERE f.farmer_id = %s LIMIT 1
        """,
        (fid,),
    )
    row = cur.fetchone()
    if not row:
        return fid, fno, fname
    fname = _farmer_display_name(row)
    if not fno:
        fno = str(row.get("farm_code") or "").strip() or str(fid)
    return fid, fno, fname


def submit_client_report(body: dict) -> tuple[dict, int]:
    payload = body if isinstance(body, dict) else {}
    reporter_name = str(payload.get("reporter_name") or "").strip()
    if not reporter_name:
        return {"ok": False, "error": "reporter_name is required."}, 400

    reason_category = str(payload.get("reason_category") or payload.get("reason") or "").strip()
    if not reason_category:
        return {"ok": False, "error": "reason_category is required."}, 400

    reason_detail = str(payload.get("reason_detail") or "").strip()
    allegation = str(payload.get("allegation") or "").strip()
    if not allegation:
        return {"ok": False, "error": "allegation is required."}, 400

    reporter_contact = str(payload.get("reporter_contact") or "").strip()
    chat_json = payload.get("chat_log") or payload.get("chat_json")
    chat_str: str | None = None
    if chat_json is not None:
        chat_str = chat_json if isinstance(chat_json, str) else json.dumps(chat_json, ensure_ascii=False)

    farmer_id = int(payload.get("farmer_id") or 0)
    farmer_no = str(payload.get("farmer_no") or "").strip()
    farmer_name = str(payload.get("farmer_name") or "").strip()

    conn = None
    try:
        conn = beanthentic_env.connect()
        cur = conn.cursor()
        farmer_id, farmer_no, farmer_name = _resolve_farmer_fields(
            cur, farmer_id, farmer_no, farmer_name
        )

        report_id = _insert_returning_id(
            cur,
            """
            INSERT INTO client_misconduct_report
              (reporter_name, reporter_contact, reason_category, reason_detail, allegation, chat_json,
               farmer_id, farmer_no, farmer_name, status)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                reporter_name,
                reporter_contact,
                reason_category,
                reason_detail,
                allegation,
                chat_str,
                farmer_id if farmer_id > 0 else None,
                farmer_no or None,
                farmer_name,
                "under review",
            ),
            "report_id",
        )
        conn.commit()
        return {
            "ok": True,
            "report_id": report_id,
            "id": report_id,
            "status": "under review",
            "message": "Your report was submitted. Our team will review it.",
        }, 200
    except Exception as exc:
        if conn:
            try:
                conn.rollback()
            except Exception:
                pass
        return {"ok": False, "error": f"client_report_submit failed: {exc}"}, 500
    finally:
        if conn:
            conn.close()


def get_client_report_status(report_id: int) -> tuple[dict, int]:
    rid = int(report_id or 0)
    if rid <= 0:
        return {"ok": False, "error": "report_id is required."}, 400

    conn = None
    try:
        conn = beanthentic_env.connect()
        cur = conn.cursor()
        if beanthentic_env.is_postgresql():
            cur.execute(
                """
                SELECT column_name FROM information_schema.columns
                WHERE table_schema = CURRENT_SCHEMA() AND table_name = 'client_misconduct_report'
                """
            )
        else:
            cur.execute(
                """
                SELECT COLUMN_NAME AS column_name FROM information_schema.COLUMNS
                WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'client_misconduct_report'
                """
            )
        columns = {
            str(row.get("column_name") or row.get("COLUMN_NAME") or "").lower()
            for row in cur.fetchall() or []
        }
        note_columns = [
            name for name in ("resolution_note", "resolution_notes", "admin_note", "review_note")
            if name in columns
        ]
        select_columns = ["report_id", "status"] + note_columns
        cur.execute(
            f"SELECT {', '.join(select_columns)} FROM client_misconduct_report WHERE report_id = %s LIMIT 1",
            (rid,),
        )
        row = cur.fetchone()
        if not row:
            return {"ok": False, "error": "Report not found."}, 404
        status = str(row.get("status") or "under review").strip().lower().replace("_", " ")
        if status not in ("pending", "under review", "resolved", "dismissed"):
            status = "under review"
        note = ""
        for name in note_columns:
            note = str(row.get(name) or "").strip()
            if note:
                break
        return {
            "ok": True,
            "report_id": rid,
            "status": status,
            "resolution_note": note,
        }, 200
    except Exception as exc:
        return {"ok": False, "error": f"client_report_status failed: {exc}"}, 500
    finally:
        if conn:
            conn.close()
