"""Database housekeeping used at start-up and by the seed scripts."""
from sqlalchemy import MetaData, inspect

from app import db


def schema_problems():
    """Tables that already exist in the database but are missing columns the code now expects.

    db.create_all() only creates MISSING tables — it never alters existing ones.
    A database left over from an older version of the app therefore fails in
    confusing ways ('no such column'). Catching it here gives one clear message.
    """
    inspector = inspect(db.engine)
    existing = set(inspector.get_table_names())
    problems = []
    for table in db.metadata.sorted_tables:
        if table.name not in existing:
            continue
        have = {column["name"] for column in inspector.get_columns(table.name)}
        missing = sorted({column.name for column in table.columns} - have)
        if missing:
            problems.append(f"table '{table.name}' is missing columns {missing}")
    return problems


def legacy_tables():
    """Tables in the database that this version of the app no longer uses (leftovers from the old schema)."""
    known = {table.name for table in db.metadata.sorted_tables}
    return sorted(set(inspect(db.engine).get_table_names()) - known)


def drop_everything():
    """Drop EVERY table in the database — including old ones the current code doesn't know about.

    db.drop_all() only drops tables the current models define, so a leftover
    table from an older version that points at 'farmers' would block the drop.
    Reflecting the real database first avoids that. Use only on a database
    dedicated to this app.
    """
    meta = MetaData()
    meta.reflect(bind=db.engine)
    meta.drop_all(bind=db.engine)
