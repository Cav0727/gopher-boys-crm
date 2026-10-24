"""Gopher Boys CRM — Flask + SQLite with multi-user auth."""

from __future__ import annotations

import csv
import io
import os
from functools import wraps
from typing import Any

from flask import (
    Flask,
    Response,
    flash,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from werkzeug.security import check_password_hash, generate_password_hash

from db import (
    ROLES,
    STATUSES,
    add_activity,
    db,
    get_user_by_email,
    get_user_by_id,
    init_db,
    utcnow,
)
from messaging import (
    build_driver_dispatch_sms,
    build_invoice,
    default_sms_body,
    email_configured,
    mailto_link,
    send_email,
    send_sms_twilio,
    sms_clipboard_text,
    twilio_configured,
)
from pricing import RATE_TABLE, calc_delivery_quote, calc_wait_fee

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY") or "gopher-boys-crm-local-dev-key"

CONTACT_NAME = "Mack Ridgeway"
CONTACT_PHONE = "(912) 777-2339"
SERVICE_AREA = "Pooler, GA 31322"
SLOGAN = "WE PICK UP. YOU KEEP BUILDING."

# Public endpoints that skip login
PUBLIC_ENDPOINTS = {"login", "health", "static"}


@app.context_processor
def inject_globals():
    return {
        "CONTACT_NAME": CONTACT_NAME,
        "CONTACT_PHONE": CONTACT_PHONE,
        "SERVICE_AREA": SERVICE_AREA,
        "SLOGAN": SLOGAN,
        "STATUSES": STATUSES,
        "status_label": lambda s: s.replace("_", " ").title() if s else "",
        "current_user": getattr(g, "user", None),
        "twilio_ready": twilio_configured(),
        "email_ready": email_configured(),
    }


@app.before_request
def load_user_and_protect():
    g.user = None
    uid = session.get("user_id")
    if uid:
        user = get_user_by_id(uid)
        if user and user["is_active"]:
            g.user = user
        else:
            session.clear()

    endpoint = request.endpoint or ""
    if endpoint in PUBLIC_ENDPOINTS or endpoint.startswith("static"):
        return None
    if g.user is None:
        return redirect(url_for("login", next=request.path))
    return None


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if g.user is None:
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)

    return wrapped


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if g.user is None:
            return redirect(url_for("login", next=request.path))
        if g.user["role"] != "admin":
            flash("Admin access required.", "error")
            return redirect(url_for("dashboard"))
        return view(*args, **kwargs)

    return wrapped


def current_user_id() -> int | None:
    return g.user["id"] if g.user else None


def _f(name: str, default: str = "") -> str:
    return (request.form.get(name) or default).strip()


def as_form(obj) -> dict:
    """Normalize Row / MultiDict / dict for templates (.get)."""
    if obj is None:
        return {}
    if isinstance(obj, dict):
        return {k: ("" if v is None else v) for k, v in obj.items()}
    if hasattr(obj, "keys"):
        return {k: ("" if obj[k] is None else obj[k]) for k in obj.keys()}
    return {}


def _float(name: str, default: float | None = None) -> float | None:
    raw = request.form.get(name)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


# ── Health (for hosts) ─────────────────────────────────────────────────────


@app.route("/health")
def health():
    return "ok", 200


# ── Auth ───────────────────────────────────────────────────────────────────


@app.route("/login", methods=["GET", "POST"])
def login():
    if g.user:
        return redirect(url_for("dashboard"))
    if request.method == "POST":
        email = _f("email").lower()
        password = request.form.get("password") or ""
        user = get_user_by_email(email) if email else None
        if (
            user
            and user["is_active"]
            and check_password_hash(user["password_hash"], password)
        ):
            session.clear()
            session["user_id"] = user["id"]
            session.permanent = True
            flash(f"Welcome, {user['name']}.", "success")
            nxt = request.args.get("next") or url_for("dashboard")
            if not nxt.startswith("/"):
                nxt = url_for("dashboard")
            return redirect(nxt)
        flash("Invalid email or password.", "error")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    flash("You have been logged out.", "success")
    return redirect(url_for("login"))


# ── Users (admin) ──────────────────────────────────────────────────────────


@app.route("/users")
@admin_required
def users_list():
    with db() as conn:
        users = conn.execute(
            "SELECT id, email, name, role, is_active, created_at FROM users ORDER BY name"
        ).fetchall()
    return render_template("users_list.html", users=users)


@app.route("/users/new", methods=["GET", "POST"])
@admin_required
def user_new():
    if request.method == "POST":
        email = _f("email").lower()
        name = _f("name")
        role = _f("role") or "staff"
        password = request.form.get("password") or ""
        if not email or not name or not password:
            flash("Email, name, and password are required.", "error")
            return render_template("user_form.html", user=None, form=as_form(request.form))
        if role not in ROLES:
            role = "staff"
        if get_user_by_email(email):
            flash("That email is already in use.", "error")
            return render_template("user_form.html", user=None, form=as_form(request.form))
        with db() as conn:
            conn.execute(
                """
                INSERT INTO users (email, name, password_hash, role, is_active, created_at)
                VALUES (?, ?, ?, ?, 1, ?)
                """,
                (email, name, generate_password_hash(password), role, utcnow()),
            )
        flash("User created.", "success")
        return redirect(url_for("users_list"))
    return render_template("user_form.html", user=None, form={})


