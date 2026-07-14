"""MBR Web Application — Generate, edit, and export Monthly Business Reviews."""

import os
import sys
import uuid
import json
import shutil
import subprocess
import tempfile
import threading
from datetime import datetime
from pathlib import Path

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Load .env file if present
env_path = PROJECT_ROOT / ".env"
if env_path.exists():
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, val = line.split("=", 1)
                os.environ.setdefault(key.strip(), val.strip())

from flask import (Flask, render_template, request, jsonify, send_file,
                   redirect, url_for, Response)

app = Flask(__name__,
            template_folder="templates",
            static_folder="static")
app.secret_key = os.urandom(24)

# In-memory session store: {session_id: {"data": MBRData, "html": str, "created": datetime,
#   "brand_bank_path": str, "marketing_image_path": str, "launches_image_path": str}}
sessions = {}

# Batch job store: {job_id: {"total": N, "completed": M, "status": str, "zip_path": str, "errors": []}}
batch_jobs = {}

# Omni API key from environment
OMNI_KEY = os.environ.get("OMNI_API_KEY", "")

# Persistent storage base — use PERSISTENT_DIR env var on Render, falls back to data/ locally
_persist_base = os.environ.get("PERSISTENT_DIR", str(Path(__file__).parent.parent / "data"))

# Persistent session storage
SESSIONS_DIR = Path(_persist_base) / "sessions"
SESSIONS_DIR.mkdir(parents=True, exist_ok=True)

# Monthly assets storage (launches & brand bank shared across all practices)
MONTHLY_DIR = Path(_persist_base) / "monthly"
MONTHLY_DIR.mkdir(parents=True, exist_ok=True)

# Supabase DB layer — activated by SUPABASE_URL + SUPABASE_SERVICE_KEY env vars.
# Falls back to the file-based paths above when those vars are absent.
try:
    from src import db as _db
    _DB_ENABLED = _db.enabled()
except ImportError:
    _db = None
    _DB_ENABLED = False


# ── Off-site backup of saved reports & version history ──
# Mirrors PERSISTENT_DIR to a private GitHub repo on every save so user
# work survives a Render disk failure or accidental wipe. Set BACKUP_REPO_URL
# (https://github.com/<owner>/<repo>.git) and BACKUP_TOKEN (fine-grained PAT
# with read+write on that repo) in the environment to enable. If unset, the
# tool runs without backup.

import subprocess
import threading

_BACKUP_REPO_URL = os.environ.get("BACKUP_REPO_URL", "").strip()
_BACKUP_TOKEN = os.environ.get("BACKUP_TOKEN", "").strip()
_backup_lock = threading.Lock()
_last_backup_at = 0.0
_BACKUP_THROTTLE_SECONDS = 60  # collapse rapid auto-saves into one push


def _git(*args, capture_output=False, check=True):
    """Run a git command inside the persistence dir."""
    cmd = ["git", "-C", str(_persist_base), *args]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if check and result.returncode != 0:
        raise RuntimeError(
            f"git {' '.join(args)} failed (exit {result.returncode}): "
            f"{result.stderr.strip() or result.stdout.strip()}"
        )
    return result.stdout if capture_output else result


def _authed_remote_url() -> str:
    """Inject the PAT into the HTTPS remote URL so pushes authenticate."""
    if not _BACKUP_REPO_URL.startswith("https://"):
        return _BACKUP_REPO_URL
    return _BACKUP_REPO_URL.replace(
        "https://", f"https://x-access-token:{_BACKUP_TOKEN}@", 1
    )


def _ensure_backup_repo():
    """Initialize the backup git repo on first use; refresh the auth URL otherwise."""
    git_dir = Path(_persist_base) / ".git"
    if git_dir.exists():
        # Always refresh the URL with the current token (in case it rotated).
        _git("remote", "set-url", "origin", _authed_remote_url(), check=False)
        return

    print(f"Backup: initializing git repo in {_persist_base}")
    _git("init", "-b", "main")
    _git("config", "user.email", "mbr-tool@joinmoxie.com")
    _git("config", "user.name", "MBR Tool")
    _git("remote", "add", "origin", _authed_remote_url())

    # If the remote already has commits (e.g. a README), pull them in first
    # so our first push isn't rejected.
    fetched = _git("fetch", "origin", "main", check=False)
    if fetched.returncode == 0:
        _git("reset", "--hard", "origin/main", check=False)


def _backup_run(commit_msg: str):
    """Background worker: stage, commit, and push current PERSISTENT_DIR state."""
    with _backup_lock:
        try:
            _ensure_backup_repo()
            _git("add", "-A")
            status = _git("status", "--porcelain", capture_output=True)
            if not status.strip():
                return  # nothing changed since last push
            _git("commit", "-m", commit_msg)
            # Catch up with any remote changes (rare, but safe), then push.
            _git("pull", "--rebase", "--autostash", "origin", "main", check=False)
            _git("push", "origin", "main")
            print(f"Backup: pushed '{commit_msg}'")
        except Exception as e:
            print(f"Backup failed (save still succeeded): {e}")


def _backup_to_git_async(commit_msg: str, force: bool = False):
    """Schedule a backup push in a background thread.

    `force=True` (used for explicit Save) bypasses the throttle so every
    user-initiated save lands in the backup repo immediately. Auto-saves
    use force=False and are collapsed to at most one push per minute.
    """
    if not (_BACKUP_REPO_URL and _BACKUP_TOKEN):
        return
    global _last_backup_at
    import time as _time
    now = _time.time()
    if not force and (now - _last_backup_at) < _BACKUP_THROTTLE_SECONDS:
        return
    _last_backup_at = now
    threading.Thread(target=_backup_run, args=(commit_msg,), daemon=True).start()


# Startup diagnostic — visible in Render logs after each deploy.
print("=" * 60)
print("MBR Tool starting")
if _DB_ENABLED:
    print(f"  Storage:           Supabase ({os.environ.get('SUPABASE_URL', '')})")
else:
    print(f"  Storage:           File-based ({_persist_base})")
    print(f"  Sessions dir:      {SESSIONS_DIR}")
    try:
        _existing_count = len(list(SESSIONS_DIR.glob('*.json')))
        print(f"  Existing reports:  {_existing_count}")
    except Exception:
        pass
    if _BACKUP_REPO_URL and _BACKUP_TOKEN:
        print(f"  Backup repo:       {_BACKUP_REPO_URL} (token configured)")
    else:
        missing = []
        if not _BACKUP_REPO_URL: missing.append("BACKUP_REPO_URL")
        if not _BACKUP_TOKEN: missing.append("BACKUP_TOKEN")
        print(f"  Backup repo:       NOT configured (missing {', '.join(missing)})")
print("=" * 60)


def _monthly_key(month: int, year: int) -> str:
    return f"{year}-{month:02d}"


def _load_monthly_assets(month: int, year: int) -> dict:
    """Load monthly assets (launches, brand_bank_items) for a given month."""
    if _DB_ENABLED:
        return _db.load_monthly_assets(month, year)
    path = MONTHLY_DIR / f"{_monthly_key(month, year)}.json"
    if not path.exists():
        return {"launches": [], "brand_bank_items": []}
    with open(path) as f:
        return json.load(f)


def _save_monthly_assets(month: int, year: int, assets: dict):
    """Save monthly assets."""
    if _DB_ENABLED:
        _db.save_monthly_assets(month, year, assets)
        return
    path = MONTHLY_DIR / f"{_monthly_key(month, year)}.json"
    with open(path, "w") as f:
        json.dump(assets, f, default=str)
    _backup_to_git_async(f"monthly-assets: {_monthly_key(month, year)}", force=True)


def _save_monthly_upload(month: int, year: int, asset_type: str, src_path: str, original_filename: str) -> Path:
    """Persist an uploaded file. Returns a path usable for immediate AI extraction."""
    suffix = Path(original_filename).suffix.lower() or Path(src_path).suffix.lower() or ".png"
    if _DB_ENABLED:
        try:
            storage_key = f"{_monthly_key(month, year)}_{asset_type}{suffix}"
            _db.upload_file("monthly-assets", storage_key, src_path)
        except Exception as e:
            print(f"  Warning: Storage upload failed (data still saved): {e}")
        return Path(src_path)
    dest = MONTHLY_DIR / f"{_monthly_key(month, year)}_{asset_type}{suffix}"
    shutil.copy2(src_path, dest)
    _backup_to_git_async(f"monthly-upload: {_monthly_key(month, year)} {asset_type}", force=True)
    return dest


def _serialize_data(data) -> dict:
    """Serialize MBRData to a JSON-safe dict."""
    from dataclasses import asdict
    d = asdict(data)
    return d


def _deserialize_data(d: dict):
    """Restore MBRData from a JSON dict."""
    from src.data_schema import (MBRData, StaffMember, ServiceItem, ReviewsPlatform,
                                  MarketingData, LaunchFeature, BrandBankItem, MembershipType,
                                  CampaignData)
    staff = [StaffMember(**s) for s in d.pop("staff", [])]
    services = [ServiceItem(**s) for s in d.pop("services", [])]
    reviews = [ReviewsPlatform(**r) for r in d.pop("reviews", [])]
    launches = [LaunchFeature(**l) for l in d.pop("launches", [])]
    brand_bank_items = [BrandBankItem(**b) for b in d.pop("brand_bank_items", [])]
    membership_types = [MembershipType(**m) for m in d.pop("membership_types", [])]
    mkt = d.pop("marketing", None)
    if mkt:
        campaigns = [CampaignData(**c) for c in mkt.pop("campaigns", [])]
        mkt.pop("estimated_booked_revenue", None)  # legacy field, removed
        marketing = MarketingData(**mkt, campaigns=campaigns)
    else:
        marketing = None
    ma = d.pop("marketing_analysis", None)
    marketing_analysis = None
    if ma:
        marketing_analysis = _build_marketing_analysis(ma)
    return MBRData(**d, staff=staff, services=services, reviews=reviews,
                   marketing=marketing, marketing_analysis=marketing_analysis,
                   launches=launches, brand_bank_items=brand_bank_items,
                   membership_types=membership_types)


MAX_VERSIONS = 20  # keep last 20 versions per session


def _snapshot_version(session_id: str, current_path: Path):
    """Copy current session file to a timestamped version (file-based mode only)."""
    versions_dir = SESSIONS_DIR / f"{session_id}_versions"
    versions_dir.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = versions_dir / f"v_{timestamp}.json"
    shutil.copy2(current_path, dest)
    # Prune old versions
    versions = sorted(versions_dir.glob("v_*.json"))
    while len(versions) > MAX_VERSIONS:
        versions.pop(0).unlink()


def _list_versions(session_id: str) -> list:
    """Return list of available versions with timestamps."""
    if _DB_ENABLED:
        return _db.list_versions(session_id)
    versions_dir = SESSIONS_DIR / f"{session_id}_versions"
    if not versions_dir.exists():
        return []
    versions = []
    for f in sorted(versions_dir.glob("v_*.json"), reverse=True):
        ts_str = f.stem[2:]  # strip "v_"
        try:
            ts = datetime.strptime(ts_str, "%Y%m%d_%H%M%S")
            versions.append({"filename": f.name, "timestamp": ts.isoformat(),
                             "display": ts.strftime("%b %d, %Y %I:%M:%S %p")})
        except ValueError:
            pass
    return versions


def _save_session(session_id: str, sess: dict, snapshot: bool = True):
    """Persist a session, keeping version history."""
    payload = {
        "data": _serialize_data(sess["data"]),
        "brand_bank_path": sess.get("brand_bank_path"),
        "marketing_image_path": sess.get("marketing_image_path"),
        "launches_image_path": sess.get("launches_image_path"),
        "created": sess["created"].isoformat(),
    }

    if _DB_ENABLED:
        if snapshot:
            existing = _db.load_session_raw(session_id)
            if existing:
                _db.snapshot_version(session_id, existing)
        _db.save_session(session_id, payload)
        return

    # File-based fallback
    path = SESSIONS_DIR / f"{session_id}.json"
    if snapshot and path.exists():
        _snapshot_version(session_id, path)
    with open(path, "w") as f:
        json.dump(payload, f, default=str)
    practice = sess["data"].practice_name if sess.get("data") else session_id
    kind = "save" if snapshot else "auto"
    _backup_to_git_async(f"{kind}: {practice} ({session_id})", force=snapshot)


