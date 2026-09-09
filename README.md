# 🍃 Kericho Tea Tracker — Factory-Owned Edition

A factory-owned tea buying and accountability platform, built to make tea buying corruption-resistant
and give farmers data the instant their tea is weighed — no more relying solely on the factory's own
paper books, and no more listening for a lorry's horn to know it has arrived.

## Who uses this system

- **Farmers** — registered by the factory (not self-signup). See their official kilos sold, digital
  receipts, their buying center's live status, their route's current tallyboy, the notice board,
  fertilizer received, and can raise complaints.
- **Clerks / Tallyboys** — log in, select which buying center on their route they're working today,
  then scan farm cards and record kilos as tea is weighed. Their session going live/offline is what
  turns a buying center's "green mark" on and off.
- **Managers** — see every buying center in real time, register farmers, manage buying centers,
  routes, and tallyboy assignments, create staff accounts, post notices, log fertilizer distribution,
  and resolve complaints.

## The buying flow, end to end

1. A manager registers a farmer under a specific buying center. The system auto-generates their farm
   number (`{FactoryCode}{CenterCode}{Sequence}`, e.g. `CY039001`) and a barcode for their physical card.
2. The farmer gets a verification code (currently via the SMS stub in `app/sms.py` — see below) to set
   their own password.
3. A clerk logs in, picks their buying center for the day — that center now shows **live** to farmers
   and managers.
4. The clerk swipes the farmer's card (the scanning device types the farm number into a focused input,
   like a keyboard) and enters the weighed kilos.
5. The purchase is saved immediately: the farmer's dashboard updates, a digital receipt is created and
   stored permanently, and the manager's live view reflects it — all without delay.
6. When the clerk logs out, their buying center goes quiet again.

## Feature list

