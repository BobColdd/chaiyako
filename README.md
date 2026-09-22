# Chai Yako — Tea Factory Information Management System

One system for the whole factory: farmers and farms, buying centres, weighing, receipts,
quality grading, notices, complaints, fertilizer, staff access and an audit trail.

## The tea-buying flow

1. The clerk scans the farmer's farm card. The farmer's name appears on screen.
2. The tea is hung on the scale. The scale's weight appears on screen (the clerk never types it).
3. The clerk clicks **Confirm and print receipt** once. In one all-or-nothing step the system
   records the transaction (so it shows on the farmer's side) **and** issues the numbered receipt,
   which prints automatically. A double click cannot record the tea twice.

A wrong transaction is never edited or deleted: a manager **voids** it (with a reason) and the tea is weighed again.

## Access model

Everyone uses the same login. What a person sees and can do comes from their **roles**, and each
role is a set of **permissions** (see `app/catalogue.py`). A manager can lend one permission to
someone else through a **delegation**; the audit trail records whenever a delegation was used.
Every change writes an audit row in the same database transaction, and audit rows cannot be edited or deleted.

| Role | Main work |
|---|---|
| Customer Service Officer | Registers farmers and farms, edits contact details, posts notices |
| Field Officer | Visits farms, verifies them, issues farm numbers and cards |
| Tea Buying Clerk | Buys tea at a buying centre (scan, weigh, confirm) |
| Tea Buying Manager | Buying centres, scales, voiding transactions, reports |
| Tea Receiver | Grades tea quality; sees centre-level trends (never farmer names) |
| Farmer Relations Officer | Resolves complaints |
| Inputs Officer | Records fertilizer distribution |
| Finance Officer / Manager | Statements (screens arrive in a later phase) |
| IT Officer / Manager | Accounts, roles, scales, audit trail |
| Factory Manager | Oversight and reports |

## Notices

One board, three audiences: **Department** (only that department), **All staff**, and **Public**
(farmers and visitors — served to the farmer app at `/api/public/notices`,
optionally `?centre=<code>` for one buying centre). Public and all-staff notices need the
`PUBLISH_NOTICE` permission.

## Running it

```bash
pip install -r requirements.txt
python demo_seed.py --reset        # demo data (drops every table first!)
python run.py                      # http://127.0.0.1:5000  (the home page is the login page)
```

Demo accounts all use the password `Demo@1234` (usernames: manager, customer, field, clerk,
buyingmanager, receiver, finance, inputs, relations, it). Without `demo_seed.py` the app starts
with the departments, permissions and roles only; create the first account with:

```bash
python - <<'PY'
from app import create_app
from app.models import Role, Department
from app.services import create_staff
from app import db
app = create_app()
with app.app_context():
    it = Role.query.filter_by(name="IT Manager").one()
    dept = Department.query.filter_by(name="IT and Information Systems").one()
    create_staff(None, first_name="First", last_name="Admin", username="admin", password="CHANGE-ME-now1",
                 department_id=dept.id, role_ids=[it.id])
    db.session.commit()
PY
```

**Database from the old version:** the tables changed completely. The app refuses to start on an old
database and tells you so. For demo data use `python demo_seed.py --reset`. Real data needs a migration.

## Settings (environment variables)

`SECRET_KEY`, `DATABASE_URL`, `FACTORY_NAME`, `FACTORY_CODE` (prefix of farm numbers, e.g. `CY039001`),
`ALLOW_MANUAL_WEIGHT` (1 lets a clerk key in a weight while no scale is connected; every such weight is
flagged MANUAL. Set 0 once scales are live), `WEIGHT_MAX_AGE_SECONDS`, `MAX_SINGLE_WEIGHT_KG`,
`SESSION_COOKIE_SECURE=1` on HTTPS, `TRUST_PROXY=1` behind Render's proxy.

## Connecting a weighing scale

Register the scale under **Scales** (its key is shown once). A small program next to the scale posts each
*stable* weight:

```
POST /api/scale/reading
X-Scale-Key: <the scale's key>
{"scale_identifier": "SC-001", "weight_kg": 18.4}
```

## Clerk mobile app API

`POST /api/login` (`username` + `pin`, set with `flask --app run set-pin <username> <pin>` or on the Staff page)
returns a bearer token. Then `GET /api/me`, `GET /api/farm/<farm_number>?buying_centre_id=`,
`GET /api/weight/latest?buying_centre_id=`, `POST /api/buy`, `GET /api/transactions/today`.

## Tests

```bash
python -m unittest discover tests
```
The rule tests need nothing. `tests/test_flow.py` runs the whole buying flow against in-memory SQLite and
needs the packages in `requirements.txt`.

## Not built yet (Phase 2)

Cases and case workflow, delegation screens (the table and permission check already work),
finance statements, farmer-facing app screens, SMS (`app/sms.py` is still a stub), and database migrations.
