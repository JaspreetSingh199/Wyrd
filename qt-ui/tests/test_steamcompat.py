from pathlib import Path
import json

import steamcompat
import steam_mo2_launch
import steam_mo2_discovery
from wyrd_mo2 import inspect_mo2_instances, copy_mo2_instance_to_prefix, _steam_root_for_game


def test_prefix_path_is_skyrim_appid():
    assert steamcompat.steam_compat_prefix(Path("/steam")) == Path("/steam/steamapps/compatdata/489830")


def test_launch_refuses_uninstalled_mo2(monkeypatch, tmp_path):
    monkeypatch.setattr(steam_mo2_discovery, "inspect_skyrim_prefix_mo2_installation", lambda game, home: {"installed": False})
    result = steamcompat.launch_mo2_in_skyrim_prefix(tmp_path)
    assert not result["ok"]
    assert "not installed" in result["message"].lower()


def test_launch_refuses_missing_skyrim_prefix(monkeypatch, tmp_path):
    root = tmp_path / "Steam"
    install = tmp_path / "mo2"
    proton = tmp_path / "proton"
    game = tmp_path / "Skyrim"
    steam_prefix = root / "steamapps/compatdata/489830"
    install.mkdir()
    proton.mkdir()
    game.mkdir()
    metadata = {"proton": str(proton), "steam_root": str(root), "game_path": str(game)}
    monkeypatch.setattr(steam_mo2_discovery, "inspect_skyrim_prefix_mo2_installation", lambda game, home: {
        "installed": True, "metadata": metadata, "executable": str(install / "ModOrganizer.exe"),
        "prefix": str(steam_prefix), "install": str(install), "steam_root": str(root),
    })
    monkeypatch.setattr(steam_mo2_launch, "inspect_proton_build", lambda path: {"ok": True})
    monkeypatch.setattr(steam_mo2_launch, "_runtime_environment", lambda proton, root, prefix: {})
    result = steamcompat.launch_mo2_in_skyrim_prefix(tmp_path)
    assert not result["ok"]
    assert "not initialized" in result["message"].lower()


def test_launch_uses_skyrim_compatdata_and_does_not_initialize_it(monkeypatch, tmp_path):
    root = tmp_path / "Steam"
    install = tmp_path / "mo2"
    proton = tmp_path / "Proton"
    game = tmp_path / "Skyrim"
    prefix = root / "steamapps/compatdata/489830"
    (prefix / "pfx/drive_c/users/steamuser/AppData/Local/ModOrganizer/Skyrim Special Edition").mkdir(parents=True)
    (prefix / "pfx/drive_c/users/steamuser/AppData/Local/ModOrganizer/Skyrim Special Edition/ModOrganizer.ini").write_text("[General]\\ngameName=Skyrim Special Edition\\n", encoding="utf-8")
    install.mkdir()
    (install / "ModOrganizer.exe").write_bytes(b"MZ")
    proton.mkdir()
    game.mkdir()
    metadata = {"proton": str(proton), "steam_root": str(root), "game_path": str(game)}
    monkeypatch.setattr(steamcompat, "inspect_wyrd_installation", lambda home: {
        "installed": True, "metadata": metadata, "executable": str(install / "ModOrganizer.exe"
    )})
    monkeypatch.setattr(steam_mo2_launch, "inspect_proton_build", lambda path: {"ok": True})
    monkeypatch.setattr(steam_mo2_launch, "_runtime_environment", lambda proton, root, prefix: {})
    class Proc:
        pid = 1234
    calls = {}
    def fake_popen(command, **kwargs):
        calls["command"] = command
        calls["env"] = kwargs["env"]
        calls["cwd"] = kwargs["cwd"]
        return Proc()
    monkeypatch.setattr(steamcompat.subprocess, "Popen", fake_popen)
    result = steamcompat.launch_mo2_in_skyrim_prefix(tmp_path)
    assert result["ok"]
    assert result["pid"] == 1234
    assert calls["env"]["STEAM_COMPAT_DATA_PATH"] == str(prefix.resolve())
    assert calls["env"]["SteamAppId"] == "489830"
    assert calls["command"][-2:] == ["--instance", "Skyrim Special Edition"]
    assert (prefix / "pfx/drive_c").is_dir()

