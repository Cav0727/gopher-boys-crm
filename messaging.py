"""SMS / email draft helpers for Gopher Boys CRM.

Customer/driver messages: never auto-send. Live send only when env credentials
are set AND the user clicks an explicit Send button in the UI.

Exception: owner alert on new public /request submissions — automatic to Chris.
Primary: email when RESEND_API_KEY or SMTP_* is set (ALERT_EMAIL).
Fallback: SMS when Twilio is configured (ALERT_PHONE / CONTACT_TEL).
Never auto-emails customers.
"""

from __future__ import annotations

import os
import smtplib
import urllib.parse
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any

CONTACT_NAME = os.environ.get("CONTACT_NAME") or "Chris Vincent"
CONTACT_PHONE = os.environ.get("CONTACT_PHONE") or "(912) 244-0464"
CONTACT_TEL = os.environ.get("CONTACT_TEL") or "+19122440464"
ALERT_PHONE = os.environ.get("ALERT_PHONE") or CONTACT_TEL
ALERT_EMAIL = (
    os.environ.get("ALERT_EMAIL") or "chris@ridgewayroofingco.com"
).strip()
SERVICE_AREA = "Pooler, GA 31322"
BUSINESS_NAME = "Gopher Boys"


def twilio_configured() -> bool:
    return bool(
        os.environ.get("TWILIO_ACCOUNT_SID")
        and os.environ.get("TWILIO_AUTH_TOKEN")
        and os.environ.get("TWILIO_FROM")
    )


def email_configured() -> bool:
    if os.environ.get("RESEND_API_KEY"):
        return True
    return bool(
        os.environ.get("SMTP_HOST")
        and os.environ.get("SMTP_USER")
        and os.environ.get("SMTP_PASSWORD")
        and os.environ.get("SMTP_FROM")
    )


def default_sms_body(contractor_name: str | None = None, job_ref: str | None = None) -> str:
    who = contractor_name or "there"
    job_bit = f" about job {job_ref}" if job_ref else ""
    return (
        f"Hi {who}, this is {CONTACT_NAME} with {BUSINESS_NAME}{job_bit}. "
        f"Please call/text {CONTACT_PHONE} to confirm pickup/delivery. "
        f"Thanks! — {BUSINESS_NAME}, {SERVICE_AREA}"
    )


def build_owner_alert_sms(
    *,
    company: str | None,
    contact: str | None,
    phone: str | None,
    supplier: str | None,
    jobsite: str | None,
    city: str | None = None,
    miles: float | None = None,
    quote_amount: float | None = None,
    job_id: int | None = None,
) -> str:
    """Short owner-alert SMS for a new public pickup request."""
    who_bits = [b for b in (company, contact) if b]
    who = " / ".join(who_bits) if who_bits else "Unknown"
    site = jobsite or "—"
    if city:
        site = f"{site}, {city}"
    miles_s = f"{miles:.1f} mi" if miles is not None else "—"
    quote_s = f"${quote_amount:.2f}" if quote_amount is not None else "TBD"
    job_bit = f"#{job_id} " if job_id else ""
    lines = [
        f"Gopher Boys — new pickup request {job_bit}".rstrip(),
        f"{who}",
        f"Phone: {phone or '—'}",
        f"Supplier: {supplier or '—'}",
        f"Jobsite: {site}",
        f"Miles/quote: {miles_s} / {quote_s}",
    ]
    return "\n".join(lines)


def build_owner_alert_email(
    *,
    company: str | None,
    contact: str | None,
    phone: str | None,
    supplier: str | None,
    jobsite: str | None,
    email: str | None = None,
    city: str | None = None,
    miles: float | None = None,
    quote_amount: float | None = None,
    job_id: int | None = None,
    materials: str | None = None,
) -> tuple[str, str, str]:
    """Return (subject, plain, html) for owner alert email on new /request."""
    who_bits = [b for b in (company, contact) if b]
    who = " / ".join(who_bits) if who_bits else "Unknown"
    site = jobsite or "—"
    if city:
        site = f"{site}, {city}"
    miles_s = f"{miles:.1f} mi" if miles is not None else "—"
    quote_s = f"${quote_amount:.2f}" if quote_amount is not None else "TBD"
    job_bit = f"#{job_id}" if job_id else "new"
    subject = f"Gopher Boys — new pickup request {job_bit}"

    lines = [
        f"{BUSINESS_NAME} — new web pickup request",
        "",
        f"Job: {job_bit}",
        f"Company / contact: {who}",
        f"Phone: {phone or '—'}",
        f"Email: {email or '—'}",
        f"Supplier: {supplier or '—'}",
        f"Jobsite: {site}",
        f"Materials: {materials or '—'}",
        f"Miles / quote: {miles_s} / {quote_s}",
        "",
        "Open the CRM Jobs → Request filter to review and confirm before dispatch.",
        "This alert goes only to the owner — the customer was not emailed or texted.",
        "",
        f"— {BUSINESS_NAME} CRM",
        SERVICE_AREA,
    ]
    plain = "\n".join(lines)

    html = f"""<!DOCTYPE html>
<html><body style="font-family:Segoe UI,Arial,sans-serif;color:#1a2332;line-height:1.5">
  <div style="max-width:560px;margin:0 auto;border:1px solid #d5dde8;border-radius:12px;overflow:hidden">
    <div style="background:#0b1f3a;color:#fff;padding:1.25rem 1.5rem">
      <h1 style="margin:0;font-size:1.35rem">{BUSINESS_NAME}</h1>
      <p style="margin:0.35rem 0 0;opacity:0.85">New web pickup request</p>
    </div>
    <div style="padding:1.5rem">
      <p><strong>Job:</strong> {job_bit}</p>
      <p>
        <strong>Company / contact:</strong> {who}<br>
        <strong>Phone:</strong> {phone or '—'}<br>
        <strong>Email:</strong> {email or '—'}<br>
        <strong>Supplier:</strong> {supplier or '—'}<br>
        <strong>Jobsite:</strong> {site}<br>
        <strong>Materials:</strong> {materials or '—'}<br>
        <strong>Miles / quote:</strong> {miles_s} / {quote_s}
      </p>
      <p style="color:#5a6a7e;font-size:0.9rem">
        Review in CRM under Jobs → Request. Customer was not emailed or texted.
      </p>
    </div>
  </div>
</body></html>"""
    return subject, plain, html


