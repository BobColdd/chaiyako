"""Fill the database with demo data.

    python demo_seed.py            add demo data to an empty (or fresh) database
    python demo_seed.py --reset    DROP EVERY TABLE first, then rebuild with demo data

Never run --reset against a database that holds real records.
All demo accounts use the password printed at the end.
"""
import random
import sys
from datetime import datetime, time, timedelta

from sqlalchemy import MetaData, create_engine

from config import Config

DEMO_PASSWORD = "Demo@1234"

if "--reset" in sys.argv:
    engine = create_engine(Config.SQLALCHEMY_DATABASE_URI)
    meta = MetaData()
    meta.reflect(bind=engine)
    meta.drop_all(bind=engine)
    engine.dispose()
    print("All existing tables dropped.")

from app import create_app, db, rules                       # noqa: E402
from app.catalogue import role_home_department               # noqa: E402
from app.models import (                                     # noqa: E402
    BuyingCentre, Complaint, Department, FarmVerification, Farmer, FertilizerDistribution, Notice,
    QualityRecord, Receipt, Role, TeaTransaction, User,
)
from app.services import (                                   # noqa: E402
    create_centre, create_scale, create_staff, next_sequence, register_farmer, verify_farm,
)
from app.timeutil import today_utc, utcnow                   # noqa: E402

app = create_app()

STAFF = [
    ("Grace", "Wanjiru", "manager", "Factory Manager"),
    ("Peter", "Kiptoo", "customer", "Customer Service Officer"),
    ("Mary", "Chebet", "field", "Field Officer"),
    ("Samuel", "Rono", "clerk", "Tea Buying Clerk"),
    ("Joyce", "Langat", "buyingmanager", "Tea Buying Manager"),
    ("David", "Koech", "receiver", "Tea Receiver"),
    ("Ann", "Mutai", "finance", "Finance Officer"),
    ("Brian", "Kirui", "inputs", "Inputs Officer"),
    ("Lucy", "Njeri", "relations", "Farmer Relations Officer"),
    ("Kevin", "Ochieng", "it", "IT Manager"),
]
CENTRES = [("Kapsuser", "039"), ("Kapkatet", "040"), ("Litein", "041"), ("Kabianga", "042")]
FARMERS = [
    ("John", "Kiprop"), ("Sarah", "Jepkorir"), ("Daniel", "Kimutai"), ("Esther", "Chepkoech"),
    ("Moses", "Kemboi"), ("Ruth", "Jelagat"), ("Isaac", "Sang"), ("Naomi", "Cherono"),
    ("Paul", "Bett"), ("Alice", "Tanui"), ("James", "Ruto"), ("Faith", "Chepngetich"),
    ("Peter", "Langat"), ("Grace", "Chepkemoi"), ("Vincent", "Korir"), ("Mercy", "Jerop"),
    ("Dennis", "Kigen"), ("Linet", "Chepkirui"), ("Stephen", "Maiyo"), ("Agnes", "Cheruto"),
    ("Erick", "Kibet"), ("Beatrice", "Jebet"), ("Hillary", "Rotich"), ("Purity", "Chelangat"),
]
HISTORY_DAYS = 180  # about 6 months, so week / month / 90-day / 180-day views all have real data
# Slightly different buying pace per centre, so "today vs yesterday" and the trend column aren't flat
CENTRE_PACE = {0: 1.15, 1: 0.90, 2: 1.05, 3: 0.80}