def test_external_prefix_inventory_is_not_labelled_wyrd_managed(tmp_path):
    prefix = tmp_path / "compatdata/489830"
    instance = prefix / "pfx/drive_c/users/steamuser/AppData/Local/ModOrganizer/Skyrim Special Edition"
    instance.mkdir(parents=True)
    (instance / "ModOrganizer.ini").write_text(
        "[General]\ngameName=Skyrim Special Edition\nselected_profile=Default\n"
        "[Settings]\nbase_directory=%BASE_DIR%\nprofiles_directory=%BASE_DIR%/profiles\n",
        encoding="utf-8",
    )
    (instance / "profiles/Default").mkdir(parents=True)
    (instance / "mods").mkdir()
    (instance / "downloads").mkdir()
    result = inspect_mo2_instances(prefix, require_managed=False)
    assert result["ok"]
    assert result["managed"] is False
    found = result["skyrim_instances"][0]
    assert found["storage"]["mods"]["status"] == "available"
    assert found["storage"]["downloads"]["status"] == "available"
    assert found["storage"]["profiles"]["status"] == "available"


def test_inventory_reports_custom_external_storage_without_traversing_it(tmp_path):
    prefix = tmp_path / "compatdata/489830"
    instance = prefix / "pfx/drive_c/users/steamuser/AppData/Local/ModOrganizer/Skyrim Special Edition"
    instance.mkdir(parents=True)
    outside = tmp_path / "external-mods"
    outside.mkdir()
    (instance / "ModOrganizer.ini").write_text(
        "[General]\ngameName=Skyrim Special Edition\n"
        "[Settings]\nbase_directory=%BASE_DIR%\nmod_directory=" + str(outside) + "\n",
        encoding="utf-8",
    )
    result = inspect_mo2_instances(prefix, require_managed=False)
    assert result["ok"]
    found = result["skyrim_instances"][0]
    assert found["storage"]["mods"]["status"] == "available"
    assert found["storage"]["mods"]["path"] == str(outside.resolve())



def make_source_instance(tmp_path, name="Skyrim Special Edition - G"):
    source = tmp_path / "source" / name
    (source / "profiles/Default").mkdir(parents=True)
    (source / "mods/Example Mod").mkdir(parents=True)
    (source / "downloads").mkdir()
    (source / "ModOrganizer.ini").write_text(
        "[General]\\ngameName=Skyrim Special Edition\\nselected_profile=Default\\n"
        "[Settings]\\nbase_directory=%BASE_DIR%\\n"
        "profiles_directory=%BASE_DIR%/profiles\\n"
        "mod_directory=%BASE_DIR%/mods\\n"
        "download_directory=%BASE_DIR%/downloads\\n",
        encoding="utf-8",
    )
    return source


def test_copy_instance_preserves_source_and_repoints_storage(tmp_path):
    source = make_source_instance(tmp_path)
    prefix = tmp_path / "Steam/steamapps/compatdata/489830"
    (prefix / "pfx/drive_c/users").mkdir(parents=True)
    result = copy_mo2_instance_to_prefix(source, prefix)
    assert result["ok"]
    assert result["status"] == "copied"
    assert result["source_preserved"] is True
    assert (source / "ModOrganizer.ini").is_file()
    assert (source / "mods/Example Mod").is_dir()
    copied = Path(result["destination"])
    assert (copied / "profiles/Default").is_dir()
    assert (copied / "mods/Example Mod").is_dir()
    assert (copied / "downloads").is_dir()
    config = copied / "ModOrganizer.ini"
    import configparser
    parser = configparser.ConfigParser(interpolation=None)
    parser.read(config, encoding="utf-8")
    assert parser["Settings"]["base_directory"] == str(copied)
    assert parser["Settings"]["mod_directory"] == "%BASE_DIR%/mods"


def test_copy_instance_refuses_external_mod_storage(tmp_path):
    source = make_source_instance(tmp_path)
    external = tmp_path / "shared-mods"
    external.mkdir()
    config = source / "ModOrganizer.ini"
    config.write_text(
        "[General]\\ngameName=Skyrim Special Edition\\n"
        "[Settings]\\nbase_directory=%BASE_DIR%\\n"
        "mod_directory=" + str(external) + "\\n",
        encoding="utf-8",
    )
    prefix = tmp_path / "Steam/steamapps/compatdata/489830"
    (prefix / "pfx/drive_c/users").mkdir(parents=True)
    result = copy_mo2_instance_to_prefix(source, prefix)
    assert not result["ok"]
    assert "outside" in result["message"].lower()
    assert source.is_dir()
    assert not (prefix / "pfx/drive_c/users/steamuser/AppData/Local/ModOrganizer/Skyrim Special Edition - G").exists()


