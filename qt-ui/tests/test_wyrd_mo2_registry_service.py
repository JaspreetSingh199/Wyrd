import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from wyrd_mo2_registry import RegistryError
from wyrd_mo2_registry_service import RegistryService


class RegistryServiceTests(unittest.TestCase):
    def _roots(self, root):
        prefix = root / "prefix"
        install = root / "install"
        (prefix / "pfx/drive_c/users/steamuser").mkdir(parents=True)
        install.mkdir()
        (install / "ModOrganizer.exe").write_bytes(b"not executed")
        instance = prefix / "pfx/drive_c/users/steamuser/AppData/Local/ModOrganizer/Skyrim"
        instance.mkdir(parents=True)
        ini = instance / "ModOrganizer.ini"
        ini.write_text("[General]\ngameName=Skyrim Special Edition\n", encoding="utf-8")
        return prefix, install, instance, ini

    def _inventory(self, instance, ini, managed=False):
        return {
            "ok": True,
            "managed": managed,
            "instances": [{
                "instance": instance.name,
                "instance_path": str(instance),
                "config": str(ini),
                "game_name": "Skyrim Special Edition",
                "is_skyrim_se": True,
                "paths": {
                    "base": {"path": str(instance), "status": "available"},
                    "mods": {"path": str(instance / "mods"), "status": "missing"},
                    "downloads": {"path": str(instance / "downloads"), "status": "missing"},
                    "cache": {"path": str(instance / "webcache"), "status": "missing"},
                    "profiles": {"path": str(instance / "profiles"), "status": "missing"},
                    "overwrite": {"path": str(instance / "overwrite"), "status": "missing"},
                },
            }],
            "shared_paths": [],
            "errors": [],
            "message": "Read-only inventory complete.",
        }

    def test_discovery_is_read_only_and_external_owner_is_explicit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            prefix, install, instance, ini = self._roots(root)
            service = RegistryService(root / "registry.json")
            with patch("wyrd_mo2.inspect_mo2_storage", return_value=self._inventory(instance, ini)):
                result = service.discover(prefix, install)
            self.assertTrue(result["ok"])
            self.assertEqual(result["installation"]["owner"], "external")
            self.assertEqual(result["proposals"][0]["instance_type"], "global")
            self.assertEqual(result["proposals"][0]["registration_status"], "discovered-not-registered")
            self.assertEqual(set(result["proposals"][0]["storage"]), {
                "base", "mods", "downloads", "cache", "profiles", "overwrite"
            })
            self.assertFalse((root / "registry.json").exists())
            self.assertEqual(ini.read_text(encoding="utf-8"), "[General]\ngameName=Skyrim Special Edition\n")

    def test_discovery_refuses_symlinked_roots(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            prefix, install, _, _ = self._roots(root)
            link = root / "prefix-link"
            link.symlink_to(prefix, target_is_directory=True)
            result = RegistryService(root / "registry.json").discover(link, install)
            self.assertFalse(result["ok"])
            self.assertIn("Symlinked", result["message"])

    def test_invalid_existing_ownership_marker_is_not_downgraded_to_external(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            prefix, install, _, _ = self._roots(root)
            (prefix / ".wyrd-managed.json").write_text('{"managed_by":"someone-else"}', encoding="utf-8")
            result = RegistryService(root / "registry.json").discover(prefix, install)
            self.assertFalse(result["ok"])
            self.assertIn("Unrecognized ownership marker", result["message"])

    def test_managed_ownership_requires_both_valid_markers(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            prefix, install, instance, ini = self._roots(root)
            (prefix / ".wyrd-managed.json").write_text(json.dumps({
                "managed_by": "Wyrd", "game_app_id": "489830"
            }), encoding="utf-8")
            (install / ".wyrd-managed.json").write_text(json.dumps({
                "managed_by": "Wyrd"
            }), encoding="utf-8")
            service = RegistryService(root / "registry.json")
            with patch("wyrd_mo2.inspect_mo2_storage", return_value=self._inventory(instance, ini, managed=True)):
                result = service.discover(prefix, install)
            self.assertTrue(result["ok"])
            self.assertEqual(result["installation"]["owner"], "wyrd")

    def test_registration_requires_explicit_game_and_persists_registry_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            prefix, install, instance, ini = self._roots(root)
            service = RegistryService(root / "registry.json")
            with patch("wyrd_mo2.inspect_mo2_storage", return_value=self._inventory(instance, ini)):
                found = service.discover(prefix, install)
            proposal = found["proposals"][0]
            with self.assertRaises(RegistryError):
                service.register(found["installation"], proposal, {})
            saved = service.register(
                found["installation"], proposal,
                {"id": "skyrim-se", "appid": "489830"},
                {"id": "proton-ge", "kind": "steam-proton"},
            )
            self.assertEqual(len(saved["installations"]), 1)
            self.assertEqual(saved["instances"][0]["runtime_id"], "proton-ge")
            self.assertEqual(ini.read_text(encoding="utf-8"), "[General]\ngameName=Skyrim Special Edition\n")

    def test_ui_display_classification_does_not_mutate_fresh_proposal(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            prefix, install, instance, ini = self._roots(root)
            service = RegistryService(root / "registry.json")
            with patch("wyrd_mo2.inspect_mo2_storage", return_value=self._inventory(instance, ini)):
                found = service.discover(prefix, install)

            proposal = found["proposals"][0]
            registered_paths = set()
            identity = os.path.normcase(os.path.realpath(proposal["instance_path"]))
            already_registered = identity in registered_paths
            # The UI keeps duplicate/display state outside of the service proposal.
            display_state = {"already_registered": already_registered}

            self.assertFalse(display_state["already_registered"])
            self.assertEqual(proposal["registration_status"], "discovered-not-registered")
            saved = service.register(
                found["installation"], proposal,
                {"id": "skyrim-se", "app_id": "489830"},
                {"id": "proton-ge", "path": "/runtime"},
            )
            self.assertEqual(len(saved["instances"]), 1)

    def test_registration_rejects_proposal_for_different_installation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            prefix, install, instance, ini = self._roots(root)
            service = RegistryService(root / "registry.json")
            with patch("wyrd_mo2.inspect_mo2_storage", return_value=self._inventory(instance, ini)):
                found = service.discover(prefix, install)
            with self.assertRaisesRegex(RegistryError, "does not belong"):
                service.register({"id": "other"}, found["proposals"][0], {"id": "skyrim"})

    def test_registration_rejects_duplicate_registration(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            prefix, install, instance, ini = self._roots(root)
            service = RegistryService(root / "registry.json")
            with patch("wyrd_mo2.inspect_mo2_storage", return_value=self._inventory(instance, ini)):
                found = service.discover(prefix, install)
            service.register(found["installation"], found["proposals"][0], {"id": "skyrim"})
            with self.assertRaises(RegistryError):
                service.register(found["installation"], found["proposals"][0], {"id": "skyrim"})


    def test_portable_instance_is_classified_from_install_root_ini(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            prefix, install, _, _ = self._roots(root)
            ini = install / "ModOrganizer.ini"
            ini.write_text("[General]\\ngameName=Skyrim Special Edition\\n", encoding="utf-8")
            inventory = self._inventory(install, ini)
            service = RegistryService(root / "registry.json")
            with patch("wyrd_mo2.inspect_mo2_storage", return_value=inventory):
                result = service.discover(prefix, install)
            self.assertTrue(result["ok"])
            self.assertEqual(result["proposals"][0]["instance_type"], "portable")


if __name__ == "__main__":
    unittest.main()
