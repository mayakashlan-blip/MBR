"""AI narrative generation for MBR executive summary, assessments, and PSM feedback."""

import json
import os
from .data_schema import MBRData


def _build_metrics_context(data: MBRData) -> str:
    """Build a structured metrics summary for the AI prompt."""
    staff_lines = []
    for s in data.staff:
        staff_lines.append(
            f"  - {s.name}: Net Rev ${s.net_revenue:,.0f}, AOV ${s.aov:,.0f}, "
            f"Util {s.utilization*100:.0f}% " if s.utilization else "" +
            f"Rebook {s.rebooking_rate*100:.1f}%" if s.rebooking_rate else "N/A"
        )

    services_lines = [f"  - {s.name}: ${s.revenue:,.0f} ({s.pct_of_total:.1f}%)"
                      for s in data.services[:10]]

    membership_churn = (data.memberships_cancelled / data.memberships_active * 100
                        if data.memberships_active > 0 else 0)

    context = f"""Practice: {data.practice_name}
Period: {data.month_name} {data.year}

KEY METRICS:
- Monthly Net Revenue: ${data.monthly_net_revenue:,.2f}
- Total Appointments: {data.total_appointments}
- AOV: ${data.aov:,.2f}
- Quarter to Date: {"${:,.2f}".format(data.quarter_to_date) if data.quarter_to_date else "N/A"}

PERFORMANCE GAUGES:
- % of Net Revenue Goal: {data.pct_net_revenue_goal*100:.1f}%
- % of AOV Goal: {data.pct_aov_goal*100:.0f}%
- Utilization Rate: {data.utilization_rate*100:.1f}%
- Rebooking Rate: {data.rebooking_rate*100:.0f}%
- Retention (180D): {data.retention_180d*100:.0f}%

MEMBERSHIPS:
- Active: {data.memberships_active}
- New: {data.memberships_new}
- Cancelled: {data.memberships_cancelled}
- Churn Rate: {membership_churn:.1f}%
- MRR: ${data.mrr:,.0f}

CLIENT MIX:
- New Clients: {data.new_clients} ({data.new_client_pct:.0f}%)
- Existing Clients: {data.existing_clients} ({data.existing_client_pct:.0f}%)

REVENUE BREAKDOWN:
- Service Revenue: ${data.service_revenue:,.0f}
- Prepayment Revenue: ${data.prepayment_revenue:,.0f}
- Membership Sales: ${data.membership_sales:,.0f}
- Retail Revenue: ${data.retail_revenue:,.0f}
- Total Gross: ${data.total_gross:,.0f}
- Retail-to-Service Ratio: {data.retail_to_service_ratio*100:.0f}%
- Discounts: ${data.discounts:,.0f}
- Redemptions: ${data.redemptions:,.0f}

TOP SERVICES:
{chr(10).join(services_lines)}

STAFF PERFORMANCE:
{chr(10).join(staff_lines)}

BENCHMARKS FOR ASSESSMENT:
| Metric | Warning | Opportunity | Strength |
| % of Revenue Goal | <85% | 85-99% | 100%+ |
| Utilization | <40% | 40-59% | 60%+ |
| Rebooking Rate | <40% | 40-59% | 60%+ |
| Retail-to-Service Ratio | <5% | 5-19% | 20%+ |
| Membership Churn | >10% | 5-10% | <5% |
"""

    # Append marketing context if available
    if data.marketing and data.marketing.ad_spend > 0:
        mkt = data.marketing
        cpl = mkt.ad_spend / mkt.leads if mkt.leads > 0 else 0
        ltb = mkt.booked / mkt.leads * 100 if mkt.leads > 0 else 0
        btc = mkt.completed / mkt.booked * 100 if mkt.booked > 0 else 0
        aov = mkt.revenue / mkt.completed if mkt.completed > 0 else 0
        roi = mkt.first_visit_roi or 0

        context += f"""
MARKETING PERFORMANCE:
- Ad Spend: ${mkt.ad_spend:,.0f}
- New Patient Leads: {mkt.leads}
- Cost per Lead: ${cpl:,.0f}
- Appointments Booked: {mkt.booked}
- Lead-to-Booking Rate: {ltb:.1f}% (Goal: 15%)
- Appointments Completed: {mkt.completed}
- Booking-to-Completion Rate: {btc:.1f}%
- New Patient Revenue (attributed to marketing): ${mkt.revenue:,.0f}
- First-Visit AOV: ${aov:,.0f}
- First-Visit ROI: {roi:.1f}x (Goal: 3x)

MARKETING BENCHMARKS:
| Metric | Below Target | On Track | Excellent |
| ROI | <2x | 2-3x | 3x+ |
| Lead-to-Booking Rate | <10% | 10-15% | 15%+ |
| Cost per Lead | >$30 | $15-30 | <$15 |
| First-Visit AOV | <$300 | $300-575 | $575+ |
"""

    return context


