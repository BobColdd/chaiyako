"""Inputs and Fertilizer: the Info and Distribution pages, against an in-memory SQLite database.

    python -m unittest discover tests
"""
import os, sys, unittest
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    import flask_sqlalchemy, flask_login  # noqa: F401
    HAVE_DEPS = True
except ImportError:
    HAVE_DEPS = False


@unittest.skipUnless(HAVE_DEPS, "Flask-SQLAlchemy / Flask-Login not installed")
class InputsPages(unittest.TestCase):
    def setUp(self):
        from app import create_app, db
        from app.models import Department, FarmVerification, Role
        from app.services import create_centre, create_staff, register_farmer, verify_farm
        self.app = create_app({"SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:", "TESTING": True, "SECRET_KEY": "t"})
        self.ctx = self.app.app_context()
        self.ctx.push()
        roles = {r.name: r for r in Role.query.all()}
        dept = next(iter(Department.query.all())).id

        def staff(username, role):
            return create_staff(None, first_name=username.title(), last_name="Test", username=username,
                                password="Passw0rd!", department_id=dept, role_ids=[roles[role].id])
        staff("inputs", "Inputs Officer")
        staff("clerk", "Tea Buying Clerk")
        field = staff("field", "Field Officer")
        self.centre = create_centre(None, name="Test Centre", code="001")
        self.other = create_centre(None, name="Other Centre", code="002")

        def farmer(first, phone, verify):
            f, farm = register_farmer(field, first_name=first, last_name="Kip", phone=phone,
                                      buying_centre_id=self.centre.id, tea_bushes=500)
            if verify:
                task = FarmVerification.query.filter_by(farm_id=farm.id).first()
                farm = verify_farm(field, task, decision="VERIFIED", visit_date=date.today(),
                                   observations="ok", tea_bushes=500)
            return f, farm
        self.amos, self.amos_farm = farmer("Amos", "0712345678", True)
        self.beth, self.beth_farm = farmer("Beth", "0712345679", True)
        self.pending, _ = farmer("Pam", "0712345680", False)
        db.session.commit()
        self.http = self.app.test_client()

    def tearDown(self):
        from app import db
        db.session.remove()
        self.ctx.pop()

    def login(self, username):
        self.http.post("/", data={"username": username, "password": "Passw0rd!"})

    def give(self, farm_number, **over):
        data = {"farm_number": farm_number, "buying_centre_id": self.centre.id,
                "fertilizer_type": "NPK 25:5:5", "quantity_kg": "50"}
        data.update(over)
        return self.http.post("/inputs/distribution", data=data, follow_redirects=True)

    def test_sidebar_has_info_first_then_distribution(self):
        self.login("inputs")
        page = self.http.get("/inputs/").get_data(as_text=True)
        self.assertLess(page.index(">Info<"), page.index(">Distribution<"))
        self.assertLess(page.index(">Distribution<"), page.index(">Farmers<"))
        self.assertNotIn(">Fertilizer<", page)

    def test_info_shows_zero_for_farmers_who_took_nothing(self):
        self.login("inputs")
        self.give(self.amos_farm.farm_number)
        page = self.http.get("/inputs/").get_data(as_text=True)
        self.assertIn("Amos Kip", page)
        self.assertIn("Beth Kip", page)
        self.assertIn("50.0", page)
        # "Not yet taken" narrows the farmer list, which is the part of the page after the records table.
        farmer_list = self.http.get("/inputs/?show=none").get_data(as_text=True).split("<h3>Farmers</h3>")[1]
        self.assertIn("Beth Kip", farmer_list)
        self.assertNotIn("Amos Kip", farmer_list)

    def test_records_filter_by_buying_centre(self):
        self.login("inputs")
        self.give(self.amos_farm.farm_number, quantity_kg="11")
        self.give(self.beth_farm.farm_number, quantity_kg="22", buying_centre_id=self.other.id)
        at_test = self.http.get(f"/inputs/?centre={self.centre.id}&show=taken").get_data(as_text=True)
        self.assertIn("Amos Kip", at_test)
        self.assertNotIn("Beth Kip", at_test)
        at_other = self.http.get(f"/inputs/?centre={self.other.id}").get_data(as_text=True)
        self.assertIn("22.0", at_other)
        self.assertNotIn("11.0", at_other)

    def test_date_filter_excludes_other_days(self):
        self.login("inputs")
        self.give(self.amos_farm.farm_number)
        page = self.http.get("/inputs/?from=2000-01-01&to=2000-01-31").get_data(as_text=True)
        self.assertIn("No distribution records match", page)

    def test_print_and_pdf_follow_the_filter(self):
        self.login("inputs")
        self.give(self.amos_farm.farm_number, quantity_kg="11")
        self.give(self.beth_farm.farm_number, quantity_kg="22", buying_centre_id=self.other.id)
        printed = self.http.get(f"/inputs/print?centre={self.other.id}").get_data(as_text=True)
        self.assertIn("Beth Kip", printed)
        self.assertNotIn("Amos Kip", printed)
        self.assertIn("22.0 kg", printed)
        pdf = self.http.get(f"/inputs/pdf?centre={self.other.id}")
        self.assertEqual(pdf.status_code, 200)
        self.assertEqual(pdf.mimetype, "application/pdf")
        self.assertTrue(pdf.data.startswith(b"%PDF"))
        self.assertIn("attachment", pdf.headers["Content-Disposition"])

    def test_farm_list_only_has_verified_farms(self):
        self.login("inputs")
        data = self.http.get("/inputs/distribution/farms").get_json()
        numbers = [f["farm_number"] for f in data["farms"]]
        self.assertEqual(sorted(numbers), sorted([self.amos_farm.farm_number, self.beth_farm.farm_number]))

    def test_recording_and_history(self):
        self.login("inputs")
        reply = self.give(self.amos_farm.farm_number, quantity_kg="25.5")
        self.assertIn("Recorded 25.5 kg", reply.get_data(as_text=True))
        history = self.http.get(f"/inputs/distribution/history/{self.amos.id}").get_json()
        self.assertEqual(len(history["records"]), 1)
        self.assertAlmostEqual(history["total_kg"], 25.5)

    def test_rejects_bad_entries(self):
        self.login("inputs")
        self.assertIn("pick the farmer", self.give("NOPE").get_data(as_text=True))
        self.assertIn("more than zero", self.give(self.amos_farm.farm_number, quantity_kg="0").get_data(as_text=True))
        history = self.http.get(f"/inputs/distribution/history/{self.amos.id}").get_json()
        self.assertEqual(history["records"], [])

    def test_only_fertilizer_staff_can_open_pages(self):
        self.login("clerk")
        for path in ("/inputs/", "/inputs/distribution", "/inputs/distribution/farms", "/inputs/print", "/inputs/pdf"):
            self.assertEqual(self.http.get(path).status_code, 403, path)


if __name__ == "__main__":
    unittest.main()
