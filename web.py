import json
import os
import re
import socket
import subprocess
import threading
from datetime import date, datetime
from http.client import RemoteDisconnected
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from flask import Flask, Response, jsonify, redirect, render_template, request, url_for

import beanthentic_env
from config.client_reports import get_transaction_farmers, submit_client_report
from config.client_product_prices import (
    GCB_PRICE_PER_KG,
    ROASTED_PACK_PRICES,
    compute_order_total,
    prices_for_client_api,
)
from config.client_transactions import (
    get_client_transaction_status,
    get_receipt_download,
    submit_client_transaction,
)
from config.client_qr import ensure_client_qr_files, resolve_client_web_url
from config.farmer_photo_sync import bootstrap_farmer_photos
from config.farmer_profile_photo import get_farmer_profile_photo

app = Flask(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent
SETTINGS_PATH = PROJECT_ROOT / "settings.json"
STATIC_ROOT = PROJECT_ROOT / "static"


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes")


if _env_flag("BEANTHENTIC_BEHIND_PROXY", True):
    try:                                       
        from werkzeug.middleware.proxy_fix import ProxyFix

        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)
    except ImportError:
        pass

def _bootstrap_photos_background() -> None:
    def _run() -> None:
        import time

        time.sleep(8)
        try:
            result = bootstrap_farmer_photos()
            from config.farmer_profile_photo import refresh_supabase_storage_cache

            refresh_supabase_storage_cache()
            if isinstance(result, dict) and result.get("missing_farmer_ids"):
                print(
                    "  Farmer photos missing from Supabase for IDs:",
                    result.get("missing_farmer_ids"),
                    "- turn on app server then POST /api/farmer-photos/sync",
                )
        except Exception as photo_boot_err:
            print("  Farmer photo bootstrap skipped:", photo_boot_err)

    threading.Thread(
        target=_run, daemon=True, name="farmer-photo-bootstrap"
    ).start()


if _env_flag("BEANTHENTIC_PHOTO_BOOTSTRAP", True):
    _bootstrap_photos_background()


# Default on so template/CSS edits show up without stale cache (waitress + debug off caches otherwise).
LIVE_UPDATES = _env_flag("BEANTHENTIC_LIVE_UPDATES", True)
if LIVE_UPDATES:
    app.config["TEMPLATES_AUTO_RELOAD"] = True
    app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0


def _static_asset_version(filename: str) -> int:
    path = STATIC_ROOT / Path(filename)
    try:
        return int(path.stat().st_mtime)
    except OSError:
        return 0