def _load_session(session_id: str) -> dict:
    """Load a session from DB or disk, or return None."""
    if _DB_ENABLED:
        payload = _db.load_session_raw(session_id)
        if not payload:
            return None
    else:
        path = SESSIONS_DIR / f"{session_id}.json"
        if not path.exists():
            return None
        with open(path) as f:
            payload = json.load(f)
    data = _deserialize_data(payload["data"])

    # If the saved report has no launches or brand-bank items but monthly
    # assets exist for this month/year now, inject them. Covers the case
    # where a report was generated/saved before monthly assets were
    # uploaded — without this, those reports would never reflect the
    # later upload. Per-report customization (saved lists with items) is
    # preserved untouched.
    try:
        from src.data_schema import LaunchFeature, BrandBankItem
        if data.month and data.year:
            assets = _load_monthly_assets(data.month, data.year)
            if not data.launches and assets.get("launches"):
                data.launches = [LaunchFeature(**l) for l in assets["launches"]]
            if not data.brand_bank_items and assets.get("brand_bank_items"):
                data.brand_bank_items = [BrandBankItem(**b) for b in assets["brand_bank_items"]]
    except Exception as e:
        print(f"  Warning: could not inject monthly assets on load: {e}")

    created_raw = payload.get("created") or ""
    try:
        created = datetime.fromisoformat(created_raw.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        created = datetime.now()
    return {
        "data": data,
        "html": "",
        "needs_render": True,  # render lazily on first /api/preview call, not on load
        "brand_bank_path": payload.get("brand_bank_path"),
        "marketing_image_path": payload.get("marketing_image_path"),
        "launches_image_path": payload.get("launches_image_path"),
        "created": created,
    }


def _get_session(session_id: str) -> dict:
    """Get session from memory, falling back to disk."""
    if session_id in sessions:
        return sessions[session_id]
    sess = _load_session(session_id)
    if sess:
        sessions[session_id] = sess
    return sess


def _practice_key(practice_name: str, month: int, year: int) -> str:
    """Generate a deterministic session key from practice name + month/year."""
    import re
    safe = re.sub(r'[^a-z0-9]+', '-', practice_name.lower()).strip('-')
    return f"{safe}_{year}-{month:02d}"


def _list_archived_reports() -> list:
    """List all archived reports with metadata."""
    if _DB_ENABLED:
        import calendar
        reports = []
        for row in _db.list_sessions():
            month = row.get("month") or 0
            year = row.get("year") or 0
            month_name = calendar.month_name[month] if month else ""
            reports.append({
                "session_id": row["id"],
                "practice_name": row.get("practice_name", ""),
                "month": month,
                "year": year,
                "month_name": month_name,
                "period": f"{month_name} {year}" if month_name else "",
                "created": row.get("created_at", ""),
                "versions": 0,  # not pre-fetched; available via /api/versions/<id>
            })
        return reports

    # File-based fallback
    reports = []
    for f in sorted(SESSIONS_DIR.glob("*.json"), reverse=True):
        if f.stem.startswith("."):
            continue
        try:
            with open(f) as fh:
                payload = json.load(fh)
            d = payload.get("data", {})
            practice = d.get("practice_name", "")
            month = d.get("month", 0)
            year = d.get("year", 0)
            month_name = ""
            if month:
                import calendar
                month_name = calendar.month_name[month]
            versions_dir = SESSIONS_DIR / f"{f.stem}_versions"
            version_count = len(list(versions_dir.glob("v_*.json"))) if versions_dir.exists() else 0
            created = payload.get("created", "")
            reports.append({
                "session_id": f.stem,
                "practice_name": practice,
                "month": month,
                "year": year,
                "month_name": month_name,
                "period": f"{month_name} {year}" if month_name else "",
                "created": created,
                "versions": version_count,
            })
        except Exception:
            continue
    return reports


def _apply_payload(data, payload):
    """Apply a JSON payload of editable fields to an MBRData object."""
    # Text fields
    for text_field in ["executive_summary", "psm_feedback", "psm_name", "marketing_recommendations"]:
        if text_field in payload:
            setattr(data, text_field, payload[text_field])

    # Boolean toggles
    if "show_executive_summary" in payload:
        data.show_executive_summary = bool(payload["show_executive_summary"])
    if "show_marketing_recommendations" in payload:
        data.show_marketing_recommendations = bool(payload["show_marketing_recommendations"])
    if "show_gfe_section" in payload:
        data.show_gfe_section = bool(payload["show_gfe_section"])
    if "show_marketing_section" in payload:
        data.show_marketing_section = bool(payload["show_marketing_section"])

    # Assessments
    if "assessments" in payload:
        data.assessments = payload["assessments"]

    # All numeric scalar fields
    numeric_fields = [
        "monthly_net_revenue", "total_appointments", "aov", "quarter_to_date",
        "revenue_mom_pct", "appointments_mom_pct", "aov_mom_pct",
        "pct_net_revenue_goal", "pct_aov_goal", "utilization_rate", "rebooking_rate",
        "retention_180d", "utilization_mom_pct", "rebooking_mom_pct", "retention_mom_pct",
        "memberships_active", "memberships_new", "memberships_cancelled", "mrr",
        "new_clients", "existing_clients",
        "service_revenue", "prepayment_revenue", "membership_sales", "custom_items",
        "retail_revenue", "total_gross", "retail_to_service_ratio",
        "retail_revenue_mom_pct",
        "discounts", "redemptions", "client_fees",
        "supplies_total_savings",
        "gfe_completed_month", "gfe_value_month",
        "gfe_completed_ytd", "gfe_value_ytd",
    ]
    for field in numeric_fields:
        if field in payload and payload[field] is not None:
            try:
                val = float(payload[field]) if "." in str(payload[field]) else int(payload[field])
                setattr(data, field, val)
            except (ValueError, TypeError):
                pass

    # Total gross is derived from the five revenue components so the bar chart
    # and Total Gross row always agree.
    data.total_gross = (data.service_revenue + data.prepayment_revenue +
                        data.membership_sales + data.custom_items +
                        data.retail_revenue)

    # Marketing data
    if "marketing" in payload:
        if payload["marketing"]:
            from src.data_schema import MarketingData, CampaignData
            mkt = dict(payload["marketing"])
            camp_payload = mkt.pop("campaigns", None) or []
            campaigns = [CampaignData(**c) for c in camp_payload]
            data.marketing = MarketingData(**mkt, campaigns=campaigns)
        else:
            data.marketing = None

    # Marketing analysis (legacy)
    if "marketing_analysis" in payload and payload["marketing_analysis"]:
        data.marketing_analysis = _build_marketing_analysis(payload["marketing_analysis"])

    # Reviews
    if "reviews" in payload:
        from src.data_schema import ReviewsPlatform
        data.reviews = []
        for r in payload["reviews"]:
            if any(r.get(k) for k in ("new_reviews", "avg_new_rating", "total_reviews", "overall_rating")):
                data.reviews.append(ReviewsPlatform(
                    platform=r.get("platform", ""),
                    new_reviews=int(r["new_reviews"]) if r.get("new_reviews") else None,
                    avg_new_rating=float(r["avg_new_rating"]) if r.get("avg_new_rating") else None,
                    total_reviews=int(r["total_reviews"]) if r.get("total_reviews") else None,
                    overall_rating=float(r["overall_rating"]) if r.get("overall_rating") else None,
                ))

    # Staff
    if "staff" in payload:
        from src.data_schema import StaffMember
        data.staff = []
        for s in payload["staff"]:
            if s.get("name"):
                data.staff.append(StaffMember(
                    name=s["name"],
                    net_revenue=float(s.get("net_revenue", 0)),
                    aov=float(s.get("aov", 0)),
                    utilization=float(s["utilization"]) if s.get("utilization") is not None else None,
                    rebooking_rate=float(s["rebooking_rate"]) if s.get("rebooking_rate") is not None else None,
                    service_revenue=float(s.get("service_revenue", 0)),
                    retail_revenue=float(s.get("retail_revenue", 0)),
                    gross_revenue=float(s.get("gross_revenue", 0)),
                    hours_worked=float(s["hours_worked"]) if s.get("hours_worked") else None,
                ))

    # Services
    if "services" in payload:
        from src.data_schema import ServiceItem
        data.services = [ServiceItem(name=s["name"], revenue=float(s.get("revenue", 0)))
                         for s in payload["services"] if s.get("name")]
        data.compute_service_percentages()

    # Launches
    if "launches" in payload:
        from src.data_schema import LaunchFeature
        data.launches = [LaunchFeature(**l) for l in payload["launches"] if l.get("title")]

    # Brand bank items
    if "brand_bank_items" in payload:
        from src.data_schema import BrandBankItem
        data.brand_bank_items = [BrandBankItem(**b) for b in payload["brand_bank_items"] if b.get("title")]

    # Membership types
    if "membership_types" in payload:
        from src.data_schema import MembershipType
        data.membership_types = []
        for m in payload["membership_types"]:
            if m.get("name"):
                data.membership_types.append(MembershipType(
                    name=m["name"],
                    active=int(m.get("active", 0)),
                    new=int(m.get("new", 0)),
                    churned=int(m.get("churned", 0)),
                    mrr=float(m.get("mrr", 0)),
                ))

    # Supplies by brand
    if "supplies_by_brand" in payload:
        data.supplies_by_brand = payload["supplies_by_brand"]




def _get_omni_key():
    return OMNI_KEY or os.environ.get("OMNI_API_KEY", "")


def _cleanup_old_sessions():
    """Remove sessions older than 2 hours."""
    cutoff = datetime.now().timestamp() - 7200
    to_remove = [k for k, v in sessions.items()
                 if v["created"].timestamp() < cutoff]
    for k in to_remove:
        del sessions[k]


# ── Pages ──

@app.route("/")
def dashboard():
    return render_template("dashboard.html", omni_key_set=bool(_get_omni_key()))


@app.route("/editor/<session_id>")
def editor(session_id):
    sess = _get_session(session_id)
    if not sess:
        return render_template("editor.html",
                               session_id=session_id,
                               data=None,
                               not_found=True)
    return render_template("editor.html",
                           session_id=session_id,
                           data=sess["data"],
                           not_found=False)


@app.route("/archive")
def archive_page():
    return render_template("archive.html")


@app.route("/api/archive")
def api_archive():
    """Return list of all saved reports."""
    reports = _list_archived_reports()
    return jsonify({"reports": reports})


@app.route("/monthly-assets")
def monthly_assets_page():
    return render_template("monthly_assets.html")


@app.route("/batch")
def batch_page():
    return render_template("batch.html", omni_key_set=bool(_get_omni_key()))


@app.route("/beta")
def beta_page():
    return render_template("beta.html", omni_key_set=bool(_get_omni_key()))


# ── API Endpoints ──

@app.route("/api/practices")
def api_practices():
    """Return list of practice names from Omni."""
    key = _get_omni_key()
    if not key:
        return jsonify({"practices": [], "error": "No Omni API key set"})

    try:
        import copy
        from src.omni_loader import _api_get, _run_query, DASHBOARD_ID

        dash = _api_get(f"/v1/documents/{DASHBOARD_ID}/queries", key)
        queries = {q["name"]: q["query"] for q in dash.get("queries", [])}

        q = copy.deepcopy(queries["Medspa Name"])
        # Add tier field (provider_segment_post_launch) so the dashboard segment browser
        # always reflects the current Omni tier
        tier_field = "dbt__moxie_medspas_mart.provider_segment_post_launch"
        if tier_field not in q.get("fields", []):
            q.setdefault("fields", []).append(tier_field)
        q["limit"] = 50000  # no practical cap — Moxie adds practices over time
        result = _run_query(q, key)

        names = []
        ids = []
        tiers = []
        for k, v in result.items():
            if "medspa_name" in k and "with_id" not in k:
                names = v
            elif "medspa_id" in k:
                ids = v
            elif "provider_segment_post_launch" in k:
                tiers = v

        # Build list of {name, id, tier} pairs, filtering out deactivated
        practices = []
        seen = set()
        for i in range(len(names)):
            n = names[i]
            if not n or n.startswith("(DEACTIVATED"):
                continue
            mid = int(ids[i]) if i < len(ids) and ids[i] is not None else None
            tier = tiers[i] if i < len(tiers) and tiers[i] else ""
            if n not in seen:
                seen.add(n)
                practices.append({"name": n, "id": mid, "tier": tier})
        practices.sort(key=lambda p: p["name"])

        return jsonify({"practices": practices})
    except Exception as e:
        return jsonify({"practices": [], "error": str(e)})


@app.route("/api/monthly-assets", methods=["GET"])
def api_get_monthly_assets():
    """Get monthly assets (launches, brand bank) for a given month."""
    month = int(request.args.get("month", 1))
    year = int(request.args.get("year", 2026))
    assets = _load_monthly_assets(month, year)
    # Include saved file info
    resp = {
        "launches": assets.get("launches", []),
        "brand_bank_items": assets.get("brand_bank_items", []),
        "launches_file": assets.get("launches_file"),
        "brand_bank_file": assets.get("brand_bank_file"),
    }
    return jsonify(resp)


@app.route("/api/monthly-assets/all", methods=["GET"])
def api_list_all_monthly_assets():
    """List all months that have saved assets."""
    month_names = ["", "January", "February", "March", "April", "May", "June",
                   "July", "August", "September", "October", "November", "December"]
    results = []

    if _DB_ENABLED:
        for row in _db.list_all_monthly_assets():
            month, year = row["month"], row["year"]
            launches = row.get("launches") or []
            bb_items = row.get("brand_bank_items") or []
            has_upload = bool(row.get("launches_file") or row.get("brand_bank_file"))
            if not launches and not bb_items and not has_upload:
                continue
            results.append({
                "month": month,
                "year": year,
                "label": f"{month_names[month]} {year}",
                "launches_count": len(launches),
                "brand_bank_count": len(bb_items),
                "launches_file": row.get("launches_file"),
                "brand_bank_file": row.get("brand_bank_file"),
            })
        return jsonify({"months": results})

    # File-based fallback
    for f in sorted(MONTHLY_DIR.glob("*.json"), reverse=True):
        key = f.stem  # e.g. "2026-03"
        parts = key.split("-")
        if len(parts) != 2:
            continue
        year, month = int(parts[0]), int(parts[1])
        assets = _load_monthly_assets(month, year)
        launches = assets.get("launches", [])
        bb_items = assets.get("brand_bank_items", [])
        has_upload = bool(assets.get("launches_file") or assets.get("brand_bank_file")
                          or assets.get("launches_path") or assets.get("brand_bank_path"))
        if not launches and not bb_items and not has_upload:
            continue
        results.append({
            "month": month,
            "year": year,
            "label": f"{month_names[month]} {year}",
            "launches_count": len(launches),
            "brand_bank_count": len(bb_items),
            "launches_file": assets.get("launches_file"),
            "brand_bank_file": assets.get("brand_bank_file"),
        })
    return jsonify({"months": results})


@app.route("/api/monthly-assets", methods=["DELETE"])
def api_delete_monthly_assets():
    """Delete specific monthly assets (launches, brand_bank, or both)."""
    month = int(request.json.get("month", 1))
    year = int(request.json.get("year", 2026))
    delete_type = request.json.get("type", "all")  # "launches", "brand_bank", or "all"

    assets = _load_monthly_assets(month, year)
    key = _monthly_key(month, year)

    if delete_type in ("launches", "all"):
        assets["launches"] = []
        assets.pop("launches_file", None)
        assets.pop("launches_path", None)
        if not _DB_ENABLED:
            for ext in (".pdf", ".png", ".jpg", ".jpeg"):
                p = MONTHLY_DIR / f"{key}_launches{ext}"
                if p.exists():
                    p.unlink()

    if delete_type in ("brand_bank", "all"):
        assets["brand_bank_items"] = []
        assets.pop("brand_bank_file", None)
        assets.pop("brand_bank_path", None)
        if not _DB_ENABLED:
            for ext in (".pdf", ".png", ".jpg", ".jpeg"):
                p = MONTHLY_DIR / f"{key}_brand_bank{ext}"
                if p.exists():
                    p.unlink()

    if not _DB_ENABLED and not assets.get("launches") and not assets.get("brand_bank_items"):
        json_path = MONTHLY_DIR / f"{key}.json"
        if json_path.exists():
            json_path.unlink()
    else:
        _save_monthly_assets(month, year, assets)

    return jsonify({"ok": True})


@app.route("/api/monthly-assets", methods=["POST"])
def api_save_monthly_assets():
    """Save edited monthly assets."""
    month = int(request.json.get("month", 1))
    year = int(request.json.get("year", 2026))
    assets = _load_monthly_assets(month, year)
    if "launches" in request.json:
        assets["launches"] = [l for l in request.json["launches"] if l.get("title")]
    if "brand_bank_items" in request.json:
        assets["brand_bank_items"] = [b for b in request.json["brand_bank_items"] if b.get("title")]
    _save_monthly_assets(month, year, assets)
    return jsonify({"ok": True})


@app.route("/api/upload-monthly-launches", methods=["POST"])
def api_upload_monthly_launches():
    """Upload launches PDF/image for a given month. AI-extracts features."""
    month = int(request.form.get("month", 1))
    year = int(request.form.get("year", 2026))

    if "launches_image" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400
    file = request.files["launches_image"]
    if not file.filename:
        return jsonify({"error": "No file selected"}), 400

    suffix = Path(file.filename).suffix.lower()
    try:
        image_path = _save_upload(file, "launch", keep_pdf=(suffix == ".pdf"))
    except Exception as e:
        return jsonify({"error": f"Failed to process file: {e}"}), 400

    # Save upload persistently to monthly dir
    persistent_path = _save_monthly_upload(month, year, "launches", image_path, file.filename)

    # AI extraction
    try:
        items = _analyze_launches_image(persistent_path)
    except Exception as e:
        print(f"  Warning: Could not analyze launches: {e}")
        items = []

    # Save to monthly assets
    assets = _load_monthly_assets(month, year)
    assets["launches"] = items
    assets["launches_file"] = file.filename
    assets["launches_path"] = str(persistent_path)
    _save_monthly_assets(month, year, assets)

    return jsonify({"ok": True, "launches": items, "filename": file.filename})


@app.route("/api/upload-monthly-brand-bank", methods=["POST"])
def api_upload_monthly_brand_bank():
    """Upload brand bank image for a given month. AI-extracts items."""
    month = int(request.form.get("month", 1))
    year = int(request.form.get("year", 2026))
    month_names = ["", "January", "February", "March", "April", "May", "June",
                   "July", "August", "September", "October", "November", "December"]

    if "brand_bank" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400
    file = request.files["brand_bank"]
    if not file.filename:
        return jsonify({"error": "No file selected"}), 400

    suffix = Path(file.filename).suffix.lower()
    try:
        image_path = _save_upload(file, "bb", keep_pdf=(suffix == ".pdf"))
    except Exception as e:
        return jsonify({"error": f"Failed to process file: {e}"}), 400

    # Save upload persistently to monthly dir
    persistent_path = _save_monthly_upload(month, year, "brand_bank", image_path, file.filename)

    # AI extraction
    try:
        items = _analyze_brand_bank_image(str(persistent_path), month_names[month])
    except Exception as e:
        print(f"  Warning: Could not analyze brand bank: {e}")
        items = []

    # Save to monthly assets
    assets = _load_monthly_assets(month, year)
    assets["brand_bank_items"] = items
    assets["brand_bank_file"] = file.filename
    assets["brand_bank_path"] = str(persistent_path)
    _save_monthly_assets(month, year, assets)

    return jsonify({"ok": True, "brand_bank_items": items, "filename": file.filename})


@app.route("/api/status")
def api_status():
    """Return app status — used by the dashboard to show warnings."""
    return jsonify({
        "db_enabled": _DB_ENABLED,
        "storage": "supabase" if _DB_ENABLED else "file",
    })


@app.route("/api/download-monthly-asset")
def api_download_monthly_asset():
    """Download a saved monthly asset file (launches or brand bank)."""
    month = int(request.args.get("month", 1))
    year = int(request.args.get("year", 2026))
    asset_type = request.args.get("type", "launches")  # "launches" or "brand_bank"

    if asset_type not in ("launches", "brand_bank"):
        return jsonify({"error": "Invalid type"}), 400

    assets = _load_monthly_assets(month, year)
    filename_key = "launches_file" if asset_type == "launches" else "brand_bank_file"
    original_filename = assets.get(filename_key) or f"{_monthly_key(month, year)}_{asset_type}"

    suffix = Path(original_filename).suffix.lower() or ".pptx"
    storage_key = f"{_monthly_key(month, year)}_{asset_type}{suffix}"

    if _DB_ENABLED:
        try:
            local_path = _db.download_file("monthly-assets", storage_key, suffix=suffix)
            return send_file(local_path, as_attachment=True, download_name=original_filename)
        except Exception as e:
            return jsonify({"error": f"File not found in storage: {e}"}), 404

    # File-based fallback
    local_path = MONTHLY_DIR / storage_key
    if not local_path.exists():
        return jsonify({"error": "File not found"}), 404
    return send_file(str(local_path), as_attachment=True, download_name=original_filename)


_SUPPLY_DATA_WHITELIST = {
    'transactions_galderma.json', 'transactions_allergan.json',
    'transactions_evolus.json', 'transactions_merz.json',
    'transactions_revance.json', 'rebates.json', 'medspas.json',
    'name_map.json', 'medspa_meta.json', 'revenue_monthly.json',
    'pricing_eras.json', 'vendor_config.json',
}


@app.route("/api/supply-data/<path:filename>")
def api_supply_data(filename):
    """Serve supply savings data — live from Shannon's GitHub Pages for transaction
    files, local static files for reference data. Used by the savings dashboard."""
    basename = filename.split('/')[-1]
    if basename not in _SUPPLY_DATA_WHITELIST:
        return jsonify({"error": "Not found"}), 404

    # For transaction files, try Shannon's live GitHub Pages first
    from src.savings_loader import _fetch_remote_json, _REMOTE_TRANSACTION_FILES
    if basename in _REMOTE_TRANSACTION_FILES:
        remote = _fetch_remote_json(basename)
        if remote is not None:
            return jsonify(remote)

    # Fall back to local committed copy
    local_path = Path(app.static_folder) / 'supplies-savings' / 'data' / basename
    if not local_path.exists():
        return jsonify({"error": "Not found"}), 404
    with open(local_path) as f:
        return app.response_class(f.read(), mimetype='application/json')


@app.route("/api/debug-practice")
def api_debug_practice():
    """Show what Omni returns for a practice name lookup. Dev/debug only."""
    practice = request.args.get("name", "").strip()
    if not practice or not OMNI_KEY:
        return jsonify({"error": "name param required and OMNI_API_KEY must be set"})
    try:
        import copy
        from src.omni_loader import _run_query, _api_get, DASHBOARD_ID
        dashboard = _api_get(f"/v1/documents/{DASHBOARD_ID}/queries", OMNI_KEY)
        queries = {q["name"]: q["query"] for q in dashboard.get("queries", [])}
        tier_q = copy.deepcopy(queries.get("Medspa Name", {}))
        if not tier_q:
            return jsonify({"error": "Medspa Name query not found", "available": list(queries.keys())[:10]})
        tier_q["filters"]["dbt__moxie_medspas_mart.medspa_name"] = {
            "kind": "CONTAINS", "type": "string",
            "values": [practice.split()[0]],
            "is_negative": False,
        }
        for f in ["dbt__moxie_medspas_mart.provider_segment_post_launch",
                  "dbt__moxie_medspas_mart.medspa_name",
                  "dbt__moxie_medspas_mart.medspa_id"]:
            if f not in tier_q.get("fields", []):
                tier_q.setdefault("fields", []).append(f)
        tier_q["limit"] = 50
        result = _run_query(tier_q, OMNI_KEY)
        names = result.get("dbt__moxie_medspas_mart.medspa_name", [])
        tiers = result.get("dbt__moxie_medspas_mart.provider_segment_post_launch", [])
        ids = result.get("dbt__moxie_medspas_mart.medspa_id", [])
        rows = [{"name": names[i], "tier": tiers[i] if i < len(tiers) else None,
                 "id": ids[i] if i < len(ids) else None}
                for i in range(len(names)) if names[i]]
        return jsonify({"query_prefix": practice.split()[0], "rows": rows})
    except Exception as e:
        return jsonify({"error": str(e)})


@app.route("/api/debug-gfe")
def api_debug_gfe():
    """Show raw GFE query result for a practice+month+year. Dev/debug only."""
    practice = request.args.get("name", "").strip()
    month = int(request.args.get("month", 6))
    year = int(request.args.get("year", 2026))
    if not practice or not OMNI_KEY:
        return jsonify({"error": "name param required and OMNI_API_KEY must be set"})
    try:
        import copy, re as _re
        from src.omni_loader import _run_query, _api_get, DASHBOARD_ID
        dashboard = _api_get(f"/v1/documents/{DASHBOARD_ID}/queries", OMNI_KEY)
        queries = {q["name"]: q["query"] for q in dashboard.get("queries", [])}
        start_date = f"{year}-{month:02d}-01"
        results = {}
        for qname in ["Monthly GFE Savings", "YTD GFE Savings"]:
            if qname not in queries:
                results[qname] = "NOT IN DASHBOARD"
                continue
            gq = copy.deepcopy(queries[qname])
            # Show raw query fields/filters so we can find the date field
            results[f"{qname}__fields"] = gq.get("fields", [])
            results[f"{qname}__filter_keys"] = list(gq.get("filters", {}).keys())
            gq["filters"]["dbt__moxie_medspas_mart.medspa_name"] = {
                "kind": "EQUALS", "type": "string", "values": [practice], "is_negative": False,
            }
            # Try date detection
            all_field_names = list(gq.get("fields", [])) + list(gq.get("filters", {}).keys())
            unbracketed = [f for f in all_field_names
                           if "[" not in f and ("date" in f.lower() or "_at" in f.lower() or "issued" in f.lower())]
            date_field = unbracketed[0] if unbracketed else None
            if not date_field:
                bracketed = next((f for f in all_field_names
                                  if "[" in f and ("date" in f.lower() or "_at" in f.lower() or "issued" in f.lower())), None)
                date_field = _re.sub(r"\[[^\]]+\](?:__raw)?$", "", bracketed) if bracketed else None
            results[f"{qname}__date_field_detected"] = date_field
            duration = "1 months" if "Monthly" in qname else f"{month} months"
            left = start_date if "Monthly" in qname else f"{year}-01-01"
            if date_field:
                gq["filters"][date_field] = {
                    "kind": "TIME_FOR_INTERVAL_DURATION", "type": "date", "ui_type": "PAST",
                    "left_side": left, "right_side": duration, "is_negative": False,
                }
            r = _run_query(gq, OMNI_KEY)
            results[f"{qname}__raw"] = {k: v for k, v in r.items() if not k.startswith("$")}
        return jsonify({
            "all_dashboard_queries": sorted(queries.keys()),
            "query_results": results,
        })
    except Exception as e:
        import traceback
        return jsonify({"error": str(e), "traceback": traceback.format_exc()})


@app.route("/api/exists", methods=["POST"])
def api_exists():
    """Quick check: does a saved session exist for this practice+month+year?
    Returns in <1s so the dashboard can show the right status message before
    starting the full generate.
    """
    practice = (request.json or {}).get("practice", "").strip()
    month = int((request.json or {}).get("month", 1))
    year = int((request.json or {}).get("year", 2026))
    if not practice:
        return jsonify({"exists": False})
    session_id = _practice_key(practice, month, year)
    if session_id in sessions:
        return jsonify({"exists": True, "session_id": session_id})
    if _DB_ENABLED:
        raw = _db.load_session_raw(session_id)
        return jsonify({"exists": bool(raw), "session_id": session_id})
    path = SESSIONS_DIR / f"{session_id}.json"
    return jsonify({"exists": path.exists(), "session_id": session_id})


@app.route("/api/generate", methods=["POST"])
def api_generate():
    """Generate a report for a practice.

    If a saved session already exists for this practice+month and the
    caller did not pass force_refresh=true, returns the saved session
    untouched so prior edits are preserved.
    """
    _cleanup_old_sessions()

    practice = request.json.get("practice", "").strip()
    month = int(request.json.get("month", 1))
    year = int(request.json.get("year", 2026))
    force_refresh = bool(request.json.get("force_refresh", False))

    if not practice:
        return jsonify({"error": "Practice name required"}), 400

    session_id = _practice_key(practice, month, year)

    # Preserve previously saved versions: only pull fresh from Omni if no
    # saved session exists or the caller explicitly asked to refresh.
    if not force_refresh:
        existing = _get_session(session_id)
        if existing:
            return jsonify({"session_id": session_id, "ok": True, "from_saved": True})

    try:
        key = _get_omni_key()
        if not key:
            return jsonify({"error": "No Omni API key configured. Set OMNI_API_KEY environment variable."}), 400

        from src.omni_loader import load_from_omni
        from src.narrative import generate_narratives
        from src.html_renderer import render_html
        from src.data_schema import LaunchFeature, BrandBankItem

        # Load data
        data = load_from_omni(practice, month, year, api_key=key)

        # Inject monthly assets (launches & brand bank)
        assets = _load_monthly_assets(month, year)
        if assets.get("launches"):
            data.launches = [LaunchFeature(**l) for l in assets["launches"]]
        if assets.get("brand_bank_items"):
            data.brand_bank_items = [BrandBankItem(**b) for b in assets["brand_bank_items"]]

        # Generate narratives
        generate_narratives(data)

        # Render HTML
        html = render_html(data)

        sessions[session_id] = {
            "data": data,
            "html": html,
            "brand_bank_path": None,
            "marketing_image_path": None,
            "launches_image_path": None,
            "created": datetime.now(),
        }
        _save_session(session_id, sessions[session_id])

        return jsonify({"session_id": session_id, "ok": True, "from_saved": False})

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/generate-beta", methods=["POST"])
def api_generate_beta():
    """Generate an extended-period report (QBR, annual, custom range)."""
    _cleanup_old_sessions()

    practice = request.json.get("practice", "").strip()
    month = int(request.json.get("month", 1))
    year = int(request.json.get("year", 2026))
    duration_months = int(request.json.get("duration_months", 1))
    force_refresh = bool(request.json.get("force_refresh", False))

    if not practice:
        return jsonify({"error": "Practice name required"}), 400
    if duration_months < 1 or duration_months > 24:
        return jsonify({"error": "duration_months must be between 1 and 24"}), 400

    base_key = _practice_key(practice, month, year)
    session_id = base_key if duration_months == 1 else f"{base_key}_{duration_months}mo"

    if not force_refresh:
        existing = _get_session(session_id)
        if existing:
            return jsonify({"session_id": session_id, "ok": True, "from_saved": True})

    try:
        key = _get_omni_key()
        if not key:
            return jsonify({"error": "No Omni API key configured. Set OMNI_API_KEY environment variable."}), 400

        from src.omni_loader import load_from_omni
        from src.narrative import generate_narratives
        from src.html_renderer import render_html
        from src.data_schema import LaunchFeature, BrandBankItem

        # Load data over extended period
        data = load_from_omni(practice, month, year, api_key=key, duration_months=duration_months)

        # Inject monthly assets only for single-month pulls
        if duration_months == 1:
            assets = _load_monthly_assets(month, year)
            if assets.get("launches"):
                data.launches = [LaunchFeature(**l) for l in assets["launches"]]
            if assets.get("brand_bank_items"):
                data.brand_bank_items = [BrandBankItem(**b) for b in assets["brand_bank_items"]]

        generate_narratives(data)
        html = render_html(data)

        sessions[session_id] = {
            "data": data,
            "html": html,
            "brand_bank_path": None,
            "marketing_image_path": None,
            "launches_image_path": None,
            "created": datetime.now(),
        }
        _save_session(session_id, sessions[session_id])

        return jsonify({"session_id": session_id, "ok": True, "from_saved": False})

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/preview/<session_id>")
def api_preview(session_id):
    """Return rendered report HTML for iframe. Re-renders if needed."""
    sess = _get_session(session_id)
    if not sess:
        return "Session not found", 404
    if sess.get("needs_render"):
        _rerender(sess)
        sess["needs_render"] = False
    return Response(sess["html"], content_type="text/html")


@app.route("/api/rerender/<session_id>", methods=["POST"])
def api_rerender(session_id):
    """Force re-render the report HTML from the current template + data."""
    sess = _get_session(session_id)
    if not sess:
        return jsonify({"error": "Session not found"}), 404
    _rerender(sess)
    return jsonify({"ok": True})


@app.route("/api/update/<session_id>", methods=["POST"])
def api_update(session_id):
    """Update editable fields and re-render (auto-save, no version snapshot)."""
    sess = _get_session(session_id)
    if not sess:
        return jsonify({"error": "Session not found"}), 404
    _apply_payload(sess["data"], request.json)
    _rerender(sess)
    _save_session(session_id, sess, snapshot=False)
    return jsonify({"ok": True})


@app.route("/api/save/<session_id>", methods=["POST"])
def api_save(session_id):
    """Explicit save — updates data, re-renders, and creates version snapshot."""
    sess = _get_session(session_id)
    if not sess:
        return jsonify({"error": "Session not found"}), 404
    _apply_payload(sess["data"], request.json)
    _rerender(sess)
    _save_session(session_id, sess, snapshot=True)
    return jsonify({"ok": True})


@app.route("/api/versions/<session_id>")
def api_versions(session_id):
    """List available versions for a session."""
    sess = _get_session(session_id)
    if not sess:
        return jsonify({"error": "Session not found"}), 404
    versions = _list_versions(session_id)
    return jsonify({"versions": versions})


@app.route("/api/restore/<session_id>", methods=["POST"])
def api_restore(session_id):
    """Restore a previous version."""
    sess = _get_session(session_id)
    if not sess:
        return jsonify({"error": "Session not found"}), 404

    filename = request.json.get("filename", "")

    if _DB_ENABLED:
        version_payload = _db.load_version(filename)
        if not version_payload:
            return jsonify({"error": "Version not found"}), 404
        data = _deserialize_data(version_payload["data"])
        sess["data"] = data
        _rerender(sess)
        _save_session(session_id, sess, snapshot=True)
        sessions[session_id] = sess
        return jsonify({"ok": True})

    # File-based fallback
    if ".." in filename or "/" in filename:
        return jsonify({"error": "Invalid filename"}), 400
    versions_dir = SESSIONS_DIR / f"{session_id}_versions"
    version_path = versions_dir / filename
    if not version_path.exists():
        return jsonify({"error": "Version not found"}), 404
    current_path = SESSIONS_DIR / f"{session_id}.json"
    if current_path.exists():
        _snapshot_version(session_id, current_path)
    with open(version_path) as f:
        payload = json.load(f)
    data = _deserialize_data(payload["data"])
    sess["data"] = data
    _rerender(sess)
    _save_session(session_id, sess, snapshot=False)
    sessions[session_id] = sess
    return jsonify({"ok": True})


@app.route("/api/admin/refresh-session/<session_id>", methods=["POST"])
def api_admin_refresh_session(session_id):
    """Force a single saved report to be re-pulled from Omni.

    Snapshots the current saved state to version history first so the
    pre-refresh report is never lost (the user can restore it from the
    versions list if the fresh pull looks wrong). Requires the site
    password to gate accidental clicks.
    """
    payload = request.json or {}
    if payload.get("password") != "moxie2026":
        return jsonify({"error": "Invalid admin password"}), 403

    # Read existing session to recover practice_name + month/year, since
    # the session_id slug isn't reversible.
    existing = _get_session(session_id)
    if not existing:
        return jsonify({"error": f"No saved session for '{session_id}'"}), 404

    practice_name = existing["data"].practice_name
    month = existing["data"].month
    year = existing["data"].year

    # Beta sessions carry a "_{N}mo" suffix; preserve the duration on refresh.
    duration_months = 1
    if "_" in session_id and session_id.endswith("mo"):
        try:
            duration_months = int(session_id.rsplit("_", 1)[-1][:-2])
        except (ValueError, IndexError):
            duration_months = 1

    try:
        key = _get_omni_key()
        if not key:
            return jsonify({"error": "Omni API key not configured"}), 500

        from src.omni_loader import load_from_omni
        from src.narrative import generate_narratives
        from src.html_renderer import render_html
        from src.data_schema import LaunchFeature, BrandBankItem

        # Snapshot the current saved state so the refresh is undoable.
        current_path = SESSIONS_DIR / f"{session_id}.json"
        if current_path.exists():
            _snapshot_version(session_id, current_path)

        data = load_from_omni(practice_name, month, year, api_key=key,
                              duration_months=duration_months)

        if duration_months == 1:
            assets = _load_monthly_assets(month, year)
            if assets.get("launches"):
                data.launches = [LaunchFeature(**l) for l in assets["launches"]]
            if assets.get("brand_bank_items"):
                data.brand_bank_items = [BrandBankItem(**b) for b in assets["brand_bank_items"]]

        generate_narratives(data)
        html = render_html(data)

        sessions[session_id] = {
            "data": data,
            "html": html,
            "brand_bank_path": None,
            "marketing_image_path": None,
            "launches_image_path": None,
            "created": datetime.now(),
        }
        _save_session(session_id, sessions[session_id], snapshot=False)  # already snapshotted above

        return jsonify({
            "session_id": session_id, "ok": True,
            "practice": practice_name, "month": month, "year": year,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/export/<session_id>")
def api_export(session_id):
    """Export current report as PDF using the exact same HTML shown in preview."""
    sess = _get_session(session_id)
    if not sess:
        return "Session not found", 404
    data = sess["data"]

    # Ensure we have up-to-date HTML (re-render if needed)
    if sess.get("needs_render") or not sess.get("html"):
        _rerender(sess)
        sess["needs_render"] = False

    # Generate PDF from the SAME HTML the user sees in the preview
    tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
    tmp.close()

    try:
        from src.html_renderer import html_to_pdf
        html_to_pdf(sess["html"], tmp.name)

        safe_name = data.practice_name.replace(" ", "_")
        filename = f"{safe_name}_MBR_{data.month_name}_{data.year}.pdf"

        return send_file(tmp.name, as_attachment=True, download_name=filename,
                         mimetype="application/pdf")
    except Exception as e:
        os.unlink(tmp.name)
        return jsonify({"error": str(e)}), 500


@app.route("/api/email/<session_id>", methods=["POST"])
def api_email(session_id):
    """Generate PDF and send it to the given recipient via Gmail SMTP."""
    import smtplib
    from email.message import EmailMessage

    gmail_user = os.environ.get("GMAIL_USER", "")
    gmail_password = os.environ.get("GMAIL_APP_PASSWORD", "")
    if not gmail_user or not gmail_password:
        return jsonify({"error": "GMAIL_USER and GMAIL_APP_PASSWORD are not configured on this server."}), 400

    body = request.json or {}
    raw = body.get("emails") or ([body.get("email")] if body.get("email") else [])
    recipients = [e.strip() for e in raw if e and "@" in str(e)]
    if not recipients:
        return jsonify({"error": "Please enter at least one valid email address."}), 400

    sess = _get_session(session_id)
    if not sess:
        return jsonify({"error": "Session not found"}), 404
    data = sess["data"]

    if sess.get("needs_render") or not sess.get("html"):
        _rerender(sess)
        sess["needs_render"] = False

    tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
    tmp.close()
    try:
        from src.html_renderer import html_to_pdf
        html_to_pdf(sess["html"], tmp.name)
        with open(tmp.name, "rb") as f:
            pdf_bytes = f.read()
    finally:
        os.unlink(tmp.name)

    safe_name = data.practice_name.replace(" ", "_")
    filename = f"{safe_name}_MBR_{data.month_name}_{data.year}.pdf"

    msg = EmailMessage()
    msg["Subject"] = f"Monthly Business Review — {data.practice_name} — {data.month_name} {data.year}"
    msg["From"] = f"Moxie Reports <{gmail_user}>"
    msg["To"] = ", ".join(recipients)
    msg.set_content(
        f"Hi,\n\nPlease find attached the Monthly Business Review for "
        f"{data.practice_name} — {data.month_name} {data.year}.\n\n"
        f"Moxie Partners, Inc. · Private & Confidential"
    )
    msg.add_attachment(pdf_bytes, maintype="application", subtype="pdf", filename=filename)

    try:
        with smtplib.SMTP("smtp.gmail.com", 587) as smtp:
            smtp.ehlo()
            smtp.starttls()
            smtp.login(gmail_user, gmail_password)
            smtp.send_message(msg)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/upload-brand-bank/<session_id>", methods=["POST"])
def api_upload_brand_bank(session_id):
    """Upload a brand bank image for the report."""
    sess = _get_session(session_id)
    if not sess:
        return jsonify({"error": "Session not found"}), 404

    if "brand_bank" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400

    file = request.files["brand_bank"]
    if not file.filename:
        return jsonify({"error": "No file selected"}), 400

    suffix = Path(file.filename).suffix.lower()
    try:
        image_path = _save_upload(file, "bb", keep_pdf=(suffix == ".pdf"))
    except Exception as e:
        return jsonify({"error": f"Failed to process file: {e}"}), 400

    # Remove old file if exists
    old_path = sess.get("brand_bank_path")
    if old_path and os.path.exists(old_path):
        os.unlink(old_path)

    sess["brand_bank_path"] = image_path
    data = sess["data"]

    # AI extraction
    try:
        from src.data_schema import BrandBankItem
        items = _analyze_brand_bank_image(image_path, data.month_name)
        data.brand_bank_items = [BrandBankItem(**item) for item in items]
    except Exception as e:
        print(f"  Warning: Could not analyze brand bank: {e}")
        data.brand_bank_items = []

    # Re-render with brand bank
    from src.html_renderer import render_html
    sess["html"] = render_html(data,
                               brand_bank_path=image_path,
                               marketing_image_path=sess.get("marketing_image_path"),
                               launches_image_path=sess.get("launches_image_path"))

    _save_session(session_id, sess)
    bb_list = [{"title": b.title, "category": b.category} for b in data.brand_bank_items]
    return jsonify({"ok": True, "brand_bank_items": bb_list})


@app.route("/api/remove-brand-bank/<session_id>", methods=["POST"])
def api_remove_brand_bank(session_id):
    """Remove the brand bank image."""
    sess = _get_session(session_id)
    if not sess:
        return jsonify({"error": "Session not found"}), 404
    old_path = sess.get("brand_bank_path")
    if old_path and os.path.exists(old_path):
        os.unlink(old_path)
    sess["brand_bank_path"] = None
    sess["data"].brand_bank_items = []

    # Re-render without brand bank
    from src.html_renderer import render_html
    sess["html"] = render_html(sess["data"],
                               marketing_image_path=sess.get("marketing_image_path"),
                               launches_image_path=sess.get("launches_image_path"))

    _save_session(session_id, sess)
    return jsonify({"ok": True})


def _pdf_to_png(pdf_path: str) -> str:
    """Convert the first page of a PDF to a PNG image. Returns the PNG path."""
    png_path = pdf_path.rsplit(".", 1)[0] + ".png"

    # Try PyMuPDF (fitz) first — works everywhere without system dependencies
    try:
        import fitz
        doc = fitz.open(pdf_path)
        page = doc[0]
        pix = page.get_pixmap(dpi=200)
        pix.save(png_path)
        doc.close()
        if os.path.exists(png_path):
            return png_path
    except ImportError:
        pass  # fitz not available, try pdftoppm

    # Fallback to pdftoppm (system tool)
    for pdftoppm_path in ["/opt/homebrew/bin/pdftoppm", "/usr/bin/pdftoppm", "pdftoppm"]:
        try:
            out_prefix = pdf_path.rsplit(".", 1)[0]
            subprocess.run(
                [pdftoppm_path, "-png", "-f", "1", "-l", "1",
                 "-r", "200", "-singlefile", pdf_path, out_prefix],
                check=True, capture_output=True,
            )
            if os.path.exists(png_path):
                return png_path
        except (FileNotFoundError, subprocess.CalledProcessError):
            continue

    raise FileNotFoundError(f"PDF conversion failed — neither PyMuPDF nor pdftoppm available")


def _save_upload(file_storage, prefix: str, keep_pdf: bool = False) -> str:
    """Save an uploaded file (image or PDF). If PDF, convert first page to PNG.
    Returns the path to the final image file.
    If keep_pdf=True, return the raw PDF path (for hyperlink extraction)."""
    suffix = Path(file_storage.filename).suffix.lower() or ".png"
    tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False, prefix=f"mbr_{prefix}_")
    file_storage.save(tmp.name)
    tmp.close()

    if suffix == ".pdf":
        if keep_pdf:
            return tmp.name  # caller needs the raw PDF for link extraction
        png_path = _pdf_to_png(tmp.name)
        os.unlink(tmp.name)  # remove the PDF, keep the PNG
        return png_path

    return tmp.name


def _rerender(sess):
    """Re-render a session's HTML with all current image paths.
    Skip image paths when structured data exists to avoid fallback."""
    from src.html_renderer import render_html
    data = sess["data"]
    bb_path = sess.get("brand_bank_path") if not data.brand_bank_items else None
    launches_path = sess.get("launches_image_path") if not data.launches else None
    sess["html"] = render_html(data,
                               brand_bank_path=bb_path,
                               marketing_image_path=sess.get("marketing_image_path"),
                               launches_image_path=launches_path)


def _analyze_marketing_image(image_path: str, practice_name: str, month_name: str, year: int) -> dict:
    """Use Claude to analyze a marketing screenshot and return structured analysis.

    Returns dict with keys: metrics, summary, next_steps
    """
    import anthropic
    import base64

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return {
            "metrics": [],
            "summary": "Marketing image uploaded. Set ANTHROPIC_API_KEY to enable AI analysis.",
            "next_steps": [],
        }

    with open(image_path, "rb") as f:
        img_data = base64.standard_b64encode(f.read()).decode()

    suffix = Path(image_path).suffix.lower()
    media_type = "image/jpeg" if suffix in (".jpg", ".jpeg") else "image/png"

    client = anthropic.Anthropic(api_key=api_key)
    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=2048,
        messages=[{
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {"type": "base64", "media_type": media_type, "data": img_data},
                },
                {
                    "type": "text",
                    "text": (
                        f"You are a marketing analyst for {practice_name}, a medspa practice. "
                        f"This is their Meta Ads / marketing performance report for {month_name} {year}.\n\n"
                        f"Analyze the data and return ONLY valid JSON (no markdown, no code fences) with this structure:\n"
                        f'{{\n'
                        f'  "funnel": [\n'
                        f'    {{"label": "Ad Spend", "value": "$942", "subtitle": "Monthly Budget"}},\n'
                        f'    {{"label": "Leads", "value": "67", "subtitle": "New Patient Leads"}},\n'
                        f'    {{"label": "Booked", "value": "11", "subtitle": "# of Booked Appointments"}},\n'
                        f'    {{"label": "Completed", "value": "5", "subtitle": "# of Completed Appointments"}},\n'
                        f'    {{"label": "Revenue", "value": "$2,224", "subtitle": "First-visit Revenue"}}\n'
                        f'  ],\n'
                        f'  "kpis": [\n'
                        f'    {{"label": "First-visit ROI", "value": "2.36x", "goal": "Goal: 3x", "status": "Below Target"}},\n'
                        f'    {{"label": "Lead to Booking Rate", "value": "16.42%", "goal": "Goal: 15%", "status": "On Track"}},\n'
                        f'    {{"label": "First-Visit AOV", "value": "$444.80", "goal": "Goal: $575", "status": "Below Target"}}\n'
                        f'  ],\n'
                        f'  "roi_headline": "For every $1 you spend on this campaign, you generate $2.36 from new patients on their first visit",\n'
                        f'  "summary": "Brief 1-2 sentence performance summary.",\n'
                        f'  "next_steps": [\n'
                        f'    {{"title": "Short action title", "description": "1-2 sentence explanation of why and how."}},\n'
                        f'    {{"title": "Another action", "description": "Explanation."}},\n'
                        f'    {{"title": "Third action", "description": "Explanation."}}\n'
                        f'  ]\n'
                        f'}}\n\n'
                        f"IMPORTANT:\n"
                        f"- Extract the marketing FUNNEL metrics from the image (spend, leads, booked, completed, revenue). "
                        f"Include whatever funnel stages are visible. Each needs a label, value, and subtitle.\n"
                        f"- Extract 2-4 KEY PERFORMANCE INDICATORS with target comparisons. For each, determine if the practice is "
                        f"'On Track', 'Below Target', or 'Above Target' based on industry benchmarks for medspas "
                        f"(ROI goal: 3x, lead-to-booking: 15%, first-visit AOV: $575, cost per lead: <$30).\n"
                        f"- Write a roi_headline like 'For every $1 you spend, you generate $X.XX' if ROI data is available.\n"
                        f"- Give 3 specific, actionable next_steps, each with a short title and a 1-2 sentence description.\n"
                        f"- Format values for easy reading ($ for money, % for rates, commas for large numbers).\n"
                        f"- Return ONLY the JSON object, nothing else."
                    ),
                },
            ],
        }],
    )

    raw = message.content[0].text.strip()
    # Strip markdown code fences if present
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
        if raw.endswith("```"):
            raw = raw[:-3]
        raw = raw.strip()

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # Fallback: return as unstructured summary
        return {"metrics": [], "summary": raw, "next_steps": []}


