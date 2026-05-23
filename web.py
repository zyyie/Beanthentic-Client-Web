import json
import os
from datetime import date, datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from flask import Flask, jsonify, redirect, render_template, request, url_for
import pymysql
from pymysql.cursors import DictCursor

from config.mysql_app_bridge import connect_app_mysql

app = Flask(__name__)

SETTINGS_PATH = Path(__file__).resolve().parent / "settings.json"

CLIENT_FARMERS_SQL = """
  SELECT
    f.farmer_id,
    f.status,
    f.profile_photo,
    f.created_at,
    f.updated_at,
    u.username,
    u.phone_number,
    u.email,
    pi.first_name,
    pi.last_name,
    COALESCE(pi.barangay, fi.barangay) AS barangay,
    ai.federation_assoc,
    ai.coop_name
  FROM farmers f
  INNER JOIN users u ON u.user_id = f.user_id
  LEFT JOIN personal_information pi ON pi.farmer_id = f.farmer_id
  LEFT JOIN farm_information fi ON fi.farmer_id = f.farmer_id
  LEFT JOIN affiliation_information ai ON ai.farmer_id = f.farmer_id
  ORDER BY COALESCE(f.updated_at, f.created_at) DESC, f.farmer_id DESC
  LIMIT %s
"""

CLIENT_FARMERS_SQL_NO_COOP = """
  SELECT
    f.farmer_id,
    f.status,
    f.profile_photo,
    f.created_at,
    f.updated_at,
    u.username,
    u.phone_number,
    u.email,
    pi.first_name,
    pi.last_name,
    COALESCE(pi.barangay, fi.barangay) AS barangay,
    ai.federation_assoc,
    '' AS coop_name
  FROM farmers f
  INNER JOIN users u ON u.user_id = f.user_id
  LEFT JOIN personal_information pi ON pi.farmer_id = f.farmer_id
  LEFT JOIN farm_information fi ON fi.farmer_id = f.farmer_id
  LEFT JOIN affiliation_information ai ON ai.farmer_id = f.farmer_id
  ORDER BY COALESCE(f.updated_at, f.created_at) DESC, f.farmer_id DESC
  LIMIT %s
"""

FARMER_DETAIL_SQL = """
  SELECT
    f.farmer_id,
    u.user_id,
    u.username,
    u.phone_number,
    u.email,
    f.status,
    f.profile_photo,
    pi.first_name,
    pi.last_name,
    pi.birthday,
    COALESCE(pi.barangay, fi.barangay) AS barangay,
    fi.ownership_status,
    ai.federation_assoc,
    ai.rsbsa_registered,
    ai.rsbsa_number
  FROM farmers f
  INNER JOIN users u ON u.user_id = f.user_id
  LEFT JOIN personal_information pi ON pi.farmer_id = f.farmer_id
  LEFT JOIN farm_information fi ON fi.farmer_id = f.farmer_id
  LEFT JOIN affiliation_information ai ON ai.farmer_id = f.farmer_id
  WHERE f.farmer_id = %s
  LIMIT 1
"""


def _read_settings() -> dict:
    try:
        if not SETTINGS_PATH.exists():
            return {}
        data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _read_connection_settings() -> dict:
    settings = _read_settings()
    conn = settings.get("connection")
    return conn if isinstance(conn, dict) else {}


def _app_server_base() -> str:
    base = os.getenv("BEANTHENTIC_APP_SERVER_BASE", "").strip()
    if base:
        return base.rstrip("/")
    cfg = _read_connection_settings()
    base = str(cfg.get("app_server_base") or "").strip()
    return base.rstrip("/") if base else ""


