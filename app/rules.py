"""Pure business rules.

Nothing in this file touches the database or Flask, so every rule can be
unit-tested on its own (see tests/test_rules.py) and reused by both the web
pages and the mobile API.
"""
import hashlib
import hmac
import secrets

# ---------------------------------------------------------------- phone / text

def clean_phone(raw):
    """'0712 345-678' -> '0712345678'. Keeps a leading '+'. None if it can't be a phone number."""
    text = (raw or "").strip()
    plus = text.startswith("+")
    digits = "".join(ch for ch in text if ch.isdigit())
    if not 9 <= len(digits) <= 15:
        return None
    return ("+" if plus else "") + digits


def split_terms(query):
    """'Samuel  Rono' -> ['Samuel', 'Rono'] (used so a full name can be searched)."""
    return [t for t in (query or "").split() if t]


def pin_is_valid(pin):
    return bool(pin) and pin.isdigit() and 4 <= len(pin) <= 6


def password_problem(password):
    """None if the password is acceptable, else a sentence explaining why not."""
    if not password or len(password) < 8:
        return "Passwords need at least 8 characters."
    return None


# --------------------------------------------------------------------- weight

def parse_weight(value, max_kg):
    """Validate a weight and round it to 0.1 kg. Raises ValueError with a user-facing message."""
    try:
        kilos = float(value)
    except (TypeError, ValueError):
        raise ValueError("The weight must be a number.")
    if kilos != kilos or kilos in (float("inf"), float("-inf")):
        raise ValueError("The weight must be a number.")
    kilos = round(kilos, 1)
    if kilos <= 0:
        raise ValueError("The weight must be more than zero.")
    if kilos > max_kg:
        raise ValueError(f"A weight above {max_kg:g} kg looks wrong. Check the scale.")
    return kilos


# ------------------------------------------------------------------- numbering

def format_farm_number(factory_code, centre_code, sequence):
    return f"{factory_code}{centre_code}{sequence:03d}"


def format_farmer_number(sequence):
    return f"FMR-{sequence:05d}"


def format_employee_number(sequence):
    return f"EMP-{sequence:04d}"


def format_transaction_number(day, sequence):
    return f"TX-{day:%Y%m%d}-{sequence:06d}"


def format_receipt_number(day, sequence):
    return f"RC-{day:%Y%m%d}-{sequence:05d}"


# ----------------------------------------------------------------- delegations

def delegation_is_active(status, start_at, expires_at, withdrawn_at, now):
    """True while a delegation may be used.

    Phase 1 treats every delegation as a time window. ONE_TIME and RECURRING
    delegations get their own rules when the delegation screens arrive.
    """
    if status != "ACTIVE" or withdrawn_at is not None:
        return False
    if start_at is not None and now < start_at:
        return False
    if expires_at is not None and now >= expires_at:
        return False
    return True


# --------------------------------------------------------------------- notices

NOTICE_AUDIENCES = ("PUBLIC", "STAFF", "DEPARTMENT")
NOTICE_CATEGORIES = ("general", "price", "bonus", "fertilizer", "schedule")


def notice_post_problem(audience, *, can_post, can_publish, actor_department_id, target_department_id):
    """None if this person may post a notice to this audience, else the reason why not.

    * PUBLIC (farmers and visitors) and STAFF (every employee) need publishing rights.
    * DEPARTMENT notices need posting rights, and only reach the poster's own
      department unless the poster also has publishing rights.
    """
    if audience in ("PUBLIC", "STAFF"):
        if can_publish:
            return None
        return "Only staff with publishing rights can post public or all-staff notices."
    if audience == "DEPARTMENT":
        if target_department_id is None:
            return "Choose a department."
        if not (can_post or can_publish):
            return "You are not allowed to post notices."
        if can_publish or target_department_id == actor_department_id:
            return None
        return "You can only post to your own department."
    return "Choose who the notice is for."


# ------------------------------------------------------------------- API keys

def generate_api_key():
    return secrets.token_urlsafe(32)


def hash_api_key(key):
    """Scale keys are long random strings, so a fast hash is fine (no need for a slow password hash)."""
    return hashlib.sha256((key or "").encode("utf-8")).hexdigest()


def api_key_matches(key, stored_hash):
    return bool(stored_hash) and hmac.compare_digest(hash_api_key(key), stored_hash)