@app.after_request
def _apply_response_headers(resp):
    """CORS for phone API + disable browser cache while developing."""
    path = request.path or ""
    if path.startswith("/api/") or path in ("/phone-test",):
        resp.headers.setdefault("Access-Control-Allow-Origin", "*")
        resp.headers.setdefault("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        resp.headers.setdefault("Access-Control-Allow-Headers", "Content-Type")

    if LIVE_UPDATES and (
        path.startswith("/static/")
        or (resp.content_type or "").startswith("text/html")
    ):
        resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        resp.headers["Pragma"] = "no-cache"
        resp.headers["Expires"] = "0"
    return resp

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
  WHERE LOWER(TRIM(COALESCE(f.status, ''))) = 'active'
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
  WHERE LOWER(TRIM(COALESCE(f.status, ''))) = 'active'
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
    pi.province,
    pi.municipality,
    COALESCE(pi.barangay, fi.barangay) AS barangay,
    fi.ownership_status,
    fi.farm_size_ha,
    ai.federation_assoc,
    ai.coop_name,
    ai.ncfrs,
    ai.rsbsa_registered,
    ai.rsbsa_number,
    ai.rsbsa_status,
    tc.liberica_bearing,
    tc.liberica_non_bearing,
    tc.robusta_bearing,
    tc.robusta_non_bearing,
    tc.excelsa_bearing,
    tc.excelsa_non_bearing,
    tc.record_year AS tree_record_year,
    prod.liberica_qty_kg,
    prod.robusta_qty_kg,
    prod.excelsa_qty_kg,
    prod.production_year
  FROM farmers f
  INNER JOIN users u ON u.user_id = f.user_id
  LEFT JOIN personal_information pi ON pi.farmer_id = f.farmer_id
  LEFT JOIN farm_information fi ON fi.farmer_id = f.farmer_id
  LEFT JOIN affiliation_information ai ON ai.farmer_id = f.farmer_id
  LEFT JOIN tree_counts tc
    ON tc.farmer_id = f.farmer_id
   AND tc.record_year = (
      SELECT MAX(t2.record_year) FROM tree_counts t2 WHERE t2.farmer_id = f.farmer_id
    )
  LEFT JOIN production_information prod
    ON prod.farmer_id = f.farmer_id
   AND prod.production_year = (
      SELECT MAX(p2.production_year) FROM production_information p2 WHERE p2.farmer_id = f.farmer_id
    )
  WHERE f.farmer_id = %s
  LIMIT 1
"""

FARMER_REGISTRATION_RANK_SQL = """
  SELECT COUNT(*) AS registration_no
  FROM farmers f2
  INNER JOIN farmers f1 ON f1.farmer_id = %s
  WHERE f2.created_at < f1.created_at
     OR (f2.created_at = f1.created_at AND f2.farmer_id <= f1.farmer_id)
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


_app_server_reachable_cache: bool | None = None


def _app_server_is_reachable() -> bool:
    """Quick check so offline app server does not crash page loads."""
    global _app_server_reachable_cache
    if _app_server_reachable_cache is not None:
        return _app_server_reachable_cache
    base = _app_server_base()
    if not base:
        _app_server_reachable_cache = False
        return False
    try:
        req = Request(f"{base}/", headers={"Accept": "*/*"})
        with urlopen(req, timeout=3) as resp:
            _app_server_reachable_cache = resp.status < 500
    except (HTTPError, URLError, TimeoutError, OSError, RemoteDisconnected, ValueError):
        _app_server_reachable_cache = False
    return bool(_app_server_reachable_cache)


def _http_get_text(url: str, timeout: int = 8) -> tuple[str | None, str | None]:
    try:
        req = Request(url, headers={"Accept": "application/json, */*"})
        with urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", errors="replace"), None
    except HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8", errors="replace").strip()[:400]
        except Exception:
            pass
        msg = f"HTTP {e.code}"
        if detail:
            msg += f" — {detail}"
        return None, f"Request failed ({url}): {msg}"
    except (URLError, TimeoutError, OSError, RemoteDisconnected, ValueError) as e:
        return None, f"Request failed ({url}): {e}"


def _connection_hint(exc: Exception | None = None) -> str:
    return str(exc or "Could not connect to database.")


def _app_db_connect():
    try:
        conn = beanthentic_env.connect()
        return conn, None
    except Exception as e:
        return None, _connection_hint(e)


def _registration_no_from_rows(rows: list[dict], farmer_id: int) -> int | None:
    fid = int(farmer_id or 0)
    if fid <= 0:
        return None

    def _sort_key(row: dict) -> tuple:
        created = row.get("created_at")
        if created is None or created == "":
            return (datetime.min, int(row.get("farmer_id") or 0))
        if isinstance(created, datetime):
            return (created, int(row.get("farmer_id") or 0))
        if isinstance(created, date):
            return (datetime.combine(created, datetime.min.time()), int(row.get("farmer_id") or 0))
        return (datetime.min, int(row.get("farmer_id") or 0))

    ordered = sorted(rows, key=_sort_key)
    for index, row in enumerate(ordered, start=1):
        if int(row.get("farmer_id") or 0) == fid:
            return index
    return None


def _fetch_farmer_registration_no(farmer_id: int) -> int | None:
    fid = int(farmer_id or 0)
    if fid <= 0:
        return None

    conn, err = _app_db_connect()
    if conn:
        try:
            with conn.cursor() as cur:
                cur.execute(FARMER_REGISTRATION_RANK_SQL, (fid,))
                row = cur.fetchone()
                if row and row.get("registration_no") is not None:
                    return int(row["registration_no"])
        except Exception:
            pass
        finally:
            conn.close()

    rows, _list_err, _demo = _fetch_farmer_rows(limit=500)
    rank = _registration_no_from_rows(rows, fid)
    if rank is not None:
        return rank

    return fid


def _apply_registration_number(farmer: dict) -> None:
    fid = int(farmer.get("farmer_id") or 0)
    reg_no = _fetch_farmer_registration_no(fid)
    farmer["registration_no"] = reg_no
    farmer["registration_no_display"] = (
        f"Registration No. {int(reg_no):03d}" if reg_no else ""
    )


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


def _farmer_is_registered(row: dict | None) -> bool:
    """Only show farmers who finished registration (status active in DB)."""
    if not row:
        return False
    return str(row.get("status") or "").strip().lower() == "active"


def _filter_registered_farmers(rows: list[dict]) -> list[dict]:
    return [row for row in rows if _farmer_is_registered(row)]


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
            rows = _filter_registered_farmers(
                [_normalize_farmer_row(r) for r in (cur.fetchall() or [])]
            )
            return rows, None
    except Exception as e:
        return [], _connection_hint(e)
    finally:
        conn.close()


def _fetch_farmer_rows_http() -> tuple[list[dict], str | None]:
    base = _app_server_base()
    if not base:
        return [], "app_server_base is not set in settings.json (e.g. http://192.168.x.x:8080)."
    if not _app_server_is_reachable():
        return [], "App server is offline. Using database only."
    url = base + "/api/client_farmers.php"
    raw, err = _http_get_text(url, timeout=8)
    if err:
        return [], err
    try:
        data = json.loads(raw or "{}")
    except ValueError as e:
        return [], f"HTTP fallback failed ({url}): {e}"
        if not isinstance(data, dict) or data.get("ok") is not True:
            return [], "App server returned an invalid farmer list."
        items = data.get("farmers")
        if not isinstance(items, list):
            return [], None
        return _filter_registered_farmers(
            [_normalize_farmer_row(x) for x in items if isinstance(x, dict)]
        ), None


def _use_demo_data() -> bool:
    """Preview/demo farmers are disabled — always use Supabase database."""
    return False


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
    """Returns (rows, db_error, demo_mode). Always reads from Supabase/DB — no sample data."""
    rows, err = _fetch_farmer_rows_mysql(limit)
    if rows:
        return rows, None, False
    if _app_server_is_reachable():
        rows_http, err_http = _fetch_farmer_rows_http()
        if rows_http:
            return rows_http, None, False
        if err_http and not err:
            err = err_http
    return [], err, False


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
    if not _app_server_is_reachable():
        return None, "App server is offline."
    url = f"{base}/api/client_farmer_profile.php?farmer_id={int(farmer_id)}"
    raw, err = _http_get_text(url, timeout=8)
    if err:
        return None, err
    try:
        data = json.loads(raw or "{}")
    except ValueError as e:
        return None, f"HTTP profile fallback failed ({url}): {e}"
    if not isinstance(data, dict) or data.get("ok") is not True:
        err_msg = str(data.get("error") or "App server returned an invalid farmer profile.")
        return None, err_msg
    row = _map_http_farmer_payload(data)
    if not row:
        return None, "Farmer profile not found."
    return _normalize_farmer_row(row), None


def _fetch_farmer_details(farmer_id: int) -> tuple[dict | None, str | None]:
    conn, err = _app_db_connect()
    if not conn:
        if _app_server_is_reachable():
            http_row, http_err = _fetch_farmer_details_http(farmer_id)
            if http_row:
                return http_row, None
            return None, err or http_err
        return None, err
    try:
        with conn.cursor() as cur:
            cur.execute(FARMER_DETAIL_SQL, (int(farmer_id),))
            row = cur.fetchone()
            if not row:
                return None, None
            farmer = _normalize_farmer_row(row)
            if not _farmer_is_registered(farmer):
                return None, None
            return farmer, None
    except Exception as e:
        return None, _connection_hint(e)
    finally:
        conn.close()


def _fetch_farmer_profile(farmer_id: int) -> tuple[dict | None, str | None]:
    """Database first, then HTTP via app server when online."""
    farmer, err = _fetch_farmer_details(farmer_id)
    if farmer:
        return farmer, None
    http_err = None
    if _app_server_is_reachable():
        farmer, http_err = _fetch_farmer_details_http(farmer_id)
        if farmer and _farmer_is_registered(farmer):
            return farmer, None
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
    except (URLError, TimeoutError, OSError, RemoteDisconnected, ValueError) as e:
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


def _fmt_birthday_registration(value) -> str:
    """MM/DD/YYYY — same as Farmer Registration summary."""
    if value is None or value == "":
        return ""
    if isinstance(value, datetime):
        value = value.date()
    if isinstance(value, date):
        return value.strftime("%m/%d/%Y")
    raw = str(value).strip()
    if not raw:
        return ""
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", raw)
    if m:
        return f"{m.group(2)}/{m.group(3)}/{m.group(1)}"
    return raw


def _display_or_dash(value) -> str:
    text = str(value or "").strip()
    return text if text else "—"


def _rsbsa_registered_label(val) -> str:
    try:
        iv = int(val or 0)
    except (TypeError, ValueError):
        return "No"
    if iv == 1:
        return "Yes"
    if iv == 2:
        return "Pending"
    return "No"


def _rsbsa_status_label(raw) -> str:
    s = str(raw or "").strip().lower()
    if s == "not_yet_applied":
        return "Not Yet Applied"
    if s == "pending_rsbsa":
        return "Pending RSBSA"
    text = str(raw or "").strip()
    return text if text else "—"


def _ncfrs_label(val) -> str:
    try:
        return "Yes" if int(val or 0) == 1 else "No"
    except (TypeError, ValueError):
        return "No"


def _ownership_label(raw) -> str:
    s = str(raw or "").strip().lower()
    if not s:
        return "—"
    mapping = {
        "landowner": "Landowner",
        "cloa_holder": "CLOA holder",
        "list_holder": "LIST holder",
        "sessional_farm_worker": "Seasonal farm worker",
        "others": "Others",
        "owner": "Landowner",
        "tenant": "Seasonal farm worker",
        "co-owner": "CLOA holder",
        "co_owner": "CLOA holder",
        "coowner": "CLOA holder",
        "other": "Others",
    }
    label = mapping.get(s)
    if label:
        return label
    return str(raw).strip().title()


def _fmt_farm_size_ha(val) -> str:
    if val is None or val == "":
        return "—"
    try:
        return f"{float(val):.4f} Ha"
    except (TypeError, ValueError):
        text = str(val).strip()
        return text if text else "—"


def _fmt_prod_qty(val) -> str:
    if val is None or val == "":
        return "—"
    try:
        return f"{float(val):.2f}"
    except (TypeError, ValueError):
        text = str(val).strip()
        return text if text else "—"


def _fmt_tree_count(val) -> str:
    if val is None or val == "":
        return "—"
    try:
        return str(int(val))
    except (TypeError, ValueError):
        text = str(val).strip()
        return text if text else "—"


def _apply_registration_display_fields(farmer: dict) -> None:
    """Format farmer registration DB fields for personal_information.html."""
    farmer["birthday_display"] = _display_or_dash(_fmt_birthday_registration(farmer.get("birthday")))
    farmer["province_display"] = _display_or_dash(farmer.get("province") or "Batangas")
    farmer["municipality_display"] = _display_or_dash(farmer.get("municipality") or "Lipa City")
    farmer["barangay_display"] = _display_or_dash(farmer.get("barangay"))
    farmer["federation_display"] = _display_or_dash(farmer.get("federation_assoc"))
    farmer["ncfrs_display"] = _ncfrs_label(farmer.get("ncfrs"))
    farmer["rsbsa_registered_display"] = _rsbsa_registered_label(farmer.get("rsbsa_registered"))
    rsbsa_num = str(farmer.get("rsbsa_number") or "").strip()
    farmer["rsbsa_number_display"] = rsbsa_num if rsbsa_num else "N/A"
    farmer["rsbsa_status_display"] = _rsbsa_status_label(farmer.get("rsbsa_status"))
    farmer["ownership_display"] = _ownership_label(farmer.get("ownership_status"))
    farmer["farm_size_display"] = _fmt_farm_size_ha(farmer.get("farm_size_ha"))
    farmer["liberica_bearing_display"] = _fmt_tree_count(farmer.get("liberica_bearing"))
    farmer["liberica_non_bearing_display"] = _fmt_tree_count(farmer.get("liberica_non_bearing"))
    farmer["robusta_bearing_display"] = _fmt_tree_count(farmer.get("robusta_bearing"))
    farmer["robusta_non_bearing_display"] = _fmt_tree_count(farmer.get("robusta_non_bearing"))
    farmer["excelsa_bearing_display"] = _fmt_tree_count(farmer.get("excelsa_bearing"))
    farmer["excelsa_non_bearing_display"] = _fmt_tree_count(farmer.get("excelsa_non_bearing"))
    farmer["prod_liberica_display"] = _fmt_prod_qty(farmer.get("liberica_qty_kg"))
    farmer["prod_robusta_display"] = _fmt_prod_qty(farmer.get("robusta_qty_kg"))
    farmer["prod_excelsa_display"] = _fmt_prod_qty(farmer.get("excelsa_qty_kg"))
    prod_year = farmer.get("production_year")
    try:
        farmer["production_year_display"] = int(prod_year) if prod_year else datetime.now().year
    except (TypeError, ValueError):
        farmer["production_year_display"] = datetime.now().year


def _farmer_has_profile_photo(photo_path) -> bool:
    path = str(photo_path or "").strip()
    if not path:
        return False
    return "farmer-profile-photo.png" not in path.lower()


def _photo_cache_token(farmer: dict | None = None, farmer_id: int = 0) -> str:
    row = farmer or {}
    fid = int(farmer_id or row.get("farmer_id") or 0)
    stamp = row.get("updated_at") or row.get("created_at")
    if stamp is not None:
        if hasattr(stamp, "timestamp"):
            return f"{fid}-{int(stamp.timestamp())}"
        text = str(stamp).strip()
        if text:
            return f"{fid}-{abs(hash(text)) % 100000000}"
    return str(fid or 0)


def _apply_farmer_photo_fields(farmer: dict) -> None:
    from config.farmer_profile_photo import supabase_public_photo_url

    fid = int(farmer.get("farmer_id") or 0)
    profile_photo = str(farmer.get("profile_photo") or "").strip()
    farmer["has_photo"] = bool(
        fid > 0
        and (
            bool(profile_photo)
            or profile_photo.startswith(("http://", "https://"))
            or bool(supabase_public_photo_url(fid, profile_photo))
        )
    )
    farmer["photo_url"] = _get_photo_url(
        farmer.get("profile_photo"),
        farmer_id=fid,
        farmer=farmer,
    )


def _get_photo_url(
    photo_path: str,
    farmer_id: int = 0,
    farmer: dict | None = None,
) -> str:
    from config.farmer_profile_photo import _path_matches_farmer, supabase_public_photo_url

    fid = int(farmer_id or (farmer or {}).get("farmer_id") or 0)
    cache_v = _photo_cache_token(farmer, fid)
    if fid > 0:
        photo_path = str(photo_path or "").strip()
        direct = ""
        if photo_path.startswith(("http://", "https://")) and _path_matches_farmer(
            photo_path, fid
        ):
            direct = photo_path.split("?")[0]
        else:
            direct = supabase_public_photo_url(fid, photo_path)
        if direct:
            sep = "&" if "?" in direct else "?"
            return f"{direct}{sep}v={cache_v}"
        if photo_path.startswith("/uploads/") and _path_matches_farmer(photo_path, fid):
            basename = Path(photo_path).name
            if basename:
                local = PROJECT_ROOT / "uploads" / "farmers" / basename
                if local.is_file():
                    return url_for("serve_farmer_upload", filename=basename, v=cache_v)
            base_url = _app_server_base()
            if base_url:
                return f"{base_url}/{photo_path.lstrip('/')}?v={cache_v}"
        return url_for("farmer_profile_photo", farmer_id=fid, v=cache_v)

    if str(photo_path or "").startswith(("http://", "https://", "data:image/")):
        return str(photo_path)

    base_url = _app_server_base()
    if base_url and photo_path:
        return f"{base_url}/{str(photo_path).lstrip('/')}"

    return url_for("static", filename="images/icon-farmer-line.svg")


def _default_farmer_profile(farmer_id: int = 0) -> dict:
    return {
        "farmer_id": farmer_id,
        "first_name": "Juan",
        "last_name": "Dela Cruz",
        "birthday": "1985-03-15",
        "province": "Batangas",
        "municipality": "Lipa City",
        "barangay": "Adya",
        "ownership_status": "landowner",
        "farm_size_ha": 2.5,
        "federation_assoc": "Member",
        "ncfrs": 0,
        "rsbsa_registered": 0,
        "rsbsa_number": "",
        "rsbsa_status": "pending_rsbsa",
        "liberica_bearing": 20,
        "liberica_non_bearing": 300,
        "robusta_bearing": 75,
        "robusta_non_bearing": 150,
        "excelsa_bearing": 50,
        "excelsa_non_bearing": 220,
        "liberica_qty_kg": 260.0,
        "robusta_qty_kg": 550.0,
        "excelsa_qty_kg": 60.0,
        "production_year": datetime.now().year,
        "profile_photo": None,
        "photo_url": url_for("static", filename="images/farmer-profile-photo.png"),
        "has_photo": False,
        "is_default": True,
    }


@app.route("/")
def home():
    return render_template("index.html")


@app.route("/client-qr")
def client_qr_page():
    """Preview and download the client-website QR code."""
    url = resolve_client_web_url(request.args.get("url"))
    files = ensure_client_qr_files(url)
    query = urlencode({"url": files["target_url"]})
    return render_template(
        "client_qr.html",
        client_url=files["target_url"],
        preview_url=url_for("static", filename="images/client-website-qr.png")
        + f"?v={_static_asset_version('images/client-website-qr.png')}",
        download_print_url=url_for("download_client_website_qr", kind="print") + f"?{query}",
        download_plain_url=url_for("download_client_website_qr") + f"?{query}",
    )


@app.route("/download/client-website-qr")
@app.route("/download/client-website-qr/<kind>")
def download_client_website_qr(kind: str = "plain"):
    """Download the client website QR as a PNG file."""
    from flask import send_file

    target_url = resolve_client_web_url(request.args.get("url"))
    files = ensure_client_qr_files(target_url)
    if kind == "print":
        path = files["print"]
        filename = "beanthentic-client-website-qr-print.png"
    else:
        path = files["plain"]
        filename = "beanthentic-client-website-qr.png"
    if not path.is_file():
        return "QR file not found", 404
    return send_file(
        path,
        mimetype="image/png",
        as_attachment=True,
        download_name=filename,
    )


@app.route("/api/public-url", methods=["GET"])
def public_url():
    """Public HTTPS URL for QR codes (Cloudflare Tunnel or BEANTHENTIC_PUBLIC_URL)."""
    from config.client_qr import resolve_client_web_url

    url = resolve_client_web_url()
    return jsonify({"ok": True, "url": url, "https": url.startswith("https://")})


@app.route("/api/health", methods=["GET", "OPTIONS"])
def api_health():
    """Lightweight health check for browser refresh / uptime monitoring."""
    if request.method == "OPTIONS":
        return "", 204
    db_ok, db_msg = beanthentic_env.verify_connection()
    return jsonify(
        {
            "ok": True,
            "server": "up",
            "db_ok": db_ok,
            "db_message": db_msg if not db_ok else "OK",
        }
    )


@app.route("/api/lan-ping", methods=["GET", "OPTIONS"])
def lan_ping():
    """Open this URL on your phone to confirm it reached the laptop."""
    if request.method == "OPTIONS":
        return "", 204
    port = request.environ.get("SERVER_PORT", os.getenv("BEANTHENTIC_PORT", "5001"))
    return jsonify(
        {
            "ok": True,
            "message": "Your phone reached this laptop.",
            "ips": _get_wifi_ipv4_addresses(),
            "port": port,
        }
    )


@app.route("/phone-test")
def phone_test():
    """Simple page for phone browser — confirms Wi-Fi connection to laptop."""
    port = request.environ.get("SERVER_PORT", os.getenv("BEANTHENTIC_PORT", "5001"))
    ips = _get_wifi_ipv4_addresses()
    ip = ips[0] if ips else "unknown"
    return (
        "<!DOCTYPE html><html><head><meta charset=utf-8>"
        "<meta name=viewport content='width=device-width,initial-scale=1'>"
        "<title>Beanthentic — connected</title>"
        "<style>body{font-family:system-ui,sans-serif;max-width:420px;margin:2rem auto;"
        "padding:1rem;text-align:center}h1{color:#1e6216}.ok{font-size:3rem}</style>"
        "</head><body>"
        "<p class=ok>✓</p><h1>Phone connected!</h1>"
        f"<p>Your phone reached the laptop on Wi-Fi.<br>Laptop IP: <b>{ip}</b> · port {port}</p>"
        f"<p><a href='http://{ip}:{port}/'>Open Beanthentic home</a></p>"
        "</body></html>"
    )


def _render_farmer_profile_page(farmer_id: int) -> str:
    farmer = None
    db_error = None
    demo_mode = _use_demo_data()
    if farmer_id > 0:
        farmer, db_error = _fetch_farmer_profile(farmer_id)
    if farmer:
        _apply_farmer_photo_fields(farmer)
        _apply_registration_number(farmer)
        _apply_transaction_link(farmer)
        _apply_registration_display_fields(farmer)
        if not demo_mode and not farmer.get("is_default"):
            farmer["is_default"] = False
        farmer["profile_not_found"] = False
        return render_template(
            "personal_information.html",
            farmer=farmer,
            demo_mode=demo_mode or bool(farmer.get("is_default")),
        )

    missing = {
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
    }
    _apply_transaction_link(missing)
    return render_template("personal_information.html", farmer=missing)


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
    account_missing = {
        "farmer_id": 0,
        "first_name": "",
        "last_name": "",
        "has_photo": False,
        "photo_url": "",
        "is_default": False,
        "profile_not_found": True,
        "load_error": err or "Could not open your account profile.",
    }
    _apply_transaction_link(account_missing)
    return render_template("personal_information.html", farmer=account_missing)


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
    db_url = beanthentic_env.get_db_url()
    out = {
        "ok": False,
        "configured": db_url is not None,
        "app_server_base": _app_server_base(),
    }
    conn = None
    try:
        conn, err = _app_db_connect()
        if conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) AS c FROM farmers")
                result = cur.fetchone()
                out["farmers_count"] = int((result.get("c") or 0) if result else 0)
                cur.execute(
                    """
                    SELECT COUNT(*) AS c FROM farmers f
                    INNER JOIN users u ON u.user_id = f.user_id
                    """
                )
                result = cur.fetchone()
                out["farmers_with_user"] = int((result.get("c") or 0) if result else 0)
            out["ok"] = True
            rows, _ = _fetch_farmer_rows_mysql(10)
            out["sample_list_count"] = len(rows)
        else:
            out["error"] = err
        return jsonify(out), 200
    except Exception as e:
        out["error"] = str(e)
        out["hint"] = _connection_hint(e)
        if _app_server_is_reachable():
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
    return {
        "app_server_base": _app_server_base(),
        "asset_version": _static_asset_version("css/style.css"),
    }


