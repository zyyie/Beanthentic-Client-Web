"""Sync registered farmer profile photos from assets/local disk to Supabase Storage."""
from __future__ import annotations

import shutil
from pathlib import Path

import beanthentic_env
from config.farmer_profile_photo import (
    _FARMER_UPLOADS_DIR,
    _IMAGE_EXTS,
    _app_assets_roots,
    _fetch_farmer_row,
    _guess_mimetype,
    _is_stale_local_file,
)

_BASE_DIR = Path(__file__).resolve().parent.parent


def ensure_local_farmer_photos() -> int:
    """Copy only non-stale farmer photos from Beanthentic-App assets into Client-Web uploads."""
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


def _upload_bytes_to_supabase(file_bytes: bytes, object_name: str, content_type: str) -> str | None:
    from urllib.error import HTTPError, URLError
    from urllib.request import Request, urlopen

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

    return beanthentic_env.supabase_storage_public_url(object_name)


def sync_farmer_photos_to_supabase() -> dict:
    """
    Upload local farmer photos to Supabase Storage and save public URLs in farmers.profile_photo.
    Requires BEANTHENTIC_SUPABASE_SERVICE_ROLE_KEY in .env.
    """
    key = beanthentic_env.supabase_service_role_key()
    if not key:
        return {"ok": False, "skipped": True, "reason": "no_service_role_key"}

    ensure_local_farmer_photos()
    if not _FARMER_UPLOADS_DIR.is_dir():
        return {"ok": False, "error": "No local farmer photos found."}

    conn = None
    uploaded = 0
    updated = 0
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
            farmer_row = _fetch_farmer_row(fid) or row
            src = None
            for ext in _IMAGE_EXTS:
                candidate = _FARMER_UPLOADS_DIR / f"farmer_{fid}{ext}"
                if candidate.is_file() and not _is_stale_local_file(candidate, farmer_row):
                    src = candidate
                    break
            if src is None:
                existing = str(row.get("profile_photo") or "").strip()
                if existing:
                    name = Path(existing).name
                    candidate = _FARMER_UPLOADS_DIR / name
                    if candidate.is_file() and not _is_stale_local_file(candidate, farmer_row):
                        src = candidate
            if src is None:
                continue

            object_name = src.name
            data = src.read_bytes()
            if len(data) < 64:
                continue
            public_url = _upload_bytes_to_supabase(data, object_name, _guess_mimetype(src))
            if not public_url:
                errors.append(f"farmer_{fid}: upload failed")
                continue
            uploaded += 1
            cur.execute(
                "UPDATE farmers SET profile_photo = %s WHERE farmer_id = %s",
                (public_url, fid),
            )
            updated += 1

        conn.commit()
        return {
            "ok": True,
            "uploaded": uploaded,
            "updated": updated,
            "errors": errors,
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


def purge_stale_local_photos() -> int:
    """Remove outdated farmer_*.jpg/png cached locally (wrong person for current DB row)."""
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


def bootstrap_farmer_photos() -> None:
    """Run on app startup: drop stale cache, sync valid files, upload to Supabase when configured."""
    purge_stale_local_photos()
    ensure_local_farmer_photos()
    sync_farmer_photos_to_supabase()