def _app_db_params() -> dict | None:
    host = os.getenv("BEANTHENTIC_APP_DB_HOST", "").strip()
    cfg = _read_connection_settings()
    if not host:
        host = str(cfg.get("app_db_host") or "").strip()
    if not host:
        return None
    return {
        "host": host,
        "port": int(os.getenv("BEANTHENTIC_APP_DB_PORT", str(cfg.get("app_db_port") or "3306"))),
        "user": os.getenv("BEANTHENTIC_APP_DB_USER", str(cfg.get("app_db_user") or "root")),
        "password": os.getenv("BEANTHENTIC_APP_DB_PASS", str(cfg.get("app_db_pass") or "")),
        "database": os.getenv(
            "BEANTHENTIC_APP_DB_NAME", str(cfg.get("app_db_name") or "beanthentic_app")
        ),
        "charset": "utf8mb4",
        "cursorclass": DictCursor,
        "autocommit": True,
    }


def _connection_hint(exc: Exception | None = None) -> str:
    msg = str(exc or "").lower()
    if "1130" in msg or "not allowed to connect" in msg:
        return (
            "MySQL error 1130: on the XAMPP PC, run Beanthentic-App/xampp-enable-lan-mysql.sql "
            "in phpMyAdmin, set bind-address=0.0.0.0, restart MySQL, and use user beanthentic_remote."
        )
    if "2003" in msg or "timed out" in msg or "can't connect" in msg:
        return (
            "Cannot reach MySQL at app_db_host. Use the LAN IP of the PC running XAMPP (ipconfig on that device), "
            "not 127.0.0.1 unless Client Web and XAMPP are on the same PC."
        )
    if "1045" in msg or "access denied" in msg:
        return (
            "MySQL login failed (1045). On the XAMPP PC (192.168.0.106), run "
            "Beanthentic-App/xampp-enable-lan-mysql.sql in phpMyAdmin (user beanthentic_remote, "
            "password StrongPass123!), OR set Client Web app_db_user/app_db_pass to match Admin "
            "(e.g. root with empty password — same as Beanthentic/settings.json on the working PC). "
            "You can also set BEANTHENTIC_APP_DB_USER and BEANTHENTIC_APP_DB_PASS env vars without editing settings.json."
        )
    if not _app_db_params():
        return "Set connection.app_db_host in Beanthentic-Client-Web/settings.json to the XAMPP device LAN IP."
    return (
        "Could not load farmers from the app database. Check settings.json (app_db_host, user, password) "
        "and that port 3306 is open on the XAMPP PC."
    )


def _app_db_connect():
    params = _app_db_params()
    if not params:
        return None, _connection_hint()
    try:
        return connect_app_mysql(params), None
    except Exception as e:
        return None, _connection_hint(e)


def _normalize_farmer_row(row: dict) -> dict:
    first = str(row.get("first_name") or "").strip()
    last = str(row.get("last_name") or "").strip()
    if not first and not last:
        full = str(row.get("username") or "").strip()
        if full:
            parts = full.split()
            if len(parts) >= 2:
                last = parts[-1]
                first = " ".join(parts[:-1])
            else:
                first = full
    out = dict(row)
    out["first_name"] = first
    out["last_name"] = last
    out["barangay"] = str(row.get("barangay") or "").strip()
    return out


def _fetch_farmer_rows_mysql(limit: int = 500) -> tuple[list[dict], str | None]:
    conn, err = _app_db_connect()
    if not conn:
        return [], err
    limit = max(1, min(int(limit or 500), 500))
    try:
        with conn.cursor() as cur:
            try:
                cur.execute(CLIENT_FARMERS_SQL, (limit,))
            except Exception as e:
                if "coop_name" not in str(e).lower():
                    raise
                cur.execute(CLIENT_FARMERS_SQL_NO_COOP, (limit,))
            rows = [_normalize_farmer_row(r) for r in (cur.fetchall() or [])]
            return rows, None
    except Exception as e:
        return [], _connection_hint(e)
    finally:
        conn.close()


