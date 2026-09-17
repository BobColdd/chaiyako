"""
Non-interactive demo data loader — creates a fully working sample factory so
you can log in to every role immediately, without going through seed.py's
interactive prompts.

Scale of this demo:
    1  factory
    1  manager
    5  tallyboys / clerks
    12 routes
    24 buying centers (2 per route)
    50 farmers (~60 farms — 10 farmers have a second plot)
    ~350 historical purchases + a handful of "live today" purchases
    notices, fertilizer distributions, and complaints for realism

Run once (locally against SQLite, or on Render against Postgres):
    python demo_seed.py

Run again after data already exists and it's a no-op. Force a completely
fresh rebuild (drops all tables first) with:
    python demo_seed.py --reset

On Render: open the web service's Shell tab (or `render ssh`) after your
first deploy and run `python demo_seed.py` there — it uses the exact same
DATABASE_URL your app is running with, so the data lands in your Render
Postgres instance, not a local file.

If seeding fails partway (most commonly because an old teafarm.db from an
earlier version of this app has a stale schema), the error is caught and a
plain-language fix is printed instead of a bare traceback.
"""

import sys
import random
from datetime import datetime, timedelta, date

from app import create_app, db
from app.models import Farmer, Farm
from app.factory_models import (
    Factory, FactoryUser, BuyingCenter, Route, RouteAssignment,
    ClerkSession, Purchase, Notice, FertilizerDistribution, Complaint,
)

random.seed(42)  # reproducible demo data across runs

# ----------------------------------------------------------------------------
# Static demo data: names, routes, centers
# ----------------------------------------------------------------------------

TALLYBOYS = [
    # (full_name, username, pin)
    ("John Kiplagat", "clerk1", "1234"),
    ("Mary Jepkosgei", "clerk2", "5678"),
    ("Vincent Kiptoo", "clerk3", "2468"),
    ("Beatrice Chelangat", "clerk4", "1357"),
    ("Erick Kiprono", "clerk5", "9090"),
]

# 12 routes, each with 2 buying centers — named after real tea-growing
# localities around Kericho/Bomet/Kisii so it reads naturally.
ROUTES = [
    ("Route A — Kapsuser Loop",      [("Kapsuser Buying Center", "039"),  ("Cheborge Buying Center", "041")]),
    ("Route B — Kapkatet Circuit",   [("Kapkatet Buying Center", "011"),  ("Cheplanget Buying Center", "012")]),
    ("Route C — Litein Loop",        [("Litein Buying Center", "021"),    ("Kapkoros Buying Center", "022")]),
    ("Route D — Kaptumo Circuit",    [("Kaptumo Buying Center", "031"),   ("Kapsimotwa Buying Center", "032")]),
    ("Route E — Sotik Loop",         [("Sotik Town Buying Center", "051"), ("Ndanai Buying Center", "052")]),
    ("Route F — Bureti Circuit",     [("Kapkwen Buying Center", "061"),   ("Chemosot Buying Center", "062")]),
    ("Route G — Chepseon Loop",      [("Chepseon Buying Center", "071"),  ("Kabng'etuny Buying Center", "072")]),
    ("Route H — Kipkelion Circuit",  [("Kipkelion Buying Center", "081"), ("Chepkorio Buying Center", "082")]),
    ("Route I — Kabianga Loop",      [("Kabianga Buying Center", "091"),  ("Kericho Town Buying Center", "092")]),
    ("Route J — Fort Ternan Circuit",[("Fort Ternan Buying Center", "101"), ("Muhoroni Border Buying Center", "102")]),
    ("Route K — Belgut Loop",        [("Cheplin Buying Center", "111"),   ("Kabosgei Buying Center", "112")]),
    ("Route L — Roret Circuit",      [("Roret Buying Center", "121"),     ("Kimulot Buying Center", "122")]),
]

# Which clerk currently runs which routes (index into TALLYBOYS, 0-based).
# Every route gets an active tallyboy; some tallyboys cover more than one
# route (realistic — 5 people, 12 routes).
ROUTE_CLERK_MAP = [0, 0, 0, 1, 1, 1, 2, 2, 3, 3, 4, 4]