def _analyze_launches_image(image_path: str) -> list:
    """Use Claude to extract launch features from an uploaded image or PDF.

    For PDFs, extracts hyperlinks using PyMuPDF (fitz) and converts pages to images.
    """
    import anthropic
    import base64

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return []

    suffix = Path(image_path).suffix.lower()
    links_text = ""
    content = []

    if suffix == ".pdf":
        # Try to extract hyperlinks and convert pages to images using PyMuPDF
        try:
            import fitz
            doc = fitz.open(image_path)

            # Extract hyperlinks from all pages
            for page_num, page in enumerate(doc):
                for link in page.get_links():
                    if link.get("uri"):
                        rect = link["from"]
                        text = page.get_text("text", clip=fitz.Rect(rect)).strip()
                        if text:
                            links_text += f"- {text}: {link['uri']}\n"
                        else:
                            links_text += f"- (unnamed link): {link['uri']}\n"

            # Convert each page to a PNG image
            for page in doc:
                pix = page.get_pixmap(dpi=150)
                img_bytes = pix.tobytes("png")
                img_b64 = base64.standard_b64encode(img_bytes).decode()
                content.append({
                    "type": "image",
                    "source": {"type": "base64", "media_type": "image/png", "data": img_b64},
                })
            doc.close()
        except ImportError:
            # fitz not available — fall back to treating the converted PNG as an image
            with open(image_path, "rb") as f:
                img_data = base64.standard_b64encode(f.read()).decode()
            media_type = "image/png"
            content.append({
                "type": "image",
                "source": {"type": "base64", "media_type": media_type, "data": img_data},
            })
    else:
        # Regular image file
        with open(image_path, "rb") as f:
            img_data = base64.standard_b64encode(f.read()).decode()
        media_type = "image/jpeg" if suffix in (".jpg", ".jpeg") else "image/png"
        content.append({
            "type": "image",
            "source": {"type": "base64", "media_type": media_type, "data": img_data},
        })

    # Build the prompt text
    prompt_text = (
        "This is a Moxie Suite Launches image showing new product features for medspa software. "
        "Extract each feature and return ONLY valid JSON (no markdown, no code fences):\n"
        '[\n'
        '  {"title": "Feature Name", "category": "Short category tag", "description": "2-3 sentence description of the feature and its benefit.", "url": "https://..."},\n'
        '  ...\n'
        ']\n\n'
        "IMPORTANT:\n"
        "- Extract ALL features shown in the image\n"
        "- title: the feature name exactly as shown\n"
        "- category: a short tag like 'Calendar', 'Billing', 'Online Booking', 'Products', etc.\n"
        "- description: faithfully capture the key points from the image description. Keep the practice-friendly tone.\n"
        "- url: if a hyperlink is associated with this feature (from the extracted links below), include it. Otherwise leave empty string.\n"
        "- Return ONLY the JSON array"
    )

    if links_text:
        prompt_text += f"\n\nExtracted hyperlinks from the PDF:\n{links_text}\nMatch each feature to its hyperlink URL if available."

    content.append({"type": "text", "text": prompt_text})

    client = anthropic.Anthropic(api_key=api_key)
    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=2048,
        messages=[{"role": "user", "content": content}],
    )

    raw = message.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
        if raw.endswith("```"):
            raw = raw[:-3]
        raw = raw.strip()

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return []