def generate_narratives(data: MBRData, api_key: str = None):
    """Generate all AI narrative sections using Claude API.

    If no API key is provided, falls back to rule-based generation.
    """
    api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")

    if api_key:
        return _generate_with_claude(data, api_key)
    else:
        print("  No ANTHROPIC_API_KEY found. Using rule-based narrative generation.")
        return _generate_rule_based(data)


def _generate_with_claude(data: MBRData, api_key: str):
    """Generate narratives using Claude API."""
    try:
        import anthropic
    except ImportError:
        print("  anthropic package not installed. Run: pip3 install anthropic")
        print("  Falling back to rule-based generation.")
        return _generate_rule_based(data)

    client = anthropic.Anthropic(api_key=api_key)
    context = _build_metrics_context(data)

    try:
        return _call_claude_api(client, context, data)
    except Exception as e:
        print(f"  API error: {e}")
        print("  Falling back to rule-based generation.")
        return _generate_rule_based(data)


def _call_claude_api(client, context: str, data: MBRData):
    """Make the actual Claude API calls. Separated for clean error handling."""
    # Generate executive summary
    exec_response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=500,
        messages=[{
            "role": "user",
            "content": f"""You are writing the executive summary for a Monthly Business Review (MBR) for a medspa practice. Write exactly 3-4 sentences analyzing this month's performance.

Call out the biggest win and biggest opportunity with specific numbers.

TONE: Think of yourself as a supportive business coach — celebratory about wins, constructive about gaps. Be encouraging and specific. NEVER use alarming or hyperbolic language like "catastrophic", "immediately", "critical", "alarming", "dire", "urgent", "plummeted", "collapsed", etc. Even when metrics are below target, frame them as opportunities with clear next steps, not emergencies. Use phrases like "room to grow", "the clearest path forward", "a strong foundation to build on".

Do not use bullet points. Do not start with "This month" or "In {data.month_name}".

{context}"""
        }]
    )
    data.executive_summary = exec_response.content[0].text.strip()

    # Generate assessments
    assess_response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=1000,
        messages=[{
            "role": "user",
            "content": f"""You are generating assessment cards for a medspa Monthly Business Review. Based on the metrics and benchmarks below, generate 4-5 assessment cards.

Each card must be a JSON object with:
- "tag": one of "STRENGTH", "OPPORTUNITY", or "WARNING"
- "title": short title (3-5 words)
- "text": 1-2 sentences with specific numbers and an actionable insight

Use the benchmarks to determine tags. Be specific with numbers.

TONE: Supportive business coach. Celebrate wins genuinely. Frame gaps as growth opportunities, not problems. NEVER use alarming language (catastrophic, critical, alarming, plummeted, etc.). Even WARNING cards should be constructive — e.g. "There's meaningful room to grow here" not "This needs immediate attention."

Return ONLY a JSON array, no other text.

{context}"""
        }]
    )

    try:
        assess_text = assess_response.content[0].text.strip()
        # Extract JSON from response
        if "```" in assess_text:
            assess_text = assess_text.split("```")[1]
            if assess_text.startswith("json"):
                assess_text = assess_text[4:]
            assess_text = assess_text.strip()
        data.assessments = json.loads(assess_text)
    except (json.JSONDecodeError, IndexError):
        print(f"  Warning: Could not parse assessments JSON. Using rule-based.")
        _generate_rule_based_assessments(data)

    # Generate PSM feedback
    psm_response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=800,
        messages=[{
            "role": "user",
            "content": f"""You are a Practice Success Manager writing coaching feedback for a medspa owner in their Monthly Business Review.

Write 3-4 paragraphs. Each paragraph should:
1. Lead with a bold insight (use ** for bold)
2. Reference specific numbers from the data
3. Give an actionable recommendation

Tone: experienced, supportive business coach — celebrate what's working, then guide on what to focus on next. Be warm, confident, and specific. NEVER use alarming or hyperbolic language (catastrophic, immediately, critical, alarming, dire, urgent, plummeted, collapsed, etc.). Frame every gap as a growth opportunity with a clear path forward. Think "here's how we can build on this" not "this is a problem."

Each paragraph should be separated by a blank line.

{context}"""
        }]
    )
    data.psm_feedback = psm_response.content[0].text.strip()

    # Generate marketing recommendations if marketing data exists
    if data.marketing and data.marketing.ad_spend > 0:
        _generate_marketing_recommendations(data, client, context)

    return data