# 50 farmer full names — mix of common given names + Kalenjin surnames
# typical of the Kericho tea belt, for a believable demo.
GIVEN_NAMES = [
    "Samuel", "John", "Peter", "David", "James", "Joseph", "Daniel", "Paul",
    "Moses", "Elijah", "Kennedy", "Wilson", "Geoffrey", "Robert", "Stephen",
    "Edwin", "Dennis", "Nicholas", "Alice", "Mary", "Grace", "Jane", "Ruth",
    "Esther", "Naomi", "Sarah", "Beatrice", "Lydia", "Winnie", "Caroline",
    "Milka", "Joyce", "Agnes", "Purity", "Faith", "Rebecca", "Emily", "Susan",
    "Anne", "Judith", "Collins", "Bernard", "Titus", "Cyrus", "Evans",
    "Duncan", "Zipporah", "Consolata", "Phyllis", "Everlyne",
]
SURNAMES = [
    "Rono", "Kiplagat", "Jepkosgei", "Chepkoech", "Kirui", "Sang", "Cheruiyot",
    "Kosgei", "Bett", "Langat", "Rotich", "Chepkurui", "Chelangat", "Jelagat",
    "Kiprono", "Kiptoo", "Chebet", "Jepchumba", "Kandie", "Towett", "Kiptum",
    "Kimaiyo", "Cheptoo", "Jepkemboi", "Kigen", "Kirwa", "Yego", "Kemboi",
    "Tanui", "Koech", "Cheserek", "Ngeno", "Kiptanui", "Chepkwony", "Sigei",
    "Maiyo", "Bore", "Chepngeno", "Kandagor", "Barsosio",
]


def _make_farmer_names(n):
    names = []
    used = set()
    i = 0
    while len(names) < n:
        given = GIVEN_NAMES[i % len(GIVEN_NAMES)]
        surname = SURNAMES[(i * 7) % len(SURNAMES)]  # decorrelate the two cycles
        full = f"{given} {surname}"
        if full in used:
            i += 1
            continue
        used.add(full)
        names.append(full)
        i += 1
    return names


