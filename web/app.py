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


def _monthly_key(month: int, year: int) -> str:
    return f"{year}-{month:02d}"


def _load_monthly_assets(month: int, year: int) -> dict:
    """Load monthly assets (launches, brand_bank_items) for a given month."""
    path = MONTHLY_DIR / f"{_monthly_key(month, year)}.json"
    if not path.exists():
        return {"launches": [], "brand_bank_items": []}
    with open(path) as f:
        return json.load(f)


def _save_monthly_assets(month: int, year: int, assets: dict):
    """Save monthly assets to disk."""
    path = MONTHLY_DIR / f"{_monthly_key(month, year)}.json"
    with open(path, "w") as f:
        json.dump(assets, f, default=str)


def _save_monthly_upload(month: int, year: int, asset_type: str, src_path: str, original_filename: str) -> Path:
    """Copy an uploaded file to persistent monthly storage. Returns the persistent path."""
    suffix = Path(original_filename).suffix.lower() or Path(src_path).suffix.lower() or ".png"
    dest = MONTHLY_DIR / f"{_monthly_key(month, year)}_{asset_type}{suffix}"
    shutil.copy2(src_path, dest)
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
    """Copy current session file to a timestamped version."""
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
    """Persist a session to disk, keeping version history."""
    path = SESSIONS_DIR / f"{session_id}.json"

    # Snapshot current version before overwriting
    if snapshot and path.exists():
        _snapshot_version(session_id, path)

    payload = {
        "data": _serialize_data(sess["data"]),
        "brand_bank_path": sess.get("brand_bank_path"),
        "marketing_image_path": sess.get("marketing_image_path"),
        "launches_image_path": sess.get("launches_image_path"),
        "created": sess["created"].isoformat(),
    }
    with open(path, "w") as f:
        json.dump(payload, f, default=str)


def _load_session(session_id: str) -> dict:
    """Load a session from disk, or return None."""
    path = SESSIONS_DIR / f"{session_id}.json"
    if not path.exists():
        return None
    with open(path) as f:
        payload = json.load(f)
    data = _deserialize_data(payload["data"])
    from src.html_renderer import render_html
    html = render_html(data,
                       brand_bank_path=payload.get("brand_bank_path"),
                       marketing_image_path=payload.get("marketing_image_path"),
                       launches_image_path=payload.get("launches_image_path"))
    return {
        "data": data,
        "html": html,
        "brand_bank_path": payload.get("brand_bank_path"),
        "marketing_image_path": payload.get("marketing_image_path"),
        "launches_image_path": payload.get("launches_image_path"),
        "created": datetime.fromisoformat(payload["created"]),
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
        "discounts", "redemptions", "client_fees",
        "supplies_total_savings",
    ]
    for field in numeric_fields:
        if field in payload and payload[field] is not None:
            try:
                val = float(payload[field]) if "." in str(payload[field]) else int(payload[field])
                setattr(data, field, val)
            except (ValueError, TypeError):
                pass

    # Marketing data
    if "marketing" in payload:
        if payload["marketing"]:
            from src.data_schema import MarketingData
            data.marketing = MarketingData(**payload["marketing"])
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
        return redirect(url_for("dashboard"))
    return render_template("editor.html",
                           session_id=session_id,
                           data=sess["data"])


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
    for f in sorted(MONTHLY_DIR.glob("*.json"), reverse=True):
        key = f.stem  # e.g. "2026-03"
        parts = key.split("-")
        if len(parts) != 2:
            continue
        year, month = int(parts[0]), int(parts[1])
        assets = _load_monthly_assets(month, year)
        launches = assets.get("launches", [])
        bb_items = assets.get("brand_bank_items", [])
        if not launches and not bb_items:
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
        # Remove persisted file
        for ext in (".pdf", ".png", ".jpg", ".jpeg"):
            p = MONTHLY_DIR / f"{key}_launches{ext}"
            if p.exists():
                p.unlink()

    if delete_type in ("brand_bank", "all"):
        assets["brand_bank_items"] = []
        assets.pop("brand_bank_file", None)
        assets.pop("brand_bank_path", None)
        for ext in (".pdf", ".png", ".jpg", ".jpeg"):
            p = MONTHLY_DIR / f"{key}_brand_bank{ext}"
            if p.exists():
                p.unlink()

    # If everything is empty, remove the JSON file too
    if not assets.get("launches") and not assets.get("brand_bank_items"):
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


@app.route("/api/generate", methods=["POST"])
def api_generate():
    """Generate a report for a practice."""
    _cleanup_old_sessions()

    practice = request.json.get("practice", "").strip()
    month = int(request.json.get("month", 1))
    year = int(request.json.get("year", 2026))

    if not practice:
        return jsonify({"error": "Practice name required"}), 400

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

        # Store in session — keyed by practice+month for persistent archive
        session_id = _practice_key(practice, month, year)
        sessions[session_id] = {
            "data": data,
            "html": html,
            "brand_bank_path": None,
            "marketing_image_path": None,
            "launches_image_path": None,
            "created": datetime.now(),
        }
        _save_session(session_id, sessions[session_id])

        return jsonify({"session_id": session_id, "ok": True})

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
    if ".." in filename or "/" in filename:
        return jsonify({"error": "Invalid filename"}), 400

    versions_dir = SESSIONS_DIR / f"{session_id}_versions"
    version_path = versions_dir / filename
    if not version_path.exists():
        return jsonify({"error": "Version not found"}), 404

    # Snapshot current state before restoring (so restore is undoable)
    current_path = SESSIONS_DIR / f"{session_id}.json"
    if current_path.exists():
        _snapshot_version(session_id, current_path)

    # Load version and replace current session
    with open(version_path) as f:
        payload = json.load(f)
    data = _deserialize_data(payload["data"])
    sess["data"] = data
    _rerender(sess)
    _save_session(session_id, sess, snapshot=False)  # don't double-snapshot
    sessions[session_id] = sess

    return jsonify({"ok": True})


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
        model="claude-sonnet-4-20250514",
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
        model="claude-sonnet-4-20250514",
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
        model="claude-sonnet-4-20250514",
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
