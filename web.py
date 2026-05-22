import json
import os
from pathlib import Path
from flask import Flask, render_template, jsonify, url_for, redirect
import pymysql
from pymysql.cursors import DictCursor

app = Flask(__name__)

SETTINGS_PATH = Path(__file__).resolve().parent / "settings.json"

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

def _app_db_params() -> dict | None:
    host = os.getenv("BEANTHENTIC_APP_DB_HOST", "").strip()
    if not host:
        cfg = _read_connection_settings()
        host = str(cfg.get("app_db_host") or "").strip()
    if not host:
        return None
    cfg = _read_connection_settings()
    return {
        "host": host,
        "port": int(os.getenv("BEANTHENTIC_APP_DB_PORT", str(cfg.get("app_db_port") or "3306"))),
        "user": os.getenv("BEANTHENTIC_APP_DB_USER", str(cfg.get("app_db_user") or "root")),
        "password": os.getenv("BEANTHENTIC_APP_DB_PASS", str(cfg.get("app_db_pass") or "")),
        "database": os.getenv("BEANTHENTIC_APP_DB_NAME", str(cfg.get("app_db_name") or "beanthentic_app")),
        "charset": "utf8mb4",
        "cursorclass": DictCursor,
        "autocommit": True,
    }

def _app_db_connect():
    params = _app_db_params()
    if not params:
        return None
    try:
        return pymysql.connect(**params)
    except Exception:
        return None

def _app_fetch_farmer_rows(limit: int = 2000) -> list[dict]:
    conn = _app_db_connect()
    if not conn:
        return []
    sql = """
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
        fi.farm_name,
        fi.ownership_status,
        fi.farm_size_ha
      FROM farmers f
      LEFT JOIN users u ON u.user_id = f.user_id
      LEFT JOIN personal_information pi ON pi.farmer_id = f.farmer_id
      LEFT JOIN farm_information fi ON fi.farmer_id = f.farmer_id
      ORDER BY f.farmer_id ASC
      LIMIT %s
    """
    try:
        with conn.cursor() as cur:
            cur.execute(sql, (limit,))
            rows = cur.fetchall() or []
            return list(rows)
    except Exception:
        return []
    finally:
        conn.close()

def _app_fetch_farmer_details(farmer_id: int) -> dict | None:
    conn = _app_db_connect()
    if not conn:
        return None
    sql = """
      SELECT
        f.farmer_id,
        u.user_id,
        u.username,
        u.phone_number,
        u.email,
        f.status,
        f.profile_photo,
        pi.*,
        fi.*,
        ai.federation_assoc,
        ai.rsbsa_registered,
        ai.rsbsa_number
      FROM farmers f
      LEFT JOIN users u ON u.user_id = f.user_id
      LEFT JOIN personal_information pi ON pi.farmer_id = f.farmer_id
      LEFT JOIN farm_information fi ON fi.farmer_id = f.farmer_id
      LEFT JOIN affiliation_information ai ON ai.farmer_id = f.farmer_id
      WHERE f.farmer_id = %s
    """
    try:
        with conn.cursor() as cur:
            cur.execute(sql, (farmer_id,))
            return cur.fetchone()
    except Exception:
        return None
    finally:
        conn.close()

def _get_photo_url(photo_path: str) -> str:
    if not photo_path:
        return "/static/images/farmer-profile-photo.png"
    
    # If it's already a full URL or base64 data, return it
    if photo_path.startswith(("http://", "https://", "data:image/")):
        return photo_path
        
    # If it's a relative path from the App server
    settings = _read_connection_settings()
    base_url = settings.get("app_server_base", "").rstrip("/")
    if base_url:
        return f"{base_url}/{photo_path.lstrip('/')}"
        
    return "/static/images/farmer-profile-photo.png"

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
        "is_default": True,
    }

@app.route("/")
def home():
    return render_template("index.html")

@app.route("/farmer/<int:farmer_id>")
def farmer_detail(farmer_id):
    farmer = None if farmer_id == 0 else _app_fetch_farmer_details(farmer_id)
    if not farmer:
        farmer = _default_farmer_profile(farmer_id)
    else:
        farmer["photo_url"] = _get_photo_url(farmer.get("profile_photo"))
        farmer["is_default"] = False
    return render_template("personal_information.html", farmer=farmer)

@app.route("/farmer-profiles")
def farmer_profiles():
    farmers = _app_fetch_farmer_rows()
    for f in farmers:
        f['photo_url'] = _get_photo_url(f.get('profile_photo'))
    return render_template("farmer_profiles.html", farmers=farmers)


@app.route("/transaction")
def transaction():
    return render_template("transaction.html")


@app.route("/history")
def history():
    return render_template("history.html")


@app.route("/report")
def report():
    return render_template("report.html")


@app.route("/news-updates")
def news_updates():
    return render_template("news_updates.html")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001, debug=True)