def main():
    with app.app_context():
        if User.query.count():
            print("The database already has accounts. Use --reset to rebuild it from scratch.")
            return
        rng = random.Random(2026)

        roles = {r.name: r for r in Role.query.all()}
        departments = {d.name: d for d in Department.query.all()}
        users = {}
        for first, last, username, role in STAFF:
            users[username] = create_staff(
                None, first_name=first, last_name=last, username=username, password=DEMO_PASSWORD,
                department_id=departments[role_home_department(role)].id, role_ids=[roles[role].id],
                pin="1234" if username == "clerk" else "")
        db.session.commit()

        centres = [create_centre(None, name=f"{n} Buying Centre", code=c, location=n) for n, c in CENTRES]
        db.session.commit()
        scale, key = create_scale(None, centre_id=centres[0].id, scale_identifier="SC-001", model="Demo scale")
        db.session.commit()

        # Farmers, all verified except the last two (so the field queue has work).
        farmers = []
        for i, (first, last) in enumerate(FARMERS):
            centre = centres[i % len(centres)]
            farmer, farm = register_farmer(
                users["customer"], first_name=first, last_name=last, phone=f"07{12000000 + i * 137}",
                buying_centre_id=centre.id, tea_bushes=rng.randint(400, 2500), location=f"{centre.location} ridge")
            db.session.flush()
            if i < len(FARMERS) - 2:
                verification = FarmVerification.query.filter_by(farm_id=farm.id).first()
                verify_farm(users["field"], verification, decision="VERIFIED", visit_date=today_utc(),
                            observations="Demo visit", tea_bushes=farm.tea_bushes)
            farmers.append(farmer)
        db.session.commit()

        # About six months of deliveries, straight into the tables (history, so no scale needed).
        # A gentle upward trend plus per-centre pace and weekday shape, so every chart — the
        # 14/30/90/180-day line, the today-vs-yesterday bars, and the week/month totals — has
        # something real to draw, whichever buying centre or "all centres" is selected.
        clerk = users["clerk"]
        today = today_utc()
        farmer_centre_index = {f.id: i % len(centres) for i, f in enumerate(farmers)}
        for back in range(HISTORY_DAYS - 1, -1, -1):
            day = today - timedelta(days=back)
            if day.weekday() == 6:      # no buying on Sundays in the demo
                continue
            season = 0.85 + 0.30 * (HISTORY_DAYS - back) / HISTORY_DAYS       # slow growth over the 6 months
            weekday_factor = 1.15 if day.weekday() in (0, 1) else (0.9 if day.weekday() == 5 else 1.0)
            stamp = day.strftime("%Y%m%d")
            for farmer in farmers:
                farm = farmer.farms[0]
                if not farm.is_verified:
                    continue
                pace = CENTRE_PACE.get(farmer_centre_index[farmer.id], 1.0)
                if rng.random() > 0.78 * pace * weekday_factor:
                    continue
                moment = datetime.combine(day, time(rng.randint(5, 18), rng.randint(0, 59)))
                if moment > utcnow():
                    continue
                weight = round(rng.uniform(12, 95) * season * pace, 1)
                tx = TeaTransaction(
                    transaction_number=rules.format_transaction_number(moment, next_sequence(f"transaction:{stamp}")),
                    farmer_id=farmer.id, farm_id=farm.id, buying_centre_id=farmer.buying_centre_id,
                    clerk_employee_id=clerk.employee_id, weight_kg=weight,
                    transaction_time=moment, source="SCALE")
                db.session.add(tx)
                db.session.flush()
                db.session.add(Receipt(
                    receipt_number=rules.format_receipt_number(moment, next_sequence(f"receipt:{stamp}")),
                    transaction_id=tx.id, farmer_id=farmer.id, issued_at=moment, print_count=1))
            if back % 20 == 0:
                db.session.flush()
        db.session.commit()

        # A few deliveries already recorded today, so "Today" isn't 0.0 kg the moment you seed.
        now = utcnow()
        stamp = today.strftime("%Y%m%d")
        for farmer in farmers:
            farm = farmer.farms[0]
            if not farm.is_verified or rng.random() > 0.4:
                continue
            moment = now - timedelta(minutes=rng.randint(5, 240))
            if moment.date() != today:
                continue
            tx = TeaTransaction(
                transaction_number=rules.format_transaction_number(moment, next_sequence(f"transaction:{stamp}")),
                farmer_id=farmer.id, farm_id=farm.id, buying_centre_id=farmer.buying_centre_id,
                clerk_employee_id=clerk.employee_id, weight_kg=round(rng.uniform(12, 95), 1),
                transaction_time=moment, source="SCALE")
            db.session.add(tx)
            db.session.flush()
            db.session.add(Receipt(
                receipt_number=rules.format_receipt_number(moment, next_sequence(f"receipt:{stamp}")),
                transaction_id=tx.id, farmer_id=farmer.id, issued_at=moment, print_count=1))
        db.session.commit()

        # Quality grades for the last 30 days at every centre, so the quality-mix chart has data too.
        for c in centres:
            for back in range(0, 30):
                day = today - timedelta(days=back)
                if day.weekday() == 6:
                    continue
                db.session.add(QualityRecord(
                    buying_centre_id=c.id, date=day, grade=rng.choice(["good", "good", "good", "average", "poor"]),
                    recorded_by_id=users["receiver"].employee_id))
        db.session.add(Complaint(farmer_id=farmers[0].id, category="weighing",
                                 description="The weight on my receipt looks lower than what I delivered."))
        db.session.add(FertilizerDistribution(buying_centre_id=centres[0].id, recorded_by_id=users["inputs"].employee_id,
                                              fertilizer_type="NPK 25:5:5", quantity_kg=50, notes="Demo entry"))
        mgr = users["manager"].employee
        db.session.add_all([
            Notice(audience="PUBLIC", category="price", title="Green leaf rate for this month",
                   content="The provisional rate will be announced at each buying centre.", posted_by_id=mgr.id),
            Notice(audience="STAFF", category="general", title="Staff meeting Friday",
                   content="All departments, 8:00 am at the boardroom.", posted_by_id=mgr.id),
            Notice(audience="DEPARTMENT", department_id=departments["Tea Buying and Operations"].id, category="schedule",
                   title="Weighing scale calibration", content="Scales are checked on Thursday morning.", posted_by_id=mgr.id),
        ])
        db.session.commit()

        print("\nDemo data created.")
        print(f"  Password for every account: {DEMO_PASSWORD}")
        print("  Usernames: " + ", ".join(u for _, _, u, _ in STAFF))
        print("  Clerk mobile PIN: 1234 (username 'clerk')")
        print(f"  Scale SC-001 (Kapsuser) key, shown once: {key}")


main()