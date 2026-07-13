"""Tox Club email template renderer and Gmail draft creator."""

import calendar
import json
import os
import re
import urllib.request
import urllib.parse
import base64
from typing import Optional

MONTH_NAMES = [
    "", "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December"
]

GRANDFATHERED = {95, 1156, 1271}  # medspa IDs on legacy rates


def _perk_for_revenue(revenue: float) -> str:
    """Return the Moxie Partner Perk line based on revenue tier."""
    if revenue >= 10000:
        return "Complimentary Marketing Consult"
    elif revenue >= 5000:
        return "Priority Support Access"
    else:
        return "Partner Resource Hub Access"


def render_email_html(partner: dict, stats: dict, month: int, year: int,
                      quote: Optional[dict] = None,
                      win_text: Optional[str] = None) -> str:
    """Render the full Tox Club MBR email HTML for one medspa.

    partner: dict with keys name, id, email, state, psm, psm_email, notes
    stats:   dict with keys total, new_members, returning_members, prebook_rate, revenue
    quote:   optional dict with keys text, name (first + last initial)
    win_text: optional custom win text; if None, auto-generates from stats
    """
    month_name = MONTH_NAMES[month]
    next_month = MONTH_NAMES[month % 12 + 1] if month < 12 else "January"
    next_year = year if month < 12 else year + 1

    total = stats.get("total") or 0
    new_m = stats.get("new_members")
    ret_m = stats.get("returning_members")
    prebook = stats.get("prebook_rate")
    revenue = stats.get("revenue") or 0.0

    # Format display values
    total_str = str(total) if total else "0"
    new_str = str(new_m) if new_m is not None else "—"
    ret_str = str(ret_m) if ret_m is not None else "—"
    prebook_str = f"{round(prebook * 100)}%" if prebook is not None else "—"
    rev_str = f"${revenue:,.0f}" if revenue else "$0"
    perk = _perk_for_revenue(revenue)

    # Auto win text
    if not win_text:
        if total == 0:
            win_text = f"We noticed {partner['name']} had a quieter {month_name} for Tox Club. Your PSM is ready to help you re-engage members and drive bookings in {next_month}."
        elif prebook is not None and prebook >= 0.7:
            win_text = f"{prebook_str} of your Tox Club members pre-booked their next visit in {month_name} — that's exceptional retention."
        elif new_m is not None and new_m > 0:
            win_text = f"{new_m} new Tox Club member{'s' if new_m != 1 else ''} joined in {month_name}. Strong acquisition momentum heading into {next_month}."
        else:
            win_text = f"{total} Tox Club appointment{'s' if total != 1 else ''} completed in {month_name}. Keep the momentum going."

    # For next month recommendations
    rec1_title = "Boost Pre-Bookings"
    rec1_text = "At checkout, ask each member to schedule their next Tox Club visit before they leave. Even a 10% lift in pre-bookings compounds over time."
    rec2_title = "Re-engage Lapsed Members"
    rec2_text = "Pull a list of Tox Club members who haven't booked in 60+ days and send a personalized check-in message."

    if prebook is not None and prebook < 0.5:
        rec1_title = "Prioritize Pre-Booking"
        rec1_text = f"Your pre-booking rate was {prebook_str} in {month_name}. Ask every member at checkout to lock in their next Tox Club appointment before leaving."

    if new_m is not None and new_m == 0:
        rec2_title = "Drive New Member Enrollment"
        rec2_text = "No new Tox Club members joined this month. Consider a brief consultation script at the end of relevant services to introduce the program."

    # Quote block (only if quote is provided)
    quote_html = ""
    if quote:
        quote_text = _esc(quote.get("text", ""))
        quote_name = _esc(quote.get("name", ""))
        quote_html = f"""
      <!-- MEMBER QUOTE -->
      <tr><td bgcolor="#FFFEF8" style="background-color:#FFFEF8;padding:0 24px 10px;border:1px solid #e8e3d8;border-top:none;border-bottom:none;">
        <table width="100%" cellpadding="12" cellspacing="0" bgcolor="#f5f3ee" style="background-color:#f5f3ee;border-radius:0 6px 6px 0;border-left:3px solid #C8DAEB;"><tr><td>
          <p style="font-size:8px;letter-spacing:2px;text-transform:uppercase;color:#888888;margin:0 0 6px;font-family:Arial,sans-serif;">&#11088; What a member said</p>
          <p style="font-size:12px;color:#333333;line-height:1.6;font-style:italic;margin:0 0 4px;font-family:Arial,sans-serif;">&ldquo;{quote_text}&rdquo;</p>
          <p style="font-size:10px;color:#888888;margin:0;font-family:Arial,sans-serif;">&mdash; {quote_name}</p>
        </td></tr></table>
      </td></tr>"""

    # Inactive medspa message
    if total == 0:
        body_content = f"""
      <tr><td bgcolor="#FFFEF8" style="background-color:#FFFEF8;padding:0 24px 10px;border:1px solid #e8e3d8;border-top:none;border-bottom:none;">
        <table width="100%" cellpadding="14" cellspacing="0" bgcolor="#f5f3ee" style="background-color:#f5f3ee;border-radius:6px;border-left:3px solid #000000;"><tr><td>
          <p style="font-size:12px;color:#333333;line-height:1.6;margin:0;font-family:Arial,sans-serif;">No Tox Club appointments were recorded for {month_name}. Reach out to your PSM — they&#8217;re ready to help you re-activate members and build momentum heading into {next_month}.</p>
        </td></tr></table>
      </td></tr>"""
    else:
        body_content = f"""
      <!-- KPI CARDS -->
      <tr><td bgcolor="#FFFEF8" style="background-color:#FFFEF8;padding:0 24px 10px;border:1px solid #e8e3d8;border-top:none;border-bottom:none;">
        <table width="100%" cellpadding="0" cellspacing="0"><tr>
          <td width="25%" style="padding-right:6px;vertical-align:top;">
            <table width="100%" cellpadding="0" cellspacing="0" bgcolor="#000000" style="background-color:#000000;border-radius:6px;">
              <tr><td align="center" style="padding:20px 12px;">
                <p style="font-size:8px;letter-spacing:2px;text-transform:uppercase;color:#ffffff;margin:0 0 8px;font-family:Arial,sans-serif;">Total</p>
                <p style="font-size:26px;font-weight:bold;color:#ffffff;margin:0;line-height:1;font-family:Arial,sans-serif;">{total_str}</p>
              </td></tr>
            </table>
          </td>
          <td width="25%" style="padding:0 3px;vertical-align:top;">
            <table width="100%" cellpadding="0" cellspacing="0" bgcolor="#ffffff" style="background-color:#ffffff;border:1px solid #e8e3d8;border-radius:6px;">
              <tr><td align="center" style="padding:20px 12px;">
                <p style="font-size:8px;letter-spacing:2px;text-transform:uppercase;color:#888888;margin:0 0 8px;font-family:Arial,sans-serif;">New</p>
                <p style="font-size:26px;font-weight:bold;color:#000000;margin:0;line-height:1;font-family:Arial,sans-serif;">{new_str}</p>
              </td></tr>
            </table>
          </td>
          <td width="25%" style="padding:0 3px;vertical-align:top;">
            <table width="100%" cellpadding="0" cellspacing="0" bgcolor="#ffffff" style="background-color:#ffffff;border:1px solid #e8e3d8;border-radius:6px;">
              <tr><td align="center" style="padding:20px 12px;">
                <p style="font-size:8px;letter-spacing:2px;text-transform:uppercase;color:#888888;margin:0 0 8px;font-family:Arial,sans-serif;">Returning</p>
                <p style="font-size:26px;font-weight:bold;color:#000000;margin:0;line-height:1;font-family:Arial,sans-serif;">{ret_str}</p>
              </td></tr>
            </table>
          </td>
          <td width="25%" style="padding-left:6px;vertical-align:top;">
            <table width="100%" cellpadding="0" cellspacing="0" bgcolor="#C8DAEB" style="background-color:#C8DAEB;border-radius:6px;">
              <tr><td align="center" style="padding:20px 12px;">
                <p style="font-size:8px;letter-spacing:2px;text-transform:uppercase;color:#454744;margin:0 0 8px;font-family:Arial,sans-serif;">Pre-booked</p>
                <p style="font-size:26px;font-weight:bold;color:#000000;margin:0;line-height:1;font-family:Arial,sans-serif;">{prebook_str}</p>
              </td></tr>
            </table>
          </td>
        </tr></table>
      </td></tr>

      <!-- REVENUE -->
      <tr><td bgcolor="#FFFEF8" style="background-color:#FFFEF8;padding:0 24px 10px;border:1px solid #e8e3d8;border-top:none;border-bottom:none;">
        <table width="100%" cellpadding="14" cellspacing="0" bgcolor="#000000" style="background-color:#000000;border-radius:6px;"><tr>
          <td>
            <p style="font-size:8px;letter-spacing:2px;text-transform:uppercase;color:#ffffff;margin:0 0 3px;font-family:Arial,sans-serif;">Revenue (Paid + Upsell)</p>
            <p style="font-size:20px;font-weight:bold;color:#ffffff;margin:0;line-height:1;font-family:Arial,sans-serif;">{rev_str}+</p>
          </td>
          <td align="right">
            <p style="font-size:8px;letter-spacing:2px;text-transform:uppercase;color:#ffffff;margin:0 0 3px;font-family:Arial,sans-serif;">Earned Moxie Partner Perk</p>
            <p style="font-size:20px;font-weight:bold;color:#C8DAEB;margin:0;line-height:1;font-family:Arial,sans-serif;">{_esc(perk)}</p>
          </td>
        </tr></table>
      </td></tr>
{quote_html}
      <!-- MONTH WIN -->
      <tr><td bgcolor="#FFFEF8" style="background-color:#FFFEF8;padding:0 24px 10px;border:1px solid #e8e3d8;border-top:none;border-bottom:none;">
        <table width="100%" cellpadding="12" cellspacing="0" bgcolor="#C8DAEB" style="background-color:#C8DAEB;border-radius:6px;"><tr><td>
          <p style="font-size:9px;font-weight:bold;letter-spacing:1px;text-transform:uppercase;color:#000000;margin:0 0 4px;font-family:Arial,sans-serif;">&#127775; {month_name} Win</p>
          <p style="font-size:12px;color:#000000;margin:0;line-height:1.6;font-family:Arial,sans-serif;">{_esc(win_text)}</p>
        </td></tr></table>
      </td></tr>

      <!-- FOR NEXT MONTH -->
      <tr><td bgcolor="#FFFEF8" style="background-color:#FFFEF8;padding:0 24px 10px;border:1px solid #e8e3d8;border-top:none;border-bottom:none;">
        <table width="100%" cellpadding="14" cellspacing="0" bgcolor="#EADDC1" style="background-color:#EADDC1;border-radius:6px;"><tr><td>
          <p style="font-size:9px;font-weight:bold;letter-spacing:1px;text-transform:uppercase;color:#5a4a2a;margin:0 0 10px;font-family:Arial,sans-serif;">For {next_month}{' ' + str(next_year) if next_year != year else ''}</p>
          <table width="100%" cellpadding="0" cellspacing="0"><tr>
            <td width="16" valign="top" style="padding-right:8px;"><p style="font-size:13px;color:#000000;margin:0;line-height:1.4;font-family:Arial,sans-serif;">&#10003;</p></td>
            <td><p style="font-size:12px;color:#3a2e0e;margin:0 0 8px;line-height:1.5;font-family:Arial,sans-serif;"><strong>{_esc(rec1_title)}:</strong> {_esc(rec1_text)}</p></td>
          </tr></table>
          <table width="100%" cellpadding="0" cellspacing="0"><tr>
            <td width="16" valign="top" style="padding-right:8px;"><p style="font-size:13px;color:#000000;margin:0;line-height:1.4;font-family:Arial,sans-serif;">&#10003;</p></td>
            <td><p style="font-size:12px;color:#3a2e0e;margin:0;line-height:1.5;font-family:Arial,sans-serif;"><strong>{_esc(rec2_title)}:</strong> {_esc(rec2_text)}</p></td>
          </tr></table>
        </td></tr></table>
      </td></tr>"""

    # Reminders block
    reminders_html = """
      <!-- REMINDERS -->
      <tr><td bgcolor="#FFFEF8" style="background-color:#FFFEF8;padding:0 24px 24px;border:1px solid #e8e3d8;border-top:none;">
        <table width="100%" cellpadding="12" cellspacing="0" bgcolor="#f5f3ee" style="background-color:#f5f3ee;border-radius:0 6px 6px 0;border-left:3px solid #000000;"><tr><td>
          <p style="font-size:9px;font-weight:bold;letter-spacing:1px;text-transform:uppercase;color:#000000;margin:0 0 8px;font-family:Arial,sans-serif;">Reminders &amp; Updates</p>
          <table width="100%" cellpadding="0" cellspacing="0"><tr>
            <td width="16" valign="top" style="padding-right:6px;"><p style="font-size:12px;color:#444444;margin:0;line-height:1.4;font-family:Arial,sans-serif;">&#9672;</p></td>
            <td><p style="font-size:11px;color:#444444;margin:0 0 6px;line-height:1.5;font-family:Arial,sans-serif;">Please review the updated <strong>Medspa Hub Program + Partner Standards</strong> and <strong>Practice Management System / EHR Requirements</strong> on the Tox Club Notion hub.</p></td>
          </tr></table>
          <table width="100%" cellpadding="0" cellspacing="0"><tr>
            <td width="16" valign="top" style="padding-right:6px;"><p style="font-size:12px;color:#444444;margin:0;line-height:1.4;font-family:Arial,sans-serif;">&#9672;</p></td>
            <td><p style="font-size:11px;color:#444444;margin:0;line-height:1.5;font-family:Arial,sans-serif;">Mark all appointments <strong>Completed</strong> before the bi-weekly payout cutoff to avoid any delays in payment.</p></td>
          </tr></table>
        </td></tr></table>
      </td></tr>"""

    return f"""<table width="100%" cellpadding="0" cellspacing="0" bgcolor="#f0ede6">
  <tr><td align="center" style="padding:20px;">
    <table width="600" cellpadding="0" cellspacing="0">

      <!-- HEADER -->
      <tr><td bgcolor="#000000" style="background-color:#000000;padding:36px 28px 24px;text-align:center;border-radius:10px 10px 0 0;">
        <p style="font-size:22px;font-weight:bold;letter-spacing:10px;color:#ffffff;text-transform:uppercase;margin:0 0 12px;font-family:Arial,sans-serif;">TOX CLUB</p>
        <table width="32" cellpadding="0" cellspacing="0" style="margin:0 auto 12px;">
          <tr><td height="1" bgcolor="#C8DAEB" style="background-color:#C8DAEB;font-size:0;line-height:0;">&nbsp;</td></tr>
        </table>
        <p style="font-size:9px;letter-spacing:3px;text-transform:uppercase;color:#ffffff;margin:0;font-family:Arial,sans-serif;">{month_name.upper()} {year} &middot; MONTHLY REVIEW</p>
      </td></tr>

      <!-- PERF HEADER -->
      <tr><td bgcolor="#FFFEF8" style="background-color:#FFFEF8;padding:24px 24px 8px;border:1px solid #e8e3d8;border-top:none;">
        <p style="font-size:13px;font-weight:600;color:#000000;margin:0 0 12px;font-family:Arial,sans-serif;">{month_name} {year} Performance</p>
      </td></tr>
{body_content}
{reminders_html}
      <!-- FOOTER -->
      <tr><td bgcolor="#000000" style="background-color:#000000;padding:14px 24px;border-radius:0 0 10px 10px;">
        <table width="100%" cellpadding="0" cellspacing="0"><tr>
          <td><p style="font-size:11px;color:#ffffff;font-weight:bold;margin:0;font-family:Arial,sans-serif;">Lisa @ Tox Club</p><p style="font-size:10px;color:#ffffff;margin:2px 0 0;font-family:Arial,sans-serif;">(314) 931-4904</p></td>
          <td align="right"><p style="font-size:9px;letter-spacing:2px;text-transform:uppercase;color:#ffffff;margin:0;font-family:Arial,sans-serif;">{month_name.upper()} {year}</p></td>
        </tr></table>
      </td></tr>

    </table>
  </td></tr>
</table>"""


