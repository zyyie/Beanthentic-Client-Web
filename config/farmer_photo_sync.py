"""Sync registered farmer profile photos to Supabase Storage."""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import beanthentic_env
from config.farmer_profile_photo import (
    _FARMER_UPLOADS_DIR,
    _IMAGE_EXTS,
    _app_assets_roots,
    _fetch_farmer_row,
    _guess_mimetype,
    _is_stale_local_file,
    _profile_storage_object_names,
)

_BASE_DIR = Path(__file__).resolve().parent.parent
_SETTINGS_PATH = _BASE_DIR / "settings.json"


def _read_settings_connection() -> dict:
    try:
        data = json.loads(_SETTINGS_PATH.read_text(encoding="utf-8"))
        return data.get("connection") or {}
    except (OSError, json.JSONDecodeError, AttributeError):
        return {}


def _app_server_bases() -> list[str]:
    bases: list[str] = []
    for raw in (
        os.getenv("BEANTHENTIC_APP_SERVER_BASE", "").strip(),
        str(_read_settings_connection().get("app_server_base") or "").strip(),
    ):
        if not raw:
            continue
        base = raw.rstrip("/")
        if base and base not in bases:
            bases.append(base)
    return bases


def _app_server_base() -> str:
    bases = _app_server_bases()
    return bases[0] if bases else ""


def _app_server_reachable() -> bool:
    for base in _app_server_bases():
        try:
            req = Request(f"{base}/", headers={"Accept": "*/*"})
            with urlopen(req, timeout=3) as resp:
                if resp.status < 500:
                    return True
        except (HTTPError, URLError, TimeoutError, OSError, ValueError):
            continue
    return False


def _list_storage_prefix(prefix: str) -> list[str]:
    base = beanthentic_env.supabase_project_url()
    key = beanthentic_env.supabase_service_role_key()
    bucket = beanthentic_env.supabase_storage_bucket()
    if not base or not key or not bucket:
        return []
    try:
        req = Request(
            f"{base}/storage/v1/object/list/{bucket}",
            data=json.dumps({"prefix": prefix, "limit": 1000, "offset": 0}).encode(),
            method="POST",
            headers={
                "Authorization": f"Bearer {key}",
                "apikey": key,
                "Content-Type": "application/json",
            },
        )
        with urlopen(req, timeout=20) as resp:
            items = json.loads(resp.read().decode())
        if not isinstance(items, list):
            return []
        out: list[str] = []
        for item in items:
            name = str(item.get("name") or "").strip()
            if not name:
                continue
            out.append(f"{prefix}{name}" if prefix else name)
        return out
    except (HTTPError, URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError):
        return []


def _storage_object_names() -> set[str]:
    names = set(_list_storage_prefix(""))
    names.update(_list_storage_prefix("farmers/"))
    names.update(_list_storage_prefix("uploads/farmers/"))
    names.update(_list_storage_prefix("client_ids/"))
    return names


def _object_names_for_farmer(farmer_id: int, profile_photo: str = "") -> list[str]:
    return _profile_storage_object_names(profile_photo, farmer_id)


def _public_url_for_object(object_name: str) -> str:
    return beanthentic_env.supabase_storage_public_url(object_name) or ""


def _upload_bytes_to_supabase(file_bytes: bytes, object_name: str, content_type: str) -> str | None:
    base = beanthentic_env.supabase_project_url()
    key = beanthentic_env.supabase_service_role_key()
    bucket = beanthentic_env.supabase_storage_bucket()
    if not base or not key or not bucket:
        return None

    object_name = str(object_name or "").strip().lstrip("/")
    if not object_name:
        return None

    url = f"{base}/storage/v1/object/{bucket}/{object_name}"
    try:
        req = Request(
            url,
            data=file_bytes,
            method="POST",
            headers={
                "Authorization": f"Bearer {key}",
                "apikey": key,
                "Content-Type": content_type,
                "x-upsert": "true",
            },
        )
        with urlopen(req, timeout=30) as resp:
            if resp.status >= 400:
                return None
    except HTTPError:
        return None
    except (URLError, OSError, ValueError):
        return None

    return _public_url_for_object(object_name)


