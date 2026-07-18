import unittest

from app.size_units import DECIMAL_BYTES_PER_GB, bytes_to_gb, format_gb


class DecimalSizeUnitTests(unittest.TestCase):
    def test_decimal_gigabyte_conversion(self):
        self.assertEqual(DECIMAL_BYTES_PER_GB, 1_000_000_000)
        self.assertEqual(bytes_to_gb(1_000_000_000), 1.0)
        self.assertEqual(bytes_to_gb(350_000_000), 0.35)
        self.assertEqual(format_gb(0.000000094), "0.000000094")
        self.assertEqual(format_gb(2.0), "2")


if __name__ == "__main__":
    unittest.main()
