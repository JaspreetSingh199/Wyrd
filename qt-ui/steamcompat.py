"""Configure and launch MO2 inside Skyrim's Steam Proton compatdata.

This integration reuses Skyrim's existing AppID prefix. It never resets the
prefix or modifies saves/game files; narrowly scoped drive and game-registry
discovery metadata may be provisioned for MO2 compatibility.
"""
from __future__ import annotations

import re
import subprocess
import tempfile
from pathlib import Path

from skyrim_detection import steam_libraries
from proton_runtime import _runtime_environment
from mo2_instances import _resolve_windows_path

from mo2_registry import (
    _reject_symlinked_path_components, synchronize_mo2_instance_game_paths,
)
from steam_prefix import APP_ID, steam_compat_prefix
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
from steam_drive_mapping import discover_wine_game_path, verify_wine_game_path
from steam_game_registry import configure_skyrim_se_registry