def _http_image_bytes(url: str, timeout: int = 3) -> tuple[bytes, str] | None:
    try:
        req = Request(url, headers={"Accept": "image/*,*/*"})
        with urlopen(req, timeout=timeout) as resp:
            data = resp.read()
            if len(data) < 64:
                return None
            ctype = str(resp.headers.get("Content-Type") or "image/jpeg").split(";")[0].strip()
            if not ctype.startswith("image/"):
                return None
            return data, ctype
    except (HTTPError, URLError, TimeoutError, OSError, ValueError):
        return None


def _fetch_photo_bytes_from_app_server(
    farmer_id: int, profile_photo: str = "", bases: list[str] | None = None
) -> tuple[bytes, str] | None:
    fid = int(farmer_id or 0)
    if fid <= 0:
        return None
    server_bases = bases if bases is not None else _app_server_bases()
    for base in server_bases:
        for url in (
            f"{base}/api/admin_farmer_profile_photo.php?farmer_id={fid}",
            f"{base}/uploads/farmers/farmer_{fid}.jpg",
            f"{base}/uploads/farmers/farmer_{fid}.png",
            f"{base}/uploads/farmers/farmer_{fid}.webp",
        ):
            got = _http_image_bytes(url)
            if got:
                return got
        raw = str(profile_photo or "").strip()
        if raw and not raw.startswith(("http://", "https://", "data:image/")):
            got = _http_image_bytes(f"{base}/{raw.lstrip('/')}")
            if got:
                return got
    return None


def _local_photo_bytes(farmer_id: int, profile_photo: str = "") -> tuple[bytes, str] | None:
    row = _fetch_farmer_row(farmer_id)
    from config.farmer_profile_photo import _local_candidate_paths

    for path in _local_candidate_paths(farmer_id, profile_photo):
        if not path.is_file() or _is_stale_local_file(path, row):
            continue
        data = path.read_bytes()
        if len(data) > 32:
            return data, _guess_mimetype(path)
    return None


def ensure_local_farmer_photos() -> int:
    copied = 0
    _FARMER_UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    for assets_root in _app_assets_roots():
        src_dir = assets_root / "uploads" / "farmers"
        if not src_dir.is_dir():
            continue
        for src in src_dir.iterdir():
            if not src.is_file() or src.suffix.lower() not in _IMAGE_EXTS:
                continue
            if not src.name.startswith("farmer_"):
                continue
            try:
                fid = int(src.stem.split("_", 1)[1])
            except (IndexError, ValueError):
                continue
            row = _fetch_farmer_row(fid)
            if _is_stale_local_file(src, row):
                continue
            dest = _FARMER_UPLOADS_DIR / src.name
            try:
                if not dest.is_file() or src.stat().st_mtime > dest.stat().st_mtime:
                    shutil.copy2(src, dest)
                    copied += 1
            except OSError:
                continue
    return copied


def _save_local_copy(farmer_id: int, data: bytes, ext: str) -> None:
    try:
        _FARMER_UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
        (_FARMER_UPLOADS_DIR / f"farmer_{int(farmer_id)}{ext}").write_bytes(data)
    except OSError:
        pass


