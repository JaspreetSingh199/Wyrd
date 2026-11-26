"""Verify and synchronize Skyrim's existing Steam/MO2 game-path mapping.

This service is deliberately UI-independent. It verifies the Proton drive
mapping before updating MO2 instance metadata or Bethesda registry values.
"""
from __future__ import annotations

from pathlib import Path

from proton_runtime import _steam_root_for_game
from steam_drive_mapping import discover_wine_game_path, verify_wine_game_path
from steam_game_registry import configure_skyrim_se_registry
from mo2_registry import synchronize_mo2_instance_game_paths


def verify_mo2_game_mapping(
    game_path: Path, prefix_path: Path, proton_value: str | None
) -> tuple[dict, list[str]]:
    """Return verified mapping state and diagnostic messages; fail closed."""
    messages: list[str] = []
    try:
        steam_root = _steam_root_for_game(game_path, Path.home())
        state = discover_wine_game_path(prefix_path, game_path, steam_root)
        if state.get("ok"):
            if proton_value:
                wine_path_state = verify_wine_game_path(
                    Path(proton_value), prefix_path, steam_root,
                    state["windows_game_path"],
                )
            else:
                wine_path_state = {
                    "ok": False, "message": "No Proton runtime is selected."
                }
            state["proton_path_verification"] = wine_path_state
            if not wine_path_state.get("ok"):
                state["ok"] = False
                messages.append(
                    "Proton game-path verification failed closed: "
                    + wine_path_state.get("message", "unknown error")
                )
            sync_state = (
                synchronize_mo2_instance_game_paths(
                    prefix_path, game_path, state["windows_game_path"]
                )
                if wine_path_state.get("ok") else
                {"ok": False, "message": wine_path_state.get(
                    "message", "Proton path verification failed."
                )}
            )
            state["instance_sync"] = sync_state
            if sync_state.get("ok"):
                changed = sync_state.get("updated", [])
                unchanged = sync_state.get("unchanged", [])
                messages.append(
                    "MO2 gamePath synchronization: "
                    f"{len(changed)} updated, {len(unchanged)} already current; "
                    + state["windows_game_path"]
                )
            else:
                state["ok"] = False
                messages.append(
                    "MO2 gamePath synchronization failed closed: "
                    + sync_state.get("message", "unknown error")
                )
            if proton_value and state.get("ok"):
                registry_state = configure_skyrim_se_registry(
                    prefix_path, game_path, steam_root, Path(proton_value),
                    state["windows_game_path"],
                )
                state["registry"] = registry_state
                if registry_state.get("ok"):
                    messages.append(
                        "MO2 Bethesda registry: " + registry_state["windows_path"]
                    )
                else:
                    state["ok"] = False
                    messages.append(
                        "MO2 Bethesda registry provisioning failed closed: "
                        + registry_state.get("message", "unknown error")
                    )
            elif not proton_value:
                state["registry"] = {
                    "ok": False, "message": "No Proton runtime is selected."
                }
    except (OSError, RuntimeError, ValueError) as error:
        state = {"ok": False, "message": str(error)}

    if state.get("ok"):
        messages.append(
            "MO2 Steam discovery: " + state["windows_game_path"]
            + " resolves to " + state["resolved_game_path"]
        )
    else:
        messages.append(
            "MO2 Steam discovery verification failed closed: "
            + state.get("message", "unknown mapping error")
        )
    return state, messages
