#!/usr/bin/env python3
"""MBR Automation CLI — Generate Monthly Business Reviews for Moxie practices."""

import argparse
import os
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(
        prog="mbr",
        description="Generate Monthly Business Review PDFs for Moxie practices"
    )
    sub = parser.add_subparsers(dest="command")

    gen = sub.add_parser("generate", help="Generate an MBR report")
    gen.add_argument("--practice", required=True, help="Practice name")
    gen.add_argument("--month", required=True, type=int, help="Month number (1-12)")
    gen.add_argument("--year", required=True, type=int, help="Year")
    gen.add_argument("--data", help="Path to CSV data file")
    gen.add_argument("--reviews", help="Path to reviews JSON file")
    gen.add_argument("--marketing", help="Path to marketing JSON file")
    gen.add_argument("--brand-bank", help="Path to brand bank image")
    gen.add_argument("--output", default="./output", help="Output directory")
    gen.add_argument("--psm-name", help="PSM name for feedback section")
    gen.add_argument("--moxie-start", help="Moxie start month (e.g. 'October 2025')")
    gen.add_argument("--no-ai", action="store_true", help="Skip AI narrative generation (use rule-based)")
    gen.add_argument("--omni-key", help="Omni API key (or set OMNI_API_KEY env var)")
    gen.add_argument("--html-only", action="store_true", help="Output HTML instead of PDF (for debugging)")
    gen.add_argument("--format", choices=["pdf", "pptx"], default="pdf",
                     help="Output format: pdf (default, HTML-based) or pptx (legacy)")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    if args.command == "generate":
        run_generate(args)


def run_generate(args):
    from src.csv_loader import load_csv
    from src.narrative import generate_narratives

    print(f"Generating MBR for {args.practice} — {args.month}/{args.year}")
    print()

    # ── Step 1: Load data ──
    print("[1/3] Loading data...")
    omni_key = getattr(args, 'omni_key', None) or os.environ.get("OMNI_API_KEY")
    if args.data:
        data = load_csv(args.data, args.practice, args.month, args.year)
        print(f"  Loaded from CSV: {args.data}")
    elif omni_key:
        from src.omni_loader import load_from_omni
        data = load_from_omni(args.practice, args.month, args.year, api_key=omni_key)
        print(f"  Loaded from Omni API")
    else:
        print("  ERROR: No data source specified.")
        print("  Use --data for CSV input, or --omni-key / OMNI_API_KEY for Omni API.")
        sys.exit(1)

    if args.moxie_start:
        data.moxie_start_month = args.moxie_start

    # Load optional reviews
    if args.reviews:
        import json
        with open(args.reviews) as f:
            reviews_data = json.load(f)
        from src.data_schema import ReviewsPlatform
        for r in reviews_data:
            data.reviews.append(ReviewsPlatform(**r))
        print(f"  Loaded reviews from: {args.reviews}")

    # Load optional marketing data
    if args.marketing:
        import json
        with open(args.marketing) as f:
            mkt_data = json.load(f)
        from src.data_schema import MarketingData
        data.marketing = MarketingData(**mkt_data)
        print(f"  Loaded marketing data from: {args.marketing}")

    print(f"  Net Revenue: ${data.monthly_net_revenue:,.2f}")
    print(f"  Appointments: {data.total_appointments}")
    print(f"  Staff: {len(data.staff)}")
    print(f"  Services: {len(data.services)}")
    print()

    # ── Step 2: Generate narratives ──
    print("[2/3] Generating narratives...")
    if args.no_ai:
        os.environ.pop("ANTHROPIC_API_KEY", None)
    generate_narratives(data)
    print(f"  Executive summary: {len(data.executive_summary)} chars")
    print(f"  Assessments: {len(data.assessments)} cards")
    print(f"  PSM feedback: {len(data.psm_feedback)} chars")
    print()

    # ── Step 3: Render output ──
    os.makedirs(args.output, exist_ok=True)
    safe_name = args.practice.replace(" ", "_")
    month_name = data.month_name

    if args.format == "pptx":
        # Legacy PPTX pipeline
        from src.slide_builder import build_mbr
        print("[3/3] Building PPTX slides...")
        pptx_path = os.path.join(args.output, f"{safe_name}_MBR_{month_name}_{args.year}.pptx")
        build_mbr(data, pptx_path, brand_bank_path=args.brand_bank)
        print(f"  PPTX saved: {pptx_path}")
    else:
        # New HTML → PDF pipeline
        from src.html_renderer import render_pdf, render_html_file

        if args.html_only:
            print("[3/3] Rendering HTML...")
            html_path = os.path.join(args.output, f"{safe_name}_MBR_{month_name}_{args.year}.html")
            render_html_file(data, html_path, brand_bank_path=args.brand_bank)
            print(f"  HTML saved: {html_path}")
        else:
            print("[3/3] Rendering PDF...")
            pdf_path = os.path.join(args.output, f"{safe_name}_MBR_{month_name}_{args.year}.pdf")
            render_pdf(data, pdf_path, brand_bank_path=args.brand_bank)
            print(f"  PDF saved: {pdf_path}")

    print()
    print("Done!")


if __name__ == "__main__":
    main()