def test_copy_instance_refuses_existing_destination_without_overwrite(tmp_path):
    source = make_source_instance(tmp_path)
    prefix = tmp_path / "Steam/steamapps/compatdata/489830"
    users = prefix / "pfx/drive_c/users/steamuser/AppData/Local/ModOrganizer"
    users.mkdir(parents=True)
    destination = users / source.name
    destination.mkdir()
    (destination / "keep.txt").write_text("keep")
    result = copy_mo2_instance_to_prefix(source, prefix)
    assert not result["ok"]
    assert (destination / "keep.txt").read_text() == "keep"
    assert source.is_dir()


def _external_game_layout(tmp_path):
    root = tmp_path / ".steam/steam"
    game = root / "steamapps/common/Skyrim Special Edition"
    game.mkdir(parents=True)
    prefix = root / "steamapps/compatdata/489830"
    exe = prefix / "pfx/drive_c/Modding/MO2/ModOrganizer.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"MZ-external")
    return root, game, prefix, exe


def test_external_mo2_discovery_is_read_only_and_not_managed(monkeypatch, tmp_path):
    root, game, prefix, exe = _external_game_layout(tmp_path)
    before = {p.relative_to(prefix): p.read_bytes() for p in exe.parent.rglob("*") if p.is_file()}
    monkeypatch.setattr(steam_mo2_discovery, "_steam_root_for_game", lambda game, home: root)
    state = steamcompat.inspect_external_skyrim_prefix_mo2_installation(game, tmp_path)
    assert state["installed"] is True
    assert state["managed"] is False
    assert state["executable"] == str(exe)
    assert not (exe.parent / ".wyrd-managed.json").exists()
    after = {p.relative_to(prefix): p.read_bytes() for p in exe.parent.rglob("*") if p.is_file()}
    assert after == before


def test_external_mo2_refuses_symlinked_executable(monkeypatch, tmp_path):
    root, game, prefix, exe = _external_game_layout(tmp_path)
    outside = tmp_path / "ModOrganizer.exe"
    outside.write_bytes(b"MZ-external")
    exe.unlink()
    exe.symlink_to(outside)
    monkeypatch.setattr(steam_mo2_discovery, "_steam_root_for_game", lambda game, home: root)
    state = steamcompat.inspect_external_skyrim_prefix_mo2_installation(game, tmp_path)
    assert not state["installed"]


def test_external_mo2_refuses_symlinked_install_path(monkeypatch, tmp_path):
    root = tmp_path / "Steam"
    game = root / "steamapps/common/Skyrim Special Edition"
    game.mkdir(parents=True)
    prefix = root / "steamapps/compatdata/489830"
    drive = prefix / "pfx/drive_c"
    drive.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (drive / "Modding").symlink_to(outside, target_is_directory=True)
    monkeypatch.setattr(steam_mo2_discovery, "_steam_root_for_game", lambda game, home: root)
    state = steamcompat.inspect_external_skyrim_prefix_mo2_installation(game, tmp_path)
    assert not state["installed"]
    assert "symlink" in state["message"].lower()


def test_external_mo2_refuses_invalid_ownership_marker(monkeypatch, tmp_path):
    root, game, prefix, exe = _external_game_layout(tmp_path)
    (exe.parent / ".wyrd-managed.json").write_text('{"managed_by":"SomeoneElse"}', encoding="utf-8")
    monkeypatch.setattr(steam_mo2_discovery, "_steam_root_for_game", lambda game, home: root)
    state = steamcompat.inspect_external_skyrim_prefix_mo2_installation(game, tmp_path)
    assert not state["installed"]
    assert "marker" in state["message"].lower() or "ownership" in state["message"].lower()


