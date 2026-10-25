# Gopher Boys CRM

Internal web CRM for **Gopher Boys** — construction materials pickup & delivery (Coastal Georgia / Pooler).

**Slogan:** WE PICK UP. YOU KEEP BUILDING.

## Stack

- Python 3 + Flask
- SQLite (`DATABASE_PATH`, default `data/gopher.db`)
- Jinja2 templates + vanilla CSS
- Multi-user login (admin / staff)
- Message drafts for SMS + invoice email (manual or Twilio/SMTP when configured)
- Gunicorn for production hosting

## Quick start (local)

```bash
cd /workspace/gopher-boys-crm
./start.sh
```

Or manually:

```bash
cd /workspace/gopher-boys-crm
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
PORT=5055 ./venv/bin/python app.py
```

Open: **http://127.0.0.1:5055**

## Default logins (change immediately)

On first boot (empty `users` table), two accounts are seeded with the same password:

| Role  | Email | Name | Default password |
|-------|-------|------|------------------|
| admin | `chris@ridgewayroofingco.com` | Chris Vincent | `ChangeMeNow!` (or `ADMIN_PASSWORD` env) |
| staff | `mack@ridgewayroofingco.com` | Mack Ridgeway | same as above |

**You must change these passwords** after first login (Users → Edit, or set a strong `ADMIN_PASSWORD` before first boot so the seed uses it).

## Environment variables

| Variable | Required | Purpose |
|----------|----------|---------|
| `SECRET_KEY` | Yes in prod | Flask session signing. Fallback local-only key exists for dev. |
| `ADMIN_PASSWORD` | Recommended | Password for seeded Chris/Mack accounts on first boot. |
| `DATABASE_PATH` | Optional | SQLite file path. Default: `data/gopher.db`. On Render disk use `/var/data/gopher.db`. |
| `PORT` | Optional | Listen port. Default `5055` locally; hosts set `$PORT`. |
| `TWILIO_ACCOUNT_SID` / `TWILIO_AUTH_TOKEN` / `TWILIO_FROM` | **Required on Belmo for owner SMS alerts** | If all set: (1) auto-texts Chris on every successful `/request`, and (2) SMS drafts show a **Send via Twilio** button. Without them, jobs still save; the alert is stored as `failed` in `messages`. |
| `ALERT_PHONE` | Optional | Owner alert destination. Default: `+19122440464` (Chris). |
| `CONTACT_PHONE` | Optional | Display phone. Default: `(912) 244-0464`. |
| `CONTACT_TEL` | Optional | `tel:` href / E.164. Default: `+19122440464`. Used as alert destination when `ALERT_PHONE` unset. |
| `CONTACT_NAME` | Optional | Public contact name. Default: `Chris Vincent`. |
| `SMTP_HOST` / `SMTP_USER` / `SMTP_PASSWORD` / `SMTP_FROM` | Optional | If set, email drafts can Send via SMTP (`SMTP_PORT` default 587). |
| `RESEND_API_KEY` | Optional | Alternative to SMTP for sending invoice emails. |

## Multi-user accounts

- Routes: `/` (public home + menu), `/login` (staff), `/logout`, `/dashboard` (staff), `/users` (admin), `/users/new`, `/users/<id>/edit`
- Roles: `admin` (manage users) · `staff` (full CRM, no user admin)
- All app routes require login except `/` (public home), `/login`, `/request`, `/request/thanks`, `/about`, `/api/quote`, `/api/route-miles`, `/static/*`, and `/health`. Staff dashboard is `/dashboard`. Customers never log in.
- Jobs and contractors are **shared** company-wide (not per-user silos)
- Activity notes record which user wrote them when available

Admins create users at **Users → New user**.


## Drivers & dispatch

- `/drivers` — list / add / edit delivery drivers (name, phone, active, notes)
- Jobs have optional **assigned driver** + **materials** (what to pick up)
- On job detail: assign driver, then **Text driver next stop** → SMS draft with supplier, materials, jobsite, miles from Pooler 31322, quote, customer phone
- Dashboard shows **Next up** (soonest scheduled job per active driver)
- Same draft-first / Mark sent / Twilio Send rules as customer SMS

## Messaging (SMS + invoices)

- **Text customer** on contractor detail and job detail → saves an SMS **draft** in `messages`
- **Email invoice** on job detail → builds quote + wait fee invoice → email **draft**
- `/messages` lists all drafts/sent/failed
- Message detail: **Mark sent** (after you text/email manually), **Copy**, **mailto:** link
- Live **Send** only appears when Twilio / SMTP / Resend env vars are configured
- **Customer / driver SMS:** never auto-send — Chris must click Send (or Mark sent)
- **Owner alert exception:** every successful public `/request` auto-texts Chris at `ALERT_PHONE` (default `+19122440464`) when Twilio is configured


