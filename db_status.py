"""
Read-only database check — shows which tables exist and how many rows are
in each, using the exact same DATABASE_URL your app/demo_seed.py already
use. Never writes anything; safe to run any time, including mid-recovery
from an interrupted --reset.

Run:
    python db_status.py
"""

from sqlalchemy import inspect

from app import create_app, db

# Every table this app knows how to create, in a sensible dependency order
# (roughly: least-depended-on first) so the printout reads top-to-bottom
# the way you'd want to check it.
EXPECTED_TABLES = [
    "factories", "factory_users", "routes", "route_assignments",
    "buying_centers", "clerk_sessions", "farmers", "farms", "purchases",
    "notices", "fertilizer_distributions", "complaints", "quality_records",
]


def main():
    app = create_app()
    print(f"Using database: {app.config['SQLALCHEMY_DATABASE_URI']}\n")

    with app.app_context():
        inspector = inspect(db.engine)
        existing_tables = set(inspector.get_table_names())

        if not existing_tables:
            print("No tables exist at all — the database is completely empty.")
            print("This is consistent with --reset's db.drop_all() succeeding")
            print("but create_all() / the seed not having run (or also failing).")
            print("\nNext step: just run `python demo_seed.py` again (no --reset")
            print("needed now, since there's nothing to drop).")
            return

        print(f"{'Table':<28}{'Exists?':<10}{'Row count'}")
        print("-" * 50)
        any_rows = False
        for table in EXPECTED_TABLES:
            if table not in existing_tables:
                print(f"{table:<28}{'MISSING':<10}-")
                continue
            count = db.session.execute(db.text(f"SELECT COUNT(*) FROM {table}")).scalar()
            if count:
                any_rows = True
            print(f"{table:<28}{'yes':<10}{count}")

        unexpected = existing_tables - set(EXPECTED_TABLES)
        if unexpected:
            print(f"\nOther tables present (not part of this app's models): {sorted(unexpected)}")

        print()
        if not any_rows:
            print("All tables exist but are EMPTY — schema was (re)created but no")
            print("seed data landed. Safe to run: python demo_seed.py")
        elif "purchases" in existing_tables:
            oldest = db.session.execute(db.text("SELECT MIN(purchased_at) FROM purchases")).scalar()
            newest = db.session.execute(db.text("SELECT MAX(purchased_at) FROM purchases")).scalar()
            print(f"Purchases span: {oldest} to {newest}")
            print("\nIf that range looks like the OLD short demo (not ~182 days back),")
            print("or if any table above looks partially filled compared to the others,")
            print("the safest fix is: python demo_seed.py --reset")


if __name__ == "__main__":
    main()
