"""Fast, UI-independent contract tests for Wyrd V1.

These tests cover the safety and launch boundaries that must remain stable.
Interactive Qt behaviour and real Skyrim audio are verified manually on the
target Steam/Proton installation, not simulated in headless CI.
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import steam_mo2_launch
import steam_game_registry
from steam_prefix import steam_compat_prefix
from wyrd_mo2 import managed_paths, read_settings, reset_wyrd_settings


class WyrdV1ContractTests(unittest.TestCase):
    def _launch_fixture(self, home: Path):
        steam_root = home / ".local/share/Steam"
        game = steam_root / "steamapps/common/Skyrim Special Edition"
        game.mkdir(parents=True)
        expected_binary = game / "skse64_loader.exe"
        expected_binary.write_bytes(b"SKSE fixture")

        prefix = steam_compat_prefix(steam_root)
        (prefix / "pfx/drive_c/users/steamuser").mkdir(parents=True)
        install = prefix / "pfx/drive_c/Modding/Skyrim SE"
        install.mkdir(parents=True)
        (install / "ModOrganizer.exe").write_bytes(b"MO2 fixture")

        # Model a real portable MO2 instance. The launch adapter deliberately
        # re-discovers the selected instance immediately before starting it.
        dosdevices = prefix / "pfx/dosdevices"
        dosdevices.mkdir(parents=True, exist_ok=True)
        (dosdevices / "s:").symlink_to(steam_root, target_is_directory=True)
        (install / "ModOrganizer.ini").write_text(
            "[General]\\n"
            "gameName=Skyrim Special Edition\\n"
            r"gamePath=S:\\steamapps\\common\\Skyrim Special Edition" + "\\n",
            encoding="utf-8",
        )

        proton = home / "Proton"
        proton.mkdir()
        wrapper = proton / "proton"
        wrapper.write_text("#!/bin/sh\n")
        wrapper.chmod(0o755)
        return steam_root, game, prefix, install, proton, expected_binary

    def test_skyrim_compat_prefix_is_appid_489830(self):
        self.assertEqual(
            steam_compat_prefix(Path("/steam")),
            Path("/steam/steamapps/compatdata/489830"),
        )

    def test_skyrim_launch_uses_mo2_and_existing_steamcompat_context(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            steam_root, game, prefix, install, proton, binary = self._launch_fixture(home)
            targets = {"ok": True, "targets": [{
                "title": "SKSE", "binary": str(binary),
            }]}
            with (
                patch.object(steam_mo2_launch, "inspect_proton_build", return_value={"ok": True}),
                patch.object(steam_mo2_launch, "inspect_skyrim_launch_targets", return_value=targets),
                patch.object(steam_mo2_launch, "_runtime_environment", return_value={"PATH": "/usr/bin"}),
                patch.object(steam_mo2_launch.subprocess, "Popen") as popen,
            ):
                popen.return_value.pid = 4321
                result = steam_mo2_launch.launch_skyrim_via_mo2_in_skyrim_prefix(
                    proton, game, "Skyrim SE", "SKSE", binary,
                    prefix_path=prefix, install_root=install,
                    steam_root_path=steam_root, home=home,
                )

            self.assertTrue(result["ok"], result)
            command = popen.call_args.args[0]
            self.assertEqual(command, [
                str(proton / "proton"), "run", str(install / "ModOrganizer.exe"),
                "--instance", "Skyrim SE", "run", "--executable", "SKSE",
            ])
            env = popen.call_args.kwargs["env"]
            self.assertEqual(env["SteamAppId"], "489830")
            self.assertEqual(env["SteamGameId"], "489830")
            self.assertEqual(env["STEAM_COMPAT_APP_ID"], "489830")
            self.assertEqual(env["STEAM_COMPAT_DATA_PATH"], str(prefix.resolve()))
            self.assertNotIn("xaudio2_7=", env.get("WINEDLLOVERRIDES", ""))
            self.assertEqual(result["prefix"], str(prefix.resolve()))
            self.assertEqual(popen.call_count, 1)

    def test_skyrim_launch_refuses_to_create_a_missing_steam_prefix(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            steam_root = home / ".local/share/Steam"
            game = steam_root / "steamapps/common/Skyrim Special Edition"
            game.mkdir(parents=True)
            proton = home / "Proton"
            proton.mkdir()
            (proton / "proton").write_text("#!/bin/sh\n")
            prefix = steam_compat_prefix(steam_root)

            with (
                patch.object(steam_mo2_launch, "inspect_proton_build", return_value={"ok": True}),
                patch.object(steam_mo2_launch.subprocess, "Popen") as popen,
            ):
                result = steam_mo2_launch.launch_skyrim_via_mo2_in_skyrim_prefix(
                    proton, game, "Skyrim SE", "SKSE", game / "skse64_loader.exe",
                    prefix_path=prefix, steam_root_path=steam_root, home=home,
                )

            self.assertFalse(result["ok"])
            self.assertFalse(prefix.exists())
            popen.assert_not_called()

    def test_skyrim_launch_rejects_a_stale_or_mismatched_binary(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            steam_root, game, prefix, install, proton, binary = self._launch_fixture(home)
            other_binary = game / "SkyrimSE.exe"
            other_binary.write_bytes(b"Skyrim fixture")
            targets = {"ok": True, "targets": [{
                "title": "SKSE", "binary": str(other_binary),
            }]}
            with (
                patch.object(steam_mo2_launch, "inspect_proton_build", return_value={"ok": True}),
                patch.object(steam_mo2_launch, "inspect_skyrim_launch_targets", return_value=targets),
                patch.object(steam_mo2_launch.subprocess, "Popen") as popen,
            ):
                result = steam_mo2_launch.launch_skyrim_via_mo2_in_skyrim_prefix(
                    proton, game, "Skyrim SE", "SKSE", binary,
                    prefix_path=prefix, install_root=install,
                    steam_root_path=steam_root, home=home,
                )

            self.assertFalse(result["ok"])
            popen.assert_not_called()

    def test_reset_wyrd_settings_preserves_mo2_registry(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            config_home = home / ".config"
            with patch.dict(os.environ, {"XDG_CONFIG_HOME": str(config_home)}):
                paths = managed_paths(home)
                paths["config"].mkdir(parents=True)
                paths["settings"].write_text(json.dumps({
                    "proton": "/old/proton", "first_run_completed": True,
                }), encoding="utf-8")
                registry = paths["config"] / "mo2-registry.json"
                registry.write_text(
                    '{"kind":"wyrd-mo2-registry","version":1}', encoding="utf-8"
                )

                result = reset_wyrd_settings(home)

                self.assertTrue(result["ok"], result)
                self.assertFalse(paths["settings"].exists())
                self.assertTrue(registry.is_file())
                self.assertEqual(read_settings(home), {})

    def test_reset_wyrd_settings_refuses_symlinked_preferences(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            config_home = home / ".config"
            external = home / "important.json"
            external.write_text('{"keep":true}', encoding="utf-8")
            with patch.dict(os.environ, {"XDG_CONFIG_HOME": str(config_home)}):
                paths = managed_paths(home)
                paths["config"].mkdir(parents=True)
                paths["settings"].symlink_to(external)

                result = reset_wyrd_settings(home)

                self.assertFalse(result["ok"])
                self.assertEqual(external.read_text(encoding="utf-8"), '{"keep":true}')
                self.assertTrue(paths["settings"].is_symlink())


class SteamGameRegistryRefreshTests(unittest.TestCase):
    def test_registry_path_parser_decodes_wine_escaped_backslashes(self):
        with tempfile.TemporaryDirectory() as tmp:
            registry = Path(tmp) / "system.reg"
            registry.write_text(
                '[Software\\\\WOW6432Node\\\\Bethesda Softworks\\\\Skyrim Special Edition]\n'
                '"Installed Path"="S:\\\\steamapps\\\\common\\\\Skyrim Special Edition\\\\"\n',
                encoding="utf-8",
            )

            self.assertEqual(
                steam_game_registry._read_installed_path(registry),
                "S:\\steamapps\\common\\Skyrim Special Edition\\",
            )

    def test_matching_registry_path_does_not_start_proton_on_refresh(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            steam_root = home / ".local/share/Steam"
            game = steam_root / "steamapps/common/Skyrim Special Edition"
            game.mkdir(parents=True)
            (game / "SkyrimSE.exe").write_bytes(b"fixture")
            prefix = steam_game_registry.steam_compat_prefix(steam_root)
            (prefix / "pfx").mkdir(parents=True)
            proton = home / "Proton"
            proton.mkdir()
            (proton / "proton").write_text("#!/bin/sh\n", encoding="utf-8")
            windows_path = r"S:\steamapps\common\Skyrim Special Edition"

            with (
                patch.object(steam_game_registry, "_resolve_windows_path", return_value=game),
                patch.object(steam_game_registry, "_read_installed_path", return_value=windows_path + "\\"),
            ):
                result = steam_game_registry.configure_skyrim_se_registry(
                    prefix, game, steam_root, proton, windows_path,
                )

            self.assertTrue(result["ok"], result)
            self.assertTrue(result["already_configured"])

    def test_missing_registry_value_fails_read_only_without_proton_repair(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            steam_root = home / ".local/share/Steam"
            game = steam_root / "steamapps/common/Skyrim Special Edition"
            game.mkdir(parents=True)
            (game / "SkyrimSE.exe").write_bytes(b"fixture")
            prefix = steam_game_registry.steam_compat_prefix(steam_root)
            (prefix / "pfx").mkdir(parents=True)
            proton = home / "Proton"
            proton.mkdir()
            (proton / "proton").write_text("#!/bin/sh\\n", encoding="utf-8")
            windows_path = r"S:\\steamapps\\common\\Skyrim Special Edition"

            with (
                patch.object(steam_game_registry, "_resolve_windows_path", return_value=game),
                patch.object(steam_game_registry, "_read_installed_path", return_value=None),
            ):
                result = steam_game_registry.configure_skyrim_se_registry(
                    prefix, game, steam_root, proton, windows_path,
                )

            self.assertFalse(result["ok"])
            self.assertIn("did not start Proton", result["message"])


if __name__ == "__main__":
    unittest.main()
