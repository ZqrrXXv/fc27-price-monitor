import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from logic import determine_reasons, money, normalize


class AlertLogicTests(unittest.TestCase):
    def reasons(self, previous, new_price, edition_lowest_before=70.0):
        return determine_reasons(
            previous=previous,
            new_price=new_price,
            budget=50.0,
            significant_eur=5.0,
            significant_pct=10.0,
            edition_lowest_before=edition_lowest_before,
            alert_on_first_seen_under_budget=True,
            alert_on_new_low=True,
        )

    def test_significant_drop_by_euros(self):
        result = self.reasons({"last_price": 70.0, "in_stock": True}, 64.0, 60.0)
        self.assertIn("significant_drop", result)

    def test_budget_crossing(self):
        result = self.reasons({"last_price": 52.0, "in_stock": True}, 49.99, 45.0)
        self.assertIn("budget", result)

    def test_restock(self):
        result = self.reasons({"last_price": 55.0, "in_stock": False}, 55.0, 49.0)
        self.assertIn("restock", result)

    def test_new_low_even_small_drop(self):
        result = self.reasons({"last_price": 61.0, "in_stock": True}, 59.5, 60.0)
        self.assertIn("new_low", result)
        self.assertNotIn("significant_drop", result)

    def test_first_seen_under_budget(self):
        self.assertEqual(self.reasons(None, 47.99, None), ["budget"])

    def test_no_spam_when_unchanged(self):
        self.assertEqual(
            self.reasons({"last_price": 69.99, "in_stock": True}, 69.99, 60.0),
            [],
        )

    def test_helpers(self):
        self.assertEqual(normalize("EA SPORTS FC™ 27"), "ea sports fc 27")
        self.assertEqual(money(47.99), "47,99 €")


if __name__ == "__main__":
    unittest.main()
