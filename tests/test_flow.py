"""End-to-end test of the tea-buying flow against an in-memory SQLite database.

Needs the real dependencies (pip install -r requirements.txt), so it is skipped
where they are missing:   python -m unittest discover tests
"""
import os, sys, unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    import flask_sqlalchemy, flask_login  # noqa: F401
    HAVE_DEPS = True
except ImportError:
    HAVE_DEPS = False


@unittest.skipUnless(HAVE_DEPS, "Flask-SQLAlchemy / Flask-Login not installed")
class TeaFlow(unittest.TestCase):
    def setUp(self):
        from app import create_app, db
        from app.models import BuyingCentre, Role, Department
        from app.services import create_centre, create_scale, create_staff, register_farmer, verify_farm
        from app.models import FarmVerification
        self.app = create_app({"SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:", "TESTING": True, "SECRET_KEY": "t"})
        self.ctx = self.app.app_context()
        self.ctx.push()
        roles = {r.name: r for r in Role.query.all()}
        depts = {d.name: d for d in Department.query.all()}
        dept = next(iter(depts.values())).id

        def staff(username, role):
            return create_staff(None, first_name=username.title(), last_name="Test", username=username,
                                password="Passw0rd!", department_id=dept, role_ids=[roles[role].id])
        self.clerk = staff("clerk", "Tea Buying Clerk")
        self.manager = staff("mgr", "Tea Buying Manager")
        self.field = staff("field", "Field Officer")
        self.cs = staff("cs", "Customer Service Officer")
        staff("it", "IT Manager")
        self.fin = self._staff_in("fin", "Finance Officer", "Finance")
        self.centre = create_centre(None, name="Test Centre", code="001")
        self.other = create_centre(None, name="Other Centre", code="002")
        _, self.key = create_scale(None, centre_id=self.centre.id, scale_identifier="SC-T")
        farmer, farm = register_farmer(self.field, first_name="Amos", last_name="Kip", phone="0712345678",
                                       buying_centre_id=self.centre.id, tea_bushes=500)
        pending = FarmVerification.query.filter_by(farm_id=farm.id).first()
        self.farm = verify_farm(self.field, pending, decision="VERIFIED", visit_date=__import__("datetime").date.today(),
                                observations="ok", tea_bushes=500)
        db.session.commit()
        self.http = self.app.test_client()

    def _staff_in(self, username, role, department_name):
        from app.models import Department, Role
        from app.services import create_staff
        from app import db
        role_row = Role.query.filter_by(name=role).first()
        dept = Department.query.filter_by(name=department_name).first()
        employee = create_staff(None, first_name=username.title(), last_name="Test", username=username,
                                password="Passw0rd!", department_id=dept.id, role_ids=[role_row.id])
        db.session.commit()
        return employee

    def tearDown(self):
        from app import db
        db.session.remove()
        self.ctx.pop()

    def login(self, username):
        return self.http.post("/", data={"username": username, "password": "Passw0rd!"})

    def scale(self, kg):
        return self.http.post("/api/scale/reading", json={"scale_identifier": "SC-T", "weight_kg": kg},
                              headers={"X-Scale-Key": self.key})

    def test_farm_number_issued_only_after_verification(self):
        self.assertEqual(self.farm.farm_number, "CY001001")

    def test_scale_rejects_wrong_key(self):
        res = self.http.post("/api/scale/reading", json={"scale_identifier": "SC-T", "weight_kg": 5},
                             headers={"X-Scale-Key": "wrong"})
        self.assertEqual(res.status_code, 401)

    def test_one_click_records_transaction_and_receipt_once(self):
        self.login("clerk")
        self.http.post("/buying/centre", data={"buying_centre_id": self.centre.id})
        event_id = self.scale(18.4).get_json()["event_id"]
        payload = {"farm_number": self.farm.farm_number, "event_id": event_id}

        first = self.http.post("/buying/confirm", json=payload)
        self.assertTrue(first.get_json()["ok"], first.get_json())
        self.assertEqual(first.get_json()["weight_kg"], 18.4)
        second = self.http.post("/buying/confirm", json=payload)           # a double click
        self.assertEqual(second.status_code, 400)

        from app.models import TeaTransaction, Receipt
        self.assertEqual(TeaTransaction.query.count(), 1)
        self.assertEqual(Receipt.query.count(), 1)

    def test_card_from_another_centre_is_refused(self):
        from app import db
        from app.models import Farmer
        Farmer.query.first().buying_centre_id = self.other.id
        db.session.commit()
        self.login("clerk")
        self.http.post("/buying/centre", data={"buying_centre_id": self.centre.id})
        event_id = self.scale(10).get_json()["event_id"]
        res = self.http.post("/buying/confirm", json={"farm_number": self.farm.farm_number, "event_id": event_id})
        self.assertEqual(res.status_code, 400)

    def test_clerk_cannot_void_but_manager_can(self):
        self.login("clerk")
        self.http.post("/buying/centre", data={"buying_centre_id": self.centre.id})
        event_id = self.scale(20).get_json()["event_id"]
        self.http.post("/buying/confirm", json={"farm_number": self.farm.farm_number, "event_id": event_id})
        from app.models import TeaTransaction
        tx = TeaTransaction.query.first()
        self.assertEqual(self.http.post(f"/management/transactions/{tx.id}/void", data={"reason": "test void"}).status_code, 403)
        self.http.get("/logout")
        self.login("mgr")
        self.http.post(f"/management/transactions/{tx.id}/void", data={"reason": "wrong farmer"})
        from app import db
        db.session.expire_all()
        self.assertEqual(TeaTransaction.query.first().status, "VOIDED")

    def test_audit_rows_cannot_be_changed(self):
        from app import db
        from app.models import AuditLog
        row = AuditLog.query.first()
        row.action = "TAMPERED"
        with self.assertRaises(RuntimeError):
            db.session.commit()
        db.session.rollback()

    def test_pages_render_for_a_manager(self):
        self.login("mgr")
        for path in ("/management/", "/management/insights", "/management/centres",
                     "/management/scales", "/management/transactions", "/farmers/", "/notices/"):
            self.assertEqual(self.http.get(path).status_code, 200, path)

    def test_managers_can_read_complaints_but_not_resolve_them(self):
        self.login("mgr")
        self.assertEqual(self.http.get("/complaints/").status_code, 200)
        self.assertEqual(self.http.post("/complaints/1/resolve").status_code, 403)
        self.http.get("/logout")
        self.login("clerk")
        self.assertEqual(self.http.get("/complaints/").status_code, 403)

    def test_farmers_page_lists_the_farmers(self):
        self.login("field")
        page = self.http.get("/farmers/")
        self.assertEqual(page.status_code, 200)
        self.assertIn(b"Amos Kip", page.data)

    def test_buying_farmer_list_shows_this_centres_farmers_and_who_is_ready(self):
        self.login("clerk")
        self.http.post("/buying/centre", data={"buying_centre_id": self.centre.id})
        data = self.http.get("/buying/farmers").get_json()
        self.assertTrue(data["ok"])
        self.assertEqual([f["name"] for f in data["farmers"]], ["Amos Kip"])
        self.assertTrue(data["farmers"][0]["verified"])
        self.http.post("/buying/centre", data={"buying_centre_id": self.other.id})
        self.assertEqual(self.http.get("/buying/farmers").get_json()["farmers"], [])

    def test_bell_dot_shows_for_new_notices_and_clears_after_opening_the_board(self):
        from app import db
        from app.models import Notice
        db.session.add(Notice(audience="STAFF", category="general", title="Staff meeting",
                              content="Friday 9am", posted_by_id=self.manager.employee_id))
        db.session.commit()

        self.login("clerk")
        page = self.http.get("/buying/")
        self.assertIn(b"bell-dot", page.data)                    # unread -> green dot
        board = self.http.get("/notices/")
        self.assertIn(b"tag-new", board.data)                    # tagged New on this visit
        self.assertNotIn(b"bell-dot", board.data)                # opening the board clears it
        self.assertNotIn(b"bell-dot", self.http.get("/buying/").data)
        self.assertNotIn(b"tag-new", self.http.get("/notices/").data)

    def test_own_notice_does_not_light_the_posters_bell(self):
        from app import db
        from app.models import Notice
        db.session.add(Notice(audience="STAFF", category="general", title="Mine",
                              content="x", posted_by_id=self.manager.employee_id))
        db.session.commit()
        self.login("mgr")
        self.assertNotIn(b"bell-dot", self.http.get("/management/").data)

    # ---------------------------------------------------------------- escalations
    def _finance_dept_id(self):
        from app.models import Department
        return Department.query.filter_by(name="Finance").first().id

    def test_customer_service_can_escalate_and_the_officer_sees_it_with_a_bell_dot(self):
        from app.models import Escalation
        self.login("cs")
        page = self.http.get(f"/farmers/{self.farm.farmer_id}")
        self.assertIn(b'id="escalate-dialog"', page.data)               # the Escalate card is on the farmer page
        res = self.http.post("/escalations/", data={
            "department_id": self._finance_dept_id(), "description": "Farmer says his April payment is missing",
            "farmer_id": self.farm.farmer_id, "next": "/farmers/"}, follow_redirects=False)
        self.assertEqual(res.status_code, 302)
        self.assertEqual(Escalation.query.count(), 1)
        self.assertEqual(Escalation.query.first().status, "open")
        self.http.get("/logout")

        self.login("fin")
        page = self.http.get("/farmers/")
        self.assertIn(b"bell-dot", page.data)                            # waiting at the Finance desk
        inbox = self.http.get("/escalations/")
        self.assertIn(b"April payment is missing", inbox.data)
        self.http.post("/escalations/1/take")
        self.assertEqual(Escalation.query.first().status, "taken")

    def test_only_the_addressed_desk_can_take_or_resolve(self):
        from app import db
        from app.models import Escalation
        self.login("cs")
        self.http.post("/escalations/", data={"department_id": self._finance_dept_id(), "description": "x y z"})
        self.http.get("/logout")

        self.login("clerk")                                              # no HANDLE_ESCALATION at all
        self.assertEqual(self.http.post("/escalations/1/take").status_code, 403)
        self.http.get("/logout")

        self.login("field")                                              # a handler, but the case is for Finance
        self.http.post("/escalations/1/take")
        self.http.post("/escalations/1/resolve", data={"resolution_note": "not mine"})
        db.session.expire_all()
        escalation = Escalation.query.first()
        self.assertEqual(escalation.status, "open")
        self.assertIsNone(escalation.taken_by_id)

    def test_resolving_needs_a_note_and_tells_the_person_who_raised_it(self):
        from app import db
        from app.models import Escalation
        self.login("cs")
        self.http.post("/escalations/", data={"department_id": self._finance_dept_id(), "description": "Payment query"})
        self.http.get("/logout")
        self.login("fin")
        self.http.post("/escalations/1/resolve", data={"resolution_note": "   "})
        db.session.expire_all()
        self.assertEqual(Escalation.query.first().status, "open")        # blank note refused
        self.http.post("/escalations/1/resolve", data={"resolution_note": "Paid on the 3rd"})
        db.session.expire_all()
        self.assertEqual(Escalation.query.first().status, "resolved")
        self.http.get("/logout")

        self.login("cs")
        self.assertIn(b"bell-dot", self.http.get("/farmers/").data)      # the outcome is new to them
        self.assertIn(b"Paid on the 3rd", self.http.get("/escalations/").data)
        self.assertNotIn(b"bell-dot", self.http.get("/farmers/").data)   # reading it clears the dot

    def test_cannot_escalate_to_a_department_that_is_not_a_desk_or_to_your_own(self):
        from app.models import Department, Escalation
        self.login("cs")
        own = Department.query.filter_by(name="Customer Services").first().id
        for bad in (own, 99999, ""):
            self.http.post("/escalations/", data={"department_id": bad, "description": "hello"})
        self.assertEqual(Escalation.query.count(), 0)

    def test_escalation_does_not_redirect_off_site(self):
        self.login("cs")
        res = self.http.post("/escalations/", data={"department_id": self._finance_dept_id(),
                                                    "description": "hi there", "next": "https://evil.example/"})
        self.assertTrue(res.headers["Location"].endswith("/escalations/"))

    def test_clerk_has_no_escalate_button(self):
        self.login("clerk")
        self.assertEqual(self.http.get("/escalations/").status_code, 403)

    def test_public_notice_feed_needs_no_login(self):
        self.assertEqual(self.http.get("/api/public/notices").status_code, 200)

    def test_home_page_is_the_login_page(self):
        res = self.http.get("/")
        self.assertEqual(res.status_code, 200)
        self.assertIn(b'name="password"', res.data)
        self.assertIn(b'id="toggle"', res.data)            # the show/hide eye

    def test_wrong_username_and_wrong_password_look_identical(self):
        wrong_user = self.http.post("/", data={"username": "nobody", "password": "Passw0rd!"})
        wrong_pass = self.http.post("/", data={"username": "clerk", "password": "nope"})
        for res in (wrong_user, wrong_pass):
            self.assertIn(b"Wrong username or password.", res.data)
            self.assertNotIn(b"Invalid", res.data)
        self.assertEqual(wrong_user.data.count(b"Wrong username or password."),
                         wrong_pass.data.count(b"Wrong username or password."))

    def test_old_login_address_still_works(self):
        self.assertEqual(self.http.get("/login").status_code, 302)


if __name__ == "__main__":
    unittest.main()