def _generate_rule_based(data: MBRData):
    """Fallback: generate narratives from rules without AI."""
    # Executive Summary
    rev_status = "above" if data.pct_net_revenue_goal >= 1.0 else "below"
    rev_pct = data.pct_net_revenue_goal * 100

    biggest_service = data.services[0].name if data.services else "core services"
    biggest_service_pct = data.services[0].pct_of_total if data.services else 0

    data.executive_summary = (
        f"{data.month_name} net revenue came in at ${data.monthly_net_revenue:,.0f}, "
        f"reaching {rev_pct:.1f}% of goal across {data.total_appointments} appointments. "
        f"{biggest_service} drove {biggest_service_pct:.0f}% of service revenue, "
        f"confirming it as the practice's anchor offering. "
        f"The clearest growth lever: utilization sits at {data.utilization_rate*100:.0f}% "
        f"against a 60%+ benchmark, meaning there's significant capacity to fill."
    )

    # Assessments
    _generate_rule_based_assessments(data)

    # PSM Feedback
    membership_churn = (data.memberships_cancelled / data.memberships_active * 100
                        if data.memberships_active > 0 else 0)

    paragraphs = []

    # Revenue paragraph
    if data.pct_net_revenue_goal >= 1.0:
        paragraphs.append(
            f"Hitting {data.pct_net_revenue_goal*100:.0f}% of your revenue goal is a strong result. "
            f"With ${data.monthly_net_revenue:,.0f} in net revenue from {data.total_appointments} appointments, "
            f"your AOV of ${data.aov:,.0f} shows clients are investing in meaningful treatments."
        )
    elif data.pct_net_revenue_goal > 0:
        gap = (1.0 - data.pct_net_revenue_goal) * data.monthly_net_revenue / data.pct_net_revenue_goal
        paragraphs.append(
            f"Revenue landed at {data.pct_net_revenue_goal*100:.1f}% of goal, "
            f"roughly ${gap:,.0f} short of target. With utilization at {data.utilization_rate*100:.0f}%, "
            f"the most direct path to closing this gap is filling more available hours rather than "
            f"raising prices or adding staff."
        )

    # Utilization paragraph
    if 0 < data.utilization_rate < 0.60:
        potential_add = data.monthly_net_revenue * (0.60 / data.utilization_rate - 1)
        paragraphs.append(
            f"Utilization at {data.utilization_rate*100:.0f}% means there are open hours going unfilled. "
            f"Getting to 60% could add roughly ${potential_add:,.0f} in monthly revenue "
            f"with no additional staff cost. Focus on rebooking at checkout and filling cancellation gaps."
        )

    # Retail paragraph
    if data.retail_to_service_ratio < 0.20:
        paragraphs.append(
            f"Your retail-to-service ratio is {data.retail_to_service_ratio*100:.0f}% "
            f"against a 20% benchmark. Even a modest skincare recommendation routine could add "
            f"meaningful margin. Consider a \"top 3 products\" protocol for your highest-volume services."
        )

    # Membership paragraph
    if membership_churn < 5:
        paragraphs.append(
            f"Membership health is solid: {data.memberships_active} active members, "
            f"{data.memberships_new} new adds, and only {data.memberships_cancelled} cancellations "
            f"({membership_churn:.1f}% churn). Your ${data.mrr:,.0f} MRR provides a reliable "
            f"revenue floor each month."
        )

    data.psm_feedback = "\n\n".join(paragraphs)

    # Marketing recommendations (rule-based)
    if data.marketing and data.marketing.ad_spend > 0:
        _generate_rule_based_marketing(data)

    return data