def _farmer_display_name(row: dict | None) -> str:
    if not row:
        return ""
    first = str(row.get("first_name") or "").strip()
    last = str(row.get("last_name") or "").strip()
    name = f"{first} {last}".strip()
    if name:
        return name
    return str(row.get("username") or "").strip()


def _farmer_transaction_url(farmer: dict | None) -> str:
    """Canonical link to the transaction page for a selected farmer."""
    row = farmer if isinstance(farmer, dict) else {}
    fid = int(row.get("farmer_id") or 0)
    name = _farmer_display_name(row)
    params: dict[str, str | int] = {"new": "1"}
    if fid > 0:
        params["farmer_id"] = fid
    if name:
        params["farmer_name"] = name
    base = url_for("transaction")
    if not params:
        return base
    return f"{base}?{urlencode(params)}"


def _apply_transaction_link(farmer: dict) -> None:
    farmer["transaction_url"] = _farmer_transaction_url(farmer)


@app.route("/transaction")
def transaction():
    farmer_id = request.args.get("farmer_id", type=int) or 0
    farmer_name = (request.args.get("farmer_name") or "").strip()
    farmer_row = None
    if farmer_id > 0:
        farmer_row, _ = _fetch_farmer_profile(farmer_id)
        if farmer_row:
            db_name = _farmer_display_name(farmer_row)
            if db_name:
                farmer_name = db_name
            farmer_id = int(farmer_row.get("farmer_id") or farmer_id)
    farmer_rows, farmers_error, _demo_mode = _fetch_farmer_rows()
    farmers_for_select = []
    for row in farmer_rows:
        fid = int(row.get("farmer_id") or 0)
        if fid <= 0:
            continue
        farmers_for_select.append(
            {
                "farmer_id": fid,
                "display_name": _farmer_display_name(row),
                "barangay": str(row.get("barangay") or "").strip(),
            }
        )
    farmers_for_select.sort(key=lambda item: item["display_name"].lower())
    product_prices: list[dict] = []
    gcb_prices = GCB_PRICE_PER_KG
    roasted_packs = ROASTED_PACK_PRICES
    if beanthentic_env.get_db_url():
        try:
            payload = prices_for_client_api()
            product_prices = payload.get("prices") or []
            gcb_prices = payload.get("gcb_prices") or GCB_PRICE_PER_KG
            roasted_packs = payload.get("roasted_packs") or ROASTED_PACK_PRICES
        except Exception:
            product_prices = []
    return render_template(
        "transaction.html",
        farmer_id=farmer_id,
        farmer_name=farmer_name,
        farmers=farmers_for_select,
        farmers_error=farmers_error,
        farmer_profiles_url=url_for("farmer_profiles"),
        product_prices=product_prices,
        gcb_prices=gcb_prices,
        roasted_packs=roasted_packs,
    )