- Factory-controlled farmer registration (no self-signup) with SMS-based first-login password setup
- Barcode-based farm cards (Code128, printable from the manager's "Farmer Card" view)
- Live buying-center status ("green mark") tied to clerk login/logout
- Instant purchase recording → immediate farmer dashboard update + permanent digital receipt
- Manager live dashboard: kilos bought today, per-center status, recent transactions across the factory
- Routes (stable groups of buying centers) with tallyboy rotation history
- Factory-wide or buying-center-specific notice board (prices, bonuses, fertilizer, route changes)
- Per-farmer AND center-wide fertilizer distribution log
- Farmer complaints (harassment, short-weighing, unfair terms, general) tied optionally to a specific
  receipt, with manager resolution (Open → Resolved)
- The original farmer self-tracking tools are kept alongside the official factory records: self-logged
  plucking diary, pruning history, tool inventory, notes, tea news page, weather card, and the
  rule-based farm assistant chatbot (including a personalized "overview" of your own records)

## First-time setup

```bash
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env            # edit SECRET_KEY if you like
```

**Option A — instant demo (recommended for trying it out):**

```bash
python demo_seed.py
python run.py
```

This creates a fully working sample factory in one shot — a manager, two clerks (one already
"live" at a buying center), two farmers with pre-set passwords, a route, two buying centers, ~12
sample purchases, notices, a fertilizer record, and a complaint. Credentials are printed to your
terminal when it finishes. Safe to run only once (checks for existing demo data first).

**Option B — build it up yourself from scratch:**

```bash
python seed.py                  # creates the Factory + first Manager account (interactive)
python run.py
```

Then:
1. Visit `/factory/login` and log in as the manager you just created.
2. Go to **Buying Centers** → add at least one center (and optionally a route).
3. Go to **Staff** → create a clerk/tallyboy account.
4. Go to **Routes & Tallyboys** → create a route, add centers to it, assign the clerk.
5. Go to **Farmers** → register a farmer under a center. Note the verification code printed to your
   server console (the SMS stub logs it there instead of sending a real text — see below).
6. Log in as the clerk at `/factory/login`, select the buying center, and record a purchase using the
   farm number from step 5.
7. Log in as the farmer at `/login` using their phone number — since no password is set yet, you'll be
   sent to `/verify` to enter the code and set one. Then see the dashboard update with that purchase.

## Going live with real SMS

`app/sms.py` currently just logs the message to the server console — it does **not** send real SMS.
To wire up real delivery (Africa's Talking is the standard choice in Kenya), see the instructions in
that file's docstring. This is deliberately decoupled so the rest of the app works end-to-end in
development without needing a paid SMS account yet.

## Deploying to Render

Same as before — `render.yaml` provisions both the web service and a free Postgres database. After
first deploy, run `python seed.py` via Render's shell to bootstrap the factory and manager account
(you'll need to run it non-interactively or adapt it to read from environment variables for a fully
automated deploy — the interactive prompts are meant for local/manual setup).

## Project structure (what's new)

```
app/
├── factory_models.py   # Factory, FactoryUser, BuyingCenter, Route, RouteAssignment,
│                       # ClerkSession, Purchase, Notice, FertilizerDistribution, Complaint
├── factory_auth.py     # staff login/logout (clerk + manager, shared login page)
├── manager.py          # all manager routes: dashboard, farmers, centers, routes, staff,
│                       # notices, fertilizer, complaints
├── clerk.py            # clerk routes: select buying center, record purchases
├── sms.py              # SMS stub — see docstring for going live
├── models.py           # Farmer (now factory-registered, SMS-verified), Farm (+ buying_center_id),
│                       # PluckingRecord, PruningRecord, Tool, Note  (self-tracking, unchanged)
├── auth.py             # farmer login + first-time password verification (self-signup removed)
├── main.py             # farmer dashboard + self-tracking + new: notices, receipts, complaints, fertilizer
templates/
├── factory/            # shared factory base template + staff login
├── manager/            # manager-only pages
├── clerk/              # clerk-only pages
seed.py                 # one-time bootstrap: creates Factory + first Manager account
```

## Troubleshooting

**"Demo logins don't work" / database looks empty:**

1. **Delete any old `teafarm.db`** in the project root before seeding. `db.create_all()` only
   creates tables that don't exist yet — it never adds new columns to a table left over from an
   earlier version of this app. If you ever ran a previous version of this project in the same
   folder, its old `teafarm.db` has a stale schema, and seeding will fail (or worse, silently skip
   rows) against it. `demo_seed.py` now prints a clear error if this happens, and:
   ```bash
   python demo_seed.py --reset
   ```
   forces a full drop-and-recreate of every table before seeding, which fixes this in one step.

2. **Check the printed "Using database: ..." line** — both `demo_seed.py` and `run.py` print the
   resolved `SQLALCHEMY_DATABASE_URI` on startup now. If they show different values (e.g. one
   points at local SQLite, the other at a Postgres URL from a stale environment variable), you're
   seeding one database and running against another. They must match.

3. `.env` is now actually loaded (it wasn't before — a real bug in the first version of this
   config, now fixed via `python-dotenv`'s `load_dotenv()` in `config.py`). If you'd previously set
   `DATABASE_URL` in `.env` and it seemed to have no effect, that's why — it now does.

If none of that explains it, run `python demo_seed.py` and share the exact output — the script now
reports row counts at the end (`Factories: 1  Staff: 3  Farmers: 2 ...`) and, on failure, the full
traceback plus a plain-language next step.

## Honest limitations / things worth improving next

- **SMS is stubbed**, not real — see above.
- The manager's fertilizer form asks for a farmer's internal database ID to target an individual
  farmer, which isn't very usable yet — a proper farmer picker/search would be a quick follow-up.
- No price/payment calculation by design (kilos only, per your instruction) — prices and bonuses are
  communicated via the notice board instead.
- Single-factory assumption in a few places (e.g. the open-complaints count on the manager dashboard
  isn't scoped by factory) — fine for one factory, would need tightening for true multi-factory use.
- I could not run this app live in this environment (no network access to install dependencies), so
  while every Python file compiles cleanly and every template has been parsed and validated with
  Jinja2, I'd recommend a real local smoke test (`python seed.py && python run.py`) before deploying.