def _generate_rule_based_assessments(data: MBRData):
    """Generate assessment cards based on benchmark rules."""
    assessments = []
    membership_churn = (data.memberships_cancelled / data.memberships_active * 100
                        if data.memberships_active > 0 else 0)

    # Revenue Goal
    if data.pct_net_revenue_goal >= 1.0:
        assessments.append({
            "tag": "STRENGTH",
            "title": "Revenue Goal Achieved",
            "text": f"You hit {data.pct_net_revenue_goal*100:.0f}% of your revenue goal with ${data.monthly_net_revenue:,.0f} in net revenue. Strong execution across the board."
        })
    elif data.pct_net_revenue_goal >= 0.85:
        gap = (1.0 - data.pct_net_revenue_goal) * data.monthly_net_revenue / data.pct_net_revenue_goal if data.pct_net_revenue_goal > 0 else 0
        assessments.append({
            "tag": "OPPORTUNITY",
            "title": "Revenue Gap to Goal",
            "text": f"You came in at {data.pct_net_revenue_goal*100:.1f}% of goal, roughly ${gap:,.0f} short. Focus on booking 3-4 more high-value appointments to close this gap."
        })
    elif data.pct_net_revenue_goal > 0:
        assessments.append({
            "tag": "WARNING",
            "title": "Revenue Below Target",
            "text": f"At {data.pct_net_revenue_goal*100:.1f}% of goal, revenue needs attention. Review pricing, appointment volume, and service mix for quick wins."
        })

    # Utilization
    if data.utilization_rate >= 0.60:
        assessments.append({
            "tag": "STRENGTH",
            "title": "Strong Utilization",
            "text": f"At {data.utilization_rate*100:.0f}%, your providers are well-booked. Focus on maintaining this through proactive rebooking and waitlist management."
        })
    elif data.utilization_rate >= 0.40:
        potential = data.monthly_net_revenue * (0.60 / data.utilization_rate - 1) if data.utilization_rate > 0 else 0
        assessments.append({
            "tag": "OPPORTUNITY",
            "title": "Utilization Opportunity",
            "text": f"Your chairs are filled {data.utilization_rate*100:.0f}% of available hours. Getting to 60%+ could add ~${potential:,.0f} in monthly revenue with no additional staff cost."
        })
    elif data.utilization_rate > 0:
        assessments.append({
            "tag": "WARNING",
            "title": "Low Utilization",
            "text": f"At {data.utilization_rate*100:.0f}%, significant capacity is going unused. This is the #1 lever for revenue growth."
        })

    # Rebooking
    if data.rebooking_rate >= 0.60:
        assessments.append({
            "tag": "STRENGTH",
            "title": "Strong Rebooking Rate",
            "text": f"At {data.rebooking_rate*100:.0f}%, most clients schedule their next visit before leaving. This is the foundation of predictable revenue."
        })
    elif data.rebooking_rate >= 0.40:
        assessments.append({
            "tag": "OPPORTUNITY",
            "title": "Rebooking Room to Grow",
            "text": f"Rebooking at {data.rebooking_rate*100:.0f}% is decent but below the 60% benchmark. Implement checkout rebooking protocols to lift this."
        })
    else:
        assessments.append({
            "tag": "WARNING",
            "title": "Low Rebooking Rate",
            "text": f"At {data.rebooking_rate*100:.0f}%, too many clients leave without scheduling. Train staff to rebook at checkout — this is low-hanging fruit."
        })

    # Retail
    if data.retail_to_service_ratio >= 0.20:
        assessments.append({
            "tag": "STRENGTH",
            "title": "Strong Retail Revenue",
            "text": f"Your {data.retail_to_service_ratio*100:.0f}% retail-to-service ratio meets the benchmark. Keep recommending products aligned to treatments."
        })
    elif data.retail_to_service_ratio >= 0.05:
        assessments.append({
            "tag": "OPPORTUNITY",
            "title": "Retail Revenue Opportunity",
            "text": f"Your retail-to-service ratio is {data.retail_to_service_ratio*100:.0f}% vs. the 20% benchmark. A skincare recommendation routine could add significant margin."
        })
    else:
        assessments.append({
            "tag": "WARNING",
            "title": "Retail Revenue Untapped",
            "text": f"Your retail-to-service ratio is {data.retail_to_service_ratio*100:.0f}% vs. the 20% benchmark. Even modest product recommendations could add $2,000+/month."
        })

    # Membership
    if membership_churn < 5:
        assessments.append({
            "tag": "STRENGTH",
            "title": "Healthy Membership Base",
            "text": f"{data.memberships_active} active members with only {data.memberships_cancelled} cancellations ({membership_churn:.1f}% churn). Your ${data.mrr:,.0f} MRR is a strong recurring base."
        })
    elif membership_churn <= 10:
        assessments.append({
            "tag": "OPPORTUNITY",
            "title": "Membership Churn to Watch",
            "text": f"{data.memberships_cancelled} cancellations against {data.memberships_active} active members ({membership_churn:.1f}% churn). Review cancellation reasons for patterns."
        })
    else:
        assessments.append({
            "tag": "WARNING",
            "title": "High Membership Churn",
            "text": f"{membership_churn:.1f}% membership churn is above the 10% threshold. Conduct exit surveys and review your membership value proposition."
        })

    data.assessments = assessments


