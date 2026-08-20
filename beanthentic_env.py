"""
Load .env and resolve database / app-server settings for Beanthentic-App.

Supports:
  - BEANTHENTIC_DB_URL (postgresql://… Supabase, or mysql://…)
  - BEANTHENTIC_DB_TYPE=postgresql + BEANTHENTIC_DB_HOST/PORT/USER/PASS/NAME
  - BEANTHENTIC_SUPABASE_PROJECT_REF (fixes pooler username postgres → postgres.REF)
  - BEANTHENTIC_APP_SERVER_BASE
"""
from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import quote, unquote, urlparse, urlunparse

_BASE_DIR = Path(__file__).resolve().parent


def load_dotenv(path: Path | str | None = None) -> None:
    """Load KEY=VALUE lines into os.environ (does not override existing vars)."""
    candidates = []
    if path:
        candidates.append(Path(path))
    else:
        candidates.append(_BASE_DIR / ".env")
        candidates.append(_BASE_DIR / "sms-gate.env")

    for file_path in candidates:
        if not file_path.is_file():
            continue
        try:
            for raw in file_path.read_text(encoding="utf-8").splitlines():
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, val = line.split("=", 1)
                key = key.strip()
                val = val.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = val
        except OSError:
            continue


def _supabase_project_ref() -> str:
    ref = (os.environ.get("BEANTHENTIC_SUPABASE_PROJECT_REF") or "").strip()
    if ref:
        return ref
    user = (os.environ.get("BEANTHENTIC_DB_USER") or "").strip()
    if user.startswith("postgres.") and len(user) > len("postgres."):
        return user.split(".", 1)[1]
    return ""


def _build_postgres_url_from_env() -> str:
    host = (os.environ.get("BEANTHENTIC_DB_HOST") or "").strip()
    if not host:
        return ""
    port = int((os.environ.get("BEANTHENTIC_DB_PORT") or "5432").strip() or "5432")
    user = (os.environ.get("BEANTHENTIC_DB_USER") or "postgres").strip()
    password = os.environ.get("BEANTHENTIC_DB_PASS")
    if password is None:
        password = ""
    database = (os.environ.get("BEANTHENTIC_DB_NAME") or "postgres").strip()
    if "pooler.supabase.com" in host and user == "postgres":
        ref = _supabase_project_ref()
        if ref:
            user = f"postgres.{ref}"
    user_q = quote(user, safe="")
    pass_q = quote(password, safe="")
    return f"postgresql://{user_q}:{pass_q}@{host}:{port}/{database}"


def _normalize_supabase_url(url: str) -> str:
    if not url:
        return url
    raw = url.strip()
    if raw.lower().startswith("postgres://"):
        raw = "postgresql://" + raw[len("postgres://") :]

    parsed = urlparse(raw)
    host = parsed.hostname or ""
    user = unquote(parsed.username or "")
    password = unquote(parsed.password or "")

    if "pooler.supabase.com" in host and user == "postgres":
        ref = _supabase_project_ref()
        if ref:
            user = f"postgres.{ref}"
            user_q = quote(user, safe="")
            pass_q = quote(password, safe="")
            port = parsed.port or 5432
            db = (parsed.path or "/postgres").lstrip("/") or "postgres"
            netloc = f"{user_q}:{pass_q}@{host}:{port}"
            raw = urlunparse(("postgresql", netloc, f"/{db}", "", parsed.query, ""))

    return raw


def get_db_url() -> str:
    url = (os.environ.get("BEANTHENTIC_DB_URL") or os.environ.get("DATABASE_URL") or "").strip()
    if not url:
        db_type = (os.environ.get("BEANTHENTIC_DB_TYPE") or "").strip().lower()
        if db_type in ("postgresql", "postgres"):
            url = _build_postgres_url_from_env()
    if url:
        return _normalize_supabase_url(url)
    return ""


def is_postgresql() -> bool:
    url = get_db_url().lower()
    if url.startswith("postgresql://") or url.startswith("postgres://"):
        return True
    return (os.environ.get("BEANTHENTIC_DB_TYPE") or "").strip().lower() in ("postgresql", "postgres")


def is_mysql() -> bool:
    url = get_db_url().lower()
    if url.startswith("mysql://") or url.startswith("mysql+pymysql://"):
        return True
    if is_postgresql():
        return False
    return bool(os.environ.get("BEANTHENTIC_DB_HOST", "").strip()) and not is_postgresql()


def _params_from_url(url: str) -> dict:
    raw = url.strip()
    if "://" not in raw:
        raw = f"mysql://{raw}"
    parsed = urlparse(raw)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or (5432 if parsed.scheme.startswith("postgres") else 3306)
    user = unquote(parsed.username or "")
    password = unquote(parsed.password or "")
    database = (parsed.path or "").lstrip("/") or "postgres"
    return {
        "host": host,
        "port": int(port),
        "user": user,
        "password": password,
        "database": database,
    }


def mysql_params() -> dict:
    """PyMySQL connection kwargs (from URL or discrete env vars)."""
    url = get_db_url()
    if url and not is_postgresql():
        p = _params_from_url(url)
    else:
        p = {
            "host": os.environ.get("BEANTHENTIC_DB_HOST", "127.0.0.1"),
            "port": int(os.environ.get("BEANTHENTIC_DB_PORT", "3306")),
            "user": os.environ.get("BEANTHENTIC_DB_USER", "root"),
            "password": os.environ.get("BEANTHENTIC_DB_PASS", ""),
            "database": os.environ.get("BEANTHENTIC_DB_NAME", "beanthentic_app"),
        }
    return {
        "host": p["host"],
        "port": int(p["port"]),
        "user": p["user"],
        "password": p["password"],
        "database": p["database"],
        "charset": "utf8mb4",
        "autocommit": False,
        "connect_timeout": 15,
        "read_timeout": 30,
        "write_timeout": 30,
    }


