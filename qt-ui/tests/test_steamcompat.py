from pathlib import Path
import json

import steamcompat


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
    (prefix / "pfx/drive_c").mkdir(parents=True)
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
    assert calls["command"][-1] == str(install / "ModOrganizer.exe")
    assert (prefix / "pfx/drive_c").is_dir()