def _proxy_client_transaction_submit():
    """Fallback: forward multipart to XAMPP app server API."""
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
            "seller_type",
            "seller_name",
            "pickup_date",
            "product",
            "product_type",
            "bean_form",
            "classification",
            "product_quantity_pack",
            "product_quantity_kg",
            "order_selections_json",
            "quantity_kg",
            "quantity_unit",
            "payment_amount",
            "payment_method",
            "delivery_method",
            "transaction_type",
            "client_phone",
            "phone_verify_token",
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

        proof = request.files.get("payment_proof")
        if proof and proof.filename:
            pdata = proof.read()
            pctype = proof.mimetype or mimetypes.guess_type(proof.filename)[0] or "application/octet-stream"
            parts.append(
                (
                    f"--{boundary}\r\n"
                    f"Content-Disposition: form-data; name=\"payment_proof\"; filename=\"{proof.filename}\"\r\n"
                    f"Content-Type: {pctype}\r\n\r\n"
                ).encode("utf-8")
            )
            parts.append(pdata)
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


@app.route("/api/client-transaction/submit", methods=["POST", "OPTIONS"])
def client_transaction_submit_proxy():
    """Save to Supabase/PostgreSQL (or MySQL); fall back to app server proxy if DB is unavailable."""
    if request.method == "OPTIONS":
        resp = jsonify({"ok": True})
        resp.headers["Access-Control-Allow-Origin"] = "*"
        resp.headers["Access-Control-Allow-Methods"] = "POST, OPTIONS"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
        return resp, 204

    if beanthentic_env.get_db_url():
        data, status = submit_client_transaction(
            request.form,
            request.files.get("valid_id"),
            request.files.get("payment_proof"),
        )
        if data.get("ok") or status < 500:
            return jsonify(data), status

    return _proxy_client_transaction_submit()


