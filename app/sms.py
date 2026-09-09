"""
SMS sending — currently a stub.

Real SMS delivery needs a paid gateway account (Africa's Talking is the usual
choice in Kenya). Rather than block the rest of the app on getting that set
up, this function currently just logs the message and returns True, so the
whole first-login/verification flow works end-to-end in development.

To go live with real SMS:
1. Sign up for Africa's Talking (or similar) and get an API key + username.
2. `pip install africastalking` and add it to requirements.txt.
3. Replace the body of send_sms() below with a real API call, e.g.:

    import africastalking
    africastalking.initialize(username=AT_USERNAME, api_key=AT_API_KEY)
    sms = africastalking.SMS
    sms.send(message, [phone])

4. Set AT_USERNAME / AT_API_KEY as environment variables (same pattern as
   SECRET_KEY / DATABASE_URL in config.py) — never hardcode real credentials.
"""

import logging

logger = logging.getLogger("teafarm.sms")


def send_sms(phone: str, message: str) -> bool:
    logger.info("[SMS STUB] to %s: %s", phone, message)
    print(f"[SMS STUB] to {phone}: {message}")  # visible in server logs for now
    return True