def _analyze_brand_bank_image(image_path: str, month_name: str) -> list:
    """Use Claude to extract brand bank items from an uploaded image or PDF."""
    import anthropic
    import base64

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return []

    suffix = Path(image_path).suffix.lower()
    content = []

    if suffix == ".pdf":
        # Convert PDF pages to images using PyMuPDF
        try:
            import fitz
            doc = fitz.open(image_path)
            for page in doc:
                pix = page.get_pixmap(dpi=150)
                img_bytes = pix.tobytes("png")
                img_b64 = base64.standard_b64encode(img_bytes).decode()
                content.append({
                    "type": "image",
                    "source": {"type": "base64", "media_type": "image/png", "data": img_b64},
                })
            doc.close()
        except ImportError:
            print("  Warning: PyMuPDF (fitz) not available for PDF processing")
            return []
    else:
        with open(image_path, "rb") as f:
            img_data = base64.standard_b64encode(f.read()).decode()
        media_type = "image/jpeg" if suffix in (".jpg", ".jpeg") else "image/png"
        content.append({
            "type": "image",
            "source": {"type": "base64", "media_type": media_type, "data": img_data},
        })

    content.append({"type": "text", "text": (
        f"This is a Brand Bank image for a medspa showing marketing assets for {month_name}. "
        "Extract each marketing asset/item and return ONLY valid JSON (no markdown, no code fences):\n"
        '[\n'
        '  {"title": "Asset Title", "category": "Type of asset"},\n'
        '  ...\n'
        ']\n\n'
        "IMPORTANT:\n"
        "- Extract ALL items/assets shown in the image\n"
        "- title: the asset name exactly as shown (e.g. 'Valentines/Galentines Promos')\n"
        "- category: type like 'Socials Carousel', 'Print Flyer', 'Event Print & Socials', 'Social Post', etc.\n"
        "- Return ONLY the JSON array"
    )})

    client = anthropic.Anthropic(api_key=api_key)
    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=2048,
        messages=[{"role": "user", "content": content}],
    )

    raw = message.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
        if raw.endswith("```"):
            raw = raw[:-3]
        raw = raw.strip()

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return []