## Public customer request

- **`/request`** — no-login form for contractors/customers to submit pickup & delivery requests
- **`/about`** — About us + vision (keep builders building by handling supplier runs)
- Supplier **dropdown** grouped by type (Home Depot, Lowe’s, roofing, lumber, building supply) for Savannah / Pooler / Coastal Georgia — plus **Other / not listed** free-text address
- Supplier list lives in **`suppliers.py`** (easy to edit; ~27 yards). Seeded from public store locators + HBA directory — verify before relying on any single address.
- **Miles**: one-way total **Pooler GA 31322 → supplier → jobsite**. Client calls `POST /api/route-miles` when supplier + jobsite are set; **server recalculates on POST** and stores miles + quote on the job even if JS fails.
- Distance: prefer public **OSRM** driving miles; fallback Nominatim geocode + haversine × 1.3 road factor. Geocodes cached in SQLite `geocode_cache`. Nominatim User-Agent + 1 req/s throttle.
- Shows flyer **rate card** + **quote calculator** (auto-filled miles + optional wait)
- On submit: creates/links a **Contractor**, creates a Job with status **`request`**, activity note “Submitted via website”
- **Owner SMS alert:** automatically texts Chris (`ALERT_PHONE` / default `+19122440464`) with company/contact, phone, supplier, jobsite, miles/quote. Stored in `messages` as `sent` or `failed`. Does **not** text the customer.
- Customer SMS/email still never auto-send
- Spam basics: honeypot field + per-IP rate limit (5 / 10 min)
- Success page: thank-you + Chris’s phone **(912) 244-0464** for urgent needs
- Login page links “Request a pickup”; dashboard shows a badge when web requests are waiting
- Filter Jobs → **Request** to see them

### Deploy note (Belmo)

Live site is on Belmo free (`gopher-boys-crm-7fae.onbelmo.uk`). **Auto-deploy is OFF** — after pushing to `main`, redeploy manually in the Belmo dashboard. There is no Belmo CLI in this repo.

**Belmo env vars for owner SMS alerts (required for live texts on `/request`):**

- `TWILIO_ACCOUNT_SID`
- `TWILIO_AUTH_TOKEN`
- `TWILIO_FROM` (Twilio number in E.164, e.g. `+1…`)
- Optional: `ALERT_PHONE=+19122440464`, `CONTACT_PHONE=(912) 244-0464`, `CONTACT_TEL=+19122440464`

Without Twilio, requests still create jobs; the owner alert is logged as a failed message for staff. Do not invent or commit Twilio credentials.

## Features

- Dashboard with status counts, recent jobs, quick quote widget
- Contractors: list / add / edit / detail (+ their jobs)
- Jobs pipeline with status filters and auto-quote from flyer rates
- Job detail: status buttons, notes, activity timeline, SMS + invoice drafts
- Quote calculator + rate card
- Search (name, phone, address, order #)
- CSV export for contractors and jobs
- Public request form: `/request` (supplier dropdown + auto miles + rate card)
- Public about: `/about`
- Health check: `GET /health` → `200 ok`

## Pricing (from flyer)

| Miles | Price |
|------:|------:|
| ≤ 10  | $110  |
| 15    | $125  |
| 20    | $140  |
| 25    | $155  |
| 30    | $170  |
| > 30  | Custom quote |

Formula: first 10 miles = $110, then **$3/mi**. Wait: first 15 min included, then **$20 per started 15 min**.

## Deploy on Render

1. Push this repo to GitHub (or connect the folder).
2. In Render: **New → Blueprint** and select `render.yaml`, **or** New Web Service:
   - Runtime: Python
   - Build: `pip install -r requirements.txt`
   - Start: `gunicorn app:app --bind 0.0.0.0:$PORT`
3. Add a **persistent disk** mounted at `/var/data` (so SQLite survives deploys).
4. Set env vars:
   - `SECRET_KEY` — strong random string
   - `ADMIN_PASSWORD` — strong password (used only on first boot seed)
   - `DATABASE_PATH=/var/data/gopher.db`
5. Optional later: Twilio + SMTP/Resend for live Send buttons.
6. Open the Render URL → log in as Chris → **change passwords** under Users.

`from app import app` works for Gunicorn (`app:app` in Procfile).

## Seed data

- Contractor: **Ridgeway Roofing Co.** / Mack Ridgeway / (912) 777-2339
- Users: Chris (admin) + Mack (staff) as above

## Contact

Request a pickup: **Chris Vincent** — (912) 244-0464 · Pooler, GA 31322

(Seeded CRM contractor **Ridgeway Roofing Co.** / Mack Ridgeway / (912) 777-2339 is sample contractor data, not the public business contact.)
