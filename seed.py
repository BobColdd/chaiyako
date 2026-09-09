"""
One-time setup script: creates the Factory record and the first Manager
account, since there's no self-signup for factory staff (by design — only
an existing manager can create more staff accounts, and the very first one
has to come from somewhere).

Run once, locally or via Render's shell:
    python seed.py

Safe to re-run — it skips anything that already exists.
"""

import getpass
from app import create_app, db
from app.factory_models import Factory, FactoryUser, BuyingCenter, Route

app = create_app()

print(f"Using database: {app.config['SQLALCHEMY_DATABASE_URI']}")
print("(Make sure run.py resolves to this same URI, or your new manager")
print(" account won't be visible when you actually run the app.)\n")

with app.app_context():
    factory = Factory.query.first()
    if not factory:
        print("No factory found — let's create one.")
        name = input("Factory name (e.g. Chai Yako Tea Factory): ").strip() or "Chai Yako Tea Factory"
        code = input("Factory code, used in farm numbers (e.g. CY): ").strip().upper() or "CY"
        factory = Factory(name=name, code=code)
        db.session.add(factory)
        db.session.commit()
        print(f"Created factory: {factory.name} ({factory.code})")
    else:
        print(f"Factory already exists: {factory.name} ({factory.code})")

    if not FactoryUser.query.filter_by(role="manager").first():
        print("\nNo manager account yet — let's create the first one.")
        full_name = input("Manager full name: ").strip() or "Factory Manager"
        username = input("Manager username: ").strip().lower() or "manager"
        password = getpass.getpass("Manager password: ") or "changeme123"

        manager = FactoryUser(factory_id=factory.id, full_name=full_name, username=username, role="manager")
        manager.set_password(password)
        db.session.add(manager)
        db.session.commit()
        print(f"Created manager account '{username}'. Log in at /factory/login.")
    else:
        print("A manager account already exists — skipping.")

    if not BuyingCenter.query.filter_by(factory_id=factory.id).first():
        make_sample = input("\nNo buying centers yet. Create a sample route + 2 centers? [y/N]: ").strip().lower()
        if make_sample == "y":
            route = Route(factory_id=factory.id, name="Route A — Sample Loop")
            db.session.add(route)
            db.session.flush()
            db.session.add(BuyingCenter(factory_id=factory.id, route_id=route.id, name="Kapsuser Buying Center", code="039"))
            db.session.add(BuyingCenter(factory_id=factory.id, route_id=route.id, name="Cheborge Buying Center", code="041"))
            db.session.commit()
            print("Sample route and 2 buying centers created.")

print("\nDone. Farmers can be registered from the manager dashboard (Farmers → Register).")