def _build_marketing_analysis(result: dict):
    """Build MarketingAnalysis from AI JSON result."""
    from src.data_schema import (MarketingAnalysis, MarketingMetric,
                                  MarketingKPI, MarketingNextStep)
    funnel = [MarketingMetric(**m) for m in result.get("funnel", [])]
    kpis = [MarketingKPI(**k) for k in result.get("kpis", [])]
    next_steps = []
    for s in result.get("next_steps", []):
        if isinstance(s, dict):
            next_steps.append(MarketingNextStep(**s))
        else:
            next_steps.append(MarketingNextStep(title=str(s)))
    # Legacy compat: also populate metrics from funnel
    metrics = [MarketingMetric(**m) for m in result.get("metrics", [])]
    if not metrics:
        metrics = funnel

    return MarketingAnalysis(
        funnel=funnel,
        kpis=kpis,
        roi_headline=result.get("roi_headline", ""),
        summary=result.get("summary", ""),
        next_steps=next_steps,
        metrics=metrics,
    )


@app.route("/api/upload-marketing/<session_id>", methods=["POST"])
def api_upload_marketing(session_id):
    """Upload a marketing screenshot for AI analysis."""
    sess = _get_session(session_id)
    if not sess:
        return jsonify({"error": "Session not found"}), 404

    if "marketing_image" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400

    file = request.files["marketing_image"]
    if not file.filename:
        return jsonify({"error": "No file selected"}), 400

    try:
        image_path = _save_upload(file, "mkt")
    except Exception as e:
        return jsonify({"error": f"Failed to process file: {e}"}), 400

    old_path = sess.get("marketing_image_path")
    if old_path and os.path.exists(old_path):
        os.unlink(old_path)

    sess["marketing_image_path"] = image_path
    data = sess["data"]

    # Run AI analysis
    try:
        from src.data_schema import (MarketingAnalysis, MarketingMetric,
                                      MarketingKPI, MarketingNextStep)
        result = _analyze_marketing_image(
            image_path, data.practice_name, data.month_name, data.year)
        data.marketing_analysis = _build_marketing_analysis(result)
        data.marketing_recommendations = data.marketing_analysis.summary
    except Exception as e:
        data.marketing_recommendations = f"Could not analyze image: {e}"
        data.marketing_analysis = None

    _rerender(sess)
    _save_session(session_id, sess)
    analysis_dict = None
    if data.marketing_analysis:
        from dataclasses import asdict
        analysis_dict = asdict(data.marketing_analysis)
    return jsonify({"ok": True, "analysis": analysis_dict,
                    "recommendations": data.marketing_recommendations})