def _fetch_farmer_rows_http() -> tuple[list[dict], str | None]:
    base = _app_server_base()
    if not base:
        return [], "app_server_base is not set in settings.json (e.g. http://192.168.x.x:8080)."
    url = base + "/api/client_farmers.php"
    try:
        req = Request(url, headers={"Accept": "application/json"})
        with urlopen(req, timeout=12) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
        data = json.loads(raw) if raw else {}
        if not isinstance(data, dict) or data.get("ok") is not True:
            return [], "App server returned an invalid farmer list."
        items = data.get("farmers")
        if not isinstance(items, list):
            return [], None
        return [_normalize_farmer_row(x) for x in items if isinstance(x, dict)], None
    except HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8", errors="replace").strip()[:400]
        except Exception:
            pass
        msg = f"HTTP fallback failed ({url}): HTTP {e.code}"
        if detail:
            msg += f" — {detail}"
        return [], msg
    except (URLError, TimeoutError, ValueError) as e:
        return [], f"HTTP fallback failed ({url}): {e}"


def _use_demo_data() -> bool:
    flag = os.getenv("BEANTHENTIC_USE_DEMO_DATA", "").strip().lower()
    if flag in ("1", "true", "yes", "on"):
        return True
    if flag in ("0", "false", "no", "off"):
        return False
    cfg = _read_connection_settings()
    return bool(cfg.get("use_demo_data"))


def _default_farmer_rows() -> list[dict]:
    samples = [
        (1, "Juan", "Dela Cruz", "San Miguel, Jordan, Guimaras"),
        (2, "Maria", "Santos", "Buenavista, Guimaras"),
        (3, "Pedro", "Reyes", "Nueva Valencia, Guimaras"),
        (4, "Ana", "Garcia", "Jordan, Guimaras"),
        (5, "Rosa", "Lopez", "Sibunag, Guimaras"),
        (6, "Carlos", "Mendoza", "San Lorenzo, Guimaras"),
    ]
    rows = []
    for fid, first, last, barangay in samples:
        rows.append(
            {
                "farmer_id": fid,
                "first_name": first,
                "last_name": last,
                "barangay": barangay,
                "status": "active",
                "profile_photo": None,
                "username": f"{first.lower()}.{last.lower().replace(' ', '')}",
                "federation_assoc": "SAMAHAN NG MAGKAKAPE",
                "coop_name": "",
            }
        )
    return rows


def _demo_farmer_profile(farmer_id: int) -> dict | None:
    for row in _default_farmer_rows():
        if int(row.get("farmer_id") or 0) != int(farmer_id):
            continue
        profile = _default_farmer_profile(farmer_id)
        profile["first_name"] = row["first_name"]
        profile["last_name"] = row["last_name"]
        profile["barangay"] = row["barangay"]
        profile["federation_assoc"] = row.get("federation_assoc") or profile["federation_assoc"]
        profile["is_default"] = True
        return profile
    return None


def _fetch_farmer_rows(limit: int = 500) -> tuple[list[dict], str | None, bool]:
    """Returns (rows, db_error, demo_mode)."""
    if _use_demo_data():
        return _default_farmer_rows(), None, True
    rows, err = _fetch_farmer_rows_mysql(limit)
    if rows:
        return rows, None, False
    rows_http, err_http = _fetch_farmer_rows_http()
    if rows_http:
        return rows_http, None, False
    return _default_farmer_rows(), None, True


def _map_http_farmer_payload(data: dict) -> dict:
    """Normalize client_farmer_profile.php JSON for personal_information.html."""
    farmer = data.get("farmer") if isinstance(data, dict) else None
    if not isinstance(farmer, dict):
        return {}
    row = dict(farmer)
    first = str(row.get("first_name") or "").strip()
    last = str(row.get("last_name") or "").strip()
    if not first and not last:
        display = str(row.get("display_name") or "").strip()
        if display:
            parts = display.split()
            if len(parts) >= 2:
                last = parts[-1]
                first = " ".join(parts[:-1])
            else:
                first = display
    row["first_name"] = first
    row["last_name"] = last
    if not str(row.get("barangay") or "").strip():
        row["barangay"] = str(row.get("current_address") or row.get("pi_barangay") or "").strip()
    return row


