import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from mo2_inspection import (
    find_mo2_installations,
    inspect_mo2,
)


class MO2InspectionTests(unittest.TestCase):
    def test_detects_known_mo2_without_modifying_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            mo2 = home / "Games/ModOrganizer"
            mo2.mkdir(parents=True)
            exe = mo2 / "ModOrganizer.exe"
            exe.write_bytes(b"fixture")
            before = exe.read_bytes()
            self.assertEqual(find_mo2_installations(home), [mo2.resolve()])
            self.assertTrue(inspect_mo2(home)["found"])
            self.assertEqual(exe.read_bytes(), before)
            self.assertEqual(list(home.iterdir()), [home / "Games"])

    def test_mo2_missing_is_not_created(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            self.assertFalse(inspect_mo2(home)["found"])
            self.assertEqual(list(home.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