@app.route("/api/client-phone/send-code", methods=["POST", "OPTIONS"])
def client_phone_send_code():
    if request.method == "OPTIONS":
        resp = jsonify({"ok": True})
        resp.headers["Access-Control-Allow-Origin"] = "*"
        resp.headers["Access-Control-Allow-Methods"] = "POST, OPTIONS"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
        return resp, 204
    from config.client_phone_otp import send_client_otp

    payload = request.get_json(silent=True) or {}
    phone = str(payload.get("phone") or request.form.get("phone") or "").strip()
    data, status = send_client_otp(phone)
    return jsonify(data), status


@app.route("/api/client-phone/verify", methods=["POST", "OPTIONS"])
def client_phone_verify():
    if request.method == "OPTIONS":
        resp = jsonify({"ok": True})
        resp.headers["Access-Control-Allow-Origin"] = "*"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
        resp.headers["Access-Control-Allow-Methods"] = "POST, OPTIONS"
        return resp, 204
    from config.client_phone_otp import verify_client_otp

    payload = request.get_json(silent=True) or {}
    phone = str(payload.get("phone") or request.form.get("phone") or "").strip()
    code = str(payload.get("code") or request.form.get("code") or "").strip()
    data, status = verify_client_otp(phone, code)
    return jsonify(data), status


@app.route("/api/client-transaction/receipt/download", methods=["GET"])
def client_transaction_receipt_download():
    """Download receipt as PDF (preferred) or HTML attachment."""
    ref = str(request.args.get("reference_no") or "").strip()
    fmt = str(request.args.get("format") or "pdf").strip().lower()
    if not ref:
        return jsonify({"ok": False, "error": "reference_no is required."}), 400

    if beanthentic_env.get_db_url():
        data, status = get_receipt_download(ref, fmt=fmt)
        if status == 200 and data.get("ok"):
            filename = str(data.get("filename") or f"Beanthentic-Receipt-{ref}.pdf")
            content_type = str(data.get("content_type") or "application/pdf")
            if data.get("pdf"):
                return Response(
                    data["pdf"],
                    mimetype=content_type,
                    headers={
                        "Content-Disposition": f'attachment; filename="{filename}"',
                        "Content-Type": content_type,
                        "X-Content-Type-Options": "nosniff",
                        "Cache-Control": "no-store",
                    },
                )
            html_body = (data.get("html") or "").encode("utf-8")
            return Response(
                html_body,
                mimetype="application/octet-stream",
                headers={
                    "Content-Disposition": f'attachment; filename="{filename}"',
                    "Content-Type": content_type,
                    "X-Content-Type-Options": "nosniff",
                    "Cache-Control": "no-store",
                },
            )
        if status != 500:
            return jsonify(data), status

    return jsonify({"ok": False, "error": "Receipt download is not available."}), 503


@app.route("/api/client-transaction/product-prices", methods=["GET", "OPTIONS"])
def client_transaction_product_prices():
    """Official fixed prices from coffee_pricelist for the transaction form."""
    if request.method == "OPTIONS":
        resp = jsonify({"ok": True})
        resp.headers["Access-Control-Allow-Origin"] = "*"
        resp.headers["Access-Control-Allow-Methods"] = "GET, OPTIONS"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
        return resp, 204

    if not beanthentic_env.get_db_url():
        return jsonify({"ok": False, "error": "Database is not configured."}), 503

    return jsonify(prices_for_client_api())