def _fetch_farmer_details_http(farmer_id: int) -> tuple[dict | None, str | None]:
    base = _app_server_base()
    if not base:
        return None, "app_server_base is not set in settings.json (e.g. http://192.168.x.x:8080)."
    url = f"{base}/api/client_farmer_profile.php?farmer_id={int(farmer_id)}"
    try:
        req = Request(url, headers={"Accept": "application/json"})
        with urlopen(req, timeout=12) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
        data = json.loads(raw) if raw else {}
        if not isinstance(data, dict) or data.get("ok") is not True:
            err = str(data.get("error") or "App server returned an invalid farmer profile.")
            return None, err
        row = _map_http_farmer_payload(data)
        if not row:
            return None, "Farmer profile not found."
        return _normalize_farmer_row(row), None
    except HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8", errors="replace").strip()[:400]
        except Exception:
            pass
        msg = f"HTTP profile fallback failed: HTTP {e.code}"
        if detail:
            msg += f" — {detail}"
        return None, msg
    except (URLError, TimeoutError, ValueError) as e:
        return None, f"HTTP profile fallback failed ({url}): {e}"


def _fetch_farmer_details(farmer_id: int) -> tuple[dict | None, str | None]:
    conn, err = _app_db_connect()
    if not conn:
        http_row, http_err = _fetch_farmer_details_http(farmer_id)
        if http_row:
            return http_row, None
        return None, err or http_err
    try:
        with conn.cursor() as cur:
            cur.execute(FARMER_DETAIL_SQL, (int(farmer_id),))
            row = cur.fetchone()
            if not row:
                return None, None
            return _normalize_farmer_row(row), None
    except Exception as e:
        return None, _connection_hint(e)
    finally:
        conn.close()


def _fetch_farmer_profile(farmer_id: int) -> tuple[dict | None, str | None]:
    """MySQL first, then HTTP via app server on XAMPP PC."""
    if _use_demo_data():
        return _demo_farmer_profile(farmer_id), None
    farmer, err = _fetch_farmer_details(farmer_id)
    if farmer:
        return farmer, None
    farmer, http_err = _fetch_farmer_details_http(farmer_id)
    if farmer:
        return farmer, None
    demo = _demo_farmer_profile(farmer_id)
    if demo:
        return demo, None
    return None, err or http_err


def _post_app_json(path: str, payload: dict) -> tuple[dict | None, str | None]:
    base = _app_server_base()
    if not base:
        return None, "app_server_base is not set."
    url = base.rstrip("/") + path
    try:
        body = json.dumps(payload).encode("utf-8")
        req = Request(
            url,
            data=body,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            method="POST",
        )
        with urlopen(req, timeout=12) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
        data = json.loads(raw) if raw else {}
        return (data if isinstance(data, dict) else None), None
    except HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8", errors="replace").strip()[:300]
        except Exception:
            pass
        return None, f"App API error HTTP {e.code}" + (f": {detail}" if detail else "")
    except (URLError, TimeoutError, ValueError) as e:
        return None, str(e)


def _resolve_farmer_id_from_app(
    *,
    farmer_id: int = 0,
    user_id: int = 0,
    login: str = "",
) -> tuple[int, str | None]:
    fid = int(farmer_id or 0)
    if fid > 0:
        return fid, None
    login = str(login or "").strip()
    if not login:
        return 0, "Missing login (email or phone) to find your account."
    payload: dict = {"user_id": int(user_id or 0), "login": login}
    if "@" in login:
        payload["email"] = login
    else:
        payload["phone_number"] = login
    data, err = _post_app_json("/api/registration_status.php", payload)
    if err:
        return 0, err
    if not data or data.get("ok") is not True:
        return 0, str(data.get("error") if data else "Could not resolve farmer account.")
    resolved = int(data.get("farmer_id") or 0)
    if resolved > 0:
        return resolved, None
    return 0, "No farmer profile linked to this account yet. Complete Register Farm in the app first."


