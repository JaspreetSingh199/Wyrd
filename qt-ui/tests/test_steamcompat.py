from pathlib import Path
import json

import steamcompat
from wyrd_mo2 import inspect_mo2_instances, copy_mo2_instance_to_prefix


def test_prefix_path_is_skyrim_appid():
    assert steamcompat.steam_compat_prefix(Path("/steam")) == Path("/steam/steamapps/compatdata/489830")


def test_launch_refuses_uninstalled_mo2(monkeypatch, tmp_path):
    monkeypatch.setattr(steamcompat, "inspect_wyrd_installation", lambda home: {"installed": False})
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
    monkeypatch.setattr(steamcompat, "inspect_wyrd_installation", lambda home: {
        "installed": True, "metadata": metadata, "executable": str(install / "ModOrganizer.exe"
    )})
    monkeypatch.setattr(steamcompat, "inspect_proton_build", lambda path: {"ok": True})
    monkeypatch.setattr(steamcompat, "_runtime_environment", lambda proton, root, prefix: {})
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
    monkeypatch.setattr(steamcompat, "inspect_proton_build", lambda path: {"ok": True})
    monkeypatch.setattr(steamcompat, "_runtime_environment", lambda proton, root, prefix: {})
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
    monkeypatch.setattr(steamcompat, "_steam_root_for_game", lambda game, home: root)
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
    monkeypatch.setattr(steamcompat, "_steam_root_for_game", lambda game, home: root)
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
    monkeypatch.setattr(steamcompat, "_steam_root_for_game", lambda game, home: root)
    state = steamcompat.inspect_external_skyrim_prefix_mo2_installation(game, tmp_path)
    assert not state["installed"]
    assert "symlink" in state["message"].lower()


def test_external_mo2_refuses_invalid_ownership_marker(monkeypatch, tmp_path):
    root, game, prefix, exe = _external_game_layout(tmp_path)
    (exe.parent / ".wyrd-managed.json").write_text('{"managed_by":"SomeoneElse"}', encoding="utf-8")
    monkeypatch.setattr(steamcompat, "_steam_root_for_game", lambda game, home: root)
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
    monkeypatch.setattr(steamcompat, "_steam_root_for_game", lambda game, home: root)
    monkeypatch.setattr(steamcompat, "inspect_proton_build", lambda path: {"ok": True})
    monkeypatch.setattr(steamcompat, "_runtime_environment", lambda proton, steam, prefix: {})
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
    monkeypatch.setattr(steamcompat, "_steam_root_for_game", lambda game, home: root)
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


def test_s_drive_mapping_reconciles_secondary_steam_library(tmp_path):
    steam, library, game, prefix, dosdevices = _make_steam_discovery_fixture(tmp_path)
    (dosdevices / "s:").symlink_to(steam, target_is_directory=True)

    result = steamcompat.configure_steam_library_drive(prefix, game, steam)

    assert result["ok"], result
    assert result["changed"] is True
    assert (dosdevices / "s:").resolve() == library.resolve()
    assert result["windows_game_path"] == r"S:\steamapps\common\Skyrim Special Edition"
    assert Path(result["resolved_game_path"]) == game.resolve()


def test_s_drive_mapping_is_idempotent_when_library_is_already_mapped(tmp_path):
    steam, library, game, prefix, dosdevices = _make_steam_discovery_fixture(tmp_path)
    link = dosdevices / "s:"
    link.symlink_to(library, target_is_directory=True)
    original_target = link.resolve()

    result = steamcompat.configure_steam_library_drive(prefix, game, steam)

    assert result["ok"], result
    assert result["changed"] is False
    assert link.is_symlink()
    assert link.resolve() == original_target == library.resolve()
    assert result["windows_game_path"] == r"S:\steamapps\common\Skyrim Special Edition"


def test_s_drive_mapping_refuses_non_symlink_entry(tmp_path):
    steam, library, game, prefix, dosdevices = _make_steam_discovery_fixture(tmp_path)
    (dosdevices / "s:").write_text("leave me alone", encoding="utf-8")

    result = steamcompat.configure_steam_library_drive(prefix, game, steam)

    assert not result["ok"]
    assert "not a symlink" in result["message"]
    assert (dosdevices / "s:").read_text(encoding="utf-8") == "leave me alone"


def test_s_drive_mapping_refuses_wrong_compatdata_prefix(tmp_path):
    steam, library, game, prefix, dosdevices = _make_steam_discovery_fixture(tmp_path)
    wrong_prefix = tmp_path / "other-prefix"
    (wrong_prefix / "pfx/drive_c").mkdir(parents=True)
    (wrong_prefix / "pfx/dosdevices").mkdir()

    result = steamcompat.configure_steam_library_drive(wrong_prefix, game, steam)

    assert not result["ok"]
    assert "AppID 489830" in result["message"]
    assert not (wrong_prefix / "pfx/dosdevices/s:").exists()
