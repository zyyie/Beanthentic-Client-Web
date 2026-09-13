"""Resolve and serve client valid-ID images (local disk + Supabase Storage)."""
from __future__ import annotations

import json
import mimetypes
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import beanthentic_env

_BASE_DIR = Path(__file__).resolve().parent.parent
_CLIENT_ID_UPLOADS_DIR = _BASE_DIR / "uploads" / "client_ids"


def _mime_from_bytes(data: bytes) -> str:
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:4] == b"RIFF" and len(data) > 12 and data[8:12] == b"WEBP":
        return "image/webp"
    return "image/jpeg"


def _basename_from_stored(stored: str) -> str:
    s = str(stored or "").strip().replace("\\", "/")
    if not s:
        return ""
    if s.startswith(("http://", "https://")):
        return s.rstrip("/").rsplit("/", 1)[-1].split("?")[0]
    return Path(s).name


def display_url_for_stored(stored: str) -> str:
    """Browser-loadable URL for a value stored in customer_transaction.valid_id_path."""
    s = str(stored or "").strip()
    if not s:
        return ""
    if s.startswith(("http://", "https://", "data:")):
        return s
    if s.startswith("/uploads/client_ids/"):
        return s
    name = _basename_from_stored(s)
    if name:
        return f"/uploads/client_ids/{name}"
    return s


def valid_id_from_row(row: dict | None) -> str:
    if not row:
        return ""
    for key in ("valid_id_path", "valid_id"):
        val = str(row.get(key) or "").strip()
        if val:
            return val
    raw = row.get("client_form_json")
    if not raw:
        return ""
    try:
        payload = json.loads(raw) if isinstance(raw, str) else dict(raw)
        return str(payload.get("valid_id_path") or payload.get("valid_id_url") or "").strip()
    except (TypeError, ValueError, json.JSONDecodeError):
        return ""


def _download_supabase_object(object_name: str) -> tuple[bytes, str] | None:
    base = beanthentic_env.supabase_project_url()
    key = beanthentic_env.supabase_service_role_key()
    bucket = beanthentic_env.supabase_storage_bucket()
    name = str(object_name or "").strip().lstrip("/")
    if not base or not key or not bucket or not name:
        return None
    url = f"{base}/storage/v1/object/{bucket}/{name}"
    try:
        req = Request(
            url,
            headers={"Authorization": f"Bearer {key}", "apikey": key, "Accept": "image/*"},
        )
        with urlopen(req, timeout=15) as resp:
            data = resp.read()
            if len(data) < 32:
                return None
            ctype = str(resp.headers.get("Content-Type") or _mime_from_bytes(data)).split(";")[0].strip()
            return data, ctype
    except (HTTPError, URLError, TimeoutError, OSError, ValueError):
        return None


def _read_http_image(url: str) -> tuple[bytes, str] | None:
    try:
        req = Request(url, headers={"Accept": "image/*"})
        with urlopen(req, timeout=15) as resp:
            data = resp.read()
            if len(data) < 32:
                return None
            ctype = str(resp.headers.get("Content-Type") or _mime_from_bytes(data)).split(";")[0].strip()
            return data, ctype
    except (HTTPError, URLError, TimeoutError, OSError, ValueError):
        return None


def get_valid_id_bytes(stored: str) -> tuple[bytes, str] | None:
    """Load valid-ID image bytes from local disk, Supabase Storage, or a public URL."""
    s = str(stored or "").strip()
    if not s:
        return None

    if s.startswith(("http://", "https://")):
        return _read_http_image(s)

    fname = _basename_from_stored(s)
    if fname:
        local = _CLIENT_ID_UPLOADS_DIR / fname
        if local.is_file():
            data = local.read_bytes()
            if len(data) >= 32:
                mime = mimetypes.guess_type(fname)[0] or _mime_from_bytes(data)
                return data, mime

        for object_name in (f"client_ids/{fname}", fname):
            remote = _download_supabase_object(object_name)
            if remote:
                return remote

    return None


def upload_valid_id_bytes(file_bytes: bytes, fname: str, content_type: str = "image/jpeg") -> str | None:
    """Upload to Supabase Storage under client_ids/; return public URL."""
    if not file_bytes or len(file_bytes) < 32:
        return None
    object_name = f"client_ids/{Path(fname).name}"
    return beanthentic_env.upload_to_supabase_storage(file_bytes, object_name, content_type)
