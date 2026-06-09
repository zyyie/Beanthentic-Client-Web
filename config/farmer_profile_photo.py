"""Serve farmer profile photos from Beanthentic-App assets, disk, app server, or avatar."""
from __future__ import annotations

import html
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import beanthentic_env

_BASE_DIR = Path(__file__).resolve().parent.parent
_FARMER_UPLOADS_DIR = _BASE_DIR / "uploads" / "farmers"
_IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".gif")


def _app_server_base() -> str:
    base = (os.getenv("BEANTHENTIC_APP_SERVER_BASE") or "").strip()
    return base.rstrip("/") if base else ""


def _app_assets_roots() -> list[Path]:
    roots: list[Path] = []
    env = (os.getenv("BEANTHENTIC_APP_ASSETS_DIR") or "").strip()
    if env:
        roots.append(Path(env))
    sibling = (
        _BASE_DIR.parent
        / "Beanthentic-App"
        / "android-app"
        / "app"
        / "src"
        / "main"
        / "assets"
    )
    if sibling.is_dir():
        roots.append(sibling)
    seen: set[str] = set()
    out: list[Path] = []
    for root in roots:
        key = str(root.resolve())
        if key in seen:
            continue
        seen.add(key)
        out.append(root)
    return out


def _fetch_farmer_row(farmer_id: int) -> dict | None:
    fid = int(farmer_id or 0)
    if fid <= 0:
        return None
    conn = None
    try:
        conn = beanthentic_env.connect()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT f.farmer_id, f.profile_photo, f.created_at, f.updated_at,
                   COALESCE(pi.first_name, '') AS first_name,
                   COALESCE(pi.last_name, '') AS last_name,
                   COALESCE(u.username, '') AS username
            FROM farmers f
            LEFT JOIN personal_information pi ON pi.farmer_id = f.farmer_id
            LEFT JOIN users u ON u.user_id = f.user_id
            WHERE f.farmer_id = %s
            LIMIT 1
            """,
            (fid,),
        )
        row = cur.fetchone()
        return dict(row) if row else None
    except Exception:
        return None
    finally:
        if conn:
            conn.close()


def _split_name(row: dict) -> tuple[str, str]:
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
    return first, last


def _initials(first: str, last: str) -> str:
    parts = []
    for name in (first, last):
        text = str(name or "").strip()
        if text:
            parts.append(text[0].upper())
    return "".join(parts) or "?"


def build_farmer_avatar_svg(first: str, last: str) -> bytes:
    initials = html.escape(_initials(first, last))
    svg = f"""<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" width="400" height="400" viewBox="0 0 400 400" role="img" aria-label="Farmer avatar">
  <defs>
    <linearGradient id="bg" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#2b7f1f"/>
      <stop offset="100%" stop-color="#8ad41d"/>
    </linearGradient>
  </defs>
  <rect width="400" height="400" fill="url(#bg)"/>
  <text x="200" y="228" text-anchor="middle" font-family="system-ui,-apple-system,Segoe UI,sans-serif"
        font-size="132" font-weight="700" fill="#ffffff">{initials}</text>
