"""Data schema for MBR reports. Normalizes all metrics into a clean internal structure."""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class StaffMember:
    name: str
    net_revenue: float
    aov: float
    utilization: Optional[float]  # percentage as decimal e.g. 0.66
    rebooking_rate: Optional[float]
    service_revenue: float
    retail_revenue: float
    gross_revenue: float = 0.0  # gross revenue (before adjustments)
    hours_worked: Optional[float] = None  # appointment hours for the month
    # MoM changes (per-provider)
    revenue_mom_pct: Optional[float] = None
    aov_mom_pct: Optional[float] = None
    utilization_mom_pct: Optional[float] = None
    rebooking_mom_pct: Optional[float] = None
    rev_per_hour_mom_pct: Optional[float] = None

    @property
    def rev_per_hour(self) -> Optional[float]:
        if self.hours_worked and self.hours_worked > 0:
            return self.gross_revenue / self.hours_worked
        return None

    @property
    def initials(self) -> str:
        parts = self.name.split()
        return "".join(p[0].upper() for p in parts if p)[:2]


@dataclass
class MembershipType:
    name: str
    active: int = 0
    new: int = 0
    churned: int = 0
    mrr: float = 0.0

    @property
    def net_new(self) -> int:
        return self.new - self.churned


@dataclass
class ServiceItem:
    name: str
    revenue: float
    pct_of_total: float = 0.0  # computed after loading


@dataclass
class ReviewsPlatform:
    platform: str  # "Google" or "Facebook"
    new_reviews: Optional[int] = None
    avg_new_rating: Optional[float] = None
    total_reviews: Optional[int] = None
    overall_rating: Optional[float] = None


@dataclass
class MarketingData:
    ad_spend: float = 0.0
    leads: int = 0
    booked: int = 0
    completed: int = 0
    revenue: float = 0.0  # Total New Client Revenue (meta_new_clients_completed_appointment_revenue_sum)
    total_revenue_all_clients: float = 0.0  # All marketing-attributed completed revenue (meta_completed_appointment_revenue_sum)
    first_visit_roi: Optional[float] = None
    lead_to_booking_rate: Optional[float] = None
    first_visit_aov: Optional[float] = None
    campaigns: list[CampaignData] = field(default_factory=list)
    show_campaign_breakdown: bool = False  # toggle in editor; off by default, opt-in
    show_marketing_lock_screen: bool = False  # if true, render the "Unlock Marketing" lock instead of the full section
    next_steps: list[str] = field(default_factory=list)


@dataclass
class CampaignData:
    """Per-campaign marketing breakdown."""
    campaign_name: str
    ad_spend: float = 0.0
    leads: int = 0
    booked: int = 0
    completed: int = 0
    revenue: float = 0.0

    @property
    def roi(self) -> float:
        return self.revenue / self.ad_spend if self.ad_spend > 0 else 0

    @property
    def cpl(self) -> float:
        return self.ad_spend / self.leads if self.leads > 0 else 0

    @property
    def lead_to_booking_rate(self) -> float:
        return self.booked / self.leads if self.leads > 0 else 0


@dataclass
class MarketingMetric:
    label: str
    value: str  # formatted display value (e.g. "$2,500", "150", "3.2x")
    subtitle: str = ""  # e.g. "Monthly Budget", "New Patient Leads"


@dataclass
class MarketingKPI:
    label: str  # e.g. "First-visit ROI"
    value: str  # e.g. "2.36x"
    goal: str = ""  # e.g. "Goal: 3x"
    status: str = ""  # "On Track", "Below Target", "Above Target"


@dataclass
class MarketingNextStep:
    title: str  # e.g. "Pivot to Facial Rejuvenation offer"
    description: str = ""  # e.g. "Current campaign is showing fatigue..."


@dataclass
class MarketingAnalysis:
    funnel: list[MarketingMetric] = field(default_factory=list)  # Ad Spend → Leads → Booked → Completed → Revenue
    kpis: list[MarketingKPI] = field(default_factory=list)  # ROI, Lead→Booking Rate, etc. with target status
    roi_headline: str = ""  # e.g. "For every $1 you spend, you generate $2.36"
    summary: str = ""  # performance summary
    next_steps: list[MarketingNextStep] = field(default_factory=list)  # actionable recommendations with descriptions
    # Legacy fields for backward compat
    metrics: list[MarketingMetric] = field(default_factory=list)


@dataclass
class LaunchFeature:
    title: str
    category: str = ""  # e.g. "Calendar Flow", "Product Presets"
    description: str = ""
    url: str = ""  # hyperlink to feature page/article


@dataclass
class BrandBankItem:
    title: str
    category: str = ""  # e.g. "Socials", "Print", "Events"


