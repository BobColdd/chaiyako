"""Pure-rule tests. They need nothing installed:   python -m unittest discover tests"""
import os, sys, unittest
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import importlib.util
_spec = importlib.util.spec_from_file_location("rules", os.path.join(os.path.dirname(__file__), "..", "app", "rules.py"))
rules = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rules)


class PhoneAndPin(unittest.TestCase):
    def test_phone(self):
        self.assertEqual(rules.clean_phone("0712 345-678"), "0712345678")
        self.assertEqual(rules.clean_phone("+254 712 345 678"), "+254712345678")
        self.assertIsNone(rules.clean_phone("12345"))
        self.assertIsNone(rules.clean_phone(None))

    def test_pin(self):
        self.assertTrue(rules.pin_is_valid("1234"))
        self.assertFalse(rules.pin_is_valid("123"))
        self.assertFalse(rules.pin_is_valid("12ab"))


class Weight(unittest.TestCase):
    def test_valid_rounds_to_tenth(self):
        self.assertEqual(rules.parse_weight("18.46", 1000), 18.5)

    def test_rejects_bad_values(self):
        for bad in ("abc", "", None, "0", "-3", "nan", "inf", "5000"):
            with self.assertRaises(ValueError, msg=repr(bad)):
                rules.parse_weight(bad, 1000)


class Numbers(unittest.TestCase):
    def test_formats(self):
        day = datetime(2026, 9, 21)
        self.assertEqual(rules.format_farm_number("CY", "039", 7), "CY039007")
        self.assertEqual(rules.format_farmer_number(12), "FMR-00012")
        self.assertEqual(rules.format_transaction_number(day, 5), "TX-20260921-000005")
        self.assertEqual(rules.format_receipt_number(day, 5), "RC-20260921-00005")


class Delegations(unittest.TestCase):
    now = datetime(2026, 9, 21, 12, 0)

    def active(self, **kw):
        base = dict(status="ACTIVE", start_at=self.now - timedelta(hours=1), expires_at=self.now + timedelta(hours=1),
                    withdrawn_at=None, now=self.now)
        base.update(kw)
        return rules.delegation_is_active(**base)

    def test_window(self):
        self.assertTrue(self.active())
        self.assertFalse(self.active(expires_at=self.now))                 # expired exactly now
        self.assertFalse(self.active(start_at=self.now + timedelta(minutes=1)))
        self.assertFalse(self.active(withdrawn_at=self.now))
        self.assertFalse(self.active(status="WITHDRAWN"))
        self.assertTrue(self.active(expires_at=None))


class Notices(unittest.TestCase):
    def problem(self, audience, post, publish, mine=1, target=1):
        return rules.notice_post_problem(audience, can_post=post, can_publish=publish,
                                         actor_department_id=mine, target_department_id=target)

    def test_public_and_staff_need_publish(self):
        self.assertIsNone(self.problem("PUBLIC", True, True))
        self.assertIsNotNone(self.problem("PUBLIC", True, False))
        self.assertIsNotNone(self.problem("STAFF", True, False))

    def test_department_rules(self):
        self.assertIsNone(self.problem("DEPARTMENT", True, False, mine=2, target=2))
        self.assertIsNotNone(self.problem("DEPARTMENT", True, False, mine=2, target=3))
        self.assertIsNone(self.problem("DEPARTMENT", False, True, mine=2, target=3))
        self.assertIsNotNone(self.problem("DEPARTMENT", False, False, mine=2, target=2))
        self.assertIsNotNone(self.problem("BOGUS", True, True))


class ApiKeys(unittest.TestCase):
    def test_round_trip(self):
        key = rules.generate_api_key()
        stored = rules.hash_api_key(key)
        self.assertTrue(rules.api_key_matches(key, stored))
        self.assertFalse(rules.api_key_matches(key + "x", stored))
        self.assertFalse(rules.api_key_matches("", None))


if __name__ == "__main__":
    unittest.main()
