import pathlib
import re
import unittest


SOURCE = pathlib.Path(__file__).with_name("OgArb.mq5").read_text(encoding="utf-8")
BASKET = SOURCE.split("bool execBasket(", 1)[1].split("bool legRel(", 1)[0]
CONDITION = re.search(r"if\((ein[^\n]+commReq)\)", BASKET).group(1)


def rejected(net, round_trip, modeled):
    return eval(
        CONDITION,
        {"__builtins__": {}},
        {"ein": net, "commReq": round_trip, "commModeled": modeled},
    )


class CommissionGateTests(unittest.TestCase):
    def test_already_deducted_commission_is_not_charged_twice(self):
        self.assertFalse(rejected(net=1.1, round_trip=2.0, modeled=2.2))

    def test_identical_economics_give_identical_decisions(self):
        for gross in (1.0, 2.9, 3.0, 3.3, 5.0):
            with self.subTest(gross=gross):
                self.assertEqual(
                    rejected(net=gross, round_trip=2.0, modeled=0.0),
                    rejected(net=gross - 2.2, round_trip=2.0, modeled=2.2),
                )

    def test_zero_modeled_fees_do_not_bypass_real_fees(self):
        self.assertTrue(rejected(net=0.24, round_trip=2.0, modeled=0.0))

    def test_existing_safety_margin_is_preserved(self):
        self.assertTrue(rejected(net=2.9, round_trip=2.0, modeled=0.0))
        self.assertFalse(rejected(net=3.0, round_trip=2.0, modeled=0.0))


if __name__ == "__main__":
    unittest.main()