@dataclass
class MBRData:
    # Practice info
    practice_name: str
    month: int
    year: int
    moxie_start_month: str = ""  # e.g. "October 2025"
    tier: str = ""  # provider_segment_post_launch from Omni: Momentum, Growth, Silver, Gold, Enterprise

    # Tile 1 - Key Metrics
    monthly_net_revenue: float = 0.0
    total_appointments: int = 0
    aov: float = 0.0
    quarter_to_date: Optional[float] = None

    # MoM changes (optional)
    revenue_mom_pct: Optional[float] = None
    appointments_mom_pct: Optional[float] = None
    aov_mom_pct: Optional[float] = None

    # Tile 2 - Gauges
    pct_net_revenue_goal: float = 0.0
    pct_aov_goal: float = 0.0
    utilization_rate: float = 0.0
    rebooking_rate: float = 0.0
    retention_180d: float = 0.0

    # MoM changes for gauges
    utilization_mom_pct: Optional[float] = None
    rebooking_mom_pct: Optional[float] = None
    retention_mom_pct: Optional[float] = None
    new_to_second_visit_mom_pct: Optional[float] = None

    # Additional metrics
    avg_patient_ltv: Optional[float] = None
    new_to_second_visit_rate: Optional[float] = None  # decimal e.g. 0.45 = 45%

    # Tile 3 - Memberships
    memberships_active: int = 0
    memberships_new: int = 0
    memberships_cancelled: int = 0
    mrr: float = 0.0
    membership_types: list[MembershipType] = field(default_factory=list)

    # Tile 4 - Client Mix
    new_clients: int = 0
    existing_clients: int = 0

    # Tile 5 - Reviews
    reviews: list[ReviewsPlatform] = field(default_factory=list)

    # Tile 6 - Revenue Breakdown
    service_revenue: float = 0.0
    prepayment_revenue: float = 0.0
    membership_sales: float = 0.0
    custom_items: float = 0.0
    retail_revenue: float = 0.0
    total_gross: float = 0.0
    retail_to_service_ratio: float = 0.0

    # Tile 7 - Adjustments
    discounts: float = 0.0
    redemptions: float = 0.0
    client_fees: float = 0.0

    # Tile 9 - Staff
    staff: list[StaffMember] = field(default_factory=list)

    # Tile 10 - Services
    services: list[ServiceItem] = field(default_factory=list)

    # Tile 11 - Marketing
    marketing: Optional[MarketingData] = None

    # Tile 12 - Supplies Savings
    supplies_total_savings: float = 0.0
    supplies_by_brand: list[dict] = field(default_factory=list)  # [{brand, savings}]
    supplies_spend_month: float = 0.0
    supplies_savings_month: float = 0.0
    supplies_spend_3mo: float = 0.0
    supplies_savings_3mo: float = 0.0
    supplies_spend_ytd: float = 0.0
    supplies_savings_ytd: float = 0.0
    supplies_spend_all: float = 0.0
    supplies_savings_all: float = 0.0
    supplies_by_vendor_3mo: list[dict] = field(default_factory=list)  # [{vendor, spend, savings}]

    # AI-generated content (filled by narrative engine)
    executive_summary: str = ""
    show_executive_summary: bool = True  # Hide-able for Silver/Momentum/Growth tiers
    assessments: list[dict] = field(default_factory=list)  # [{tag, title, text}]
    psm_feedback: str = ""
    psm_name: str = ""
    marketing_recommendations: str = ""  # AI-generated from uploaded marketing screenshot
    show_marketing_recommendations: bool = False  # toggle for Silver/Momentum/Growth tiers; default off
    marketing_analysis: Optional[MarketingAnalysis] = None  # structured AI analysis
    launches: list[LaunchFeature] = field(default_factory=list)  # extracted from uploaded image
    brand_bank_items: list[BrandBankItem] = field(default_factory=list)  # extracted from uploaded image

    @property
    def month_name(self) -> str:
        import calendar
        return calendar.month_name[self.month]

    @property
    def total_clients(self) -> int:
        return self.new_clients + self.existing_clients

    @property
    def new_client_pct(self) -> float:
        if self.total_clients == 0:
            return 0
        return self.new_clients / self.total_clients * 100

    @property
    def existing_client_pct(self) -> float:
        if self.total_clients == 0:
            return 0
        return self.existing_clients / self.total_clients * 100

    @property
    def revenue_per_client(self) -> float:
        if self.total_clients == 0:
            return 0
        return self.monthly_net_revenue / self.total_clients

    @property
    def membership_conversion_rate(self) -> float:
        """Active members as % of total clients."""
        if self.total_clients == 0:
            return 0
        return self.memberships_active / self.total_clients * 100

    @property
    def net_new_members(self) -> int:
        return self.memberships_new - self.memberships_cancelled

    def compute_service_percentages(self):
        total = sum(s.revenue for s in self.services)
        if total > 0:
            for s in self.services:
                s.pct_of_total = s.revenue / total * 100