def _generate_marketing_recommendations(data: MBRData, client=None, context: str = ""):
    """Generate marketing analysis and recommendations. Uses AI if client provided, else rule-based."""
    if client:
        try:
            mkt_response = client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=1000,
                messages=[{
                    "role": "user",
                    "content": f"""You are a marketing strategist analyzing a medspa's paid advertising performance for their Monthly Business Review.

Write a concise marketing analysis with 3-4 paragraphs. Use **bold** for key phrases.

Structure:
1. **Performance summary** — How is the campaign doing? Reference the ROI, spend, leads, and revenue. Compare to the 3x ROI goal.
2. **Funnel analysis** — Where is the biggest drop-off? (leads→booked, booked→completed). Quantify the gap and what closing it would mean in revenue.
3. **Budget scaling insight** — Based on current efficiency, project what a 25% or 50% budget increase would yield in leads and revenue. Be specific with numbers.
4. **Actionable recommendations** — 2-3 specific things the practice can do to improve. Examples: faster lead follow-up, online booking links, offer optimization, retargeting. Tie each to a projected impact.

TONE: Data-driven but accessible. Like a smart marketing consultant who explains things clearly. Be specific with dollar amounts and percentages. Do NOT use alarming language — frame everything as growth opportunities.

Each paragraph should be separated by a blank line.

{context}"""
                }]
            )
            data.marketing_recommendations = mkt_response.content[0].text.strip()
            return
        except Exception as e:
            print(f"  Warning: Marketing AI generation failed: {e}")

    # Rule-based fallback
    _generate_rule_based_marketing(data)


