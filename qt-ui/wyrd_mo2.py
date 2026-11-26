"""Wyrd MO2 settings and instance operations with compatibility exports.

All paths are user-local. Existing MO2 installs and unknown prefixes are never
adopted or overwritten automatically.
"""
from __future__ import annotations

from mo2_instance_copy import copy_mo2_instance_to_prefix
from wyrd_settings import (
    write_settings, read_settings, reset_wyrd_settings, mark_first_run_completed,
)
from mo2_instances import (
    _instance_roots, _same_mo2_game_path, inspect_mo2_instances,
    _resolve_windows_path,
)
from mo2_storage import _resolve_mo2_storage_path, inspect_mo2_storage
from launch_log_diagnostics import read_launch_diagnostics, classify_launch_diagnostics
from process_monitor import (
    detect_game_processes, detect_skyrim_processes,
    _proc_identity, _proc_start_time, _is_descendant_process,
    launch_process_status,
)

from proton_runtime import (
    ProgressCallback, PREFIX_DIR_NAME, managed_paths, inspect_proton_build, discover_proton_builds,
    reset_mo2_compatdata, inspect_wyrd_installation, _steam_root_for_game,
    _runtime_environment, _run_proton, _prefix_ready, _initialize_prefix,
)
from steam_prefix import APP_ID
from audio_compatibility import (
    _validated_wyrd_audio_prefix, _sha256_file, _resolve_xact_dlls,
    inspect_audio_compatibility, repair_audio_compatibility,
)
from mo2_launch import (
    launch_mo2, inspect_skyrim_launch_targets, launch_skyrim_via_mo2,
)