@app.route("/api/remove-marketing/<session_id>", methods=["POST"])
def api_remove_marketing(session_id):
    """Remove the marketing image."""
    sess = _get_session(session_id)
    if not sess:
        return jsonify({"error": "Session not found"}), 404
    old_path = sess.get("marketing_image_path")
    if old_path and os.path.exists(old_path):
        os.unlink(old_path)
    sess["marketing_image_path"] = None
    sess["data"].marketing_recommendations = ""
    sess["data"].marketing_analysis = None

    _rerender(sess)
    _save_session(session_id, sess)
    return jsonify({"ok": True})


@app.route("/api/upload-launches/<session_id>", methods=["POST"])
def api_upload_launches(session_id):
    """Upload a Moxie Suite Launches image."""
    sess = _get_session(session_id)
    if not sess:
        return jsonify({"error": "Session not found"}), 404

    if "launches_image" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400

    file = request.files["launches_image"]
    if not file.filename:
        return jsonify({"error": "No file selected"}), 400

    # Keep PDFs as-is for hyperlink extraction; images are saved normally
    suffix = Path(file.filename).suffix.lower()
    try:
        image_path = _save_upload(file, "launch", keep_pdf=(suffix == ".pdf"))
    except Exception as e:
        return jsonify({"error": f"Failed to process file: {e}"}), 400

    old_path = sess.get("launches_image_path")
    if old_path and os.path.exists(old_path):
        os.unlink(old_path)

    sess["launches_image_path"] = image_path
    data = sess["data"]

    # AI extraction (handles both images and PDFs with hyperlink extraction)
    try:
        from src.data_schema import LaunchFeature
        items = _analyze_launches_image(image_path)
        data.launches = [LaunchFeature(**item) for item in items]
    except Exception as e:
        print(f"  Warning: Could not analyze launches: {e}")
        data.launches = []

    # For PDFs, also convert to PNG for the preview/render pipeline
    if suffix == ".pdf":
        try:
            png_path = _pdf_to_png(image_path)
            sess["launches_image_path"] = png_path
        except Exception:
            pass  # keep the PDF path; renderer may not show preview but data is extracted

    _rerender(sess)
    _save_session(session_id, sess)

    launches_list = [{"title": l.title, "category": l.category, "description": l.description, "url": l.url}
                     for l in data.launches]
    return jsonify({"ok": True, "launches": launches_list})


@app.route("/api/remove-launches/<session_id>", methods=["POST"])
def api_remove_launches(session_id):
    """Remove the launches image."""
    sess = _get_session(session_id)
    if not sess:
        return jsonify({"error": "Session not found"}), 404
    old_path = sess.get("launches_image_path")
    if old_path and os.path.exists(old_path):
        os.unlink(old_path)
    sess["launches_image_path"] = None
    sess["data"].launches = []

    _rerender(sess)
    _save_session(session_id, sess)
    return jsonify({"ok": True})