@app.route("/api/client-transaction/compute-price", methods=["GET", "OPTIONS"])
def client_transaction_compute_price():
    """Compute total from official prices (same rules as submit validation)."""
    if request.method == "OPTIONS":
        resp = jsonify({"ok": True})
        resp.headers["Access-Control-Allow-Origin"] = "*"
        resp.headers["Access-Control-Allow-Methods"] = "GET, OPTIONS"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
        return resp, 204

    if not beanthentic_env.get_db_url():
        return jsonify({"ok": False, "error": "Database is not configured."}), 503

    product = str(request.args.get("product") or request.args.get("coffee_variety") or "").strip()
    bean_form = str(request.args.get("bean_form") or request.args.get("bean_type") or "").strip()
    classification = str(request.args.get("classification") or "").strip()
    quantity_pack = str(request.args.get("quantity_pack") or "").strip()
    try:
        quantity_kg = float(str(request.args.get("quantity_kg") or "0"))
    except (TypeError, ValueError):
        quantity_kg = 0.0

    result = compute_order_total(
        product,
        bean_form,
        classification,
        quantity_kg,
        quantity_pack=quantity_pack,
    )
    if not result.get("ok"):
        return jsonify({"ok": False, "error": "No official price found for this selection."}), 404
    return jsonify({"ok": True, **result})


@app.route("/api/client-transaction/status", methods=["GET", "OPTIONS"])
def client_transaction_status():
    """Poll transaction approval status from the database."""
    if request.method == "OPTIONS":
        resp = jsonify({"ok": True})
        resp.headers["Access-Control-Allow-Origin"] = "*"
        resp.headers["Access-Control-Allow-Methods"] = "GET, OPTIONS"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
        return resp, 204

    ref = str(request.args.get("reference_no") or "").strip()
    tx_id = request.args.get("customer_transaction_id", type=int) or 0
    if beanthentic_env.get_db_url():
        data, status = get_client_transaction_status(
            reference_no=ref, customer_transaction_id=tx_id
        )
        if data.get("ok") or status != 500:
            return jsonify(data), status

    base = _app_server_base()
    if not base:
        return jsonify({"ok": False, "error": "Database and app server are not configured."}), 503
    url = f"{base.rstrip('/')}/api/client_transaction_status.php"
    if tx_id > 0:
        url += f"?customer_transaction_id={tx_id}"
    else:
        url += f"?reference_no={ref}"
    try:
        req = Request(url, headers={"Accept": "application/json"})
        with urlopen(req, timeout=12) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
        data = json.loads(raw) if raw else {}
        return jsonify(data), 200 if data.get("ok") else 400
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 502


@app.route("/uploads/client_ids/<path:filename>")
def serve_client_id_upload(filename):
    """Serve valid-ID uploads from local disk or Supabase Storage."""
    from config.client_valid_id import get_valid_id_bytes

    safe = Path(filename).name
    local = PROJECT_ROOT / "uploads" / "client_ids" / safe
    stored = f"/uploads/client_ids/{safe}"
    if local.is_file():
        from flask import send_file

        return send_file(local)
    result = get_valid_id_bytes(stored)
    if not result:
        return "Not found", 404
    data, mimetype = result
    return Response(
        data,
        mimetype=mimetype,
        headers={"Cache-Control": "public, max-age=300", "X-Content-Type-Options": "nosniff"},
    )


@app.route("/api/client-valid-id", methods=["GET"])
def client_valid_id_image():
    """Serve a transaction valid-ID image by reference number."""
    from config.client_transactions import get_client_transaction_status

    ref = str(request.args.get("reference_no") or "").strip()
    tx_id = request.args.get("customer_transaction_id", type=int) or 0
    if not ref and tx_id <= 0:
        return jsonify({"ok": False, "error": "reference_no or customer_transaction_id is required."}), 400

    if beanthentic_env.get_db_url():
        payload, status = get_client_transaction_status(reference_no=ref, customer_transaction_id=tx_id)
        if status != 200 or not payload.get("ok"):
            return jsonify(payload), status if status != 200 else 404
        stored = str(payload.get("valid_id_path") or "").strip()
        from config.client_valid_id import get_valid_id_bytes

        result = get_valid_id_bytes(stored)
        if not result:
            return jsonify({"ok": False, "error": "Valid ID image not found."}), 404
        data, mimetype = result
        return Response(
            data,
            mimetype=mimetype,
            headers={"Cache-Control": "public, max-age=300", "X-Content-Type-Options": "nosniff"},
        )

    return jsonify({"ok": False, "error": "Database is not configured."}), 503


@app.route("/uploads/farmers/<path:filename>")
def serve_farmer_upload(filename):
    """Serve farmer profile photos saved on this server."""
    safe = Path(filename).name
    path = PROJECT_ROOT / "uploads" / "farmers" / safe
    if not path.is_file():
        return "Not found", 404
    from flask import send_file

    return send_file(path)


@app.route("/api/farmer-profile-photo/<int:farmer_id>")
def farmer_profile_photo(farmer_id: int):
    """Serve farmer profile photo from Supabase path/storage, local files, or avatar."""
    result = get_farmer_profile_photo(farmer_id)
    if not result:
        return "Not found", 404
    data, mimetype = result
    return Response(
        data,
        mimetype=mimetype,
        headers={
            "Cache-Control": "public, max-age=300",
            "X-Content-Type-Options": "nosniff",
        },
    )


@app.route("/api/farmer-photos/sync", methods=["POST"])
def farmer_photos_sync():
    """Pull farmer photos and save Supabase Storage public URLs in farmers.profile_photo."""
    from config.farmer_photo_sync import backfill_farmer_photos_to_supabase

    result = backfill_farmer_photos_to_supabase()
    ok = bool(result.get("ok"))
    status = 200 if ok else 503
    return jsonify(result), status


@app.route("/api/farmer-photos/pull-server", methods=["POST"])
def farmer_photos_pull_server():
    """Pull farmer photos from Beanthentic-App on the LAN and upload to Supabase."""
    from config.farmer_photo_sync import backfill_farmer_photos_to_supabase

    result = backfill_farmer_photos_to_supabase()
    status = 200 if result.get("ok") or result.get("skipped") else 503
    return jsonify(result), status


