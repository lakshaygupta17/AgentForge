import unittest

from calculator import add


class CalculatorTests(unittest.TestCase):
    def test_adds_positive_integers(self) -> None:
        self.assertEqual(add(2, 3), 5)

    def test_adds_a_negative_integer(self) -> None:
        self.assertEqual(add(7, -2), 5)


if __name__ == "__main__":
    unittest.main()