def backfill_farmer_photos_to_supabase() -> dict:
    """
    Ensure every farmer uses a Supabase Storage public URL in farmers.profile_photo.
    Pulls missing images from app server / local disk, then uploads to Storage.
    """
    key = beanthentic_env.supabase_service_role_key()
    if not key:
        return {"ok": False, "skipped": True, "reason": "no_service_role_key"}

    ensure_local_farmer_photos()
    storage_objects = _storage_object_names()
    server_bases = _app_server_bases()

    conn = None
    checked = 0
    already_ok = 0
    uploaded = 0
    db_updated = 0
    missing: list[int] = []
    errors: list[str] = []
    try:
        conn = beanthentic_env.connect()
        cur = conn.cursor()
        cur.execute("SELECT farmer_id, profile_photo FROM farmers ORDER BY farmer_id")
        rows = cur.fetchall() or []

        for row in rows:
            fid = int(row.get("farmer_id") or 0)
            if fid <= 0:
                continue
            checked += 1
            profile_photo = str(row.get("profile_photo") or "").strip()
            object_names = _object_names_for_farmer(fid, profile_photo)

            existing_name = next((n for n in object_names if n in storage_objects), None)
            if existing_name:
                public_url = _public_url_for_object(existing_name)
                if profile_photo != public_url:
                    cur.execute(
                        "UPDATE farmers SET profile_photo = %s WHERE farmer_id = %s",
                        (public_url, fid),
                    )
                    db_updated += 1
                else:
                    already_ok += 1
                continue

            got = _fetch_photo_bytes_from_app_server(fid, profile_photo, server_bases)
            if not got:
                from config.farmer_profile_photo import _fetch_supabase_storage_photo

                got = _fetch_supabase_storage_photo(profile_photo, fid)
            if not got:
                got = _local_photo_bytes(fid, profile_photo)
            if not got:
                missing.append(fid)
                continue

            data, ctype = got
            ext = ".jpg"
            if "png" in ctype:
                ext = ".png"
            elif "webp" in ctype:
                ext = ".webp"
            elif "gif" in ctype:
                ext = ".gif"
            object_name = f"farmers/farmer_{fid}{ext}"
            public_url = _upload_bytes_to_supabase(data, object_name, ctype)
            if not public_url:
                errors.append(f"farmer_{fid}: upload failed")
                continue
            uploaded += 1
            storage_objects.add(object_name)
            _save_local_copy(fid, data, ext)
            cur.execute(
                "UPDATE farmers SET profile_photo = %s WHERE farmer_id = %s",
                (public_url, fid),
            )
            db_updated += 1

        conn.commit()
        from config.farmer_profile_photo import refresh_supabase_storage_cache

        refresh_supabase_storage_cache(storage_objects)
        return {
            "ok": True,
            "checked": checked,
            "already_ok": already_ok,
            "uploaded": uploaded,
            "db_updated": db_updated,
            "missing_farmer_ids": missing,
            "errors": errors,
            "app_server_reachable": _app_server_reachable(),
            "app_server_bases": server_bases,
        }
    except Exception as exc:
        if conn:
            try:
                conn.rollback()
            except Exception:
                pass
        return {"ok": False, "error": str(exc)}
    finally:
        if conn:
            conn.close()


def sync_farmer_photos_to_supabase() -> dict:
    return backfill_farmer_photos_to_supabase()


def sync_farmer_photos_from_app_server() -> dict:
    if not beanthentic_env.supabase_service_role_key():
        return {"ok": False, "skipped": True, "reason": "no_service_role_key"}
    if not _app_server_bases():
        return {"ok": False, "skipped": True, "reason": "no_app_server_base"}
    if not _app_server_reachable():
        return {"ok": False, "skipped": True, "reason": "app_server_unreachable"}
    return backfill_farmer_photos_to_supabase()


def purge_stale_local_photos() -> int:
    removed = 0
    if not _FARMER_UPLOADS_DIR.is_dir():
        return removed
    for src in _FARMER_UPLOADS_DIR.iterdir():
        if not src.is_file() or src.suffix.lower() not in _IMAGE_EXTS:
            continue
        if not src.name.startswith("farmer_"):
            continue
        try:
            fid = int(src.stem.split("_", 1)[1])
        except (IndexError, ValueError):
            continue
        row = _fetch_farmer_row(fid)
        if _is_stale_local_file(src, row):
            try:
                src.unlink()
                removed += 1
            except OSError:
                continue
    return removed


def bootstrap_farmer_photos() -> dict:
    """Run on app startup: sync farmer photos to Supabase Storage."""
    purge_stale_local_photos()
    ensure_local_farmer_photos()
    return backfill_farmer_photos_to_supabase()