def test_external_launch_uses_skyrim_prefix_and_does_not_write_marker(monkeypatch, tmp_path):
    root, game, prefix, exe = _external_game_layout(tmp_path)
    proton = tmp_path / "Proton"
    (proton / "files/bin").mkdir(parents=True)
    (proton / "proton").write_text("#!/bin/sh\\n")
    header = bytearray(20)
    header[:4] = b"\\x7fELF"
    header[4] = 2
    header[5] = 1
    header[18:20] = (62).to_bytes(2, "little")
    (proton / "files/bin/wine").write_bytes(header)
    instance = prefix / "pfx/drive_c/users/steamuser/AppData/Local/ModOrganizer/Skyrim Special Edition"
    instance.mkdir(parents=True)
    (instance / "ModOrganizer.ini").write_text(
        "[General]\\ngameName=Skyrim Special Edition\\n"
        "[Settings]\\nbase_directory=%BASE_DIR%\\n"
        "profiles_directory=%BASE_DIR%/profiles\\n",
        encoding="utf-8",
    )
    (instance / "profiles/Default").mkdir(parents=True)
    monkeypatch.setattr(steam_mo2_launch, "_steam_root_for_game", lambda game, home: root)
    monkeypatch.setattr(steam_mo2_launch, "inspect_proton_build", lambda path: {"ok": True})
    monkeypatch.setattr(steam_mo2_launch, "_runtime_environment", lambda proton, steam, prefix: {})
    class Process:
        pid = 876
    captured = {}
    def fake_popen(command, **kwargs):
        captured["command"] = command
        captured["env"] = kwargs["env"]
        captured["cwd"] = kwargs["cwd"]
        return Process()
    monkeypatch.setattr(steamcompat.subprocess, "Popen", fake_popen)
    before = exe.read_bytes()
    result = steamcompat.launch_external_mo2_in_skyrim_prefix(proton, game, tmp_path, "Skyrim Special Edition")
    assert result["ok"], result
    assert result["managed"] is False
    assert captured["env"]["STEAM_COMPAT_DATA_PATH"] == str(prefix.resolve())
    assert captured["env"]["SteamAppId"] == "489830"
    assert captured["command"][-2:] == ["--instance", "Skyrim Special Edition"]
    assert captured["command"][-3] == str(exe)
    assert exe.read_bytes() == before
    assert not (exe.parent / ".wyrd-managed.json").exists()


def test_external_prefix_inventory_uses_validated_install_and_is_read_only(monkeypatch, tmp_path):
    root, game, prefix, exe = _external_game_layout(tmp_path)
    instance = prefix / "pfx/drive_c/users/steamuser/AppData/Local/ModOrganizer/Skyrim Special Edition"
    instance.mkdir(parents=True)
    config = instance / "ModOrganizer.ini"
    config.write_text(
        "[General]\\ngameName=Skyrim Special Edition\\n"
        "[Settings]\\nbase_directory=%BASE_DIR%\\nprofiles_directory=%BASE_DIR%/profiles\\n",
        encoding="utf-8",
    )
    (instance / "profiles/Default").mkdir(parents=True)
    before = config.read_bytes()
    monkeypatch.setattr(steam_mo2_discovery, "_steam_root_for_game", lambda game, home: root)
    result = steamcompat.inspect_any_skyrim_prefix_mo2_instances(game, tmp_path)
    assert result["ok"]
    assert result["managed"] is False
    assert [item["name"] for item in result["skyrim_instances"]] == ["Skyrim Special Edition"]
    assert config.read_bytes() == before
    assert not (exe.parent / ".wyrd-managed.json").exists()


def _make_steam_discovery_fixture(tmp_path):
    steam = tmp_path / "Steam"
    library = tmp_path / "Library"
    game = library / "steamapps/common/Skyrim Special Edition"
    game.mkdir(parents=True)
    (game / "SkyrimSE.exe").write_bytes(b"fixture")
    (library / "steamapps/appmanifest_489830.acf").write_text(
        '"AppState" { "appid" "489830" "installdir" "Skyrim Special Edition" }',
        encoding="utf-8",
    )
    (steam / "steamapps").mkdir(parents=True, exist_ok=True)
    (steam / "steamapps/libraryfolders.vdf").write_text(
        '"libraryfolders" { "0" { "path" "' + str(steam) + '" } '
        '"1" { "path" "' + str(library) + '" } }',
        encoding="utf-8",
    )
    prefix = steam / "steamapps/compatdata/489830"
    (prefix / "pfx/drive_c").mkdir(parents=True)
    dosdevices = prefix / "pfx/dosdevices"
    dosdevices.mkdir()
    return steam, library, game, prefix, dosdevices


def test_steam_root_resolver_finds_owner_of_secondary_library(tmp_path):
    home = tmp_path / "home"
    steam = home / ".steam/steam"
    library = tmp_path / "Secondary Library"
    game = library / "steamapps/common/Skyrim Special Edition"
    game.mkdir(parents=True)
    (steam / "steamapps").mkdir(parents=True)
    (steam / "steamapps/libraryfolders.vdf").write_text(
        '"libraryfolders" { "0" { "path" "' + str(steam) + '" } '
        '"1" { "path" "' + str(library) + '" } }',
        encoding="utf-8",
    )

    assert _steam_root_for_game(game, home) == steam.resolve()


