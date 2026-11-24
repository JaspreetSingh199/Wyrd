import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from wyrd_mo2_registry import (
    REGISTRY_KIND,
    REGISTRY_SCHEMA_VERSION,
    RegistryError,
    empty_registry,
    load_registry,
    save_registry,
    validate_registry,
)


def sample_registry():
    data = empty_registry()
    data["games"] = [{"id": "skyrim-se", "appid": "489830"}]
    data["runtimes"] = [{"id": "skyrim-proton", "kind": "steam-proton"}]
    data["installations"] = [
        {"id": "mo2-primary", "executable": "/opt/mo2/ModOrganizer.exe", "owner": "external"},
        {"id": "mo2-tools", "executable": "/opt/mo2-tools/ModOrganizer.exe", "owner": "wyrd"},
    ]
    data["instances"] = [
        {
            "id": "skyrim-main", "installation_id": "mo2-primary",
            "instance_path": "/home/user/.local/share/ModOrganizer/Skyrim",
            "instance_type": "global", "game_id": "skyrim-se",
            "runtime_id": "skyrim-proton",
            "storage_policy": {
                "mods": "shared", "downloads": "shared",
                "profiles": "instance-local", "overwrite": "instance-local",
            },
        },
        {
            "id": "skyrim-test", "installation_id": "mo2-tools",
            "instance_path": "/home/user/.local/share/ModOrganizer/Skyrim-Test",
            "instance_type": "global", "game_id": "skyrim-se",
            "runtime_id": "skyrim-proton",
        },
    ]
    return data


class WyrdMO2RegistryTests(unittest.TestCase):
    def test_empty_registry_is_versioned_and_has_no_adopted_resources(self):
        data = empty_registry()
        self.assertEqual(data["kind"], REGISTRY_KIND)
        self.assertEqual(data["schema_version"], REGISTRY_SCHEMA_VERSION)
        self.assertEqual(data["installations"], [])
        self.assertEqual(data["instances"], [])

    def test_missing_registry_load_is_read_only_and_does_not_create_parent(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "not-created" / "registry.json"
            self.assertEqual(load_registry(path), empty_registry())
            self.assertFalse(path.parent.exists())

    def test_valid_registry_supports_multiple_installations_and_instances(self):
        result = validate_registry(sample_registry())
        self.assertEqual(len(result["installations"]), 2)
        self.assertEqual(len(result["instances"]), 2)
        self.assertEqual(result["instances"][0]["installation_id"], "mo2-primary")

    def test_rejects_unknown_schema_version(self):
        data = sample_registry()
        data["schema_version"] = 2
        with self.assertRaisesRegex(RegistryError, "unsupported registry schema"):
            validate_registry(data)

    def test_rejects_duplicate_installation_id(self):
        data = sample_registry()
        data["installations"][1]["id"] = "mo2-primary"
        with self.assertRaisesRegex(RegistryError, "duplicate installation id"):
            validate_registry(data)

    def test_rejects_duplicate_executable_even_with_different_ids(self):
        data = sample_registry()
        data["installations"][1]["executable"] = data["installations"][0]["executable"]
        with self.assertRaisesRegex(RegistryError, "duplicate installation executable"):
            validate_registry(data)

    def test_rejects_unknown_installation_reference(self):
        data = sample_registry()
        data["instances"][0]["installation_id"] = "missing"
        with self.assertRaisesRegex(RegistryError, "unknown installation"):
            validate_registry(data)

    def test_rejects_duplicate_instance_path(self):
        data = sample_registry()
        data["instances"][1]["instance_path"] = data["instances"][0]["instance_path"]
        with self.assertRaisesRegex(RegistryError, "duplicate instance path"):
            validate_registry(data)

    def test_rejects_unknown_game_and_runtime_references(self):
        data = sample_registry()
        data["instances"][0]["game_id"] = "missing-game"
        with self.assertRaisesRegex(RegistryError, "unknown game"):
            validate_registry(data)
        data = sample_registry()
        data["instances"][0]["runtime_id"] = "missing-runtime"
        with self.assertRaisesRegex(RegistryError, "unknown runtime"):
            validate_registry(data)

    def test_rejects_invalid_storage_policy_and_external_owner_label(self):
        data = sample_registry()
        data["instances"][0]["storage_policy"] = {"overwrite": "shared-everywhere"}
        with self.assertRaisesRegex(RegistryError, "storage_policy.overwrite"):
            validate_registry(data)
        data = sample_registry()
        data["installations"][0]["owner"] = "managed"
        with self.assertRaisesRegex(RegistryError, "owner must"):
            validate_registry(data)

    def test_rejects_malformed_json_and_wrong_registry_kind(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "registry.json"
            path.write_text("{broken", encoding="utf-8")
            with self.assertRaisesRegex(RegistryError, "invalid registry JSON"):
                load_registry(path)
            path.write_text(json.dumps({"kind": "other", "schema_version": 1}), encoding="utf-8")
            with self.assertRaisesRegex(RegistryError, "unrecognized registry kind"):
                load_registry(path)

    def test_save_and_reload_is_atomic_private_and_round_trips(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config" / "registry.json"
            result = save_registry(path, sample_registry())
            self.assertEqual(load_registry(path), result)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(list(path.parent.glob("*.tmp")), [])

    def test_invalid_save_does_not_replace_existing_registry(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "registry.json"
            save_registry(path, sample_registry())
            before = path.read_bytes()
            invalid = sample_registry()
            invalid["instances"][0]["installation_id"] = "not-registered"
            with self.assertRaises(RegistryError):
                save_registry(path, invalid)
            self.assertEqual(path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