def _fmt_birthday(value) -> str:
    if value is None or value == "":
        return ""
    if isinstance(value, datetime):
        value = value.date()
    if isinstance(value, date):
        return value.strftime("%B %d, %Y")
    return str(value).strip()


def _farmer_has_profile_photo(photo_path) -> bool:
    path = str(photo_path or "").strip()
    if not path:
        return False
    return "farmer-profile-photo.png" not in path.lower()


def _apply_farmer_photo_fields(farmer: dict) -> None:
    farmer["has_photo"] = _farmer_has_profile_photo(farmer.get("profile_photo"))
    farmer["photo_url"] = _get_photo_url(farmer.get("profile_photo"))


def _get_photo_url(photo_path: str) -> str:
    if not photo_path:
        return url_for("static", filename="images/farmer-profile-photo.png")

    if photo_path.startswith(("http://", "https://", "data:image/")):
        return photo_path

    base_url = _app_server_base()
    if base_url:
        return f"{base_url}/{photo_path.lstrip('/')}"

    return url_for("static", filename="images/farmer-profile-photo.png")


def _default_farmer_profile(farmer_id: int = 0) -> dict:
    return {
        "farmer_id": farmer_id,
        "first_name": "Juan",
        "last_name": "Dela Cruz",
        "birthday": "March 15, 1985",
        "barangay": "San Miguel, Jordan, Guimaras",
        "ownership_status": "owned",
        "federation_assoc": "SAMAHAN NG MAGKAKAPE",
        "rsbsa_registered": 1,
        "rsbsa_number": "RSBSA-GUIM-2024-001",
        "profile_photo": None,
        "photo_url": url_for("static", filename="images/farmer-profile-photo.png"),
        "has_photo": False,
        "is_default": True,
    }


@app.route("/")
def home():
    return render_template("index.html")


def _render_farmer_profile_page(farmer_id: int) -> str:
    farmer = None
    db_error = None
    demo_mode = _use_demo_data()
    if farmer_id > 0:
        farmer, db_error = _fetch_farmer_profile(farmer_id)
    if farmer:
        _apply_farmer_photo_fields(farmer)
        farmer["birthday"] = _fmt_birthday(farmer.get("birthday"))
        if not demo_mode and not farmer.get("is_default"):
            farmer["is_default"] = False
        farmer["profile_not_found"] = False
        return render_template(
            "personal_information.html",
            farmer=farmer,
            demo_mode=demo_mode or bool(farmer.get("is_default")),
        )

    return render_template(
        "personal_information.html",
        farmer={
            "farmer_id": farmer_id,
            "first_name": "",
            "last_name": "",
            "birthday": "",
            "barangay": "",
            "ownership_status": "",
            "federation_assoc": "",
            "rsbsa_registered": 0,
            "rsbsa_number": "",
            "has_photo": False,
            "photo_url": "",
            "is_default": False,
            "profile_not_found": True,
            "load_error": db_error
            or (
                f"No profile found for farmer #{farmer_id}."
                if farmer_id > 0
                else "Invalid profile link."
            ),
        },
    )


@app.route("/account")
def account_entry():
    """
    App account / QR entry: resolve the logged-in user's farmer_id from XAMPP, then show their profile.
    Query: farmer_id, user_id, login (email or phone).
    """
    farmer_id = request.args.get("farmer_id", type=int) or 0
    user_id = request.args.get("user_id", type=int) or 0
    login = (
        request.args.get("login", type=str)
        or request.args.get("email", type=str)
        or request.args.get("phone", type=str)
        or ""
    ).strip()
    resolved, err = _resolve_farmer_id_from_app(
        farmer_id=farmer_id, user_id=user_id, login=login
    )
    if resolved > 0:
        return redirect(url_for("farmer_detail", farmer_id=resolved))
    return render_template(
        "personal_information.html",
        farmer={
            "farmer_id": 0,
            "first_name": "",
            "last_name": "",
            "has_photo": False,
            "photo_url": "",
            "is_default": False,
            "profile_not_found": True,
            "load_error": err or "Could not open your account profile.",
        },
    )