</svg>"""
    return svg.encode("utf-8")


def _guess_mimetype(path: Path) -> str:
    ext = path.suffix.lower()
    if ext in (".jpg", ".jpeg"):
        return "image/jpeg"
    if ext == ".png":
        return "image/png"
    if ext == ".webp":
        return "image/webp"
    if ext == ".gif":
        return "image/gif"
    return "application/octet-stream"


def _mime_from_bytes(data: bytes) -> str:
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if len(data) > 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return "image/jpeg"


def _as_utc(dt: Any) -> datetime | None:
    if dt is None:
        return None
    if isinstance(dt, datetime):
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    return None


def _farmer_record_time(row: dict | None) -> datetime | None:
    if not row:
        return None
    updated = _as_utc(row.get("updated_at"))
    created = _as_utc(row.get("created_at"))
    return updated or created


def _is_stale_local_file(path: Path, row: dict | None) -> bool:
    """Local farmer_{id}.jpg from an older registration must not be reused."""
    record_time = _farmer_record_time(row)
    if record_time is None or not path.is_file():
        return False
    try:
        mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        return mtime < record_time
    except OSError:
        return True


def _dedupe_paths(paths: list[Path]) -> list[Path]:
    seen: set[str] = set()
    out: list[Path] = []
    for path in paths:
        key = str(path.resolve()) if path.exists() else str(path)
        if key in seen:
            continue
        seen.add(key)
        out.append(path)
    return out


def _local_candidate_paths(farmer_id: int, profile_photo: str | None) -> list[Path]:
    paths: list[Path] = []
    fid = int(farmer_id or 0)
    search_dirs = [_FARMER_UPLOADS_DIR]
    for assets_root in _app_assets_roots():
        search_dirs.extend(
            [
                assets_root / "uploads" / "farmers",
                assets_root / "uploads" / "profiles",
                assets_root / "uploads" / "profile_photos",
                assets_root,
            ]
        )

    if fid > 0:
        for folder in search_dirs:
            for ext in _IMAGE_EXTS:
                paths.append(folder / f"farmer_{fid}{ext}")

    raw = str(profile_photo or "").strip()
    if raw and not raw.startswith(("http://", "https://", "data:image/")):
        cleaned = raw.lstrip("/").replace("\\", "/")
        if cleaned:
            paths.append(_BASE_DIR / cleaned)
            basename = Path(cleaned).name
            if basename:
                paths.append(_FARMER_UPLOADS_DIR / basename)
                for assets_root in _app_assets_roots():
                    paths.append(assets_root / cleaned)
                    paths.append(assets_root / "uploads" / "farmers" / basename)

    return _dedupe_paths(paths)


def _cache_remote_photo(farmer_id: int, data: bytes, content_type: str) -> Path | None:
    ext = ".jpg"
    ctype = str(content_type or "").lower()
    if "png" in ctype:
        ext = ".png"
    elif "webp" in ctype:
        ext = ".webp"
    elif "gif" in ctype:
        ext = ".gif"
    try:
        _FARMER_UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
        dest = _FARMER_UPLOADS_DIR / f"farmer_{int(farmer_id)}{ext}"
        dest.write_bytes(data)
        return dest
    except OSError:
        return None


def _read_http_image(url: str, farmer_id: int = 0) -> tuple[bytes, str] | None:
    try:
        req = Request(url, headers={"Accept": "image/*,*/*"})
        with urlopen(req, timeout=2) as resp:
            data = resp.read()
            if not data:
                return None
            ctype = str(resp.headers.get("Content-Type") or "image/jpeg").split(";")[0].strip()
            if not ctype.startswith("image/"):
                return None
            if farmer_id > 0:
                _cache_remote_photo(farmer_id, data, ctype)
            return data, ctype
    except (HTTPError, URLError, TimeoutError, OSError, ValueError):
        return None


def _supabase_public_urls(profile_photo: str, farmer_id: int) -> list[str]:
    raw = str(profile_photo or "").strip()
    urls: list[str] = []
    if raw.startswith(("http://", "https://")):
        urls.append(raw)
    name = Path(raw).name if raw else ""
    fid = int(farmer_id or 0)
    if not name and fid > 0:
        name = f"farmer_{fid}.jpg"
    if name:
        for suffix in (name, f"farmers/{name}", f"uploads/farmers/{name}"):
            public = beanthentic_env.supabase_storage_public_url(suffix)
            if public:
                urls.append(public)
    seen: set[str] = set()
    out: list[str] = []
    for url in urls:
        if url and url not in seen:
            seen.add(url)
            out.append(url)
    return out


def _fetch_supabase_storage_photo(profile_photo: str, farmer_id: int) -> tuple[bytes, str] | None:
    for url in _supabase_public_urls(profile_photo, farmer_id):
        got = _read_http_image(url, farmer_id)
        if got:
            return got
    return None


def _fetch_remote_photo(profile_photo: str, farmer_id: int) -> tuple[bytes, str] | None:
    base = _app_server_base()
    fid = int(farmer_id or 0)
    if not base and not str(profile_photo or "").startswith(("http://", "https://")):
        return None

    if base and fid > 0:
        admin = _read_http_image(f"{base}/api/admin_farmer_profile_photo.php?farmer_id={fid}", fid)
        if admin:
            return admin

    raw = str(profile_photo or "").strip()
    if raw.startswith(("http://", "https://")):
        return _read_http_image(raw, fid)
    if base and raw:
        return _read_http_image(f"{base}/{raw.lstrip('/')}", fid)
    if base and fid > 0:
        return _read_http_image(f"{base}/uploads/farmers/farmer_{fid}.jpg", fid)
    return None


def get_farmer_profile_photo(farmer_id: int) -> tuple[bytes, str] | None:
    fid = int(farmer_id or 0)
    if fid <= 0:
        return None

    row = _fetch_farmer_row(fid)
    profile_photo = str((row or {}).get("profile_photo") or "").strip()
    first, last = _split_name(row or {})

    # 1) Supabase Storage / public URL stored in farmers.profile_photo
    if profile_photo.startswith(("http://", "https://")):
        remote_url = _read_http_image(profile_photo, fid)
        if remote_url:
            return remote_url

    supabase = _fetch_supabase_storage_photo(profile_photo, fid)
    if supabase:
        return supabase

    # 2) App server (same path saved in Supabase during farmer registration)
    remote = _fetch_remote_photo(profile_photo, fid)
    if remote:
        return remote

    # 3) Local files only when they match this farmer's registration time
    for path in _local_candidate_paths(fid, profile_photo):
        if not path.is_file() or _is_stale_local_file(path, row):
            continue
        data = path.read_bytes()
        if len(data) > 32:
            return data, _guess_mimetype(path)

    return build_farmer_avatar_svg(first, last), "image/svg+xml"