def sqlalchemy_database_url() -> str:
    """SQLAlchemy URI for Beanthentic admin web.py."""
    url = get_db_url()
    if url:
        low = url.lower()
        if low.startswith("postgres://"):
            url = "postgresql://" + url[len("postgres://") :]
        if low.startswith("postgresql://") and "+psycopg2" not in low and "+psycopg" not in low:
            url = url.replace("postgresql://", "postgresql+psycopg2://", 1)
        return url
    p = mysql_params()
    user = quote(p["user"], safe="")
    password = quote(p["password"], safe="")
    host = p["host"]
    port = p["port"]
    db = p["database"]
    return f"mysql+pymysql://{user}:{password}@{host}:{port}/{db}"


def _postgres_connect_url() -> str:
    url = get_db_url()
    if url.lower().startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://") :]
    sslmode = os.environ.get("BEANTHENTIC_DB_SSLMODE", "require").strip() or "require"
    if "sslmode=" not in url.lower():
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}sslmode={sslmode}"
    return url


def _connect_postgresql():
    url = _postgres_connect_url()
    last_err: Exception | None = None

    try:
        import psycopg2
        from psycopg2.extras import RealDictCursor

        conn = psycopg2.connect(url, cursor_factory=RealDictCursor)
        conn.autocommit = False
        return conn
    except ImportError as exc:
        last_err = exc
    except Exception as exc:
        if "enoidentifier" in str(exc).lower() or "tenant identifier" in str(exc).lower():
            raise RuntimeError(
                "Supabase pooler needs BEANTHENTIC_SUPABASE_PROJECT_REF in .env "
                "(Dashboard - Settings - General - Reference ID), "
                "or use URI username postgres.YOUR_PROJECT_REF"
            ) from exc
        raise

    try:
        import psycopg
        from psycopg.rows import dict_row

        conn = psycopg.connect(url, row_factory=dict_row)
        conn.autocommit = False
        return conn
    except ImportError as exc:
        raise RuntimeError(
            "PostgreSQL driver missing. Run: pip install -r requirements.txt"
        ) from (last_err or exc)
    except Exception as exc:
        if "enoidentifier" in str(exc).lower() or "tenant identifier" in str(exc).lower():
            raise RuntimeError(
                "Supabase pooler needs BEANTHENTIC_SUPABASE_PROJECT_REF in .env "
                "(Dashboard - Settings - General - Reference ID)"
            ) from exc
        raise


def connect():
    """
    Database connection for API handlers.
    PostgreSQL (Supabase) when BEANTHENTIC_DB_URL is postgresql://…
    MySQL (XAMPP) otherwise.
    """
    if is_postgresql():
        return _connect_postgresql()

    import pymysql
    from pymysql.cursors import DictCursor

    params = mysql_params()
    params["cursorclass"] = DictCursor
    return pymysql.connect(**params)


def verify_connection() -> tuple[bool, str]:
    """Quick DB probe for startup diagnostics."""
    try:
        conn = connect()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT 1 AS ok")
                cur.fetchone()
        finally:
            conn.close()
        return True, "OK"
    except Exception as exc:
        return False, str(exc)


def app_server_base() -> str:
    return os.environ.get("BEANTHENTIC_APP_SERVER_BASE", "").strip().rstrip("/")


def supabase_project_ref() -> str:
    return _supabase_project_ref()


def supabase_project_url() -> str:
    ref = _supabase_project_ref()
    if ref:
        return f"https://{ref}.supabase.co"
    return os.environ.get("BEANTHENTIC_SUPABASE_URL", "").strip().rstrip("/")


def supabase_service_role_key() -> str:
    return os.environ.get("BEANTHENTIC_SUPABASE_SERVICE_ROLE_KEY", "").strip()


def supabase_storage_bucket() -> str:
    return os.environ.get("BEANTHENTIC_SUPABASE_STORAGE_BUCKET", "profile-photos").strip()


def supabase_storage_public_url(object_path: str) -> str:
    base = supabase_project_url()
    bucket = supabase_storage_bucket()
    name = str(object_path or "").strip().lstrip("/")
    if not base or not bucket or not name:
        return ""
    return f"{base}/storage/v1/object/public/{bucket}/{name}"


def upload_to_supabase_storage(
    file_bytes: bytes, file_name: str, content_type: str = "image/jpeg"
) -> str | None:
    """Upload bytes to Supabase Storage; return public URL or None."""
    url = supabase_project_url()
    key = supabase_service_role_key()
    bucket = supabase_storage_bucket()
    if not url or not key or not bucket or not file_bytes:
        return None
    object_name = str(file_name or "").strip().lstrip("/")
    if not object_name:
        return None
    try:
        from urllib.error import HTTPError, URLError
        from urllib.request import Request, urlopen

        upload_url = f"{url}/storage/v1/object/{bucket}/{object_name}"
        req = Request(
            upload_url,
            data=file_bytes,
            method="POST",
            headers={
                "Authorization": f"Bearer {key}",
                "apikey": key,
                "Content-Type": content_type,
                "x-upsert": "true",
            },
        )
        with urlopen(req, timeout=8) as resp:
            if resp.status >= 400:
                return None
        return supabase_storage_public_url(object_name)
    except Exception:
        return None


# Load on import
load_dotenv()
