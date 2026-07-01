#!/usr/bin/env python3
"""Bulk import all MS-Marketing launches PPTX files into monthly assets (Supabase).

Usage:
    python scripts/bulk_import_assets.py [--dry-run]
"""

import re
import sys
import os
import json
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Load .env
env_path = PROJECT_ROOT / ".env"
if env_path.exists():
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, val = line.split("=", 1)
                os.environ.setdefault(key.strip(), val.strip())

DRY_RUN = "--dry-run" in sys.argv

MONTH_NAMES = {
    "january": 1, "february": 2, "march": 3, "april": 4,
    "may": 5, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12,
}
MONTH_LABELS = {v: k.capitalize() for k, v in MONTH_NAMES.items()}

SOURCE_DIR = Path("/Users/ankita/Desktop/MS-Marketing launches")

# Words that indicate a decorative/header text, not a real title
_SKIP_PATTERNS = re.compile(
    r'moxie suite launches|brand bank updates?|^brand bank$|brand bank$|new and exciting|'
    r'^may$|^june$|^july$|^august$|^september$|^october$|^november$|^december$|'
    r'^january$|^february$|^march$|^april$|^20\d\d$',
    re.IGNORECASE
)


def parse_month_year(filename: str):
    name = Path(filename).stem.lower()
    for month_name, month_num in MONTH_NAMES.items():
        m = re.search(rf'\b{month_name}\b.*\b(20\d\d)\b', name)
        if not m:
            m = re.search(rf'\b(20\d\d)\b.*\b{month_name}\b', name)
        if m:
            return month_num, int(m.group(1))
    return None, None


def extract_all_texts(pptx_path: str) -> list:
    """Extract all text from all slides, joining runs within each paragraph.
    Returns list of text-lists per slide (one entry per paragraph)."""
    _NS = 'http://schemas.openxmlformats.org/drawingml/2006/main'
    slides_texts = []
    with zipfile.ZipFile(pptx_path) as z:
        slide_files = sorted(
            [f for f in z.namelist()
             if re.match(r'ppt/slides/slide\d+\.xml', f)],
            key=lambda x: int(re.search(r'slide(\d+)', x).group(1))
        )
        for sf in slide_files:
            xml_data = z.read(sf)
            root = ET.fromstring(xml_data)
            paras = []
            for para in root.iter(f'{{{_NS}}}p'):
                # Join all text runs within the paragraph
                full = "".join(
                    t.text for t in para.iter(f'{{{_NS}}}t')
                    if t.text
                ).strip()
                if full:
                    paras.append(full)
            slides_texts.append(paras)
    return slides_texts


def infer_launch_category(title: str) -> str:
    t = title.lower()
    if any(x in t for x in ['calendar', 'schedule', 'booking', 'appointment']):
        return "Calendar"
    if any(x in t for x in ['billing', 'payment', 'invoice', 'charge']):
        return "Billing"
    if any(x in t for x in ['chart', 'clinical', 'medical', 'treatment plan']):
        return "Clinical"
    if any(x in t for x in ['message', 'sms', 'text', 'notification', 'email']):
        return "Messaging"
    if any(x in t for x in ['product', 'inventory', 'retail']):
        return "Products"
    if any(x in t for x in ['report', 'analytics', 'metric', 'dashboard']):
        return "Reporting"
    if any(x in t for x in ['online', 'client portal', 'website']):
        return "Online Booking"
    if any(x in t for x in ['room', 'provider', 'staff', 'role']):
        return "Practice Mgmt"
    if any(x in t for x in ['google', 'sync', 'integration', 'connect']):
        return "Integrations"
    return "Feature"


def infer_bb_category(title: str) -> str:
    t = title.lower()
    if any(x in t for x in ['social', 'instagram', 'reel', 'carousel', 'post']):
        return "Socials"
    if any(x in t for x in ['print', 'flyer', 'brochure', 'rack card']):
        return "Print"
    if any(x in t for x in ['event', 'holiday', 'seasonal', 'theme']):
        return "Events"
    if any(x in t for x in ['sms', 'opt-in', 'text']):
        return "SMS"
    if any(x in t for x in ['email', 'newsletter']):
        return "Email"
    if any(x in t for x in ['video', 'reel']):
        return "Video"
    if any(x in t for x in ['webinar', 'join us', 'virtual']):
        return "Webinar"
    if any(x in t for x in ['form', 'questionnaire', 'intake']):
        return "Content"
    return "Content"


def is_skip_text(text: str) -> bool:
    return bool(_SKIP_PATTERNS.search(text.strip()))


def extract_launches(pptx_path: str) -> list:
    """Parse MS Launches PPTX: each slide = one feature."""
    slides = extract_all_texts(pptx_path)
    features = []
    for slide_texts in slides:
        # Filter out decorative/header text
        real_texts = [t for t in slide_texts if not is_skip_text(t) and len(t) > 3]
        if not real_texts:
            continue
        title = real_texts[0]
        # Description: look for "What's new:" text or any longer text
        desc = ""
        for t in real_texts[1:]:
            if "what's new" in t.lower() or len(t) > 30:
                desc = t
                break
        if not desc and len(real_texts) > 1:
            desc = " ".join(real_texts[1:])
        features.append({
            "title": title,
            "category": infer_launch_category(title),
            "description": desc,
            "url": "",
        })
    return features


