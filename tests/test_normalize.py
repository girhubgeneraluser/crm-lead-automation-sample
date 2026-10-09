"""Field normalisation: emails, phones (E.164), names, companies, countries."""
import unittest

from helpers import BASE_CONFIG, raw_lead
from leadflow import normalize as n

FREEMAIL = BASE_CONFIG["normalisation"]["freemail_domains"]


class EmailTests(unittest.TestCase):
    def test_lowercase_and_trim(self):
        self.assertEqual(n.normalize_email("  Ada.Lovelace@Analytical-Engines.CO.UK "), ("ada.lovelace@analytical-engines.co.uk", None))
        self.assertEqual(n.normalize_email("<mailto:Bob@Acme.com>")[0], "bob@acme.com")

    def test_invalid_emails_rejected(self):
        for bad in ["jane.doe@", "mark.smith@@acme.com", "tom at acme.com", "anna.k@acme", "lee..ross@acme.com",
                    "info@acme.com sales", ".lead@acme.com", "x@-acme.com", "noone@localhost", "a@b.c"]:
            with self.subTest(bad=bad):
                self.assertEqual(n.normalize_email(bad), (None, "invalid_email"))

    def test_missing_and_placeholder(self):
        self.assertEqual(n.normalize_email("   ")[1], "missing_email")
        self.assertEqual(n.normalize_email("n/a")[1], "missing_email")
        self.assertEqual(n.normalize_email(None)[1], "missing_email")
        self.assertEqual(n.normalize_email("chris@example.com")[1], "placeholder_email")

    def test_valid_unusual_but_legal(self):
        for ok in ["o'brien@acme.ie", "first+tag@acme.com", "x_y-z@sub.acme.co.uk"]:
            with self.subTest(ok=ok):
                self.assertIsNone(n.normalize_email(ok)[1])


class PhoneTests(unittest.TestCase):
    def test_uk_formats(self):
        for raw in ["07700 900123", "07700-900-123", "+44 7700 900123", "+44 (0)7700 900123", "0044 7700 900123",
                    "447700900123", "7700900123"]:
            with self.subTest(raw=raw):
                self.assertEqual(n.normalize_phone(raw, "GB")[:2], ("+447700900123", True))

    def test_north_america(self):
        for raw in ["(212) 555-0147", "212.555.0147", "1-212-555-0147", "+1 212 555 0147", "2125550147 x23"]:
            with self.subTest(raw=raw):
                self.assertEqual(n.normalize_phone(raw, "US")[:2], ("+12125550147", True))

    def test_germany_and_ireland(self):
        self.assertEqual(n.normalize_phone("030 1234567", "DE")[0], "+49301234567")
        self.assertEqual(n.normalize_phone("+49 (0)30 1234567", None)[0], "+49301234567")
        self.assertEqual(n.normalize_phone("01 2345678", "IE")[0], "+35312345678")

    def test_unknown_country_keeps_raw_and_flags(self):
        self.assertEqual(n.normalize_phone(" 020 7946 0000 ", None), ("020 7946 0000", False, "phone_country_unknown"))

    def test_garbage_not_valid(self):
        self.assertEqual(n.normalize_phone("12", "GB")[1:], (False, "phone_unparseable"))
        self.assertEqual(n.normalize_phone("n/a", "GB"), (None, False, None))


class NameCompanyTests(unittest.TestCase):
    def test_name_casing(self):
        cases = {"JOHN": "John", "mary-jane": "Mary-Jane", "O'BRIEN": "O'Brien", "mcallister": "McAllister",
                 "VAN DER BERG": "Van der Berg", "  ada   ": "Ada", "DeShawn": "DeShawn", "McKay": "McKay"}
        for raw, want in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(n.normalize_name(raw), want)

    def test_company_casing_and_key(self):
        self.assertEqual(n.normalize_company("BRIGHTFOLD ANALYTICS LTD"), "Brightfold Analytics Ltd")
        self.assertEqual(n.normalize_company("larkspire energy gmbh."), "Larkspire Energy GmbH")
        self.assertEqual(n.normalize_company("Lowmeadow Systems Inc.."), "Lowmeadow Systems Inc.")
        self.assertEqual(n.normalize_company("IBM"), "IBM")
        self.assertIsNone(n.normalize_company(" n/a "))
        self.assertEqual(n.company_key("Acme Ltd."), n.company_key("ACME LIMITED"))
        self.assertEqual(n.company_key("The Acme Group Inc"), "acme group")

    def test_domain_inference_excludes_freemail(self):
        lead, fixes, _ = n.normalize_lead(raw_lead(company="", email="sam@brightfold-analytics.co.uk"), FREEMAIL)
        self.assertEqual((lead["company"], lead["company_domain"], lead["company_inferred"]),
                         ("Brightfold Analytics", "brightfold-analytics.co.uk", True))
        self.assertIn("company_from_domain", fixes)
        lead, _, _ = n.normalize_lead(raw_lead(company=None, email="sam.k@gmail.com"), FREEMAIL)
        self.assertEqual((lead["company"], lead["company_domain"], lead["email_type"]), (None, None, "freemail"))
        lead, _, _ = n.normalize_lead(raw_lead(company=None, email="sam@yahoo.co.uk"), FREEMAIL)
        self.assertIsNone(lead["company"])

    def test_subdomain_reduced_to_registrable(self):
        self.assertEqual(n.registrable_domain("mail.eu.acme.com"), "acme.com")
        self.assertEqual(n.registrable_domain("uk.acme.co.uk"), "acme.co.uk")

    def test_country_aliases_and_inference(self):
        self.assertEqual([n.normalize_country(c) for c in ["uk", "U.K.", "England", "usa", "Deutschland", "Mars"]],
                         ["GB", "GB", "GB", "US", "DE", None])
        lead, fixes, _ = n.normalize_lead(raw_lead(country=None, phone="+49 30 1234567", email="x@acme.com"), FREEMAIL)
        self.assertEqual(lead["country"], "DE")
        self.assertIn("country_from_phone", fixes)
        # .ca domain beats the ambiguous +1 calling code
        lead, _, _ = n.normalize_lead(raw_lead(country=None, phone="+1 604 555 0100", email="x@acme.ca"), FREEMAIL)
        self.assertEqual(lead["country"], "CA")

    def test_phone_normalised_after_country_inferred_from_domain(self):
        lead, _, _ = n.normalize_lead(raw_lead(country=None, phone="07700 900123", email="x@acme.co.uk"), FREEMAIL)
        self.assertEqual((lead["country"], lead["phone"], lead["phone_valid"]), ("GB", "+447700900123", True))

    def test_full_name_in_first_name_is_split(self):
        lead, fixes, _ = n.normalize_lead(raw_lead(first_name="ada LOVELACE", last_name=""), FREEMAIL)
        self.assertEqual((lead["first_name"], lead["last_name"]), ("Ada", "Lovelace"))
        self.assertIn("name_split", fixes)

    def test_sizing_fields(self):
        self.assertEqual([n.parse_company_size(v) for v in ["250+", "50-199", "1,200", 40, "", "lots", 0]],
                         [250, 50, 1200, 40, None, None, None])
        self.assertEqual(n.normalize_budget("£50K+"), "50k+")
        self.assertEqual(n.normalize_timeline("This Quarter"), "this quarter")


if __name__ == "__main__":
    unittest.main()
