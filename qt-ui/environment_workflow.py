"""Environment discovery and readiness workflow methods for MainWindow."""
from __future__ import annotations

from pathlib import Path

from skyrim_detection import detect_skyrim
from proton_runtime import discover_proton_builds
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
        self.active_launch_ready = False
        self._reset_precheck()
        selected = self.proton_combo.currentData()
        # Suppress currentIndexChanged while rebuilding the combo. Otherwise
        # clearing it can save an empty/default selection before we restore
        # the persisted runtime, overwriting the user's choice.
        self._restoring_selection = True
        self.proton_combo.blockSignals(True)
        scan_completed = False
        try:
            self._discover_game_installation()
            self._populate_proton_runtimes(selected)

            self._inspect_mo2_installation_and_mapping()
            self._discover_mo2_instance_inventory()
            self._present_mo2_installation()
            self._finalize_environment_readiness()
            scan_completed = True
        finally:
            # A failed stage must not leave the combo permanently signal-blocked
            # or allow a partial scan to persist a guessed runtime selection.
            self.proton_combo.blockSignals(False)
            if not scan_completed:
                self._restoring_selection = True

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
            self._log("Skyrim-prefix MO2 inspection: " + owner + " installation.")
        else:
            self.mo2_panel.detail_label.setText(self.mo2_state.get("message", "MO2 is not installed."))
            self.install_button.setText("Install MO2")
            self.install_button.show()
            self.install_button.setEnabled(bool(self.game_state.get("found") and self.proton_builds))

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
        active_ready = bool(getattr(self, 'active_launch_ready', False))
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
        """Delegate Steam/MO2 path verification to the environment service."""
        from mo2_game_mapping import verify_mo2_game_mapping

        state, messages = verify_mo2_game_mapping(
            game_path, Path(self.mo2_state["prefix"]), proton_value
        )
        for message in messages:
            self._log(message)
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
        # The row-level check also verifies that this exact instance has a
        # resolvable Skyrim/SKSE executable. A global environment pass alone
        # is not enough to enable a particular instance's launch button.
        launch_reasons = [
            (item, self.mo2_launch_block_reason(item)) for item in instances
        ]
        ready = any(reason is None for _item, reason in launch_reasons)
        blocked = [
            f"{item.get('name', 'Unnamed instance')}: {reason}"
            for item, reason in launch_reasons if reason
        ]
        if blocked:
            detail += "\nLaunch readiness: " + " | ".join(blocked)
        elif ready:
            detail += "\nAt least one Skyrim instance has a verified launch target."
        self.active_instance_label.setText(detail)
        self.active_launch_ready = ready
        self.mo2_library_widget.set_launch_ready(ready)

    def mo2_launch_block_reason(self, instance: dict) -> str | None:
        """Explain the first failed safety/readiness check for one instance."""
        if not instance.get("is_skyrim_se"):
            return "This MO2 instance is not configured for the detected Skyrim SE installation."
        if not self.startup_checks_complete:
            return "Environment scan is still running. Wait for it to finish or refresh."
        if not self.game_state.get("found"):
            return "Skyrim Special Edition was not detected."
        installation = self.instance_state.get("installation", {}) or {}
        executable_verified = bool(
            (self.mo2_state.get("installed") and self.mo2_state.get("executable"))
            or (installation.get("installed") and installation.get("executable"))
        )
        if not executable_verified:
            return self.mo2_state.get("message") or installation.get(
                "message", "A verified ModOrganizer.exe was not found."
            )
        if not self.proton_combo.currentData():
            return "No compatible Proton runtime is selected."
        discovery = getattr(self, "mo2_discovery_state", None)
        if not discovery or not discovery.get("ok"):
            return (discovery or {}).get(
                "message", "MO2's Skyrim Steam-drive mapping has not been verified. Refresh the environment."
            )
        if self.worker and self.worker.isRunning():
            return "Another Wyrd setup or installation task is running."
        try:
            from mo2_targets import inspect_skyrim_launch_targets
            game_path = Path(self.game_state["game_dir"]).expanduser()
            prefix = Path(self.mo2_state["prefix"])
            install_root = Path(self.mo2_state["install"])
            targets = inspect_skyrim_launch_targets(
                prefix, game_path, instance_name=instance.get("name"),
                install_root=install_root,
                require_managed=bool(self.mo2_state.get("managed", True)),
            )
            if not targets.get("ok") or not targets.get("targets"):
                return targets.get(
                    "message", "No verified Skyrim/SKSE executable is configured in this MO2 instance."
                )
        except (KeyError, OSError, RuntimeError, TypeError, ValueError) as error:
            return f"Could not verify this instance's launch target: {error}"
        return None

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