def _generate_rule_based_marketing(data: MBRData):
    """Generate marketing recommendations from rules."""
    mkt = data.marketing
    if not mkt or mkt.ad_spend <= 0:
        return

    roi = mkt.first_visit_roi or 0
    leads = mkt.leads
    booked = mkt.booked
    completed = mkt.completed
    revenue = mkt.revenue
    spend = mkt.ad_spend
    cpl = spend / leads if leads > 0 else 0
    ltb = booked / leads if leads > 0 else 0
    btc = completed / booked if booked > 0 else 0
    aov = revenue / completed if completed > 0 else 0

    paras = []

    # Performance summary
    if roi >= 3:
        paras.append(
            f"**Campaign ROI of {roi:.1f}x exceeds the 3x target** — a strong result. "
            f"The practice invested ${spend:,.0f} in ad spend and generated ${revenue:,.0f} in new patient revenue "
            f"from {completed} completed first visits. At ${cpl:,.0f} per lead, the acquisition cost is efficient "
            f"and the funnel is converting effectively."
        )
    elif roi >= 2:
        paras.append(
            f"**Campaign ROI of {roi:.1f}x is approaching the 3x goal.** "
            f"From ${spend:,.0f} in ad spend, the practice generated {leads} leads and ${revenue:,.0f} in attributed revenue. "
            f"The foundation is solid — the focus should be on converting more leads to booked appointments "
            f"and maximizing first-visit value to close the gap to 3x."
        )
    else:
        gap_rev = spend * 3 - revenue
        paras.append(
            f"**Campaign ROI of {roi:.1f}x is below the 3x target**, with ${revenue:,.0f} in revenue against ${spend:,.0f} in spend. "
            f"Closing the gap to 3x requires an additional ~${gap_rev:,.0f} in attributed revenue. "
            f"The biggest lever is improving funnel conversion — getting more leads to show up and spend more on their first visit."
        )

    # Funnel analysis
    if leads > 0 and ltb < 0.15:
        target_booked = round(leads * 0.15)
        addl_booked = target_booked - booked
        addl_rev = round(addl_booked * btc * aov) if btc > 0 and aov > 0 else 0
        paras.append(
            f"**The biggest opportunity is lead-to-booking conversion**, currently at {ltb*100:.0f}% vs. the 15% benchmark. "
            f"Of {leads} leads generated, only {booked} booked an appointment. "
            f"Reaching 15% would mean {target_booked} bookings (+{addl_booked}), which at current completion rates "
            f"could add approximately ${addl_rev:,.0f} in new patient revenue. "
            f"Speed of follow-up is critical — leads contacted within 5 minutes are 10x more likely to book."
        )
    elif booked > 0 and btc < 0.70:
        target_completed = round(booked * 0.70)
        addl_completed = target_completed - completed
        addl_rev = round(addl_completed * aov) if aov > 0 else 0
        paras.append(
            f"**The booking-to-completion rate of {btc*100:.0f}% has room to improve.** "
            f"Of {booked} booked appointments, {completed} were completed — meaning {booked - completed} no-showed or cancelled. "
            f"Reducing no-shows to a 70% completion rate could add {addl_completed} more first visits "
            f"and approximately ${addl_rev:,.0f} in revenue. "
            f"Consider appointment reminders, deposit requirements, or same-week confirmation calls."
        )
    elif leads > 0:
        paras.append(
            f"**The marketing funnel is converting well** — {ltb*100:.0f}% of leads book and "
            f"{btc*100:.0f}% of bookings complete. Focus on maintaining this efficiency while scaling volume."
        )

    # Budget scaling
    if roi > 0 and leads > 0:
        proj_50_spend = spend * 1.5
        proj_50_leads = round(leads * 1.5)
        proj_50_rev = round(revenue * 1.5)
        paras.append(
            f"**Scaling opportunity:** At current efficiency (${cpl:,.0f}/lead, {roi:.1f}x ROI), "
            f"increasing budget by 50% to ${proj_50_spend:,.0f}/month could generate approximately "
            f"{proj_50_leads} leads and ${proj_50_rev:,.0f} in new patient revenue. "
            f"A conservative first step would be a 25% increase to ${spend * 1.25:,.0f}, "
            f"yielding an estimated {round(leads * 1.25)} leads and ${round(revenue * 1.25):,.0f} in revenue."
        )

    # AOV insight
    if aov > 0 and aov < 400:
        target_aov = 500
        addl_per_patient = target_aov - aov
        total_addl = round(addl_per_patient * completed)
        paras.append(
            f"**First-visit AOV of ${aov:,.0f} can be improved.** "
            f"If each new patient spent ${target_aov:,.0f} instead of ${aov:,.0f} on their first visit "
            f"(+${addl_per_patient:,.0f} per patient), that's an additional ${total_addl:,.0f} in revenue "
            f"from the same ad spend. Consider curated new-patient packages or bundled treatment offers "
            f"to increase first-visit spend."
        )

    data.marketing_recommendations = "\n\n".join(paras)
