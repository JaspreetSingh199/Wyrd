"""Backward-compatible exports for the split Skyrim Steam integration modules.

New code should import from the focused modules directly:
steam_drive_mapping, steam_game_registry, steam_mo2_discovery,
steam_mo2_launch, steam_mo2_management, steam_prefix and mo2_registry.
"""
from __future__ import annotations

from steam_prefix import APP_ID, steam_compat_prefix
from mo2_registry import _reject_symlinked_path_components, synchronize_mo2_instance_game_paths
from steam_drive_mapping import discover_wine_game_path, verify_wine_game_path
from steam_game_registry import configure_skyrim_se_registry
from steam_mo2_discovery import (
    inspect_skyrim_prefix_mo2_installation,
    inspect_skyrim_prefix_mo2_instances,
    inspect_external_skyrim_prefix_mo2_installation,
    inspect_any_skyrim_prefix_mo2_instances,
)
from steam_mo2_launch import (
    launch_external_mo2_in_skyrim_prefix,
    launch_mo2_in_skyrim_prefix,
    launch_skyrim_via_mo2_in_skyrim_prefix,
)
from steam_mo2_management import install_mo2_into_skyrim_prefix, reset_wyrd_mo2