@app.route("/api/export-pptx/<session_id>")
def api_export_pptx(session_id):
    """Export current report as PowerPoint (PPTX)."""
    sess = _get_session(session_id)
    if not sess:
        return "Session not found", 404
    data = sess["data"]

    tmp = tempfile.NamedTemporaryFile(suffix=".pptx", delete=False)
    tmp.close()

    try:
        from src.slide_builder import build_mbr
        build_mbr(data, tmp.name, brand_bank_path=sess.get("brand_bank_path"))

        safe_name = data.practice_name.replace(" ", "_")
        filename = f"{safe_name}_MBR_{data.month_name}_{data.year}.pptx"

        return send_file(tmp.name, as_attachment=True, download_name=filename,
                         mimetype="application/vnd.openxmlformats-officedocument.presentationml.presentation")
    except Exception as e:
        os.unlink(tmp.name)
        return jsonify({"error": str(e)}), 500


@app.route("/api/batch/start", methods=["POST"])
def api_batch_start():
    """Start a batch PDF generation job."""
    practices = request.json.get("practices", [])
    month = int(request.json.get("month", 1))
    year = int(request.json.get("year", 2026))

    if not practices:
        return jsonify({"error": "No practices selected"}), 400

    job_id = str(uuid.uuid4())[:8]
    batch_jobs[job_id] = {
        "total": len(practices),
        "completed": 0,
        "current": "",
        "status": "running",
        "zip_path": None,
        "errors": [],
    }

    def run_batch():
        import asyncio

        async def _async_batch():
            key = _get_omni_key()
            out_dir = tempfile.mkdtemp(prefix="mbr_batch_")

            from src.omni_loader import load_from_omni
            from src.narrative import generate_narratives
            from src.html_renderer import render_html
            from src.data_schema import LaunchFeature, BrandBankItem
            from playwright.async_api import async_playwright

            # Load shared monthly assets once
            assets = _load_monthly_assets(month, year)

            pw = await async_playwright().start()
            browser = await pw.chromium.launch()

            for i, practice in enumerate(practices):
                batch_jobs[job_id]["current"] = practice
                try:
                    data = load_from_omni(practice, month, year, api_key=key)
                    # Inject monthly assets
                    if assets.get("launches"):
                        data.launches = [LaunchFeature(**l) for l in assets["launches"]]
                    if assets.get("brand_bank_items"):
                        data.brand_bank_items = [BrandBankItem(**b) for b in assets["brand_bank_items"]]
                    generate_narratives(data)
                    html = render_html(data)

                    safe_name = practice.replace(" ", "_")
                    pdf_path = os.path.join(out_dir, f"{safe_name}_MBR_{data.month_name}_{year}.pdf")

                    page = await browser.new_page()
                    await page.set_content(html, wait_until="networkidle")
                    await page.evaluate("() => document.fonts.ready")
                    await page.pdf(
                        path=pdf_path,
                        format="Letter",
                        print_background=True,
                        prefer_css_page_size=True,
                        margin={"top": "0", "right": "0", "bottom": "0", "left": "0"},
                    )
                    await page.close()

                except Exception as e:
                    batch_jobs[job_id]["errors"].append({"practice": practice, "error": str(e)})

                batch_jobs[job_id]["completed"] = i + 1

            await browser.close()
            await pw.stop()

            # Zip results (use zipfile for ZIP64 support with large batches)
            import zipfile
            zip_path = os.path.join(out_dir, "MBR_Reports.zip")
            with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
                for fname in os.listdir(out_dir):
                    if fname.endswith('.pdf'):
                        zf.write(os.path.join(out_dir, fname), fname)
            batch_jobs[job_id]["zip_path"] = zip_path
            batch_jobs[job_id]["status"] = "done"
            batch_jobs[job_id]["current"] = ""

        try:
            loop = asyncio.new_event_loop()
            loop.run_until_complete(_async_batch())
            loop.close()
        except Exception as e:
            # Top-level error — mark job as done with error so frontend doesn't hang
            batch_jobs[job_id]["status"] = "done"
            batch_jobs[job_id]["current"] = ""
            batch_jobs[job_id]["errors"].append({"practice": "(batch)", "error": str(e)})

    thread = threading.Thread(target=run_batch, daemon=True)
    thread.start()

    return jsonify({"job_id": job_id})


@app.route("/api/batch/status/<job_id>")
def api_batch_status(job_id):
    if job_id not in batch_jobs:
        return jsonify({"error": "Job not found"}), 404
    job = batch_jobs[job_id]
    return jsonify({
        "total": job["total"],
        "completed": job["completed"],
        "current": job["current"],
        "status": job["status"],
        "errors": job["errors"],
    })


@app.route("/api/batch/download/<job_id>")
def api_batch_download(job_id):
    if job_id not in batch_jobs:
        return "Job not found", 404
    job = batch_jobs[job_id]
    if job["status"] != "done" or not job["zip_path"]:
        return "Not ready", 425
    return send_file(job["zip_path"], as_attachment=True,
                     download_name="MBR_Reports.zip", mimetype="application/zip")



@app.route('/supplies-savings')
def supplies_savings():
    return redirect('/static/supplies-savings/app/dashboard.html')


# ── Tox Club Partner List ─────────────────────────────────────────────────────

TOX_CLUB_FILE = Path(_persist_base) / "tox_club_partners.json"

_TOX_CLUB_DEFAULTS = [
    {"name": "AM Aesthetics & Wellness", "id": "1551", "email": "amaestheticsandwellness@gmail.com", "state": "VA", "psm": "Ericka Olmos", "psm_email": "ericka@joinmoxie.com", "notes": ""},
    {"name": "Adela Medical Spa", "id": "1349", "email": "adelamedspa7@gmail.com", "state": "OH", "psm": "Ericka Olmos", "psm_email": "ericka@joinmoxie.com", "notes": ""},
    {"name": "Advanced Aesthetics + Wellness", "id": "1435", "email": "aawbyrenata@gmail.com", "state": "TX", "psm": "Michelle Garcia", "psm_email": "michellegarcia@joinmoxie.com", "notes": ""},
    {"name": "Aesthetics Lab", "id": "800", "email": "hello@aestheticslabdetroit.com", "state": "MI", "psm": "Alisha Faber", "psm_email": "alisha@joinmoxie.com", "notes": ""},
    {"name": "Alesca Aesthetics", "id": "1426", "email": "info@alescaaesthetics.com", "state": "PA", "psm": "Katie Sensing", "psm_email": "katie@joinmoxie.com", "notes": ""},
    {"name": "Aloha Aesthetic", "id": "1450", "email": "hello@alohadetroit.com", "state": "MI", "psm": "Alisha Faber", "psm_email": "alisha@joinmoxie.com", "notes": ""},
    {"name": "Aunt Boujee", "id": "171", "email": "auntboujee@gmail.com", "state": "OH", "psm": "Kendra Waller", "psm_email": "kendra@joinmoxie.com", "notes": ""},
    {"name": "Bay Delta Aesthetics", "id": "1529", "email": "danied32@yahoo.com", "state": "TX", "psm": "Jaqlyn Dreas", "psm_email": "jaqlyn@joinmoxie.com", "notes": ""},
    {"name": "BeautiLab Aesthetics", "id": "1480", "email": "hello@beautilab-aesthetics.com", "state": "VA", "psm": "Ericka Olmos", "psm_email": "ericka@joinmoxie.com", "notes": ""},
    {"name": "Beauty Babes Clinique", "id": "1698", "email": "beautybabesclinique@gmail.com", "state": "OH", "psm": "Sarah Smith", "psm_email": "sarahsmith@joinmoxie.com", "notes": ""},
    {"name": "Beauty Revival Barn", "id": "1908", "email": "cici@beautyrevivalbarn.com", "state": "TX", "psm": "Meredith DeSousa", "psm_email": "meredith.desousa@joinmoxie.com", "notes": ""},
    {"name": "Blue Rose Aesthetics - Canton", "id": "2102", "email": "info@blueroseaesthetics.com", "state": "OH", "psm": "Sarah Smith", "psm_email": "sarahsmith@joinmoxie.com", "notes": ""},
    {"name": "Blue Rose Aesthetics - Dublin", "id": "1530", "email": "info@blueroseaesthetics.com", "state": "OH", "psm": "Sarah Smith", "psm_email": "sarahsmith@joinmoxie.com", "notes": ""},
    {"name": "Brilliant Aesthetics", "id": "95", "email": "kimberlyenochs@yahoo.com", "state": "OH", "psm": "Katie Sensing", "psm_email": "katie@joinmoxie.com", "notes": "Grandfathered: $363 new / $463 returning"},
    {"name": "Centurion Injects + IV Drips", "id": "298", "email": "contact@centurioninjects.com", "state": "TX", "psm": "Kendra Waller", "psm_email": "kendra@joinmoxie.com", "notes": ""},
    {"name": "Coastal Glo Med Spa", "id": "1195", "email": "sammi@coastalglo.com", "state": "TX", "psm": "Sarah Smith", "psm_email": "sarahsmith@joinmoxie.com", "notes": ""},
    {"name": "Diverse Aesthetics", "id": "1224", "email": "info@diverseaesthetics.com", "state": "VA", "psm": "Sarah Smith", "psm_email": "sarahsmith@joinmoxie.com", "notes": ""},
    {"name": "Eden Medspa", "id": "1271", "email": "info@edenmedispa.com", "state": "OH", "psm": "Michelle Garcia", "psm_email": "michellegarcia@joinmoxie.com", "notes": "Grandfathered: $363 new / $463 returning"},
    {"name": "Elisa Grace Medspa & Boutique", "id": "1347", "email": "elisagracemedspa@gmail.com", "state": "TX", "psm": "Kendra Waller", "psm_email": "kendra@joinmoxie.com", "notes": ""},
    {"name": "Ember Aesthetics", "id": "", "email": "emberaes814@gmail.com", "state": "PA", "psm": "", "psm_email": "", "notes": "No PSM — no BCC. Data from Notion tracker."},
    {"name": "Ivy Hydration, Wellness & Aesthetics", "id": "1225", "email": "iloveivyhydration@yahoo.com", "state": "TX", "psm": "Michelle Garcia", "psm_email": "michellegarcia@joinmoxie.com", "notes": ""},
    {"name": "MediFresh Medspa", "id": "1477", "email": "info@medifreshmedspa.com", "state": "PA", "psm": "Growth Success", "psm_email": "growthsuccess@joinmoxie.com", "notes": "BCC: growthsuccess@joinmoxie.com"},
    {"name": "Metamorphosis Aesthetics & Wellness", "id": "1369", "email": "metamorph.aw@gmail.com", "state": "VA", "psm": "Sarah Smith", "psm_email": "sarahsmith@joinmoxie.com", "notes": ""},
    {"name": "Mindful Aesthetics & Wellness", "id": "1312", "email": "mindfulsalem@gmail.com", "state": "OH", "psm": "Sarah Smith", "psm_email": "sarahsmith@joinmoxie.com", "notes": ""},
    {"name": "Olamic Beauty", "id": "1123", "email": "olamicbeauty@olamicbeautyllc.com", "state": "PA", "psm": "Juliana Herrero", "psm_email": "juliana@joinmoxie.com", "notes": ""},
    {"name": "One Nova Med Spa", "id": "1290", "email": "mail@onenovamedspa.com", "state": "OH", "psm": "Katie Sensing", "psm_email": "katie@joinmoxie.com", "notes": ""},
    {"name": "Proyecto Belleza Aesthetics", "id": "1126", "email": "Proyectobelleza22@gmail.com", "state": "PA", "psm": "Chrissie Zimbleman", "psm_email": "chrissie.zimbleman@joinmoxie.com", "notes": ""},
    {"name": "Remedy Aesthetics & Weight Loss", "id": "639", "email": "info@remedyivhydration.com", "state": "TX", "psm": "Michelle Garcia", "psm_email": "michellegarcia@joinmoxie.com", "notes": ""},
    {"name": "Restore & Balance Medical Services", "id": "1156", "email": "restoreandbalance@yahoo.com", "state": "OH", "psm": "Megan Koncek", "psm_email": "megan@joinmoxie.com", "notes": "Grandfathered: $363 new / $463 returning"},
    {"name": "Revive Wellness & Esthetics", "id": "1476", "email": "contact@revivewellnessandesthetics.com", "state": "PA", "psm": "Ericka Olmos", "psm_email": "ericka@joinmoxie.com", "notes": ""},
    {"name": "Sage Aesthetics", "id": "1007", "email": "sageaestheticspa@gmail.com", "state": "MI", "psm": "Marcus Repp", "psm_email": "marcus.repp@joinmoxie.com", "notes": ""},
    {"name": "Savvy Aesthetics", "id": "1308", "email": "savvyaestheticssa@gmail.com", "state": "TX", "psm": "Michelle Garcia", "psm_email": "michellegarcia@joinmoxie.com", "notes": ""},
    {"name": "Sheer Complexions", "id": "1446", "email": "fitnesspep2024@gmail.com", "state": "OH", "psm": "Ericka Olmos", "psm_email": "ericka@joinmoxie.com", "notes": ""},
    {"name": "Shes Got The Look", "id": "1145", "email": "sheila@shesgotthelook.net", "state": "PA", "psm": "Katie Sensing", "psm_email": "katie@joinmoxie.com", "notes": ""},
    {"name": "Urban Chic Aesthetix", "id": "946", "email": "urbanchicaesthetix@gmail.com", "state": "TX", "psm": "Kendra Waller", "psm_email": "kendra@joinmoxie.com", "notes": ""},
    {"name": "Youngblood Aesthetics", "id": "1843", "email": "youngbloodaesthetics@gmail.com", "state": "MI", "psm": "Sarah Smith", "psm_email": "sarahsmith@joinmoxie.com", "notes": ""},
    {"name": "Zion Medspa", "id": "2021", "email": "info@zionmedspa.net", "state": "OH", "psm": "Michelle Garcia", "psm_email": "michellegarcia@joinmoxie.com", "notes": ""},
]


