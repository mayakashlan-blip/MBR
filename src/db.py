"""Supabase persistence layer for MBR sessions and monthly assets.

Activated when SUPABASE_URL and SUPABASE_SERVICE_KEY are set in the environment.
Falls back to file-based storage (handled in web/app.py) when unset.
"""

import os
import mimetypes
import tempfile
from datetime import datetime

_client = None
MAX_VERSIONS = 20


def enabled() -> bool:
    return bool(os.environ.get("SUPABASE_URL") and os.environ.get("SUPABASE_SERVICE_KEY"))


def get_client():
    global _client
    if _client is None:
        from supabase import create_client
        _client = create_client(
            os.environ["SUPABASE_URL"],
            os.environ["SUPABASE_SERVICE_KEY"],
        )
    return _client


# ── Sessions ──────────────────────────────────────────────────────────────────

def save_session(session_id: str, payload: dict):
    """Upsert a session. payload keys: data (dict), brand_bank_path,
    marketing_image_path, launches_image_path, created (ISO string)."""
    sb = get_client()
    data_dict = payload["data"]
    sb.table("sessions").upsert({
        "id": session_id,
        "practice_name": data_dict.get("practice_name", ""),
        "month": data_dict.get("month"),
        "year": data_dict.get("year"),
        "data": data_dict,
        "brand_bank_path": payload.get("brand_bank_path"),
        "marketing_image_path": payload.get("marketing_image_path"),
        "launches_image_path": payload.get("launches_image_path"),
        "created_at": payload.get("created"),
    }).execute()


def load_session_raw(session_id: str) -> dict | None:
    """Return raw session payload (data is a plain dict). None if not found."""
    sb = get_client()
    result = sb.table("sessions").select("*").eq("id", session_id).execute()
    if not result.data:
        return None
    row = result.data[0]
    return {
        "data": row["data"],
        "brand_bank_path": row.get("brand_bank_path"),
        "marketing_image_path": row.get("marketing_image_path"),
        "launches_image_path": row.get("launches_image_path"),
        "created": row.get("created_at"),
    }


def list_sessions() -> list:
    """List all sessions ordered newest-first."""
    sb = get_client()
    result = (sb.table("sessions")
               .select("id, practice_name, month, year, created_at")
               .order("created_at", desc=True)
               .execute())
    return result.data or []


# ── Versions ──────────────────────────────────────────────────────────────────

def snapshot_version(session_id: str, raw_payload: dict):
    """Insert a version snapshot and prune beyond MAX_VERSIONS."""
    sb = get_client()
    sb.table("session_versions").insert({
        "session_id": session_id,
        "data": raw_payload["data"],
        "brand_bank_path": raw_payload.get("brand_bank_path"),
        "marketing_image_path": raw_payload.get("marketing_image_path"),
        "launches_image_path": raw_payload.get("launches_image_path"),
    }).execute()
    # Prune oldest
    all_ids = (sb.table("session_versions")
                 .select("id")
                 .eq("session_id", session_id)
                 .order("created_at", desc=True)
                 .execute())
    ids = [r["id"] for r in all_ids.data or []]
    if len(ids) > MAX_VERSIONS:
        sb.table("session_versions").delete().in_("id", ids[MAX_VERSIONS:]).execute()


def list_versions(session_id: str) -> list:
    """Return version list with display metadata."""
    sb = get_client()
    result = (sb.table("session_versions")
               .select("id, created_at")
               .eq("session_id", session_id)
               .order("created_at", desc=True)
               .execute())
    versions = []
    for row in result.data or []:
        ts_str = row.get("created_at", "")
        try:
            dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            versions.append({
                "filename": str(row["id"]),
                "timestamp": dt.isoformat(),
                "display": dt.strftime("%b %d, %Y %I:%M:%S %p"),
            })
        except (ValueError, AttributeError):
            pass
    return versions


def load_version(version_id: str) -> dict | None:
    """Load a specific version by its integer ID."""
    sb = get_client()
    result = sb.table("session_versions").select("*").eq("id", int(version_id)).execute()
    if not result.data:
        return None
    row = result.data[0]
    return {
        "data": row["data"],
        "brand_bank_path": row.get("brand_bank_path"),
        "marketing_image_path": row.get("marketing_image_path"),
        "launches_image_path": row.get("launches_image_path"),
    }


# ── Monthly assets ────────────────────────────────────────────────────────────

def load_monthly_assets(month: int, year: int) -> dict:
    sb = get_client()
    period = f"{year}-{month:02d}"
    result = sb.table("monthly_assets").select("*").eq("period", period).execute()
    if not result.data:
        return {"launches": [], "brand_bank_items": []}
    row = result.data[0]
    return {
        "launches": row.get("launches") or [],
        "brand_bank_items": row.get("brand_bank_items") or [],
        "launches_file": row.get("launches_file"),
        "brand_bank_file": row.get("brand_bank_file"),
    }


def save_monthly_assets(month: int, year: int, assets: dict):
    sb = get_client()
    period = f"{year}-{month:02d}"
    sb.table("monthly_assets").upsert({
        "period": period,
        "month": month,
        "year": year,
        "launches": assets.get("launches", []),
        "brand_bank_items": assets.get("brand_bank_items", []),
        "launches_file": assets.get("launches_file"),
        "brand_bank_file": assets.get("brand_bank_file"),
    }).execute()


def list_all_monthly_assets() -> list:
    sb = get_client()
    result = sb.table("monthly_assets").select("*").order("period", desc=True).execute()
    return result.data or []


# ── Storage ───────────────────────────────────────────────────────────────────

def upload_file(bucket: str, storage_path: str, local_path: str) -> str:
    """Upload a local file to Supabase Storage. Returns the storage path."""
    sb = get_client()
    content_type = mimetypes.guess_type(local_path)[0] or "application/octet-stream"
    with open(local_path, "rb") as f:
        sb.storage.from_(bucket).upload(
            storage_path, f.read(),
            {"content-type": content_type, "upsert": "true"},
        )
    return storage_path


def download_file(bucket: str, storage_path: str, suffix: str = "") -> str:
    """Download from Supabase Storage to a temp file. Returns local path."""
    sb = get_client()
    data = sb.storage.from_(bucket).download(storage_path)
    tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    tmp.write(data)
    tmp.close()
    return tmp.name