def build_invoice(job: Any, contractor: Any) -> tuple[str, str, str]:
    """Return (subject, plain_text, html) for a job invoice email."""
    company = contractor["company_name"] if contractor else "Customer"
    contact = (contractor["contact_name"] if contractor else "") or ""
    quote = float(job["quote_amount"] or 0) if job["quote_amount"] is not None else None
    wait = float(job["wait_fee"] or 0)
    total = (quote + wait) if quote is not None else None
    address = job["job_address"] or "—"
    if job["city"]:
        address = f"{address}, {job['city']}"
    order_ref = job["order_ref"] or f"JOB-{job['id']}"
    miles = f"{job['miles']:.1f} mi" if job["miles"] is not None else "—"

    subject = f"Invoice from {BUSINESS_NAME} — {order_ref}"

    lines = [
        f"{BUSINESS_NAME} — Invoice",
        f"Based in {SERVICE_AREA}",
        f"Contact: {CONTACT_NAME} · {CONTACT_PHONE}",
        "",
        f"Bill to: {company}" + (f" ({contact})" if contact else ""),
        f"Job #: {job['id']}",
        f"Order / Ref: {order_ref}",
        f"Jobsite: {address}",
        f"Miles: {miles}",
        f"Supplier: {job['supplier'] or '—'}",
        f"Status: {job['status']}",
        "",
        f"Delivery quote: {'$' + f'{quote:.2f}' if quote is not None else 'TBD'}",
        f"Wait fee: ${wait:.2f}",
        f"TOTAL DUE: {'$' + f'{total:.2f}' if total is not None else 'TBD'}",
        "",
        "Price & delivery timing confirmed before dispatch.",
        f"Questions? Call {CONTACT_NAME} at {CONTACT_PHONE}.",
        "",
        f"— {BUSINESS_NAME}",
        SERVICE_AREA,
    ]
    plain = "\n".join(lines)

    quote_cell = f"${quote:.2f}" if quote is not None else "TBD"
    total_cell = f"${total:.2f}" if total is not None else "TBD"
    html = f"""<!DOCTYPE html>
<html><body style="font-family:Segoe UI,Arial,sans-serif;color:#1a2332;line-height:1.5">
  <div style="max-width:560px;margin:0 auto;border:1px solid #d5dde8;border-radius:12px;overflow:hidden">
    <div style="background:#0b1f3a;color:#fff;padding:1.25rem 1.5rem">
      <h1 style="margin:0;font-size:1.35rem">{BUSINESS_NAME}</h1>
      <p style="margin:0.35rem 0 0;opacity:0.85">WE PICK UP. YOU KEEP BUILDING.</p>
    </div>
    <div style="padding:1.5rem">
      <p><strong>Invoice</strong> · {order_ref}</p>
      <p>
        <strong>Bill to:</strong> {company}{" · " + contact if contact else ""}<br>
        <strong>Jobsite:</strong> {address}<br>
        <strong>Miles:</strong> {miles}<br>
        <strong>Supplier:</strong> {job['supplier'] or '—'}
      </p>
      <table style="width:100%;border-collapse:collapse;margin:1rem 0">
        <tr><td style="padding:0.5rem;border-bottom:1px solid #d5dde8">Delivery quote</td>
            <td style="padding:0.5rem;border-bottom:1px solid #d5dde8;text-align:right">{quote_cell}</td></tr>
        <tr><td style="padding:0.5rem;border-bottom:1px solid #d5dde8">Wait fee</td>
            <td style="padding:0.5rem;border-bottom:1px solid #d5dde8;text-align:right">${wait:.2f}</td></tr>
        <tr><td style="padding:0.75rem 0.5rem;font-weight:800">TOTAL DUE</td>
            <td style="padding:0.75rem 0.5rem;text-align:right;font-weight:800;font-size:1.2rem;color:#0b1f3a">{total_cell}</td></tr>
      </table>
      <p style="color:#5a6a7e;font-size:0.9rem">
        Based in {SERVICE_AREA}. Questions? Contact {CONTACT_NAME} —
        <a href="tel:{CONTACT_TEL}">{CONTACT_PHONE}</a>.
      </p>
    </div>
  </div>
</body></html>"""
    return subject, plain, html