@app.route("/farmer/<int:farmer_id>")
def farmer_detail(farmer_id):
    return _render_farmer_profile_page(farmer_id)


@app.route("/farmer-profiles")
def farmer_profiles():
    farmers, db_error, demo_mode = _fetch_farmer_rows()
    for f in farmers:
        _apply_farmer_photo_fields(f)
    return render_template(
        "farmer_profiles.html",
        farmers=farmers,
        db_error=db_error,
        demo_mode=demo_mode,
    )


@app.route("/api/app-db-status")
def api_app_db_status():
    """Diagnostic: same idea as admin /api/app-db-status — open in browser on Client Web PC."""
    params = _app_db_params()
    if not params:
        return jsonify(
            {
                "ok": False,
                "configured": False,
                "hint": "Set connection.app_db_host in Beanthentic-Client-Web/settings.json.",
            }
        ), 200

    out = {
        "ok": False,
        "configured": True,
        "host": params["host"],
        "port": params["port"],
        "database": params["database"],
        "user": params["user"],
        "app_server_base": _app_server_base(),
    }
    conn = None
    try:
        conn = connect_app_mysql(params)
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS c FROM farmers")
            out["farmers_count"] = int((cur.fetchone() or {}).get("c") or 0)
            cur.execute(
                """
                SELECT COUNT(*) AS c FROM farmers f
                INNER JOIN users u ON u.user_id = f.user_id
                """
            )
            out["farmers_with_user"] = int((cur.fetchone() or {}).get("c") or 0)
        out["ok"] = True
        rows, _ = _fetch_farmer_rows_mysql(10)
        out["sample_list_count"] = len(rows)
        return jsonify(out), 200
    except Exception as e:
        out["error"] = str(e)
        out["hint"] = _connection_hint(e)
        http_rows, http_err = _fetch_farmer_rows_http()
        out["http_fallback_count"] = len(http_rows)
        if http_err:
            out["http_fallback_error"] = http_err
        return jsonify(out), 200
    finally:
        if conn:
            conn.close()


@app.context_processor
def _inject_app_server_base():
    return {"app_server_base": _app_server_base()}


@app.route("/transaction")
def transaction():
    farmer_id = request.args.get("farmer_id", type=int) or 0
    farmer_name = (request.args.get("farmer_name") or "").strip()
    return render_template(
        "transaction.html",
        farmer_id=farmer_id,
        farmer_name=farmer_name,
    )


