"""Synthetic-only tests; no archived evidence or experiment output is bundled."""
import hashlib
import io
import json
import unittest
import zipfile

from verify import Evidence, TOL, compute, paired, percentile, summarize


class PredictionTests(unittest.TestCase):
    def setUp(self):
        self.row = dict(duration="2", s="2.0", a="1.9", b="3.0", capacity="contended",
                        signal_used_by_model="True", handler_input_s="0.5")
        self.terms = dict(fixed_return_s=4., residual_s=.2, new_overhead_s=.4, retry_overhead_s=.1)

    def test_event_path_uses_handler_duration_and_residual(self):
        result = compute(self.row, self.terms, "M3-new")
        self.assertEqual(result, compute(self.row, self.terms, "M3+M4"))
        self.assertAlmostEqual(result[0], 2.7)
        self.assertAlmostEqual(result[1], 2.8)
        self.assertAlmostEqual(result[2], .9)
        self.assertAlmostEqual(result[3], -200.)

    def test_missing_signal_routes_hybrid_to_fixed_not_zero(self):
        row = dict(self.row, signal_used_by_model="False", handler_input_s="")
        self.assertEqual(compute(row, self.terms, "M3+M4"), compute(row, self.terms, "M4-new"))
        self.assertEqual(compute(row, self.terms, "M3-new")[0], 2.)

    def test_spare_capacity_has_same_bind_forecast_for_all_models(self):
        row = dict(self.row, capacity="spare")
        predictions = [compute(row, self.terms, m)[1:] for m in ("M3-new", "M4-new", "M3+M4")]
        self.assertEqual(predictions[0], predictions[1])
        self.assertEqual(predictions[1], predictions[2])

    def test_return_equal_cutoff_uses_new_overhead(self):
        terms = dict(self.terms, fixed_return_s=2.)
        self.assertEqual(compute(self.row, terms, "M4-new")[1], 2.4)

    def test_max_preserves_new_request_overhead(self):
        terms = dict(self.terms, fixed_return_s=2.1)
        self.assertEqual(compute(self.row, terms, "M4-new")[1], 2.4)

    def test_descriptive_statistics_use_linear_percentile(self):
        result = summarize([-2., 0., 4., -8.])
        self.assertEqual(result["n"], 4)
        self.assertEqual(result["mae_ms"], 3.5)
        self.assertEqual(result["bias_ms"], -1.5)
        self.assertEqual(result["median_ae_ms"], 3.)
        self.assertAlmostEqual(result["p95_ae_ms_linear"], 7.4)
        self.assertEqual(percentile([3.], .95), 3.)

    def test_pair_tolerance_is_seconds_converted_to_milliseconds(self):
        rows = [dict(trial_id=str(i), signal_used_by_model="True") for i in range(4)]
        against = {"0": 2., "1": 3., "2": 4., "3": 5.}
        candidate = dict(against, **{"0": 1., "1": 3.+TOL*500, "2": 6.})
        result = paired(against, candidate, rows)
        self.assertEqual((result["improved"], result["equal"], result["worsened"]), (1, 2, 1))
        self.assertIsNone(result["top3_share"])


class EvidenceTests(unittest.TestCase):
    def archive(self, changed=False, duplicate=False, prefix="fgcs-evidence/"):
        sink = io.BytesIO()
        original = b"a,b\n1,2\n"
        manifest = {"files": {"sample.csv": hashlib.sha256(original).hexdigest()}}
        with zipfile.ZipFile(sink, "w") as z:
            z.writestr(prefix+"manifest.json", json.dumps(manifest))
            z.writestr(prefix+"sample.csv", original if not changed else b"changed")
            if duplicate:
                import warnings
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore", UserWarning)
                    z.writestr(prefix+"sample.csv", original)
        sink.seek(0)
        return sink

    def test_zip_is_verified_without_extraction(self):
        with Evidence(self.archive()) as evidence:
            self.assertEqual(evidence.verify_manifest(), 1)
            self.assertEqual(evidence.load_csv("sample.csv"), [{"a": "1", "b": "2"}])

    def test_flat_zip_is_supported(self):
        with Evidence(self.archive(prefix="")) as evidence:
            self.assertEqual(evidence.verify_manifest(), 1)

    def test_changed_manifest_member_is_rejected(self):
        with Evidence(self.archive(changed=True)) as evidence:
            with self.assertRaisesRegex(ValueError, "hash mismatch"):
                evidence.verify_manifest()

    def test_duplicate_members_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "duplicate"):
            Evidence(self.archive(duplicate=True))

    def test_manifest_cannot_escape_external_root(self):
        with Evidence(self.archive()) as evidence:
            for name in ("../sample.csv", "/sample.csv", "a/../sample.csv", "./sample.csv", ""):
                with self.subTest(name=name), self.assertRaises(ValueError):
                    evidence.read_bytes(name)


if __name__ == "__main__":
    unittest.main()
