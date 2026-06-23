"""Load MBR data from the Vanity Bar CSV format."""

import csv
import re
from pathlib import Path
from .data_schema import MBRData, StaffMember, ServiceItem, ReviewsPlatform


def parse_dollar(val: str) -> float:
    """Parse dollar string like '$74,444.63' to float."""
    if not val or val.strip().upper() == "N/A":
        return 0.0
    cleaned = re.sub(r'[,$]', '', val.strip())
    try:
        return float(cleaned)
    except ValueError:
        return 0.0


def parse_pct(val: str) -> float:
    """Parse percentage string like '81.50%' to decimal like 0.815."""
    if not val or val.strip().upper() == "N/A":
        return 0.0
    cleaned = val.strip().replace('%', '')
    try:
        return float(cleaned) / 100
    except ValueError:
        return 0.0


def parse_int(val: str) -> int:
    if not val or val.strip().upper() == "N/A":
        return 0
    cleaned = re.sub(r'[,]', '', val.strip())
    try:
        return int(float(cleaned))
    except ValueError:
        return 0


def load_csv(path: str, practice_name: str, month: int, year: int) -> MBRData:
    """Load MBR data from the CSV export format."""
    rows = []
    with open(path, 'r', encoding='utf-8-sig') as f:
        reader = csv.reader(f)
        for row in reader:
            rows.append(row)

    data = MBRData(practice_name=practice_name, month=month, year=year)

    # Build a lookup: find rows by their label in column 1
    current_tile = ""
    staff_names = []
    i = 0
    while i < len(rows):
        row = rows[i]
        label = row[1].strip() if len(row) > 1 else ""
        value = row[6].strip() if len(row) > 6 else ""

        # Track current tile
        if label.startswith("Tile "):
            current_tile = label
            i += 1
            continue

        # Tile 1 - Key Metrics
        if label == "Monthly Net Revenue":
            data.monthly_net_revenue = parse_dollar(value)
        elif label == "Total Appointments":
            data.total_appointments = parse_int(value)
        elif label == "AOV":
            data.aov = parse_dollar(value)
        elif label == "Quarter to Date":
            data.quarter_to_date = parse_dollar(value) if value.upper() != "N/A" else None

        # Tile 2 - Gauges
        elif label == "% of net revenue goal":
            data.pct_net_revenue_goal = parse_pct(value)
        elif label == "% of AOV goal":
            data.pct_aov_goal = parse_pct(value)
        elif label == "Utilization Rate":
            data.utilization_rate = parse_pct(value)
        elif label == "Rebooking Rate" and "Tile 2" in current_tile:
            data.rebooking_rate = parse_pct(value)
        elif label.startswith("Retention"):
            data.retention_180d = parse_pct(value)

        # Tile 3 - Memberships
        elif label == "Total Active":
            data.memberships_active = parse_int(value)
        elif label == "Total New":
            data.memberships_new = parse_int(value)
        elif label == "Total Cancelled":
            data.memberships_cancelled = parse_int(value)
        elif label == "MRR":
            data.mrr = parse_dollar(value)

        # Tile 4 - Client Mix
        elif label == "New Clients (#)":
            data.new_clients = parse_int(value)
        elif label == "Existing Clients (#)":
            data.existing_clients = parse_int(value)

        # Tile 6 - Revenue Breakdown
        elif label == "Service Revenue" and "Tile 6" in current_tile:
            data.service_revenue = parse_dollar(value)
        elif label == "Prepayment Revenue":
            data.prepayment_revenue = parse_dollar(value)
        elif label == "Membership Sales":
            data.membership_sales = parse_dollar(value)
        elif label == "Custom Items":
            data.custom_items = parse_dollar(value)
        elif label == "Retail Revenue" and "Tile 6" in current_tile:
            data.retail_revenue = parse_dollar(value)
        elif label == "Total Gross":
            data.total_gross = parse_dollar(value)
        elif label == "Retail to Service Ratio":
            data.retail_to_service_ratio = parse_pct(value)

        # Tile 7 - Adjustments
        elif label == "Discounts":
            data.discounts = parse_dollar(value)
        elif label == "Redemptions":
            data.redemptions = parse_dollar(value)
        elif label == "Client Fees":
            data.client_fees = parse_dollar(value)

        # Tile 9 - Staff Performance
        elif label == "Staff member name":
            # Staff names are in columns 6+
            staff_names = [c.strip() for c in row[6:] if c.strip()]
            # Read subsequent rows for staff data
            staff_data = {name: {} for name in staff_names}
            for j in range(i + 1, min(i + 8, len(rows))):
                srow = rows[j]
                slabel = srow[1].strip() if len(srow) > 1 else ""
                if not slabel or slabel.startswith("Tile"):
                    break
                vals = [c.strip() for c in srow[6:6 + len(staff_names)]]
                for k, name in enumerate(staff_names):
                    if k < len(vals):
                        staff_data[name][slabel] = vals[k]

            for name in staff_names:
                sd = staff_data[name]
                rebook = sd.get("Rebooking Rate", "N/A")
                net_rev = parse_dollar(sd.get("Net Revenue", "0"))
                gross_rev = parse_dollar(sd.get("Gross Revenue", "0")) or net_rev
                data.staff.append(StaffMember(
                    name=name,
                    net_revenue=net_rev,
                    gross_revenue=gross_rev,
                    aov=parse_dollar(sd.get("Avg. Net Revenue Per Invoice", "0")),
                    utilization=parse_pct(sd.get("Utilization", "0")) or None,
                    rebooking_rate=parse_pct(rebook) if rebook.upper() != "N/A" else None,
                    service_revenue=parse_dollar(sd.get("Service Revenue", "0")),
                    retail_revenue=parse_dollar(sd.get("Retail Net Revenue", "0")),
                ))

        # Tile 10 - Services
        elif label == "Top 10 Services":
            # First service on same row
            svc_name = value
            svc_rev_str = row[7].strip() if len(row) > 7 else "0"
            svc_rev = parse_dollar(svc_rev_str)
            if svc_name:
                data.services.append(ServiceItem(name=svc_name, revenue=svc_rev))
            # Read subsequent service rows
            for j in range(i + 1, min(i + 20, len(rows))):
                srow = rows[j]
                sname = srow[6].strip() if len(srow) > 6 else ""
                srev = srow[7].strip() if len(srow) > 7 else ""
                if not sname:
                    break
                data.services.append(ServiceItem(name=sname, revenue=parse_dollar(srev)))

        i += 1

    data.compute_service_percentages()
    return data