def _seed():
    # --- Factory ---
    factory = Factory(name="Chai Yako Tea Factory", code="CY")
    db.session.add(factory)
    db.session.flush()

    # --- Manager (1) ---
    manager = FactoryUser(factory_id=factory.id, full_name="Grace Chebet", username="manager", role="manager")
    manager.set_password("manager123")
    db.session.add(manager)

    # --- Tallyboys / clerks (5) ---
    clerks = []
    for full_name, username, pin in TALLYBOYS:
        clerk = FactoryUser(factory_id=factory.id, full_name=full_name, username=username, role="clerk")
        clerk.set_password("clerk123")
        clerk.set_pin(pin)
        clerks.append(clerk)
    db.session.add_all(clerks)
    db.session.flush()

    # --- Routes (12) + buying centers (24) ---
    routes = []
    centers = []  # flat list, in the same order as ROUTES, 2 per route
    for route_name, center_defs in ROUTES:
        route = Route(factory_id=factory.id, name=route_name)
        db.session.add(route)
        db.session.flush()
        routes.append(route)
        for center_name, code in center_defs:
            center = BuyingCenter(factory_id=factory.id, route_id=route.id, name=center_name, code=code)
            db.session.add(center)
            centers.append(center)
    db.session.flush()

    # --- Route assignments: give every route a currently-active tallyboy ---
    today = date.today()
    for route, clerk_idx in zip(routes, ROUTE_CLERK_MAP):
        start = today - timedelta(days=random.randint(20, 200))
        db.session.add(RouteAssignment(route_id=route.id, clerk_id=clerks[clerk_idx].id, start_date=start))
    # A little rotation history for realism: clerk3 used to run Route G before clerk3's own current route
    db.session.add(RouteAssignment(
        route_id=routes[6].id, clerk_id=clerks[4].id,
        start_date=today - timedelta(days=260), end_date=today - timedelta(days=201),
    ))
    db.session.flush()

    # --- Farmers (50) + farms (~58, some farmers have 2 plots) ---
    farmer_names = _make_farmer_names(50)
    farmers = []
    for idx, full_name in enumerate(farmer_names, start=1):
        phone = f"0712345{idx:03d}"
        scale = "large" if random.random() < 0.15 else "small"
        farmer = Farmer(full_name=full_name, phone=phone, scale=scale)
        farmers.append(farmer)
    db.session.add_all(farmers)
    db.session.flush()

    # local farm-number counters per center, mirroring BuyingCenter.generate_farm_number()
    seq_by_center = {c.id: 0 for c in centers}

    def new_farm(farmer, center, location, scale):
        seq_by_center[center.id] += 1
        farm_number = f"{factory.code}{center.code}{seq_by_center[center.id]:03d}"
        if scale == "large":
            bushes, acreage = random.randint(4000, 9000), round(random.uniform(3.0, 8.0), 1)
        else:
            bushes, acreage = random.randint(800, 3500), round(random.uniform(0.3, 2.5), 1)
        farm = Farm(farmer_id=farmer.id, buying_center_id=center.id, farm_number=farm_number,
                    location=location, approx_bushes=bushes, acreage=acreage)
        db.session.add(farm)
        return farm

    farms = []
    for idx, farmer in enumerate(farmers):
        center = centers[idx % len(centers)]  # spread evenly across all 24 centers
        location = center.name.replace(" Buying Center", "")
        farm = new_farm(farmer, center, location, farmer.scale)
        farms.append(farm)
        # ~1 in 5 farmers has a second plot, at a different (nearby) center
        if idx % 5 == 0:
            second_center = centers[(idx + 3) % len(centers)]
            farm2 = new_farm(farmer, second_center, f"{second_center.name.replace(' Buying Center', '')} (second plot)",
                              farmer.scale)
            farms.append(farm2)
    db.session.flush()

    # --- Live "green mark" demo: 3 clerks currently logged in and buying ---
    live_pairs = [(clerks[0], centers[0]), (clerks[1], centers[6]), (clerks[3], centers[16])]
    for clerk, center in live_pairs:
        db.session.add(ClerkSession(clerk_id=clerk.id, buying_center_id=center.id,
                                     login_at=datetime.utcnow() - timedelta(minutes=random.randint(15, 90))))
    db.session.flush()

    # --- Purchases: ~6 per farm over the last 21 days, plus a few "today" ---
    receipt_counter = 0

    def next_receipt():
        nonlocal receipt_counter
        receipt_counter += 1
        return f"RCPT-DEMO-{receipt_counter:05d}"

    # map each buying center to a clerk who could plausibly have served it
    # (whichever clerk currently runs that center's route)
    clerk_by_center = {}
    for route, clerk_idx in zip(routes, ROUTE_CLERK_MAP):
        for c in centers:
            if c.route_id == route.id:
                clerk_by_center[c.id] = clerks[clerk_idx]

    for farm in farms:
        clerk = clerk_by_center.get(farm.buying_center_id, clerks[0])
        farmer = next(f for f in farmers if f.id == farm.farmer_id)
        kilo_range = (20, 60) if farmer.scale == "large" else (8, 28)
        n_purchases = random.randint(5, 7)
        for i in range(n_purchases, 0, -1):
            days_ago = random.randint(1, 21)
            day = datetime.utcnow() - timedelta(days=days_ago)
            kilos = round(random.uniform(*kilo_range), 1)
            db.session.add(Purchase(
                receipt_number=next_receipt(), farm_id=farm.id, buying_center_id=farm.buying_center_id,
                clerk_id=clerk.id, kilos=kilos,
                purchased_at=day.replace(hour=random.randint(8, 16), minute=random.randint(0, 59)),
            ))

    # A handful of purchases recorded "today" at the live centers, for the live demo
    for clerk, center in live_pairs:
        farm_here = next((f for f in farms if f.buying_center_id == center.id), None)
        if farm_here:
            db.session.add(Purchase(
                receipt_number=next_receipt(), farm_id=farm_here.id, buying_center_id=center.id,
                clerk_id=clerk.id, kilos=round(random.uniform(15, 40), 1),
                purchased_at=datetime.utcnow() - timedelta(minutes=random.randint(5, 60)),
            ))

    db.session.flush()

    # --- Notices (mix of scopes/categories) ---
    db.session.add(Notice(factory_id=factory.id, posted_by_id=manager.id, category="price",
                           title="Green leaf price update",
                           content="This month's green leaf rate has been announced by KTDA. Check with your buying center clerk for the current rate."))
    db.session.add(Notice(factory_id=factory.id, posted_by_id=manager.id, category="bonus",
                           title="Second payment bonus announced",
                           content="The factory board has approved this year's second payment bonus. Details will be shared at your buying center."))
    db.session.add(Notice(factory_id=factory.id, posted_by_id=manager.id, category="fertilizer",
                           buying_center_id=centers[0].id, title="NPK fertilizer available at Kapsuser",
                           content="NPK 25:5:5 is now available for collection at Kapsuser Buying Center. Bring your farm card."))
    db.session.add(Notice(factory_id=factory.id, posted_by_id=manager.id, category="route_change",
                           title="Route K schedule change",
                           content="Route K (Belgut Loop) lorry will now arrive an hour earlier starting next week."))
    db.session.add(Notice(factory_id=factory.id, posted_by_id=manager.id, category="general",
                           title="Farmer training day",
                           content="A free training on good agricultural practices will be held at Kericho Town Buying Center next Saturday."))
    db.session.add(Notice(factory_id=factory.id, posted_by_id=manager.id, category="fertilizer",
                           buying_center_id=centers[10].id, title="CAN top-dressing available at Litein",
                           content="CAN top-dressing fertilizer is available for collection at Litein Buying Center this week."))

    # --- Fertilizer distributions (~15) ---
    fert_types = ["NPK 25:5:5", "CAN Top-dressing", "NPK 20:10:10", "Foliar feed"]
    for i in range(15):
        center = centers[i % len(centers)]
        farmer = random.choice(farmers) if i % 3 != 0 else None  # some are general "available" entries
        db.session.add(FertilizerDistribution(
            buying_center_id=center.id, farmer_id=farmer.id if farmer else None, recorded_by_id=manager.id,
            fertilizer_type=random.choice(fert_types),
            quantity_kg=round(random.uniform(25, 100), 0) if farmer else None,
            date=date.today() - timedelta(days=random.randint(1, 30)),
            notes=None if farmer else "Available for collection this week",
        ))

    # --- Complaints (8, mixed status) ---
    complaint_categories = ["short_weight", "harassment", "unfair_terms", "other"]
    complaint_texts = [
        "I believe my tea was under-weighed compared to what I brought.",
        "The clerk was rude when I asked about the weighing scale.",
        "I was told a different rate than what was posted at the center.",
        "My fertilizer collection was delayed without explanation.",
        "The lorry did not arrive on the scheduled day.",
        "I was asked to wait much longer than other farmers at the center.",
        "My receipt number does not match what I was told verbally.",
        "I have a general concern about how disputes are handled at my center.",
    ]
    for i in range(8):
        farmer = random.choice(farmers)
        status = "resolved" if i % 2 == 0 else "open"
        created = datetime.utcnow() - timedelta(days=random.randint(2, 25))
        complaint = Complaint(
            farmer_id=farmer.id, category=complaint_categories[i % len(complaint_categories)],
            description=complaint_texts[i], status=status, created_at=created,
        )
        if status == "resolved":
            complaint.manager_note = "Reviewed and addressed with the buying center team."
            complaint.resolved_at = created + timedelta(days=random.randint(1, 4))
        db.session.add(complaint)

    db.session.commit()

    print("Demo data created!\n")
    print("=" * 60)
    print("FACTORY STAFF LOGIN — visit /factory/login")
    print("=" * 60)
    print("  Manager -> visit /factory/manager/login")
    print("    username: manager   password: manager123")
    print("  Clerks  -> visit /factory/clerk/login")
    for full_name, username, pin in TALLYBOYS:
        print(f"  {full_name:<20} -> username: {username}   pin: {pin}")
    print()
    print(f"  {len(farmers)} demo farmers created (registered by the manager, no login of their own).")
    print()
    print("Verify it actually landed:")
    print(f"  Factories: {Factory.query.count()}  Staff: {FactoryUser.query.count()}  "
          f"Routes: {Route.query.count()}  Buying centers: {BuyingCenter.query.count()}")
    print(f"  Farmers: {Farmer.query.count()}  Farms: {Farm.query.count()}  Purchases: {Purchase.query.count()}")