@app.route("/api/farmer-photos/status", methods=["GET"])
def farmer_photos_status():
    """Diagnostic: Supabase profile_photo path vs available image sources per farmer."""
    from config.farmer_profile_photo import (
        _fetch_farmer_row,
        _is_stale_local_file,
        _local_candidate_paths,
        get_farmer_profile_photo,
    )

    conn, err = _app_db_connect()
    if not conn:
        return jsonify({"ok": False, "error": err or "Database unavailable."}), 503
    items = []
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT f.farmer_id, f.profile_photo, f.created_at,
                       pi.first_name, pi.last_name
                FROM farmers f
                LEFT JOIN personal_information pi ON pi.farmer_id = f.farmer_id
                ORDER BY f.farmer_id
                """
            )
            rows = cur.fetchall() or []
        for row in rows:
            fid = int(row.get("farmer_id") or 0)
            full_row = _fetch_farmer_row(fid) or dict(row)
            local_paths = _local_candidate_paths(fid, str(row.get("profile_photo") or ""))
            valid_local = [
                str(p)
                for p in local_paths
                if p.is_file() and not _is_stale_local_file(p, full_row)
            ]
            photo = get_farmer_profile_photo(fid)
            items.append(
                {
                    "farmer_id": fid,
                    "name": f"{row.get('first_name') or ''} {row.get('last_name') or ''}".strip(),
                    "profile_photo": str(row.get("profile_photo") or ""),
                    "created_at": str(row.get("created_at") or ""),
                    "valid_local_files": valid_local,
                    "served_as": photo[1] if photo else None,
                    "has_image": bool(photo and photo[1] != "image/svg+xml"),
                }
            )
    finally:
        conn.close()
    return jsonify({"ok": True, "farmers": items, "count": len(items)})


@app.route("/history")
def history():
    return render_template("history.html")


@app.route("/mission-vision")
def mission_vision():
    return render_template("mission_vision.html")


@app.route("/report")
def report():
    return render_template("report.html")


def _proxy_client_report_transaction_farmers(client_name: str):
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


def _proxy_client_report_submit(payload: dict):
    base = _app_server_base()
    if not base:
        return jsonify({"ok": False, "error": "app_server_base is not set in settings.json."}), 503

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

    if beanthentic_env.get_db_url():
        data, status = get_transaction_farmers(client_name)
        if data.get("ok") or status < 500:
            return jsonify(data), status

    return _proxy_client_report_transaction_farmers(client_name)


@app.route("/api/client-report/submit", methods=["POST", "OPTIONS"])
def client_report_submit_proxy():
    """Save misconduct report to Supabase/PostgreSQL (or MySQL); fall back to app server proxy."""
    if request.method == "OPTIONS":
        resp = jsonify({"ok": True})
        resp.headers["Access-Control-Allow-Origin"] = "*"
        resp.headers["Access-Control-Allow-Methods"] = "POST, OPTIONS"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
        return resp, 204

    payload = request.get_json(silent=True) or {}
    if beanthentic_env.get_db_url():
        data, status = submit_client_report(payload)
        if data.get("ok") or status < 500:
            return jsonify(data), status

    return _proxy_client_report_submit(payload)


@app.route("/news-updates")
def news_updates():
    return render_template("news_updates.html")


def _farmer_notification_display_name(row: dict) -> str:
    first = str(row.get("first_name") or "").strip()
    last = str(row.get("last_name") or "").strip()
    full = f"{first} {last}".strip()
    if full:
        return full
    username = str(row.get("username") or "").strip()
    if username:
        return username
    fid = int(row.get("farmer_id") or 0)
    return f"Farmer #{fid}" if fid > 0 else "New farmer"


@app.route("/api/notifications/farmers", methods=["GET"])
def api_notifications_farmers():
    """Active registered farmers for client-side new-registration notifications."""
    rows, err, _demo = _fetch_farmer_rows(limit=500)
    if err and not rows:
        return jsonify({"ok": False, "error": err, "farmers": []}), 503
    farmers = []
    for row in rows:
        fid = int(row.get("farmer_id") or 0)
        if fid <= 0:
            continue
        created = row.get("created_at")
        updated = row.get("updated_at")
        if hasattr(created, "isoformat"):
            created_at = created.isoformat()
        else:
            created_at = str(created or "")
        if hasattr(updated, "isoformat"):
            updated_at = updated.isoformat()
        else:
            updated_at = str(updated or "")
        farmers.append(
            {
                "farmer_id": fid,
                "first_name": str(row.get("first_name") or "").strip(),
                "last_name": str(row.get("last_name") or "").strip(),
                "username": str(row.get("username") or "").strip(),
                "display_name": _farmer_notification_display_name(row),
                "created_at": created_at,
                "updated_at": updated_at,
            }
        )
    farmers.sort(
        key=lambda x: (x.get("created_at") or "", x.get("farmer_id") or 0),
        reverse=True,
    )
    return jsonify({"ok": True, "farmers": farmers, "count": len(farmers)})


def _is_private_lan_ipv4(ip: str) -> bool:
    try:
        a, b, *_ = (int(x) for x in ip.split("."))
    except (ValueError, TypeError):
        return False
    if a == 10:
        return True
    if a == 172 and 16 <= b <= 31:
        return True
    return a == 192 and b == 168


def _parse_ipconfig_wifi_ips(text: str) -> list[str]:
    """IPv4 from Wireless LAN / Wi-Fi adapters only (not hotspot, not Ethernet)."""
    found: list[str] = []
    section = ""
    in_wifi = False

    def add(ip: str) -> None:
        ip = (ip or "").strip()
        if not ip or ip.startswith("127.") or ip.startswith("169.254."):
            return
        if not _is_private_lan_ipv4(ip) or ip in found:
            return
        found.append(ip)

    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if not line.startswith((" ", "\t")) and stripped.endswith(":"):
            section = stripped[:-1]
            low = section.lower()
            in_wifi = (
                "wireless lan adapter wi-fi" in low
                or low.endswith("wi-fi")
                or "wlan" in low
            ) and "hotspot" not in low and "mobile hotspot" not in low
            continue
        if not in_wifi:
            continue
        if "media disconnected" in stripped.lower():
            in_wifi = False
            continue
        low = stripped.lower()
        if "ipv4" in low or "ip address" in low:
            m = re.search(r"(\d{1,3}(?:\.\d{1,3}){3})", stripped)
            if m:
                add(m.group(1))

    return found


def _get_wifi_ipv4_addresses() -> list[str]:
    """Wi-Fi router IP only — use this URL on phone (same home Wi-Fi, not hotspot)."""
    if os.name == "nt":
        try:
            out = subprocess.check_output(
                ["ipconfig"],
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=5,
            )
            wifi_ips = _parse_ipconfig_wifi_ips(out)
            if wifi_ips:
                wifi_ips.sort(key=lambda ip: (0 if ip.startswith("192.168.") else 1, ip))
                return wifi_ips
        except (OSError, subprocess.SubprocessError):
            pass
    return _get_lan_ipv4_addresses()


def _get_lan_ipv4_addresses():
    """Private LAN IPv4 (192.168.x.x, etc.) — not 127.0.0.1."""
    found: list[str] = []

    def add(ip: str) -> None:
        ip = (ip or "").strip()
        if not ip or ip.startswith("127.") or ip.startswith("169.254."):
            return
        if not _is_private_lan_ipv4(ip) or ip in found:
            return
        found.append(ip)

    if os.name == "nt":
        try:
            out = subprocess.check_output(
                ["ipconfig"],
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=5,
            )
            for line in out.splitlines():
                lower = line.lower()
                if "ipv4" in lower or "ip address" in lower:
                    m = re.search(r"(\d{1,3}(?:\.\d{1,3}){3})", line)
                    if m:
                        add(m.group(1))
        except (OSError, subprocess.SubprocessError):
            pass

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.settimeout(0.5)
            s.connect(("8.8.8.8", 80))
            add(s.getsockname()[0])
    except OSError:
        pass

    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, family=socket.AF_INET):
            add(info[4][0])
    except OSError:
        pass

    found.sort(key=lambda ip: (0 if ip.startswith("192.168.") else 1, ip))
    return found


def _ipv4_subnet(ip: str) -> str | None:
    parts = ip.split(".")
    if len(parts) != 4:
        return None
    try:
        a, b, c, _ = (int(x) for x in parts)
    except ValueError:
        return None
    return f"{a}.{b}.{c}"


def _active_wifi_name() -> str:
    if os.name != "nt":
        return ""
    try:
        out = subprocess.check_output(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                "(Get-NetConnectionProfile | Where-Object { $_.InterfaceAlias -match 'Wi-Fi|WLAN' "
                "-and $_.IPv4Connectivity -ne 'NoTraffic' } | Select-Object -First 1).Name",
            ],
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=8,
        )
        return out.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def _free_listening_port(port: int) -> None:
    """Stop a leftover server on this port (common after closing the terminal)."""
    if not _env_flag("BEANTHENTIC_KILL_PORT", True):
        return
    if os.name != "nt":
        return
    try:
        result = subprocess.run(
            ["netstat", "-ano"],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return
    pids: set[str] = set()
    needle = f":{port}"
    for line in result.stdout.splitlines():
        if "LISTENING" not in line or needle not in line:
            continue
        parts = line.split()
        if len(parts) < 5:
            continue
        pid = parts[-1]
        if pid.isdigit() and pid != "0":
            pids.add(pid)
    current_pid = str(os.getpid())
    for pid in sorted(pids):
        if pid == current_pid:
            continue
        print(f"  Stopping old server on port {port} (PID {pid})...")
        subprocess.run(
            ["taskkill", "/F", "/PID", pid],
            capture_output=True,
            check=False,
        )


def _print_lan_access_help(port: int) -> None:
    line = "=" * 58
    ips = _get_wifi_ipv4_addresses()
    wifi_name = _active_wifi_name()
    print(line)
    print("  Beanthentic Client Web")
    if wifi_name:
        print(f"  Wi-Fi network:     {wifi_name}")
    print("  Mode:              HOME Wi-Fi only (NO phone/laptop hotspot)")
    if ips:
        primary = ips[0]
        print(f"  Phone URL:         http://{primary}:{port}/")
        print(f"  Phone test:        http://{primary}:{port}/phone-test")
        print(f"  Download QR:       http://{primary}:{port}/download/client-website-qr")
        print(f"  QR preview page:   http://{primary}:{port}/client-qr")
        subnet = _ipv4_subnet(primary) or "192.168.0"
        print(f"  Phone IP must be:  {subnet}.???  (check phone Wi-Fi settings)")
    else:
        print(f"  Phone URL:         http://<Wi-Fi_IP>:{port}/")
        print("  Connect laptop to home Wi-Fi first (not hotspot).")
    public_url = (
        os.getenv("BEANTHENTIC_PUBLIC_URL", "").strip()
        or (
            (PROJECT_ROOT / "public-url.txt").read_text(encoding="utf-8").strip()
            if (PROJECT_ROOT / "public-url.txt").is_file()
            else ""
        )
    )
    if public_url:
        print(f"  Public URL (QR):   {public_url}")
        print(f"  Public QR download: {public_url.rstrip('/')}/download/client-website-qr")
    else:
        print("  Public URL:        https://beanthentic.com/ (run scripts\\run-beanthentic-cloudflare.bat)")
        print("  Quick tunnel:      scripts\\run-cloudflare-tunnel.bat (random URL, testing only)")
    print(f"  Laptop only:       http://127.0.0.1:{port}/")
    print(f"  Project folder:    {PROJECT_ROOT}")
    index_path = PROJECT_ROOT / "templates" / "index.html"
    if index_path.exists():
        updated = datetime.fromtimestamp(index_path.stat().st_mtime)
        print(f"  Homepage file:     updated {updated:%Y-%m-%d %H:%M:%S}")

    cfg_host = str(_read_connection_settings().get("app_db_host") or "").strip()
    if ips and cfg_host and _is_private_lan_ipv4(cfg_host):
        laptop_net = _ipv4_subnet(ips[0])
        cfg_net = _ipv4_subnet(cfg_host)
        if laptop_net and cfg_net and laptop_net != cfg_net:
            print()
            print("  *** WRONG IP IF YOU USE PC ADDRESS ***")
            print(f"  Laptop Wi-Fi IP:    {ips[0]}  ({laptop_net}.x)")
            print(f"  Old PC in settings: {cfg_host}  ({cfg_net}.x)  <- do NOT use on phone")
            print(f"  Phone must use:     http://{ips[0]}:{port}/")

    print()
    print("  1) Laptop: connect to home router Wi-Fi")
    print("  2) Phone:  connect to THE SAME Wi-Fi name, mobile data OFF")
    print("  3) Do NOT use phone hotspot or laptop mobile hotspot")
    print("  4) Run allow-lan-access.bat once, then run-for-phone.bat")
    if LIVE_UPDATES:
        print("  5) Live updates ON — refresh browser after saving files")
    db_ok, db_msg = beanthentic_env.verify_connection()
    if db_ok:
        print("  Database:          connected")
    else:
        print("  Database:          OFFLINE (pages may load slowly)")
        print(f"                     {db_msg[:120]}")
    print(line)


def _serve_app(host: str, port: int) -> None:
    server = os.getenv("BEANTHENTIC_SERVER", "").strip().lower()
    if not server:
        server = "flask" if LIVE_UPDATES else "waitress"

    reloader_raw = os.getenv("BEANTHENTIC_RELOADER", "").strip().lower()
    if reloader_raw:
        use_reloader = reloader_raw in ("1", "true", "yes")
    else:
        # Flask reloader is unreliable on Windows and often kills the parent process.
        use_reloader = LIVE_UPDATES and server == "flask" and os.name != "nt"

    debug_raw = os.getenv("BEANTHENTIC_DEBUG", "").strip().lower()
    if debug_raw:
        debug = debug_raw in ("1", "true", "yes")
    else:
        debug = LIVE_UPDATES and server == "flask" and os.name != "nt"

    if server == "waitress":
        try:
            from waitress import serve

            print("  Server engine:     waitress (stable — refresh browser for HTML/CSS)")
            if LIVE_UPDATES:
                print("  Note:              restart this window after editing web.py")
            serve(
                app,
                host=host,
                port=port,
                threads=8,
                channel_timeout=120,
                connection_limit=200,
                cleanup_interval=30,
            )
            return
        except ImportError:
            print("  waitress not installed — run:  pip install waitress")

    print("  Server engine:     flask dev (auto-reload on file save)")
    app.run(
        host=host,
        port=port,
        debug=debug,
        use_reloader=use_reloader,
        threaded=True,
    )


if __name__ == "__main__":
    import time

    port = int(os.getenv("BEANTHENTIC_PORT", "5001"))
    host = os.getenv("BEANTHENTIC_HOST", "0.0.0.0").strip() or "0.0.0.0"
    auto_restart = _env_flag("BEANTHENTIC_AUTO_RESTART", os.name == "nt")

    while True:
        _free_listening_port(port)
        _print_lan_access_help(port)
        try:
            _serve_app(host, port)
        except KeyboardInterrupt:
            print("\n  Server stopped.")
            break
        except OSError as exc:
            print(f"  Server error: {exc}")
            if not auto_restart:
                raise
        else:
            break
        if not auto_restart:
            break
        print("  Restarting in 2 seconds...")
        time.sleep(2)
