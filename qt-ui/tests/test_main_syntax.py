"""Syntax regression guard for the Qt entry point and all runtime modules."""
from pathlib import Path
import unittest


class MainSyntaxTests(unittest.TestCase):
    def test_main_py_compiles(self):
        main_path = Path(__file__).resolve().parents[1] / "main.py"
        source = main_path.read_text(encoding="utf-8")
        compile(source, str(main_path), "exec")

    def test_all_qt_ui_python_modules_compile(self):
        ui_root = Path(__file__).resolve().parents[1]
        failures = []
        for source_path in sorted(ui_root.rglob("*.py")):
            if "__pycache__" in source_path.parts or ".venv" in source_path.parts:
                continue
            try:
                compile(source_path.read_text(encoding="utf-8"), str(source_path), "exec")
            except (OSError, SyntaxError) as error:
                failures.append(f"{source_path.relative_to(ui_root)}: {error}")
        self.assertEqual(failures, [], "\n".join(failures))


if __name__ == "__main__":
    unittest.main()