def _esc(s: str) -> str:
    if not s:
        return ""
    return (str(s)
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
            .replace("'", "&#39;"))


# ── Gmail OAuth + Draft Creation ──────────────────────────────────────────────

GMAIL_TOKEN_URL = "https://oauth2.googleapis.com/token"
GMAIL_DRAFT_URL = "https://gmail.googleapis.com/gmail/v1/users/me/drafts"
GMAIL_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GMAIL_SCOPE = "https://www.googleapis.com/auth/gmail.compose"


def get_gmail_access_token(refresh_token: str, client_id: str, client_secret: str) -> str:
    """Exchange a refresh token for a short-lived access token."""
    data = urllib.parse.urlencode({
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    }).encode()
    req = urllib.request.Request(GMAIL_TOKEN_URL, data=data, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    with urllib.request.urlopen(req, timeout=10) as resp:
        result = json.loads(resp.read().decode())
    if "access_token" not in result:
        raise RuntimeError(f"Failed to get access token: {result}")
    return result["access_token"]


def create_gmail_draft(to: str, bcc: str, subject: str, html_body: str,
                       access_token: str) -> str:
    """Create a Gmail draft. Returns the draft ID."""
    # Build RFC 2822 message
    lines = [
        "MIME-Version: 1.0",
        f"To: {to}",
    ]
    if bcc:
        lines.append(f"Bcc: {bcc}")
    lines += [
        f"Subject: {subject}",
        "Content-Type: text/html; charset=utf-8",
        "",
        html_body,
    ]
    raw_message = "\r\n".join(lines)
    encoded = base64.urlsafe_b64encode(raw_message.encode("utf-8")).decode("utf-8")

    payload = json.dumps({"message": {"raw": encoded}}).encode("utf-8")
    req = urllib.request.Request(GMAIL_DRAFT_URL, data=payload, method="POST")
    req.add_header("Authorization", f"Bearer {access_token}")
    req.add_header("Content-Type", "application/json")

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read().decode())
        return result.get("id", "")
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        raise RuntimeError(f"Gmail draft creation failed ({e.code}): {body}")


def build_gmail_auth_url(client_id: str, redirect_uri: str) -> str:
    """Build the Google OAuth authorization URL."""
    params = urllib.parse.urlencode({
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": GMAIL_SCOPE,
        "access_type": "offline",
        "prompt": "consent",
    })
    return f"{GMAIL_AUTH_URL}?{params}"


def exchange_code_for_tokens(code: str, client_id: str, client_secret: str,
                              redirect_uri: str) -> dict:
    """Exchange an auth code for access + refresh tokens."""
    data = urllib.parse.urlencode({
        "code": code,
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
    }).encode()
    req = urllib.request.Request(GMAIL_TOKEN_URL, data=data, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode())
