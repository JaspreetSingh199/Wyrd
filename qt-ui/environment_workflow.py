"""Environment discovery and readiness workflow methods for MainWindow."""
from __future__ import annotations

from pathlib import Path

from skyrim_detection import detect_skyrim
from proton_runtime import discover_proton_builds, _steam_root_for_game
from steam_drive_mapping import discover_wine_game_path, verify_wine_game_path
from steam_game_registry import configure_skyrim_se_registry
from mo2_registry import synchronize_mo2_instance_game_paths
from steam_mo2_discovery import (
    inspect_skyrim_prefix_mo2_installation,
    inspect_external_skyrim_prefix_mo2_installation,
    inspect_any_skyrim_prefix_mo2_instances,
)
from ui_path_helpers import _same_filesystem_path, _steam_prefix_for_game_path


class EnvironmentWorkflowMixin:
    def refresh_all(self) -> None:
        self.startup_checks_complete = False
        self._set_scan_progress(2, "Starting environment scan…")
        self.launch_game_button.setEnabled(False)
        self._reset_precheck()
        selected = self.proton_combo.currentData()
        # Suppress currentIndexChanged while rebuilding the combo. Otherwise
        # clearing it can save an empty/default selection before we restore
        # the persisted runtime, overwriting the user's choice.
        self._restoring_selection = True
        self.proton_combo.blockSignals(True)
        self._discover_game_installation()
        self._populate_proton_runtimes(selected)

        self._inspect_mo2_installation_and_mapping()
        self._discover_mo2_instance_inventory()
        self._present_mo2_installation()
        self._finalize_environment_readiness()

    def _inspect_mo2_installation_and_mapping(self) -> None:
        """Inspect the active Skyrim prefix and verify MO2/Steam path integration."""
        self._set_scan_progress(43, "Locating MO2 in Skyrim’s Proton environment…")
        self.mo2_state = inspect_skyrim_prefix_mo2_installation(
            Path(self.game_state["game_dir"])
        ) if self.game_state.get("found") else {
            "installed": False, "managed": False,
            "message": "Skyrim Special Edition was not detected."
        }
        if not self.mo2_state.get("installed") and self.game_state.get("found"):
            self.mo2_state = inspect_external_skyrim_prefix_mo2_installation(
                Path(self.game_state["game_dir"])
            )
        self._set_scan_progress(58, "Verifying Proton drive mappings and synchronizing MO2 game paths…")
        self.mo2_discovery_state = (
            self._verify_mo2_game_mapping(
                Path(self.game_state["game_dir"]).expanduser(),
                self.proton_combo.currentData(),
            )
            if self.mo2_state.get("installed") and self.game_state.get("found")
            else None
        )
        self._update_precheck(
            2, "Skyrim prefix and MO2 installation",
            bool(self.mo2_state.get("installed")),
            self.mo2_state.get("message", "MO2 installation was not found."),
        )
        self.mo2_target_occupied = bool(
            self.mo2_state.get("installed") or self.mo2_state.get("install")
        )
        self.reset_mo2_button.setEnabled(bool(
            self.mo2_state.get("installed") and self.mo2_state.get("managed", True)
        ))

    def _discover_mo2_instance_inventory(self) -> None:
        """Discover MO2 instances independently of executable installation checks."""
        self._set_scan_progress(78, "Scanning MO2 installations and validating instance configurations…")
        if self.game_state.get("found"):
            # Instance inventory is independent of the conventional MO2
            # executable check. Existing instances remain discoverable even
            # when MO2 is installed elsewhere or its executable is unknown.
            self.instance_state = inspect_any_skyrim_prefix_mo2_instances(
                Path(self.game_state["game_dir"])
            )
        else:
            self.instance_state = {
                "ok": False, "instances": [], "skyrim_instances": [],
                "message": "Skyrim Special Edition was not detected; MO2 instance discovery was skipped."
            }
        skyrim_instances = self.instance_state.get("skyrim_instances", [])
        self._update_precheck(
            3, "MO2 instances",
            bool(self.instance_state.get("ok") and self.instance_state.get("instances")),
            f"{len(self.instance_state.get('instances', []))} MO2 instance(s), "
            f"{len(skyrim_instances)} Skyrim SE instance(s) found."
            if self.instance_state.get("ok") else self.instance_state.get("message", "Instance scan unavailable."),
        )

    def _present_mo2_installation(self) -> None:
        """Render the current MO2 installation and instance summary in the UI."""
        skyrim_instances = self.instance_state.get("skyrim_instances", [])
        if self.mo2_state.get("installed"):
            summary = self.instance_state.get("message", "MO2 instance inventory unavailable.")
            if self.mo2_discovery_state is not None:
                if self.mo2_discovery_state.get("ok"):
                    summary += (
                        "\\nSteam discovery: " + self.mo2_discovery_state["windows_game_path"]
                        + " → " + self.mo2_discovery_state["resolved_game_path"]
                    )
                else:
                    summary += (
                        "\\nSteam discovery NOT VERIFIED: "
                        + self.mo2_discovery_state.get("message", "unknown mapping error")
                    )
            if skyrim_instances:
                summary += "\nSkyrim SE instance(s): " + ", ".join(x["name"] for x in skyrim_instances)
                summary += "\nSelected MO2 profile: " + " | ".join(
                    f'{x["name"]}: {x.get("selected_profile") or "No profile selected"}'
                    for x in skyrim_instances
                )
            elif self.instance_state.get("ok"):
                summary += "\nCreate a Skyrim Special Edition instance in MO2 to continue."
            owner = "Wyrd-managed" if self.mo2_state.get("managed", True) else "External (read-only)"
            # Keep the main screen human-readable. Full filesystem paths belong
            # on Advanced → Paths, not in the primary setup/launch workflow.
            if skyrim_instances:
                instance_summary = (
                    f"{len(skyrim_instances)} Skyrim Special Edition instance(s) detected: "
                    + ", ".join(item["name"] for item in skyrim_instances)
                )
            elif self.instance_state.get("ok"):
                instance_summary = (
                    "MO2 is installed, but no Skyrim Special Edition instance is configured yet. "
                    "Open MO2 and create a Skyrim Special Edition instance, then return here."
                )
            else:
                instance_summary = self.instance_state.get(
                    "message", "MO2 instance discovery is unavailable."
                )
            self.mo2_panel.detail_label.setText(
                f"{owner} installation · {self.mo2_state.get('version', 'MO2')}\n"
                f"{instance_summary}"
            )
            self.install_button.hide()
            self.launch_mo2_button.show()
            self.launch_mo2_button.setEnabled(
                bool(self.mo2_state.get("executable") and self.proton_combo.currentData())
            )
            self.launch_game_button.setEnabled(False)
            self._log("Skyrim-prefix MO2 inspection: " + owner + " installation.")
        else:
            self.mo2_panel.detail_label.setText(self.mo2_state.get("message", "MO2 is not installed."))
            self.install_button.setText("Install MO2")
            self.install_button.show()
            self.launch_mo2_button.hide()
            self.install_button.setEnabled(bool(self.game_state.get("found") and self.proton_builds))
            self.launch_game_button.setEnabled(False)
            self.launch_direct_button.setEnabled(bool(self.game_state.get("found")))

    def _finalize_environment_readiness(self) -> None:
        """Restore selection, refresh panels, and derive final launch readiness."""
        self._set_scan_progress(92, "Finalizing environment readiness…")
        self._refresh_audio_status()
        ready = bool(self.game_state.get("found") and self.proton_builds)
        self.install_button.setEnabled(
            ready
            and not (self.worker and self.worker.isRunning())
            and not getattr(self, "mo2_target_occupied", self.mo2_state.get("installed"))
        )
        self.proton_combo.setEnabled(bool(self.proton_builds) and not (self.worker and self.worker.isRunning()))
        self._restore_saved_selection()
        self.proton_combo.blockSignals(False)
        # Resume persistence only after a valid runtime is selected. An unavailable
        # saved path must not be replaced with a guessed/default Proton build.
        self._restoring_selection = not bool(self.proton_combo.currentData())
        # Keep both MO2 panels synchronized with detected Skyrim and the
        # selected Proton runtime. The default prefix is Skyrim's existing
        # Steam compatdata directory; this does not create or reset a prefix.
        self.manage_mo2_library()
        # A modded launch stays disabled until every startup check has completed.
        self.startup_checks_complete = True
        # Live MO2 inventory is authoritative; Wyrd registration is not required.
        self._update_active_instance_from_scan()
        active_ready = self.launch_game_button.isEnabled()
        self._update_precheck(
            4, "Detected MO2 environment", active_ready,
            self.active_instance_label.text(),
        )
        self.precheck_progress.setFormat("")
        self._set_scan_progress(100, "Environment scan complete.")
        self.precheck_status.setText(
            "Startup checks complete. " +
            ("A detected MO2 instance is ready for launch." if active_ready
             else "Some setup is still needed. Review the unchecked items above.")
        )

    def _discover_game_installation(self) -> None:
        """Discover Skyrim and update the installation checklist and summary."""
        self._set_scan_progress(12, "Scanning Steam libraries for Skyrim Special Edition…")
        self.game_state = detect_skyrim()
        saved_game = self._saved_settings.get("game_path")
        if saved_game and Path(saved_game).is_dir():
            detected_game = self.game_state.get("game_dir")
            if not self.game_state.get("found") or detected_game != saved_game:
                self.game_state = {
                    **self.game_state, "found": True,
                    "game_dir": saved_game, "library": "saved Wyrd selection",
                }
        self._update_precheck(
            0, "Skyrim installation", bool(self.game_state.get("found")),
            self.game_state.get("game_dir", "Skyrim Special Edition was not detected."),
        )
        if self.game_state.get("found"):
            game_path = self.game_state["game_dir"]
            self.game_panel.detail_label.setText(
                "Detected:\n" + game_path +
                "\nSteam library: " + self.game_state.get("library", "detected")
            )
            self._log("Skyrim SE detected: " + game_path)
        else:
            roots = self.game_state.get("steam_roots", [])
            self.game_panel.detail_label.setText(
                "Skyrim Special Edition (AppID 489830) was not found. "
                "Install it through Steam and refresh detection.\nSteam roots checked: " +
                (", ".join(roots) if roots else "No Steam library detected.")
            )
            self._log("Skyrim SE not detected.")

    def _populate_proton_runtimes(self, selected: str | None) -> None:
        """Discover Proton builds and restore the preferred existing selection."""
        self._set_scan_progress(28, "Discovering compatible Proton runtimes…")
        self.proton_builds = discover_proton_builds()
        self._update_precheck(
            1, "Compatible Proton runtime", bool(self.proton_builds),
            f"{len(self.proton_builds)} compatible Proton runtime(s) found."
            if self.proton_builds else "No compatible Proton runtime was found.",
        )
        self.proton_combo.clear()
        selected_index = -1
        for build in self.proton_builds:
            label = f'{build["name"]} ({build["architecture"]})'
            self.proton_combo.addItem(label, build["path"])
            self.proton_combo.setItemData(self.proton_combo.count() - 1, build["path"], 3)
            if selected and _same_filesystem_path(selected, build["path"]):
                selected_index = self.proton_combo.count() - 1
        saved_proton = self._saved_settings.get("proton")
        preferred_proton = selected or saved_proton
        if preferred_proton:
            selected_index = next(
                (index for index, build in enumerate(self.proton_builds)
                 if _same_filesystem_path(build["path"], preferred_proton)),
                -1,
            )
        if selected_index >= 0:
            self.proton_combo.setCurrentIndex(selected_index)
        if self.proton_builds:
            self.runtime_status.setText(
                f'{len(self.proton_builds)} compatible Proton build(s) found. '
                "The selected runtime will be saved with this Wyrd installation."
            )
            self._log("Proton discovery: " + ", ".join(item["name"] for item in self.proton_builds))
        else:
            self.runtime_status.setText(
                "No compatible Proton build found. Install a current x86_64 Proton build through Steam or ProtonUp-Qt, then refresh."
            )
            self._log("No compatible Proton builds found.")

    def _verify_mo2_game_mapping(
        self, game_path: Path, proton_value: str | None
    ) -> dict:
        """Verify the existing Steam drive mapping before syncing MO2 metadata."""
        try:
            steam_root = _steam_root_for_game(game_path, Path.home())
            state = discover_wine_game_path(
                Path(self.mo2_state["prefix"]), game_path, steam_root
            )
            if state.get("ok"):
                if proton_value:
                    wine_path_state = verify_wine_game_path(
                        Path(proton_value), Path(self.mo2_state["prefix"]),
                        steam_root, state["windows_game_path"],
                    )
                else:
                    wine_path_state = {
                        "ok": False, "message": "No Proton runtime is selected."
                    }
                state["proton_path_verification"] = wine_path_state
                if not wine_path_state.get("ok"):
                    state["ok"] = False
                    self._log(
                        "Proton game-path verification failed closed: "
                        + wine_path_state.get("message", "unknown error")
                    )
                sync_state = (
                    synchronize_mo2_instance_game_paths(
                        Path(self.mo2_state["prefix"]), game_path,
                        state["windows_game_path"],
                    )
                    if wine_path_state.get("ok") else
                    {"ok": False, "message": wine_path_state.get("message", "Proton path verification failed.")}
                )
                state["instance_sync"] = sync_state
                if sync_state.get("ok"):
                    changed = sync_state.get("updated", [])
                    unchanged = sync_state.get("unchanged", [])
                    self._log(
                        "MO2 gamePath synchronization: "
                        f"{len(changed)} updated, {len(unchanged)} already current; "
                        + state["windows_game_path"]
                    )
                else:
                    state["ok"] = False
                    self._log(
                        "MO2 gamePath synchronization failed closed: "
                        + sync_state.get("message", "unknown error")
                    )
                if proton_value and state.get("ok"):
                    registry_state = configure_skyrim_se_registry(
                        Path(self.mo2_state["prefix"]), game_path, steam_root,
                        Path(proton_value), state["windows_game_path"],
                    )
                    state["registry"] = registry_state
                    if registry_state.get("ok"):
                        self._log("MO2 Bethesda registry: " + registry_state["windows_path"])
                    else:
                        state["ok"] = False
                        self._log(
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
            self._log(
                "MO2 Steam discovery: " + state["windows_game_path"]
                + " resolves to " + state["resolved_game_path"]
            )
        else:
            self._log(
                "MO2 Steam discovery verification failed closed: "
                + state.get("message", "unknown mapping error")
            )
        return state

    def _update_active_instance_from_scan(self) -> None:
        """Derive launch readiness exclusively from current Steam/MO2 filesystem scans."""
        instances = self.instance_state.get("skyrim_instances", [])
        installation = self.instance_state.get("installation", {}) or {}
        discovered_executable = (
            installation.get("executable")
            if installation.get("installed") else None
        )
        launch_executable_verified = bool(
            self.mo2_state.get("installed") and self.mo2_state.get("executable")
            or discovered_executable
        )
        if not self.game_state.get("found"):
            detail = "Skyrim Special Edition has not been detected."
        elif not launch_executable_verified:
            if instances:
                names = ", ".join(item.get("name", "Unnamed instance") for item in instances)
                detail = (
                    f"Discovered {len(instances)} Skyrim MO2 instance(s): {names}. "
                    "The MO2 executable is not verified, so modded launch is unavailable."
                )
            else:
                detail = self.instance_state.get(
                    "message", "No MO2 instance or verified MO2 installation was found."
                )
        elif instances:
            names = ", ".join(item.get("name", "Unnamed instance") for item in instances)
            detail = f"Automatically discovered {len(instances)} Skyrim MO2 instance(s): {names}"
        else:
            detail = "MO2 is installed, but no Skyrim Special Edition instance was found. Create one in MO2, then refresh."
        proton = bool(self.proton_combo.currentData())
        mapping_ok = bool(
            self.mo2_discovery_state
            and self.mo2_discovery_state.get("ok")
        )
        busy = bool(self.worker and self.worker.isRunning())
        ready = bool(
            self.startup_checks_complete
            and self.game_state.get("found")
            and launch_executable_verified
            and instances
            and proton
            and mapping_ok
            and not busy
        )
        self.active_instance_label.setText(detail)
        self.launch_game_button.setEnabled(ready)
        self.launch_direct_button.setEnabled(bool(self.game_state.get("found") and not busy))

    def manage_mo2_library(self) -> None:
        """Refresh the read-only MO2 inventory widgets from the current scan."""
        saved_game = self.game_state.get("game_dir", "")
        prefix = _steam_prefix_for_game_path(saved_game) if saved_game else None
        install_path = self.mo2_state.get("install") or (
            str(Path(self.mo2_state["executable"]).parent)
            if self.mo2_state.get("executable") else
            str(prefix / "pfx/drive_c/Modding/MO2") if prefix else ""
        )
        context = dict(
            prefix_path=str(prefix) if prefix else "",
            install_path=install_path or "",
            game_path=saved_game or "",
            proton_path=self.proton_combo.currentData() or "",
        )
        self.mo2_library_widget.update_context(**context)
        self.mo2_advanced_widget.update_context(**context)
        self.mo2_advanced_widget._refresh_registered()
