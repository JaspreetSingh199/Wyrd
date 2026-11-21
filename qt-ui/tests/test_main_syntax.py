"""Cheap syntax regression guard for the Qt entry point."""
from pathlib import Path
import unittest


class MainSyntaxTests(unittest.TestCase):
    def test_main_py_compiles(self):
        main_path = Path(__file__).resolve().parents[1] / "main.py"
        source = main_path.read_text(encoding="utf-8")
        compile(source, str(main_path), "exec")


if __name__ == "__main__":
    unittest.main()