def _load_tox_partners():
    if TOX_CLUB_FILE.exists():
        try:
            with open(TOX_CLUB_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return list(_TOX_CLUB_DEFAULTS)


def _save_tox_partners(partners):
    TOX_CLUB_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(TOX_CLUB_FILE, "w") as f:
        json.dump(partners, f, indent=2)


@app.route("/tox-club")
def tox_club_page():
    return render_template("tox-club.html")


@app.route("/api/tox-club/partners", methods=["GET"])
def api_get_tox_partners():
    return jsonify(_load_tox_partners())


@app.route("/api/tox-club/partners", methods=["POST"])
def api_save_tox_partners():
    partners = request.json
    if not isinstance(partners, list):
        return jsonify({"error": "Expected a list"}), 400
    _save_tox_partners(partners)
    return jsonify({"ok": True, "count": len(partners)})


# Tox Club Gmail token storage
TOX_GMAIL_TOKEN_FILE = Path(_persist_base) / "tox_gmail_tokens.json"

def _load_gmail_tokens() -> dict:
    if TOX_GMAIL_TOKEN_FILE.exists():
        try:
            with open(TOX_GMAIL_TOKEN_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return {}

# Tox Club revenue CSV storage
TOX_CSV_FILE = Path(_persist_base) / "tox_club_revenue.csv"

def _load_tox_csv_data() -> dict:
    """Load parsed CSV data from disk. Returns {} if not uploaded yet."""
    if not TOX_CSV_FILE.exists():
        return {}
    try:
        from src.tox_club_loader import parse_tox_club_revenue_csv
        return parse_tox_club_revenue_csv(TOX_CSV_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_gmail_tokens(tokens: dict):
    TOX_GMAIL_TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(TOX_GMAIL_TOKEN_FILE, "w") as f:
        json.dump(tokens, f)

def _gmail_redirect_uri() -> str:
    base = os.environ.get("APP_BASE_URL", "https://mbr-4hbe.onrender.com")
    return base.rstrip("/") + "/tox-club/gmail-callback"


@app.route("/tox-club/gmail-setup")
def tox_gmail_setup():
    """Show Gmail connection status + OAuth button."""
    tokens = _load_gmail_tokens()
    connected = bool(tokens.get("refresh_token") or os.environ.get("GOOGLE_REFRESH_TOKEN"))
    client_id = os.environ.get("GOOGLE_CLIENT_ID", "")
    return render_template("tox-club-gmail-setup.html",
                           connected=connected,
                           has_client_id=bool(client_id))


@app.route("/tox-club/gmail-auth")
def tox_gmail_auth():
    """Redirect to Google OAuth."""
    try:
        from src.tox_club_email import build_gmail_auth_url
    except ImportError:
        return "Email module not available", 500
    client_id = os.environ.get("GOOGLE_CLIENT_ID", "")
    if not client_id:
        return "GOOGLE_CLIENT_ID environment variable not set", 400
    url = build_gmail_auth_url(client_id, _gmail_redirect_uri())
    return redirect(url)


@app.route("/tox-club/gmail-callback")
def tox_gmail_callback():
    """Handle OAuth callback and store refresh token."""
    code = request.args.get("code")
    error = request.args.get("error")
    if error:
        return f"OAuth error: {error}", 400
    if not code:
        return "No auth code received", 400
    try:
        from src.tox_club_email import exchange_code_for_tokens
        client_id = os.environ.get("GOOGLE_CLIENT_ID", "")
        client_secret = os.environ.get("GOOGLE_CLIENT_SECRET", "")
        tokens = exchange_code_for_tokens(code, client_id, client_secret, _gmail_redirect_uri())
        if "refresh_token" not in tokens:
            return f"No refresh token in response: {tokens}", 400
        _save_gmail_tokens({"refresh_token": tokens["refresh_token"]})
        return redirect("/tox-club?tab=generate&gmail=connected")
    except Exception as e:
        return f"Token exchange failed: {e}", 500


@app.route("/api/tox-club/upload-csv", methods=["POST"])
def api_tox_upload_csv():
    """Upload the Medspa Tox Club Revenue CSV exported from Omni."""
    f = request.files.get("file")
    if not f:
        return jsonify({"error": "No file provided"}), 400
    text = f.read().decode("utf-8")
    try:
        from src.tox_club_loader import parse_tox_club_revenue_csv
        data = parse_tox_club_revenue_csv(text)
        TOX_CSV_FILE.parent.mkdir(parents=True, exist_ok=True)
        TOX_CSV_FILE.write_text(text, encoding="utf-8")
        return jsonify({"ok": True, "medspas_found": len(data),
                        "names": [v["name"] for v in data.values()][:5]})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/tox-club/csv-status")
def api_tox_csv_status():
    if not TOX_CSV_FILE.exists():
        return jsonify({"uploaded": False})
    try:
        from src.tox_club_loader import parse_tox_club_revenue_csv
        data = parse_tox_club_revenue_csv(TOX_CSV_FILE.read_text(encoding="utf-8"))
        return jsonify({"uploaded": True, "medspas": len(data)})
    except Exception:
        return jsonify({"uploaded": False})


@app.route("/api/tox-club/gmail-status")
def api_tox_gmail_status():
    tokens = _load_gmail_tokens()
    connected = bool(tokens.get("refresh_token") or os.environ.get("GOOGLE_REFRESH_TOKEN"))
    return jsonify({"connected": connected})


@app.route("/api/tox-club/generate-preview", methods=["POST"])
def api_tox_generate_preview():
    """Pull Omni data for all partners and return rendered email HTML + stats."""
    body = request.json or {}
    month = int(body.get("month", 1))
    year = int(body.get("year", 2026))
    win_text = body.get("win_text", "")  # optional custom win

    if not OMNI_KEY:
        return jsonify({"error": "OMNI_API_KEY not configured"}), 500

    try:
        from src.tox_club_loader import load_tox_club_stats, get_revenue_from_csv
        from src.tox_club_email import render_email_html, MONTH_NAMES
    except ImportError as e:
        return jsonify({"error": f"Module import failed: {e}"}), 500

    # Load CSV revenue data if uploaded
    csv_data = _load_tox_csv_data()

    partners = _load_tox_partners()
    results = []

    for p in partners:
        name = p.get("name", "")
        medspa_id_str = p.get("id", "")
        if not name:
            continue

        item = {"name": name, "id": medspa_id_str, "email": p.get("email", ""),
                "psm_email": p.get("psm_email", ""), "status": "ok", "html": "", "stats": {}}

        try:
            # Pull appointment stats from Omni
            stats = load_tox_club_stats(name, month, year, OMNI_KEY)

            # Override revenue from CSV if available (more reliable than raw Omni query)
            if csv_data and medspa_id_str:
                try:
                    csv_rev = get_revenue_from_csv(csv_data, int(medspa_id_str), month, year)
                    if csv_rev and csv_rev.get("paid", 0) > 0:
                        stats["revenue"] = csv_rev["paid"]
                        stats["tox_credits"] = csv_rev["credits"]
                        stats["tox_pct"] = csv_rev["pct"]
                        stats["revenue_source"] = "csv"
                except (ValueError, TypeError):
                    pass

            item["stats"] = {k: v for k, v in stats.items() if k != "debug"}

            subject = f"Tox Club: Your {MONTH_NAMES[month]} {year} Highlights ✨"
            html = render_email_html(
                p, stats, month, year,
                win_text=win_text or None
            )
            item["html"] = html
            item["subject"] = subject
        except Exception as e:
            item["status"] = "error"
            item["error"] = str(e)

        results.append(item)

    return jsonify({"ok": True, "month": month, "year": year, "emails": results})


@app.route("/api/tox-club/create-drafts", methods=["POST"])
def api_tox_create_drafts():
    """Create Gmail drafts for each email in the payload."""
    body = request.json or {}
    emails = body.get("emails", [])

    if not emails:
        return jsonify({"error": "No emails provided"}), 400

    # Get Gmail credentials
    tokens = _load_gmail_tokens()
    refresh_token = tokens.get("refresh_token") or os.environ.get("GOOGLE_REFRESH_TOKEN", "")
    client_id = os.environ.get("GOOGLE_CLIENT_ID", "")
    client_secret = os.environ.get("GOOGLE_CLIENT_SECRET", "")

    if not refresh_token:
        return jsonify({"error": "Gmail not connected. Go to /tox-club/gmail-setup first."}), 400
    if not client_id or not client_secret:
        return jsonify({"error": "GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET not set."}), 400

    try:
        from src.tox_club_email import get_gmail_access_token, create_gmail_draft
        access_token = get_gmail_access_token(refresh_token, client_id, client_secret)
    except Exception as e:
        return jsonify({"error": f"Failed to get Gmail access token: {e}"}), 500

    results = []
    for em in emails:
        to = em.get("email", "")
        bcc = em.get("psm_email", "")
        subject = em.get("subject", "Tox Club MBR")
        html = em.get("html", "")
        name = em.get("name", "")

        if not to or not html:
            results.append({"name": name, "status": "skipped", "reason": "missing to/html"})
            continue

        try:
            draft_id = create_gmail_draft(to, bcc, subject, html, access_token)
            results.append({"name": name, "status": "drafted", "draft_id": draft_id})
        except Exception as e:
            results.append({"name": name, "status": "error", "error": str(e)})

    drafted = sum(1 for r in results if r["status"] == "drafted")
    errors = sum(1 for r in results if r["status"] == "error")
    return jsonify({"ok": True, "drafted": drafted, "errors": errors, "results": results})


@app.route("/api/tox-club/discover-fields")
def api_tox_discover_fields():
    """Debug endpoint: discover available Tox Club Omni fields."""
    if not OMNI_KEY:
        return jsonify({"error": "OMNI_API_KEY not configured"}), 500
    try:
        from src.tox_club_loader import discover_tox_club_fields
        return jsonify(discover_tox_club_fields(OMNI_KEY))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--omni-key":
        OMNI_KEY = sys.argv[2]
    elif not OMNI_KEY:
        OMNI_KEY = os.environ.get("OMNI_API_KEY", "")

    print("Starting MBR Web App...")
    print(f"  Omni API key: {'configured' if OMNI_KEY else 'NOT SET (set OMNI_API_KEY)'}")
    print(f"  Open http://localhost:5001 in your browser")
    print()
    port = int(os.environ.get("PORT", 5001))
    app.run(host="0.0.0.0", port=port, debug=(port == 5001))