@app.route("/users/<int:uid>/edit", methods=["GET", "POST"])
@admin_required
def user_edit(uid: int):
    with db() as conn:
        user = conn.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
    if not user:
        flash("User not found.", "error")
        return redirect(url_for("users_list"))
    if request.method == "POST":
        name = _f("name")
        role = _f("role") or user["role"]
        is_active = 1 if request.form.get("is_active") == "1" else 0
        password = request.form.get("password") or ""
        if not name:
            flash("Name is required.", "error")
            return render_template(
                "user_form.html", user=user, form=as_form(request.form)
            )
        if role not in ROLES:
            role = user["role"]
        # Prevent locking yourself out
        if uid == g.user["id"] and not is_active:
            flash("You cannot deactivate your own account.", "error")
            return render_template(
                "user_form.html", user=user, form=as_form(request.form)
            )
        if uid == g.user["id"] and role != "admin":
            flash("You cannot remove your own admin role.", "error")
            return render_template(
                "user_form.html", user=user, form=as_form(request.form)
            )
        with db() as conn:
            if password.strip():
                conn.execute(
                    """
                    UPDATE users SET name=?, role=?, is_active=?, password_hash=?
                    WHERE id=?
                    """,
                    (name, role, is_active, generate_password_hash(password), uid),
                )
            else:
                conn.execute(
                    """
                    UPDATE users SET name=?, role=?, is_active=?
                    WHERE id=?
                    """,
                    (name, role, is_active, uid),
                )
        flash("User updated.", "success")
        return redirect(url_for("users_list"))
    return render_template("user_form.html", user=user, form=as_form(user))


# ── Dashboard ──────────────────────────────────────────────────────────────


@app.route("/")
def dashboard():
    with db() as conn:
        counts = {
            r["status"]: r["c"]
            for r in conn.execute(
                "SELECT status, COUNT(*) AS c FROM jobs GROUP BY status"
            )
        }
        for s in STATUSES:
            counts.setdefault(s, 0)
        total = sum(counts.values())
        recent = conn.execute(
            """
            SELECT j.*, c.company_name, c.contact_name
            FROM jobs j
            JOIN contractors c ON c.id = j.contractor_id
            ORDER BY j.updated_at DESC
            LIMIT 10
            """
        ).fetchall()
        contractor_count = conn.execute(
            "SELECT COUNT(*) AS c FROM contractors"
        ).fetchone()["c"]
        open_jobs = conn.execute(
            """
            SELECT COUNT(*) AS c FROM jobs
            WHERE status IN ('lead','quoted','scheduled')
            """
        ).fetchone()["c"]
        revenue = conn.execute(
            """
            SELECT COALESCE(SUM(quote_amount),0) + COALESCE(SUM(wait_fee),0) AS t
            FROM jobs WHERE status IN ('delivered','invoiced')
            """
        ).fetchone()["t"]
        next_up = conn.execute(
            """
            SELECT d.id AS driver_id, d.name AS driver_name, d.phone AS driver_phone,
                   j.id AS job_id, j.job_address, j.city, j.scheduled_at, j.status,
                   c.company_name
            FROM drivers d
            LEFT JOIN jobs j ON j.id = (
                SELECT j2.id FROM jobs j2
                WHERE j2.driver_id = d.id AND j2.status = 'scheduled'
                ORDER BY COALESCE(j2.scheduled_at, j2.updated_at) ASC, j2.id ASC
                LIMIT 1
            )
            LEFT JOIN contractors c ON c.id = j.contractor_id
            WHERE d.is_active = 1
            ORDER BY d.name
            """
        ).fetchall()
    return render_template(
        "dashboard.html",
        counts=counts,
        total=total,
        recent=recent,
        contractor_count=contractor_count,
        open_jobs=open_jobs,
        revenue=revenue,
        next_up=next_up,
    )


# ── Quote API / page ───────────────────────────────────────────────────────


@app.route("/quote")
def quote_page():
    return render_template("quote.html", rate_table=RATE_TABLE)


@app.route("/api/quote")
def api_quote():
    miles = request.args.get("miles", type=float)
    wait = request.args.get("wait_extra", type=float) or 0
    q = calc_delivery_quote(miles)
    w = calc_wait_fee(wait)
    total = None
    if q.get("ok") and q.get("amount") is not None:
        total = round(q["amount"] + w["fee"], 2)
    return jsonify({"delivery": q, "wait": w, "total": total})


# ── Rate card ──────────────────────────────────────────────────────────────


@app.route("/rates")
def rates():
    return render_template("rates.html", rate_table=RATE_TABLE)


# ── Search ─────────────────────────────────────────────────────────────────