@app.route("/api/client-transaction/submit", methods=["POST", "OPTIONS"])
def client_transaction_submit_proxy():
    """Same-origin proxy: forwards multipart to XAMPP app server API."""
    if request.method == "OPTIONS":
        resp = jsonify({"ok": True})
        resp.headers["Access-Control-Allow-Origin"] = "*"
        resp.headers["Access-Control-Allow-Methods"] = "POST, OPTIONS"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
        return resp, 204

    base = _app_server_base()
    if not base:
        return jsonify({"ok": False, "error": "app_server_base is not set in settings.json."}), 503

    url = base.rstrip("/") + "/api/client_transaction_submit.php"
    try:
        import mimetypes
        from io import BytesIO

        boundary = "----BeanthenticClientWeb"
        parts: list[bytes] = []

        def add_field(name: str, value: str) -> None:
            parts.append(
                f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{value}\r\n".encode(
                    "utf-8"
                )
            )

        for key in (
            "client_name",
            "farmer_id",
            "farmer_name",
            "pickup_date",
            "product_type",
            "quantity_kg",
            "quantity_unit",
            "payment_amount",
            "payment_method",
            "transaction_type",
        ):
            val = request.form.get(key)
            if val is not None and str(val).strip() != "":
                add_field(key, str(val).strip())

        if "payment_method" not in request.form:
            add_field("payment_method", "Cash")

        f = request.files.get("valid_id")
        if f and f.filename:
            data = f.read()
            ctype = f.mimetype or mimetypes.guess_type(f.filename)[0] or "application/octet-stream"
            parts.append(
                (
                    f"--{boundary}\r\n"
                    f"Content-Disposition: form-data; name=\"valid_id\"; filename=\"{f.filename}\"\r\n"
                    f"Content-Type: {ctype}\r\n\r\n"
                ).encode("utf-8")
            )
            parts.append(data)
            parts.append(b"\r\n")

        parts.append(f"--{boundary}--\r\n".encode("utf-8"))
        body = b"".join(parts)

        req = Request(
            url,
            data=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
            method="POST",
        )
        with urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
        data = json.loads(raw) if raw else {}
        return jsonify(data), 200 if data.get("ok") else 400
    except HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")[:500]
        return jsonify({"ok": False, "error": f"Submit failed HTTP {e.code}", "detail": detail}), 502
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/history")
def history():
    return render_template("history.html")


@app.route("/report")
def report():
    return render_template("report.html")


@app.route("/api/client-report/transaction-farmers", methods=["GET", "OPTIONS"])
def client_report_transaction_farmers_proxy():
    """Farmers this client has transacted with (by buyer name in customer_transaction)."""
    if request.method == "OPTIONS":
        resp = jsonify({"ok": True})
        resp.headers["Access-Control-Allow-Origin"] = "*"
        resp.headers["Access-Control-Allow-Methods"] = "GET, OPTIONS"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
        return resp, 204

    client_name = (request.args.get("client_name") or request.args.get("buyer_name") or "").strip()
    if not client_name:
        return jsonify({"ok": False, "error": "client_name is required.", "farmers": []}), 400

    base = _app_server_base()
    if not base:
        return jsonify({"ok": False, "error": "app_server_base is not set in settings.json.", "farmers": []}), 503

    from urllib.parse import quote

    url = (
        base.rstrip("/")
        + "/api/client_transaction_farmers.php?client_name="
        + quote(client_name)
    )
    try:
        req = Request(url, method="GET")
        with urlopen(req, timeout=20) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
        data = json.loads(raw) if raw else {}
        return jsonify(data), 200 if data.get("ok") else 400
    except HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")[:500]
        return jsonify({"ok": False, "error": f"HTTP {e.code}", "detail": detail, "farmers": []}), 502
    except Exception as e:
        return jsonify({"ok": False, "error": str(e), "farmers": []}), 500


@app.route("/api/client-report/submit", methods=["POST", "OPTIONS"])
def client_report_submit_proxy():
    """Same-origin proxy: Client Web report form → XAMPP app server API."""
    if request.method == "OPTIONS":
        resp = jsonify({"ok": True})
        resp.headers["Access-Control-Allow-Origin"] = "*"
        resp.headers["Access-Control-Allow-Methods"] = "POST, OPTIONS"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
        return resp, 204

    base = _app_server_base()
    if not base:
        return jsonify({"ok": False, "error": "app_server_base is not set in settings.json."}), 503

    payload = request.get_json(silent=True) or {}
    url = base.rstrip("/") + "/api/client_report_submit.php"
    try:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = Request(
            url,
            data=body,
            headers={"Content-Type": "application/json; charset=utf-8"},
            method="POST",
        )
        with urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
        data = json.loads(raw) if raw else {}
        status = 200 if data.get("ok") else 400
        return jsonify(data), status
    except HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")[:500]
        return jsonify({"ok": False, "error": f"Submit failed HTTP {e.code}", "detail": detail}), 502
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/news-updates")
def news_updates():
    return render_template("news_updates.html")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001, debug=True)