def extract_brand_bank_new(pptx_path: str) -> list:
    """Parse new-format Brand Bank PPTX: single slide with ✨ bullet items.
    Items may be ✨-delimited within a single paragraph element."""
    slides = extract_all_texts(pptx_path)
    items = []
    seen = set()
    for slide_texts in slides:
        for text in slide_texts:
            # Split on ✨ to handle items concatenated in one paragraph
            fragments = re.split(r'✨', text)
            for frag in fragments:
                clean = re.sub(r'^[\*\-•\s]+', '', frag).strip()
                if not clean or is_skip_text(clean) or len(clean) < 4:
                    continue
                if clean in seen:
                    continue
                seen.add(clean)
                items.append({
                    "title": clean,
                    "category": infer_bb_category(clean),
                })
    return items


def extract_brand_bank_old(pptx_path: str) -> list:
    """Parse old-format MBR combined PPTX: multi-slide marketing content."""
    slides = extract_all_texts(pptx_path)
    items = []
    seen = set()
    for slide_texts in slides:
        if not slide_texts:
            continue
        # Use the first meaningful text on each slide as the item title
        for text in slide_texts:
            clean = re.sub(r'^[✨\*\-•🗓️📅\s]+', '', text).strip()
            if not clean or is_skip_text(clean) or len(clean) < 8:
                continue
            if clean in seen:
                continue
            seen.add(clean)
            items.append({
                "title": clean[:120],
                "category": infer_bb_category(clean),
            })
            break  # one item per slide
    return items


def classify_file(pptx_path: Path) -> str:
    name = pptx_path.name.lower()
    if name.startswith("copy of"):
        return "skip"
    if "ms launches" in name or "ms launch" in name:
        return "launches"
    if "brand bank" in name:
        return "brand_bank_new"
    return "brand_bank_old"


def collect_files():
    launches: dict = {}
    brand_bank: dict = {}
    brand_bank_type: dict = {}

    for pptx in sorted(SOURCE_DIR.rglob("*.pptx")):
        asset_type = classify_file(pptx)
        if asset_type == "skip":
            continue
        month, year = parse_month_year(pptx.name)
        if not month:
            continue
        key = (month, year)
        if asset_type == "launches":
            existing = launches.get(key)
            if existing is None or "ms launch" in pptx.name.lower():
                launches[key] = pptx
        else:
            brand_bank[key] = pptx
            brand_bank_type[key] = asset_type

    return launches, brand_bank, brand_bank_type


def get_supabase():
    """Get a supabase client directly (avoids Python 3.10+ syntax in src/db.py)."""
    url = os.environ.get("SUPABASE_URL", "")
    key = os.environ.get("SUPABASE_SERVICE_KEY", "")
    if not url or not key:
        return None
    from supabase import create_client
    return create_client(url, key)


def save_monthly_assets(sb, month: int, year: int, assets: dict):
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


def main():
    print(f"{'[DRY RUN] ' if DRY_RUN else ''}Bulk importing monthly assets\n")

    sb = None
    if not DRY_RUN:
        sb = get_supabase()
        if not sb:
            print("ERROR: Supabase not configured. Set SUPABASE_URL and SUPABASE_SERVICE_KEY in .env")
            sys.exit(1)
        print("Supabase: connected\n")

    launches_files, brand_bank_files, bb_types = collect_files()
    all_months = sorted(set(list(launches_files.keys()) + list(brand_bank_files.keys())))
    print(f"Found {len(all_months)} months to import.\n")

    for (month, year) in all_months:
        month_label = f"{MONTH_LABELS[month]} {year}"
        print(f"── {month_label} ──")

        assets = {"launches": [], "brand_bank_items": [],
                  "launches_file": None, "brand_bank_file": None}

        if (month, year) in launches_files:
            lpath = launches_files[(month, year)]
            print(f"  Launches:   {lpath.name}")
            if not DRY_RUN:
                items = extract_launches(str(lpath))
                print(f"    → {len(items)} features: {[i['title'] for i in items]}")
                assets["launches"] = items
                assets["launches_file"] = lpath.name

        if (month, year) in brand_bank_files:
            bbpath = brand_bank_files[(month, year)]
            bb_kind = bb_types.get((month, year), "brand_bank_old")
            print(f"  Brand Bank: {bbpath.name}")
            if not DRY_RUN:
                if bb_kind == "brand_bank_new":
                    items = extract_brand_bank_new(str(bbpath))
                else:
                    items = extract_brand_bank_old(str(bbpath))
                print(f"    → {len(items)} items: {[i['title'][:40] for i in items[:3]]}...")
                assets["brand_bank_items"] = items
                assets["brand_bank_file"] = bbpath.name

        if not DRY_RUN and sb:
            save_monthly_assets(sb, month, year, assets)
            print(f"  Saved to Supabase.")
        print()

    print(f"Done. {len(all_months)} months imported.")


if __name__ == "__main__":
    main()
