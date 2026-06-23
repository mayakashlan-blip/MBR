"""Validate and cap implausible MBR data values."""

import sys
from .data_schema import MBRData


def validate_and_cap(data: MBRData) -> MBRData:
    """Cap implausible values and warn on corrections."""

    def _cap(field: str, lo: float, hi: float):
        val = getattr(data, field)
        if val < lo:
            print(f"  Data validation: {field} ({val}) capped to {lo}", file=sys.stderr)
            setattr(data, field, lo)
        elif val > hi:
            print(f"  Data validation: {field} ({val}) capped to {hi}", file=sys.stderr)
            setattr(data, field, hi)

    # Rates stored as decimals (0.0 - 1.0), allow up to 2.0 for revenue goal
    _cap("pct_net_revenue_goal", 0.0, 2.0)
    _cap("pct_aov_goal", 0.0, 2.0)
    _cap("utilization_rate", 0.0, 1.0)
    _cap("rebooking_rate", 0.0, 1.0)
    _cap("retention_180d", 0.0, 1.0)
    _cap("retail_to_service_ratio", 0.0, 2.0)

    # Non-negative counts
    for field in ["total_appointments", "new_clients", "existing_clients",
                  "memberships_active", "memberships_new", "memberships_cancelled"]:
        val = getattr(data, field)
        if val < 0:
            setattr(data, field, 0)

    # Non-negative financials
    for field in ["monthly_net_revenue", "aov", "service_revenue", "retail_revenue",
                  "total_gross", "mrr"]:
        val = getattr(data, field)
        if val < 0:
            setattr(data, field, 0)

    # Cap staff rebooking rates
    for s in data.staff:
        if s.rebooking_rate and s.rebooking_rate > 1.0:
            s.rebooking_rate = 1.0
        if s.utilization and s.utilization > 1.0:
            s.utilization = 1.0

    return data