def test_dynamic_wine_path_prefers_discovered_root_drive_over_legacy_library_alias(tmp_path):
    steam, library, game, prefix, dosdevices = _make_steam_discovery_fixture(tmp_path)
    (dosdevices / "z:").symlink_to("/", target_is_directory=True)
    (dosdevices / "t:").symlink_to(library, target_is_directory=True)

    result = steamcompat.discover_wine_game_path(prefix, game, steam)

    assert result["ok"], result
    assert result["drive"] == "Z:"
    assert result["windows_game_path"] == (
        "Z:" + str(game.resolve()).replace("/", "\\\\")
    )
    assert Path(result["resolved_game_path"]) == game.resolve()
    assert not (dosdevices / "s:").exists()
    assert not (dosdevices / "s:").is_symlink()


def test_dynamic_wine_path_uses_arbitrary_existing_drive_when_no_root_mapping(tmp_path):
    steam, library, game, prefix, dosdevices = _make_steam_discovery_fixture(tmp_path)
    (dosdevices / "t:").symlink_to(library, target_is_directory=True)

    result = steamcompat.discover_wine_game_path(prefix, game, steam)

    assert result["ok"], result
    assert result["drive"] == "T:"
    assert result["windows_game_path"] == r"T:\\steamapps\\common\\Skyrim Special Edition"
    assert not (dosdevices / "s:").exists()
    assert not (dosdevices / "s:").is_symlink()


def test_dynamic_wine_path_falls_back_to_existing_root_drive_mapping(tmp_path):
    steam, library, game, prefix, dosdevices = _make_steam_discovery_fixture(tmp_path)
    (dosdevices / "z:").symlink_to("/", target_is_directory=True)

    result = steamcompat.discover_wine_game_path(prefix, game, steam)

    assert result["ok"], result
    assert result["drive"] == "Z:"
    assert result["windows_game_path"] == (
        "Z:" + str(game.resolve()).replace("/", "\\\\")
    )
    assert not (dosdevices / "s:").exists()
    assert not (dosdevices / "s:").is_symlink()


def test_dynamic_wine_path_fails_closed_without_existing_drive_mapping(tmp_path):
    steam, library, game, prefix, dosdevices = _make_steam_discovery_fixture(tmp_path)

    result = steamcompat.discover_wine_game_path(prefix, game, steam)

    assert not result["ok"]
    assert "No existing Proton drive mapping" in result["message"]


def test_registry_requires_a_dynamically_discovered_path(tmp_path):
    steam, library, game, prefix, dosdevices = _make_steam_discovery_fixture(tmp_path)
    result = steamcompat.configure_skyrim_se_registry(
        prefix, game, steam, tmp_path / "Proton"
    )
    assert not result["ok"]
    assert "dynamically discovered Proton game path is required" in result["message"]



def test_instance_discovery_does_not_require_conventional_mo2_install(monkeypatch, tmp_path):
    root = tmp_path / "Steam"
    game = root / "steamapps/common/Skyrim Special Edition"
    game.mkdir(parents=True)
    prefix = root / "steamapps/compatdata/489830"
    users = prefix / "pfx/drive_c/users/steamuser"
    users.mkdir(parents=True)
    monkeypatch.setattr(steam_mo2_discovery, "_steam_root_for_game", lambda game, home: root)
    monkeypatch.setattr(
        steam_mo2_discovery, "inspect_skyrim_prefix_mo2_installation",
        lambda game, home: {"installed": False, "managed": False},
    )
    monkeypatch.setattr(
        steam_mo2_discovery, "inspect_external_skyrim_prefix_mo2_installation",
        lambda game, home: {"installed": False, "managed": False,
                            "message": "No conventional executable found."},
    )
    captured = {}

    def fake_inventory(prefix_root, install_root=None, require_managed=True,
                       detected_game_path=None):
        captured.update(prefix=prefix_root, install=install_root,
                        require_managed=require_managed, game=detected_game_path)
        return {"ok": True, "instances": [{"name": "Portable"}],
                "skyrim_instances": [], "message": "Found one instance."}

    monkeypatch.setattr(steam_mo2_discovery, "inspect_mo2_instances", fake_inventory)
    result = steamcompat.inspect_any_skyrim_prefix_mo2_instances(game, tmp_path)

    assert result["ok"]
    assert result["instances"][0]["name"] == "Portable"
    assert captured["prefix"] == prefix
    assert captured["install"] is None
    assert captured["require_managed"] is False
    assert captured["game"] == game
    assert result["installation_message"] == "No conventional executable found."
