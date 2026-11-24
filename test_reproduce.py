"""Data-free tests for the separate offline evidence format."""

from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from zipfile import ZipFile

import reproduce


sys.path.insert(0, str(Path(__file__).parent / "data"))
from build_evidence_zip import pack


class EvidenceTests(unittest.TestCase):
    def fixture(self, root):
        source = root / "source"
        source.mkdir()
        payload = b"{}\n"
        for component in reproduce.COMPONENTS:
            (source / component).mkdir()
            (source / component / "example.json").write_bytes(payload)
        path = root / "evidence.zip"
        pack(source, path)
        return path

    def test_archive_and_extracted_directory(self):
        with TemporaryDirectory() as d:
            root = Path(d)
            path = self.fixture(root)
            dest = root / "checked"
            self.assertEqual(reproduce.validated_evidence(path, dest), (dest, len(reproduce.COMPONENTS)))
            self.assertEqual(reproduce.validated_evidence(dest, root / "unused"), (dest, len(reproduce.COMPONENTS)))
            (dest / "ibm" / "example.json").write_bytes(b"[]\n")
            with self.assertRaisesRegex(ValueError, "checksum"):
                reproduce.validated_evidence(dest, root / "unused")

    def test_archive_rejects_unlisted_path(self):
        with TemporaryDirectory() as d:
            root = Path(d)
            path = self.fixture(root)
            with ZipFile(path, "a") as z:
                z.writestr("../outside", b"not evidence")
            with self.assertRaisesRegex(ValueError, "unexpected"):
                reproduce.validated_evidence(path, root / "checked")
            self.assertFalse((root / "outside").exists())

    def test_manifest_rejects_nondata(self):
        self.assertFalse(reproduce.plain_path("kind/sub/../../x.json"))
        self.assertFalse(reproduce.plain_path("kind/prepare.py"))
        self.assertFalse(reproduce.plain_path("/kind/x.csv"))
        self.assertTrue(reproduce.plain_path("kind/trials.csv.gz"))
        self.assertTrue(reproduce.plain_path("within-setting/calibration/trial-00/signal.jsonl"))
        self.assertFalse(reproduce.plain_path("kind/signal.jsonl"))


if __name__ == "__main__":
    unittest.main()