def mailto_link(to_address: str, subject: str, body: str) -> str:
    q = urllib.parse.urlencode({"subject": subject, "body": body})
    return f"mailto:{to_address}?{q}"


def sms_clipboard_text(to_address: str, body: str) -> str:
    return f"To: {to_address}\n\n{body}"


def send_sms_twilio(to_address: str, body: str) -> tuple[bool, str]:
    """Send via Twilio REST API if configured. Returns (ok, detail)."""
    if not twilio_configured():
        return False, "Twilio not configured"
    sid = os.environ["TWILIO_ACCOUNT_SID"]
    token = os.environ["TWILIO_AUTH_TOKEN"]
    from_num = os.environ["TWILIO_FROM"]
    try:
        from urllib.request import Request, urlopen
        import base64
        import json

        auth = base64.b64encode(f"{sid}:{token}".encode()).decode()
        data = urllib.parse.urlencode(
            {"To": to_address, "From": from_num, "Body": body}
        ).encode()
        req = Request(
            f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json",
            data=data,
            headers={
                "Authorization": f"Basic {auth}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            method="POST",
        )
        with urlopen(req, timeout=30) as resp:
            payload = json.loads(resp.read().decode())
        return True, payload.get("sid", "sent")
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


def send_email(to_address: str, subject: str, plain: str, html: str | None = None) -> tuple[bool, str]:
    """Send via Resend API or SMTP if configured."""
    if not email_configured():
        return False, "Email not configured"

    resend_key = os.environ.get("RESEND_API_KEY")
    if resend_key:
        try:
            from urllib.request import Request, urlopen
            import json

            from_addr = os.environ.get("SMTP_FROM") or os.environ.get("RESEND_FROM") or "onboarding@resend.dev"
            payload = json.dumps(
                {
                    "from": from_addr,
                    "to": [to_address],
                    "subject": subject,
                    "text": plain,
                    **({"html": html} if html else {}),
                }
            ).encode()
            req = Request(
                "https://api.resend.com/emails",
                data=payload,
                headers={
                    "Authorization": f"Bearer {resend_key}",
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            with urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode())
            return True, data.get("id", "sent")
        except Exception as exc:  # noqa: BLE001
            return False, str(exc)

    host = os.environ["SMTP_HOST"]
    user = os.environ["SMTP_USER"]
    password = os.environ["SMTP_PASSWORD"]
    from_addr = os.environ["SMTP_FROM"]
    port = int(os.environ.get("SMTP_PORT", "587"))
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = from_addr
        msg["To"] = to_address
        msg.attach(MIMEText(plain, "plain"))
        if html:
            msg.attach(MIMEText(html, "html"))
        with smtplib.SMTP(host, port, timeout=30) as server:
            server.starttls()
            server.login(user, password)
            server.sendmail(from_addr, [to_address], msg.as_string())
        return True, "smtp-sent"
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


def build_driver_dispatch_sms(job, contractor, driver) -> str:
    """Clear next-stop SMS for an assigned driver."""
    materials = None
    try:
        materials = job["materials"]
    except (KeyError, IndexError):
        materials = None
    materials = materials or "—"
    address = job["job_address"] or "—"
    city = job["city"] or ""
    jobsite = f"{address}, {city}".strip(", ")
    miles = f"{job['miles']:.1f}" if job["miles"] is not None else "—"
    quote = f"${job['quote_amount']:.2f}" if job["quote_amount"] is not None else "TBD"
    wait = float(job["wait_fee"] or 0)
    if wait:
        quote = f"{quote} (+ ${wait:.2f} wait)"
    company = contractor["company_name"] if contractor else "—"
    phone = (contractor["phone"] if contractor else None) or "—"
    supplier = job["supplier"] or "—"
    order_ref = job["order_ref"] or "—"
    notes = job["notes"] or "—"
    driver_name = driver["name"] if driver else "Driver"
    lines = [
        "Gopher Boys — next delivery",
        f"Hi {driver_name},",
        f"Job: {job['id']}",
        f"Supplier: {supplier} (order {order_ref})",
        f"Materials: {materials}",
        f"Jobsite: {jobsite}",
        f"Miles (one-way from Pooler 31322): {miles}",
        f"Quote: {quote}",
        f"Customer: {company} · {phone}",
        f"Notes: {notes}",
        f"— {CONTACT_NAME} {CONTACT_PHONE}",
    ]
    return chr(10).join(lines)
