from decimal import Decimal as D
import unittest

from reproduce import bind_optimum


class BindOptimizerTests(unittest.TestCase):
    def test_interval_of_optimal_fits(self):
        # Both affine paths are active on the whole interval. Opposite
        # absolute-loss slopes cancel until the second residual changes sign.
        self.assertEqual(bind_optimum([(D(1),D(8)), (D(1),D(10))], D(2),D(1)),
                         [[D(7),D(9)]])

    def test_prediction_floor_yields_flat_minimum(self):
        self.assertEqual(bind_optimum([(D(4),D(6))], D(2),D(1)), [[D(0),D(5)]])

    def test_reject_invalid_timing_parameters(self):
        with self.assertRaises(ValueError):
            bind_optimum([(D(4),D(6))], D(1),D(2))


if __name__ == "__main__":
    unittest.main()