@app.route("/search")
def search():
    q = (request.args.get("q") or "").strip()
    contractors, jobs = [], []
    if q:
        like = f"%{q}%"
        with db() as conn:
            contractors = conn.execute(
                """
                SELECT * FROM contractors
                WHERE company_name LIKE ? OR contact_name LIKE ?
                   OR phone LIKE ? OR email LIKE ? OR notes LIKE ?
                ORDER BY company_name
                LIMIT 50
                """,
                (like, like, like, like, like),
            ).fetchall()
            jobs = conn.execute(
                """
                SELECT j.*, c.company_name
                FROM jobs j
                JOIN contractors c ON c.id = j.contractor_id
                WHERE j.job_address LIKE ? OR j.order_ref LIKE ?
                   OR j.supplier LIKE ? OR j.city LIKE ? OR j.notes LIKE ?
                   OR c.company_name LIKE ? OR c.phone LIKE ?
                ORDER BY j.updated_at DESC
                LIMIT 50
                """,
                (like, like, like, like, like, like, like),
            ).fetchall()
    return render_template(
        "search.html", q=q, contractors=contractors, jobs=jobs
    )


# ── Contractors ────────────────────────────────────────────────────────────


@app.route("/contractors")
def contractors_list():
    with db() as conn:
        rows = conn.execute(
            """
            SELECT c.*,
                   (SELECT COUNT(*) FROM jobs j WHERE j.contractor_id = c.id) AS job_count
            FROM contractors c
            ORDER BY c.company_name COLLATE NOCASE
            """
        ).fetchall()
    return render_template("contractors_list.html", contractors=rows)


