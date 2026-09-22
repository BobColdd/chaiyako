"""Show what is in the database (row counts) and warn about leftovers from the old schema."""
from app import create_app, db, dbtools

app = create_app()
with app.app_context():
    for table in db.metadata.sorted_tables:
        count = db.session.execute(db.select(db.func.count()).select_from(table)).scalar()
        print(f"{table.name:28} {count}")
    old = dbtools.legacy_tables()
    if old:
        print("\nTables from an older version of the app (unused now):", ", ".join(old))