def main():
    app = create_app()

    print(f"Using database: {app.config['SQLALCHEMY_DATABASE_URI']}")
    print("(This MUST match what run.py uses, or logins will look like they")
    print(" 'don't exist' even though seeding succeeded. If you set DATABASE_URL")
    print(" or edited .env, confirm both this script and run.py print the same URI.)\n")

    with app.app_context():
        existing = Factory.query.filter_by(code="CY").first() if _tables_exist() else None

        if existing and "--reset" not in sys.argv:
            print("Demo data already exists (factory code 'CY' found). Nothing to do.")
            print("For a completely fresh demo: python demo_seed.py --reset")
            return

        if "--reset" in sys.argv:
            print("--reset passed: dropping all tables first...")
            db.drop_all()

        # create_all() only creates tables that don't exist yet — it does NOT
        # add new columns to a table left over from an older schema. If you
        # hit a "no such column" error below, that's exactly what happened:
        # delete teafarm.db (or drop your Postgres tables) and re-run.
        db.create_all()

        print("Seeding demo factory, staff, routes, centers, farmers, and sample activity...\n")
        try:
            _seed()
        except Exception:
            db.session.rollback()
            print("\n" + "=" * 60)
            print("SEEDING FAILED — see the error above this line.")
            print("If it mentions a missing column or table, your database file")
            print("has an old schema left over from a previous run. Fix:")
            print("  1. Stop the app.")
            print("  2. Delete teafarm.db (SQLite), or drop all tables in your")
            print("     Postgres database (see schema.sql).")
            print("  3. Re-run: python demo_seed.py")
            print("=" * 60 + "\n")
            raise


def _tables_exist():
    """True if the factories table already exists — avoids querying a table
    that might not have been created yet on a totally fresh database."""
    try:
        from sqlalchemy import inspect
        return "factories" in inspect(db.engine).get_table_names()
    except Exception:
        return False


if __name__ == "__main__":
    main()