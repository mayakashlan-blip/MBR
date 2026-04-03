"""Render MBR report as HTML and convert to PDF via Playwright."""

import base64
import math
import re
from pathlib import Path
from jinja2 import Environment, FileSystemLoader
from .data_schema import MBRData
from .validators import validate_and_cap

TEMPLATES_DIR = Path(__file__).parent.parent / "templates"
ASSETS_DIR = Path(__file__).parent.parent / "assets"


def _file_to_data_uri(filepath: str, mime: str = "image/png") -> str:
    """Convert a file to a base64 data URI."""
    data = Path(filepath).read_bytes()
    b64 = base64.b64encode(data).decode()
    return f"data:{mime};base64,{b64}"


def _fmt_dollar(val, show_cents=True, negative=False):
    """Format a number as a dollar string."""
    if val is None:
        return "N/A"
    abs_val = abs(float(val))
    if show_cents:
        formatted = f"${abs_val:,.2f}"
    else:
        formatted = f"${abs_val:,.0f}"
    if negative and float(val) > 0:
        return f"-{formatted}"
    return formatted


def _render_bold(text):
    """Convert **bold** markdown to <strong> tags."""
    return re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', text)


def _gauge_params(pct: float, label: str, mom_pct: float = None) -> dict:
    """Compute SVG gauge parameters."""
    circumference = 2 * math.pi * 40  # r=40
    capped = min(max(pct, 0), 1.0)
    offset = circumference * (1 - capped)
    display = f"{pct * 100:.1f}"

    # Color coding based on benchmarks
    if "Revenue" in label or "AOV" in label:
        if pct >= 1.0:
            css_class = "gauge-strength"
        elif pct >= 0.85:
            css_class = "gauge-opportunity"
        else:
            css_class = "gauge-warning"
    elif "Utilization" in label:
        if pct >= 0.60:
            css_class = "gauge-strength"
        elif pct >= 0.40:
            css_class = "gauge-opportunity"
        else:
            css_class = "gauge-warning"
    elif "Rebooking" in label:
        if pct >= 0.60:
            css_class = "gauge-strength"
        elif pct >= 0.40:
            css_class = "gauge-opportunity"
        else:
            css_class = "gauge-warning"
    elif "Retention" in label or "2nd Visit" in label:
        if pct >= 0.30:
            css_class = "gauge-strength"
        elif pct >= 0.15:
            css_class = "gauge-opportunity"
        else:
            css_class = "gauge-warning"
    else:
        css_class = "gauge-neutral"

    return {
        "label": label,
        "display": display,
        "circ": f"{circumference:.2f}",
        "offset": f"{offset:.2f}",
        "css_class": css_class,
        "mom_pct": mom_pct,
    }


# Static data for launches page
LAUNCHES = [
    {
        "title": "No Provider Preference Online Booking",
        "description": "Let clients book the first available provider for a service, so they can snag the soonest appointment.",
    },
    {
        "title": "Calendar Flow",
        "description": "Build schedules that match how treatments actually run day to day with custom durations and buffers.",
    },
    {
        "title": "Product Presets",
        "description": "Save your most common product formulas right inside each service for smoother clinical days.",
    },
    {
        "title": "Automatic Retail Tax Updates",
        "description": "Retail tax rates now update automatically based on your product type and location.",
    },
]


def render_html(data: MBRData, brand_bank_path: str = None,
                marketing_image_path: str = None,
                launches_image_path: str = None) -> str:
    """Render the full MBR report as an HTML string."""
    data = validate_and_cap(data)

    env = Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=False,
    )

    # Register filters
    env.filters["dollar"] = _fmt_dollar
    env.filters["render_bold"] = _render_bold

    # Load CSS
    css = (TEMPLATES_DIR / "styles" / "main.css").read_text()

    # Build image data URIs
    logo_uri = _file_to_data_uri(ASSETS_DIR / "moxie_logo.png")

    partner_uris = []
    for i in range(3):
        p = ASSETS_DIR / f"partner_{i}.png"
        if p.exists():
            partner_uris.append(_file_to_data_uri(p))

    brand_bank_uri = None
    if brand_bank_path and Path(brand_bank_path).exists():
        suffix = Path(brand_bank_path).suffix.lower()
        mime = "image/jpeg" if suffix in (".jpg", ".jpeg") else "image/png"
        brand_bank_uri = _file_to_data_uri(brand_bank_path, mime)

    marketing_image_uri = None
    if marketing_image_path and Path(marketing_image_path).exists():
        suffix = Path(marketing_image_path).suffix.lower()
        mime = "image/jpeg" if suffix in (".jpg", ".jpeg") else "image/png"
        marketing_image_uri = _file_to_data_uri(marketing_image_path, mime)

    launches_image_uri = None
    if launches_image_path and Path(launches_image_path).exists():
        suffix = Path(launches_image_path).suffix.lower()
        mime = "image/jpeg" if suffix in (".jpg", ".jpeg") else "image/png"
        launches_image_uri = _file_to_data_uri(launches_image_path, mime)

    # Build gauge data
    gauges = [
        _gauge_params(data.pct_net_revenue_goal, "% of Net Revenue Goal"),
        _gauge_params(data.pct_aov_goal, "% of AOV Goal"),
        _gauge_params(data.utilization_rate, "Utilization", data.utilization_mom_pct),
        _gauge_params(data.rebooking_rate, "Rebooking Rate", data.rebooking_mom_pct),
        _gauge_params(data.retention_180d, "Retention (180D)", data.retention_mom_pct),
    ]

    # Build revenue items
    revenue_items = [
        {"label": "Service Revenue", "value": data.service_revenue},
        {"label": "Prepayment Revenue", "value": data.prepayment_revenue},
        {"label": "Membership Sales", "value": data.membership_sales},
        {"label": "Custom Items", "value": data.custom_items},
        {"label": "Retail Revenue", "value": data.retail_revenue},
    ]

    template = env.get_template("report.html.j2")
    return template.render(
        data=data,
        css=css,
        logo_uri=logo_uri,
        partner_uris=partner_uris,
        brand_bank_uri=brand_bank_uri,
        marketing_image_uri=marketing_image_uri,
        launches_image_uri=launches_image_uri,
        gauges=gauges,
        revenue_items=revenue_items,
        launches=LAUNCHES,
    )


def render_pdf(data: MBRData, output_path: str, brand_bank_path: str = None,
               marketing_image_path: str = None, launches_image_path: str = None) -> str:
    """Render the MBR report to PDF via Playwright Chromium."""
    html = render_html(data, brand_bank_path, marketing_image_path, launches_image_path)
    return html_to_pdf(html, output_path)


def html_to_pdf(html: str, output_path: str) -> str:
    """Convert pre-rendered HTML to PDF via Playwright Chromium."""
    import asyncio
    # Ensure clean event loop for Playwright sync API (avoids conflict with Flask async)
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            asyncio.set_event_loop(asyncio.new_event_loop())
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())

    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.set_content(html, wait_until="networkidle")
        page.pdf(
            path=output_path,
            format="Letter",
            print_background=True,
            prefer_css_page_size=True,
            margin={"top": "0", "right": "0", "bottom": "0", "left": "0"},
        )
        browser.close()

    return output_path


def render_html_file(data: MBRData, output_path: str, brand_bank_path: str = None) -> str:
    """Render the MBR report to an HTML file (for debugging)."""
    html = render_html(data, brand_bank_path)
    Path(output_path).write_text(html)
    return output_path