@app.route("/contractors/new", methods=["GET", "POST"])
def contractor_new():
    if request.method == "POST":
        company = _f("company_name")
        if not company:
            flash("Company name is required.", "error")
            return render_template(
                "contractor_form.html", contractor=None, form=as_form(request.form)
            )
        with db() as conn:
            cur = conn.execute(
                """
                INSERT INTO contractors
                    (company_name, contact_name, phone, email, notes, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    company,
                    _f("contact_name"),
                    _f("phone"),
                    _f("email"),
                    _f("notes"),
                    utcnow(),
                ),
            )
            cid = cur.lastrowid
        flash("Contractor added.", "success")
        return redirect(url_for("contractor_detail", cid=cid))
    return render_template("contractor_form.html", contractor=None, form={})


@app.route("/contractors/<int:cid>")
def contractor_detail(cid: int):
    with db() as conn:
        c = conn.execute(
            "SELECT * FROM contractors WHERE id = ?", (cid,)
        ).fetchone()
        if not c:
            flash("Contractor not found.", "error")
            return redirect(url_for("contractors_list"))
        jobs = conn.execute(
            """
            SELECT * FROM jobs WHERE contractor_id = ?
            ORDER BY updated_at DESC
            """,
            (cid,),
        ).fetchall()
        messages = conn.execute(
            """
            SELECT m.*, u.name AS created_by_name
            FROM messages m
            LEFT JOIN users u ON u.id = m.created_by
            WHERE m.contractor_id = ?
            ORDER BY m.created_at DESC
            LIMIT 20
            """,
            (cid,),
        ).fetchall()
    default_body = default_sms_body(c["contact_name"] or c["company_name"])
    return render_template(
        "contractor_detail.html",
        contractor=c,
        jobs=jobs,
        messages=messages,
        default_sms_body=default_body,
    )


@app.route("/contractors/<int:cid>/edit", methods=["GET", "POST"])
def contractor_edit(cid: int):
    with db() as conn:
        c = conn.execute(
            "SELECT * FROM contractors WHERE id = ?", (cid,)
        ).fetchone()
    if not c:
        flash("Contractor not found.", "error")
        return redirect(url_for("contractors_list"))
    if request.method == "POST":
        company = _f("company_name")
        if not company:
            flash("Company name is required.", "error")
            return render_template(
                "contractor_form.html", contractor=c, form=as_form(request.form)
            )
        with db() as conn:
            conn.execute(
                """
                UPDATE contractors SET
                    company_name=?, contact_name=?, phone=?, email=?, notes=?
                WHERE id=?
                """,
                (
                    company,
                    _f("contact_name"),
                    _f("phone"),
                    _f("email"),
                    _f("notes"),
                    cid,
                ),
            )
        flash("Contractor updated.", "success")
        return redirect(url_for("contractor_detail", cid=cid))
    return render_template("contractor_form.html", contractor=c, form=as_form(c))


# ── Jobs ───────────────────────────────────────────────────────────────────


def _save_job_fields(existing_id: int | None = None):
    contractor_id = request.form.get("contractor_id", type=int)
    if not contractor_id:
        return None, "Contractor is required."
    miles = _float("miles")
    wait_extra = _float("wait_minutes_extra", 0) or 0
    quote_override = _float("quote_amount")
    manual = request.form.get("manual_quote") == "1"

    q = calc_delivery_quote(miles)
    w = calc_wait_fee(wait_extra)

    if manual and quote_override is not None:
        quote_amount = quote_override
    elif q.get("custom"):
        quote_amount = quote_override
    elif q.get("ok"):
        quote_amount = q["amount"]
    else:
        quote_amount = quote_override

    status = _f("status") or "lead"
    if status not in STATUSES:
        status = "lead"

    fields = {
        "contractor_id": contractor_id,
        "supplier": _f("supplier"),
        "order_ref": _f("order_ref"),
        "job_address": _f("job_address"),
        "city": _f("city"),
        "miles": miles,
        "quote_amount": quote_amount,
        "wait_minutes_extra": wait_extra,
        "wait_fee": w["fee"],
        "extra_stops_notes": _f("extra_stops_notes"),
        "status": status,
        "scheduled_at": _f("scheduled_at") or None,
        "delivered_at": _f("delivered_at") or None,
        "notes": _f("notes"),
        "driver_id": request.form.get("driver_id", type=int),
        "materials": _f("materials"),
    }
    return fields, None


@app.route("/jobs")
def jobs_list():
    status = (request.args.get("status") or "").strip()
    with db() as conn:
        if status and status in STATUSES:
            jobs = conn.execute(
                """
                SELECT j.*, c.company_name, c.contact_name
                FROM jobs j
                JOIN contractors c ON c.id = j.contractor_id
                WHERE j.status = ?
                ORDER BY j.updated_at DESC
                """,
                (status,),
            ).fetchall()
        else:
            jobs = conn.execute(
                """
                SELECT j.*, c.company_name, c.contact_name
                FROM jobs j
                JOIN contractors c ON c.id = j.contractor_id
                ORDER BY j.updated_at DESC
                """
            ).fetchall()
            status = ""
    return render_template(
        "jobs_list.html", jobs=jobs, filter_status=status
    )


@app.route("/jobs/new", methods=["GET", "POST"])
def job_new():
    with db() as conn:
        contractors = conn.execute(
            "SELECT id, company_name, contact_name FROM contractors ORDER BY company_name"
        ).fetchall()
        drivers = conn.execute(
            "SELECT id, name, phone FROM drivers WHERE is_active = 1 ORDER BY name"
        ).fetchall()
    preselect = request.args.get("contractor_id", type=int)
    if request.method == "POST":
        fields, err = _save_job_fields()
        if err:
            flash(err, "error")
            return render_template(
                "job_form.html",
                job=None,
                form=as_form(request.form),
                contractors=contractors,
                drivers=drivers,
                preselect=preselect,
            )
        now = utcnow()
        uid = current_user_id()
        with db() as conn:
            cur = conn.execute(
                """
                INSERT INTO jobs (
                    contractor_id, supplier, order_ref, job_address, city,
                    miles, quote_amount, wait_minutes_extra, wait_fee,
                    extra_stops_notes, status, scheduled_at, delivered_at,
                    notes, driver_id, materials, created_at, updated_at
                ) VALUES (
                    :contractor_id, :supplier, :order_ref, :job_address, :city,
                    :miles, :quote_amount, :wait_minutes_extra, :wait_fee,
                    :extra_stops_notes, :status, :scheduled_at, :delivered_at,
                    :notes, :driver_id, :materials, :created_at, :updated_at
                )
                """,
                {**fields, "created_at": now, "updated_at": now},
            )
            jid = cur.lastrowid
            add_activity(
                conn, jid, f"Job created with status '{fields['status']}'.", uid
            )
            if fields["miles"] and fields["miles"] > 30:
                add_activity(
                    conn,
                    jid,
                    f"Miles {fields['miles']:.1f} > 30 — custom quote needed.",
                    uid,
                )
        flash("Job created.", "success")
        return redirect(url_for("job_detail", jid=jid))
    return render_template(
        "job_form.html",
        job=None,
        form={},
        contractors=contractors,
        drivers=drivers,
        preselect=preselect,
    )


@app.route("/jobs/<int:jid>")
def job_detail(jid: int):
    with db() as conn:
        job = conn.execute(
            """
            SELECT j.*, c.company_name, c.contact_name, c.phone AS contractor_phone,
                   d.name AS driver_name, d.phone AS driver_phone
            FROM jobs j
            JOIN contractors c ON c.id = j.contractor_id
            LEFT JOIN drivers d ON d.id = j.driver_id
            WHERE j.id = ?
            """,
            (jid,),
        ).fetchone()
        if not job:
            flash("Job not found.", "error")
            return redirect(url_for("jobs_list"))
        activity = conn.execute(
            """
            SELECT a.*, u.name AS user_name
            FROM activity a
            LEFT JOIN users u ON u.id = a.user_id
            WHERE a.job_id = ?
            ORDER BY a.created_at DESC, a.id DESC
            """,
            (jid,),
        ).fetchall()
        contractor = conn.execute(
            "SELECT * FROM contractors WHERE id = ?", (job["contractor_id"],)
        ).fetchone()
        messages = conn.execute(
            """
            SELECT m.*, u.name AS created_by_name
            FROM messages m
            LEFT JOIN users u ON u.id = m.created_by
            WHERE m.job_id = ?
            ORDER BY m.created_at DESC
            LIMIT 20
            """,
            (jid,),
        ).fetchall()
    q_info = calc_delivery_quote(job["miles"])
    sms_body = default_sms_body(
        (contractor["contact_name"] if contractor else None)
        or (contractor["company_name"] if contractor else None),
        job["order_ref"] or f"#{job['id']}",
    )
    inv_subject, inv_plain, inv_html = build_invoice(job, contractor)
    driver = None
    if job["driver_id"]:
        with db() as conn:
            driver = conn.execute(
                "SELECT * FROM drivers WHERE id = ?", (job["driver_id"],)
            ).fetchone()
    with db() as conn:
        all_drivers = conn.execute(
            "SELECT id, name, phone FROM drivers WHERE is_active = 1 ORDER BY name"
        ).fetchall()
    driver_sms = ""
    if driver:
        driver_sms = build_driver_dispatch_sms(job, contractor, driver)
    return render_template(
        "job_detail.html",
        job=job,
        activity=activity,
        q_info=q_info,
        contractor=contractor,
        messages=messages,
        default_sms_body=sms_body,
        invoice_subject=inv_subject,
        invoice_body=inv_plain,
        driver=driver,
        all_drivers=all_drivers,
        driver_sms_body=driver_sms,
    )


@app.route("/jobs/<int:jid>/edit", methods=["GET", "POST"])
def job_edit(jid: int):
    with db() as conn:
        job = conn.execute("SELECT * FROM jobs WHERE id = ?", (jid,)).fetchone()
        contractors = conn.execute(
            "SELECT id, company_name, contact_name FROM contractors ORDER BY company_name"
        ).fetchall()
        drivers = conn.execute(
            "SELECT id, name, phone, is_active FROM drivers ORDER BY name"
        ).fetchall()
    if not job:
        flash("Job not found.", "error")
        return redirect(url_for("jobs_list"))
    if request.method == "POST":
        fields, err = _save_job_fields(jid)
        if err:
            flash(err, "error")
            return render_template(
                "job_form.html",
                job=job,
                form=as_form(request.form),
                contractors=contractors,
                drivers=drivers,
                preselect=None,
            )
        old_status = job["status"]
        uid = current_user_id()
        with db() as conn:
            conn.execute(
                """
                UPDATE jobs SET
                    contractor_id=:contractor_id, supplier=:supplier,
                    order_ref=:order_ref, job_address=:job_address, city=:city,
                    miles=:miles, quote_amount=:quote_amount,
                    wait_minutes_extra=:wait_minutes_extra, wait_fee=:wait_fee,
                    extra_stops_notes=:extra_stops_notes, status=:status,
                    scheduled_at=:scheduled_at, delivered_at=:delivered_at,
                    notes=:notes, driver_id=:driver_id, materials=:materials,
                    updated_at=:updated_at
                WHERE id=:id
                """,
                {**fields, "updated_at": utcnow(), "id": jid},
            )
            if fields["status"] != old_status:
                add_activity(
                    conn,
                    jid,
                    f"Status changed: {old_status} → {fields['status']}.",
                    uid,
                )
            else:
                add_activity(conn, jid, "Job details updated.", uid)
        flash("Job updated.", "success")
        return redirect(url_for("job_detail", jid=jid))
    return render_template(
        "job_form.html",
        job=job,
        form=as_form(job),
        contractors=contractors,
        drivers=drivers,
        preselect=None,
    )


@app.route("/jobs/<int:jid>/status", methods=["POST"])
def job_status(jid: int):
    new_status = _f("status")
    if new_status not in STATUSES:
        flash("Invalid status.", "error")
        return redirect(url_for("job_detail", jid=jid))
    uid = current_user_id()
    with db() as conn:
        job = conn.execute("SELECT * FROM jobs WHERE id = ?", (jid,)).fetchone()
        if not job:
            flash("Job not found.", "error")
            return redirect(url_for("jobs_list"))
        old = job["status"]
        delivered_at = job["delivered_at"]
        scheduled_at = job["scheduled_at"]
        now = utcnow()
        if new_status == "delivered" and not delivered_at:
            delivered_at = now
        if new_status == "scheduled" and not scheduled_at:
            scheduled_at = now
        conn.execute(
            """
            UPDATE jobs SET status=?, delivered_at=?, scheduled_at=?, updated_at=?
            WHERE id=?
            """,
            (new_status, delivered_at, scheduled_at, now, jid),
        )
        add_activity(conn, jid, f"Status changed: {old} → {new_status}.", uid)
    flash(f"Status set to {new_status}.", "success")
    return redirect(url_for("job_detail", jid=jid))


@app.route("/jobs/<int:jid>/note", methods=["POST"])
def job_note(jid: int):
    msg = _f("message")
    if not msg:
        flash("Note cannot be empty.", "error")
        return redirect(url_for("job_detail", jid=jid))
    uid = current_user_id()
    with db() as conn:
        job = conn.execute("SELECT id FROM jobs WHERE id = ?", (jid,)).fetchone()
        if not job:
            flash("Job not found.", "error")
            return redirect(url_for("jobs_list"))
        add_activity(conn, jid, msg, uid)
        conn.execute(
            "UPDATE jobs SET updated_at=? WHERE id=?", (utcnow(), jid)
        )
    flash("Note added.", "success")
    return redirect(url_for("job_detail", jid=jid))



# ── Drivers ────────────────────────────────────────────────────────────────


@app.route("/drivers")
def drivers_list():
    with db() as conn:
        rows = conn.execute(
            """
            SELECT d.*,
                   (SELECT COUNT(*) FROM jobs j
                    WHERE j.driver_id = d.id AND j.status = 'scheduled') AS scheduled_count
            FROM drivers d
            ORDER BY d.is_active DESC, d.name COLLATE NOCASE
            """
        ).fetchall()
    return render_template("drivers_list.html", drivers=rows)


@app.route("/drivers/new", methods=["GET", "POST"])
def driver_new():
    if request.method == "POST":
        name = _f("name")
        if not name:
            flash("Name is required.", "error")
            return render_template("driver_form.html", driver=None, form=as_form(request.form))
        with db() as conn:
            conn.execute(
                """
                INSERT INTO drivers (name, phone, is_active, notes, created_at)
                VALUES (?, ?, 1, ?, ?)
                """,
                (name, _f("phone"), _f("notes"), utcnow()),
            )
        flash("Driver added.", "success")
        return redirect(url_for("drivers_list"))
    return render_template("driver_form.html", driver=None, form={})


@app.route("/drivers/<int:did>/edit", methods=["GET", "POST"])
def driver_edit(did: int):
    with db() as conn:
        driver = conn.execute("SELECT * FROM drivers WHERE id = ?", (did,)).fetchone()
    if not driver:
        flash("Driver not found.", "error")
        return redirect(url_for("drivers_list"))
    if request.method == "POST":
        name = _f("name")
        if not name:
            flash("Name is required.", "error")
            return render_template(
                "driver_form.html", driver=driver, form=as_form(request.form)
            )
        is_active = 1 if request.form.get("is_active") == "1" else 0
        with db() as conn:
            conn.execute(
                """
                UPDATE drivers SET name=?, phone=?, is_active=?, notes=?
                WHERE id=?
                """,
                (name, _f("phone"), is_active, _f("notes"), did),
            )
        flash("Driver updated.", "success")
        return redirect(url_for("drivers_list"))
    return render_template("driver_form.html", driver=driver, form=as_form(driver))


@app.route("/jobs/<int:jid>/assign-driver", methods=["POST"])
def job_assign_driver(jid: int):
    driver_id = request.form.get("driver_id", type=int)
    with db() as conn:
        job = conn.execute("SELECT * FROM jobs WHERE id = ?", (jid,)).fetchone()
        if not job:
            flash("Job not found.", "error")
            return redirect(url_for("jobs_list"))
        if driver_id:
            d = conn.execute(
                "SELECT id FROM drivers WHERE id = ? AND is_active = 1", (driver_id,)
            ).fetchone()
            if not d:
                flash("Driver not found or inactive.", "error")
                return redirect(url_for("job_detail", jid=jid))
        conn.execute(
            "UPDATE jobs SET driver_id=?, updated_at=? WHERE id=?",
            (driver_id, utcnow(), jid),
        )
        if driver_id:
            drow = conn.execute(
                "SELECT name FROM drivers WHERE id = ?", (driver_id,)
            ).fetchone()
            add_activity(
                conn,
                jid,
                f"Assigned driver: {drow['name']}.",
                current_user_id(),
            )
        else:
            add_activity(conn, jid, "Cleared driver assignment.", current_user_id())
    flash("Driver assignment saved.", "success")
    return redirect(url_for("job_detail", jid=jid))


@app.route("/jobs/<int:jid>/text-driver", methods=["POST"])
def job_text_driver(jid: int):
    with db() as conn:
        job = conn.execute("SELECT * FROM jobs WHERE id = ?", (jid,)).fetchone()
        if not job:
            flash("Job not found.", "error")
            return redirect(url_for("jobs_list"))
        if not job["driver_id"]:
            flash("Assign a driver first, then text them the next stop.", "error")
            return redirect(url_for("job_detail", jid=jid))
        driver = conn.execute(
            "SELECT * FROM drivers WHERE id = ?", (job["driver_id"],)
        ).fetchone()
        contractor = conn.execute(
            "SELECT * FROM contractors WHERE id = ?", (job["contractor_id"],)
        ).fetchone()
    if not driver or not driver["phone"]:
        flash("Driver needs a phone number. Edit the driver record first.", "error")
        return redirect(url_for("job_detail", jid=jid))
    body = _f("body") or build_driver_dispatch_sms(job, contractor, driver)
    to_address = _f("to_address") or driver["phone"]
    mid = _save_message_draft(
        contractor_id=job["contractor_id"],
        job_id=jid,
        channel="sms",
        to_address=to_address,
        body=body,
        subject=f"Driver dispatch — job #{jid}",
    )
    flash("Driver SMS draft saved. Review, then Send or Mark sent.", "success")
    return redirect(url_for("message_detail", mid=mid))


# ── Messages (SMS / email drafts) ──────────────────────────────────────────


@app.route("/messages")
def messages_list():
    status = (request.args.get("status") or "").strip()
    channel = (request.args.get("channel") or "").strip()
    sql = """
        SELECT m.*, c.company_name, u.name AS created_by_name
        FROM messages m
        JOIN contractors c ON c.id = m.contractor_id
        LEFT JOIN users u ON u.id = m.created_by
        WHERE 1=1
    """
    params: list[Any] = []
    if status in ("draft", "queued", "sent", "failed"):
        sql += " AND m.status = ?"
        params.append(status)
    if channel in ("sms", "email"):
        sql += " AND m.channel = ?"
        params.append(channel)
    sql += " ORDER BY m.created_at DESC LIMIT 200"
    with db() as conn:
        rows = conn.execute(sql, params).fetchall()
    return render_template(
        "messages_list.html",
        messages=rows,
        filter_status=status,
        filter_channel=channel,
    )


@app.route("/messages/<int:mid>")
def message_detail(mid: int):
    with db() as conn:
        msg = conn.execute(
            """
            SELECT m.*, c.company_name, c.contact_name, c.phone AS contractor_phone,
                   c.email AS contractor_email, u.name AS created_by_name
            FROM messages m
            JOIN contractors c ON c.id = m.contractor_id
            LEFT JOIN users u ON u.id = m.created_by
            WHERE m.id = ?
            """,
            (mid,),
        ).fetchone()
    if not msg:
        flash("Message not found.", "error")
        return redirect(url_for("messages_list"))
    mailto = None
    if msg["channel"] == "email":
        mailto = mailto_link(msg["to_address"], msg["subject"] or "", msg["body"])
    clipboard = sms_clipboard_text(msg["to_address"], msg["body"])
    return render_template(
        "message_detail.html",
        msg=msg,
        mailto=mailto,
        clipboard=clipboard,
    )


def _save_message_draft(
    *,
    contractor_id: int,
    channel: str,
    to_address: str,
    body: str,
    subject: str | None = None,
    job_id: int | None = None,
) -> int:
    with db() as conn:
        cur = conn.execute(
            """
            INSERT INTO messages
                (contractor_id, job_id, channel, to_address, subject, body,
                 status, created_by, created_at, sent_at)
            VALUES (?, ?, ?, ?, ?, ?, 'draft', ?, ?, NULL)
            """,
            (
                contractor_id,
                job_id,
                channel,
                to_address,
                subject,
                body,
                current_user_id(),
                utcnow(),
            ),
        )
        return cur.lastrowid


@app.route("/contractors/<int:cid>/text", methods=["POST"])
def contractor_text(cid: int):
    with db() as conn:
        c = conn.execute("SELECT * FROM contractors WHERE id = ?", (cid,)).fetchone()
    if not c:
        flash("Contractor not found.", "error")
        return redirect(url_for("contractors_list"))
    to_address = _f("to_address") or (c["phone"] or "")
    body = _f("body")
    if not to_address or not body:
        flash("Phone and message body are required.", "error")
        return redirect(url_for("contractor_detail", cid=cid))
    mid = _save_message_draft(
        contractor_id=cid,
        channel="sms",
        to_address=to_address,
        body=body,
    )
    flash("SMS draft saved. Review and mark sent or send when ready.", "success")
    return redirect(url_for("message_detail", mid=mid))


@app.route("/jobs/<int:jid>/text", methods=["POST"])
def job_text(jid: int):
    with db() as conn:
        job = conn.execute("SELECT * FROM jobs WHERE id = ?", (jid,)).fetchone()
        if not job:
            flash("Job not found.", "error")
            return redirect(url_for("jobs_list"))
        c = conn.execute(
            "SELECT * FROM contractors WHERE id = ?", (job["contractor_id"],)
        ).fetchone()
    to_address = _f("to_address") or ((c["phone"] if c else "") or "")
    body = _f("body")
    if not to_address or not body:
        flash("Phone and message body are required.", "error")
        return redirect(url_for("job_detail", jid=jid))
    mid = _save_message_draft(
        contractor_id=job["contractor_id"],
        job_id=jid,
        channel="sms",
        to_address=to_address,
        body=body,
    )
    flash("SMS draft saved.", "success")
    return redirect(url_for("message_detail", mid=mid))


@app.route("/jobs/<int:jid>/invoice", methods=["POST"])
def job_invoice(jid: int):
    with db() as conn:
        job = conn.execute("SELECT * FROM jobs WHERE id = ?", (jid,)).fetchone()
        if not job:
            flash("Job not found.", "error")
            return redirect(url_for("jobs_list"))
        c = conn.execute(
            "SELECT * FROM contractors WHERE id = ?", (job["contractor_id"],)
        ).fetchone()
    to_address = _f("to_address") or ((c["email"] if c else "") or "")
    subject = _f("subject")
    body = _f("body")
    if not subject or not body:
        inv_subject, inv_plain, _html = build_invoice(job, c)
        subject = subject or inv_subject
        body = body or inv_plain
    if not to_address:
        flash("Contractor email is required to draft an invoice. Add it on the contractor record.", "error")
        return redirect(url_for("job_detail", jid=jid))
    mid = _save_message_draft(
        contractor_id=job["contractor_id"],
        job_id=jid,
        channel="email",
        to_address=to_address,
        subject=subject,
        body=body,
    )
    flash("Invoice email draft saved.", "success")
    return redirect(url_for("message_detail", mid=mid))


@app.route("/messages/<int:mid>/mark-sent", methods=["POST"])
def message_mark_sent(mid: int):
    with db() as conn:
        msg = conn.execute("SELECT * FROM messages WHERE id = ?", (mid,)).fetchone()
        if not msg:
            flash("Message not found.", "error")
            return redirect(url_for("messages_list"))
        conn.execute(
            "UPDATE messages SET status='sent', sent_at=? WHERE id=?",
            (utcnow(), mid),
        )
        if msg["job_id"]:
            add_activity(
                conn,
                msg["job_id"],
                f"{msg['channel'].upper()} marked sent to {msg['to_address']}.",
                current_user_id(),
            )
    flash("Marked as sent.", "success")
    return redirect(url_for("message_detail", mid=mid))


@app.route("/messages/<int:mid>/send", methods=["POST"])
def message_send(mid: int):
    """Explicit Send only — never auto. Requires Twilio/SMTP/Resend env."""
    with db() as conn:
        msg = conn.execute("SELECT * FROM messages WHERE id = ?", (mid,)).fetchone()
    if not msg:
        flash("Message not found.", "error")
        return redirect(url_for("messages_list"))
    if msg["status"] == "sent":
        flash("Already sent.", "error")
        return redirect(url_for("message_detail", mid=mid))

    ok, detail = False, "unknown"
    if msg["channel"] == "sms":
        if not twilio_configured():
            flash("Twilio is not configured. Copy the message or mark sent after sending manually.", "error")
            return redirect(url_for("message_detail", mid=mid))
        ok, detail = send_sms_twilio(msg["to_address"], msg["body"])
    elif msg["channel"] == "email":
        if not email_configured():
            flash("Email is not configured. Use mailto or mark sent after sending manually.", "error")
            return redirect(url_for("message_detail", mid=mid))
        # Rebuild HTML invoice when linked to a job
        html = None
        subject = msg["subject"] or "Message from Gopher Boys"
        if msg["job_id"]:
            with db() as conn:
                job = conn.execute("SELECT * FROM jobs WHERE id = ?", (msg["job_id"],)).fetchone()
                c = conn.execute(
                    "SELECT * FROM contractors WHERE id = ?", (msg["contractor_id"],)
                ).fetchone()
            if job:
                _s, _plain, html = build_invoice(job, c)
        ok, detail = send_email(msg["to_address"], subject, msg["body"], html)
    else:
        flash("Unknown channel.", "error")
        return redirect(url_for("message_detail", mid=mid))

    with db() as conn:
        if ok:
            conn.execute(
                "UPDATE messages SET status='sent', sent_at=? WHERE id=?",
                (utcnow(), mid),
            )
            if msg["job_id"]:
                add_activity(
                    conn,
                    msg["job_id"],
                    f"{msg['channel'].upper()} sent to {msg['to_address']}.",
                    current_user_id(),
                )
            flash("Sent successfully.", "success")
        else:
            conn.execute(
                "UPDATE messages SET status='failed' WHERE id=?",
                (mid,),
            )
            flash(f"Send failed: {detail}", "error")
    return redirect(url_for("message_detail", mid=mid))


# ── CSV export ─────────────────────────────────────────────────────────────


@app.route("/export/contractors.csv")
def export_contractors():
    with db() as conn:
        rows = conn.execute(
            "SELECT * FROM contractors ORDER BY id"
        ).fetchall()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(
        ["id", "company_name", "contact_name", "phone", "email", "notes", "created_at"]
    )
    for r in rows:
        w.writerow(
            [
                r["id"],
                r["company_name"],
                r["contact_name"],
                r["phone"],
                r["email"],
                r["notes"],
                r["created_at"],
            ]
        )
    return Response(
        buf.getvalue(),
        mimetype="text/csv",
        headers={
            "Content-Disposition": "attachment; filename=gopher_contractors.csv"
        },
    )


@app.route("/export/jobs.csv")
def export_jobs():
    with db() as conn:
        rows = conn.execute(
            """
            SELECT j.*, c.company_name, c.contact_name, c.phone AS contractor_phone
            FROM jobs j
            JOIN contractors c ON c.id = j.contractor_id
            ORDER BY j.id
            """
        ).fetchall()
    buf = io.StringIO()
    w = csv.writer(buf)
    headers = [
        "id",
        "company_name",
        "contact_name",
        "contractor_phone",
        "supplier",
        "order_ref",
        "job_address",
        "city",
        "miles",
        "quote_amount",
        "wait_minutes_extra",
        "wait_fee",
        "extra_stops_notes",
        "status",
        "scheduled_at",
        "delivered_at",
        "notes",
        "created_at",
        "updated_at",
    ]
    w.writerow(headers)
    for r in rows:
        w.writerow([r[h] for h in headers])
    return Response(
        buf.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=gopher_jobs.csv"},
    )


# ── Boot ───────────────────────────────────────────────────────────────────


init_db()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5055"))
    app.run(host="0.0.0.0", port=port, debug=False)
