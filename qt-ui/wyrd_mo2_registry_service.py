"""Read-only MO2 discovery and explicit Wyrd registry registration.

The service only inspects user-selected, known roots. Discovery never writes
MO2 files or the registry. Registration writes Wyrd metadata only.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from wyrd_mo2_registry import RegistryError, load_registry, save_registry, validate_registry


class RegistryService:
    def __init__(self, registry_path: Path | str):
        self.registry_path = Path(registry_path).expanduser()

    def load(self) -> dict[str, Any]:
        return load_registry(self.registry_path)

    @staticmethod
    def _id(kind: str, path: Path) -> str:
        identity = os.path.normcase(os.path.realpath(str(path)))
        return f"{kind}-{hashlib.sha256(identity.encode()).hexdigest()[:16]}"

    @staticmethod
    def _marker_owner(root: Path) -> tuple[bool, str | None]:
        marker = root / ".wyrd-managed.json"
        if not marker.exists() and not marker.is_symlink():
            return False, None
        if marker.is_symlink() or not marker.is_file():
            raise RegistryError(f"Unsafe Wyrd ownership marker: {marker}")
        try:
            data = json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise RegistryError(f"Unreadable Wyrd ownership marker: {marker}") from error
        if not isinstance(data, dict) or data.get("managed_by") != "Wyrd":
            raise RegistryError(f"Unrecognized ownership marker: {marker}")
        return True, data.get("game_app_id")

    def discover(self, prefix_root: Path | str, install_root: Path | str) -> dict[str, Any]:
        """Inspect exactly one selected prefix and MO2 install root, without adoption."""
        from wyrd_mo2 import inspect_mo2_storage

        prefix = Path(prefix_root).expanduser()
        install = Path(install_root).expanduser()
        if prefix.is_symlink() or install.is_symlink():
            return self._failure("Symlinked prefix or installation root refused.")
        if not prefix.is_dir() or not install.is_dir():
            return self._failure("Selected prefix or installation root is missing.")
        users = prefix / "pfx/drive_c/users"
        if not users.is_dir() or users.is_symlink():
            return self._failure("Selected prefix is not an initialized Proton/Wine prefix.")
        executable = install / "ModOrganizer.exe"
        if executable.is_symlink() or not executable.is_file():
            return self._failure("Selected installation has no regular ModOrganizer.exe.")

        try:
            prefix_managed, prefix_app_id = self._marker_owner(prefix)
            install_managed, _ = self._marker_owner(install)
        except RegistryError as error:
            return self._failure(str(error))
        # Wyrd-managed Proton prefixes may belong to any registered game AppID.
        # The registry association, not a Skyrim-specific constant, binds the game.
        owner = "wyrd" if install_managed else "external"
        inventory = inspect_mo2_storage(prefix, install_root=install, require_managed=prefix_managed)
        if not inventory.get("ok"):
            return self._failure(inventory.get("message", "MO2 inspection failed."))

        installation = {
            "id": self._id("installation", executable),
            "executable": str(executable.resolve()),
            "owner": owner,
            "prefix_path": str(prefix.resolve(strict=True)),
        }
        proposals = []
        for item in inventory.get("instances", []):
            instance_path = Path(item.get("instance_path", ""))
            config = Path(item.get("config", ""))
            if not instance_path.is_dir() or instance_path.is_symlink():
                continue
            if config.is_symlink() or not config.is_file():
                continue
            resolved_instance = instance_path.resolve(strict=True)
            instance_type = "portable" if resolved_instance == install.resolve(strict=True) else "global"
            proposals.append({
                "id": self._id("instance", resolved_instance),
                "installation_id": installation["id"],
                "instance_path": str(resolved_instance),
                "instance_type": instance_type,
                "config": str(config.resolve(strict=True)),
                "game_name": item.get("game_name", ""),
                "is_skyrim_se": bool(item.get("is_skyrim_se")),
                "storage": item.get("storage", item.get("paths", {})),
                "owner": owner,
                "registration_status": "discovered-not-registered",
                "default_storage_policy": {
                    "mods": "instance-local", "downloads": "instance-local",
                    "cache": "instance-local", "profiles": "instance-local",
                    "overwrite": "instance-local",
                },
            })
        return {
            "ok": True, "installation": installation, "proposals": proposals,
            "shared_paths": inventory.get("shared_paths", []),
            "errors": inventory.get("errors", []),
            "message": inventory.get("message", "Read-only discovery complete."),
        }

    @staticmethod
    def _failure(message: str) -> dict[str, Any]:
        return {
            "ok": False, "installation": None, "proposals": [],
            "shared_paths": [], "errors": [], "message": message,
        }

    def replace_instance(self, updated: dict[str, Any]) -> dict[str, Any]:
        """Persist user-approved metadata changes for a registered instance."""
        current = self.load()
        for index, item in enumerate(current["instances"]):
            if item.get("id") == updated.get("id"):
                current["instances"][index] = dict(updated)
                validated = validate_registry(current)
                save_registry(self.registry_path, validated)
                return validated
        raise RegistryError("registered MO2 instance was not found")

    def rename_instance(self, instance_id: str, display_name: str) -> dict[str, Any]:
        """Update a Wyrd-only display label without changing any MO2 files."""
        if not isinstance(display_name, str):
            raise RegistryError("instance display name must be text")
        display_name = display_name.strip()
        if len(display_name) > 80 or any(ord(char) < 32 for char in display_name):
            raise RegistryError("instance display name must be 80 characters or fewer and contain no control characters")
        current = self.load()
        for index, instance in enumerate(current["instances"]):
            if instance.get("id") != instance_id:
                continue
            updated = dict(instance)
            if display_name:
                updated["display_name"] = display_name
            else:
                updated.pop("display_name", None)
            current["instances"][index] = updated
            validated = validate_registry(current)
            save_registry(self.registry_path, validated)
            return validated
        raise RegistryError(f"registered MO2 instance not found: {instance_id}")

    def unregister_instance(self, instance_id: str) -> dict[str, Any]:
        """Remove one Wyrd registry reference without touching MO2 or game files."""
        current = self.load()
        original_count = len(current["instances"])
        current["instances"] = [
            item for item in current["instances"] if item.get("id") != instance_id
        ]
        if len(current["instances"]) == original_count:
            raise RegistryError(f"registered MO2 instance not found: {instance_id}")
        used_games = {item.get("game_id") for item in current["instances"]}
        used_runtimes = {item.get("runtime_id") for item in current["instances"]}
        current["games"] = [item for item in current["games"] if item.get("id") in used_games]
        current["runtimes"] = [item for item in current["runtimes"] if item.get("id") in used_runtimes]
        validated = validate_registry(current)
        save_registry(self.registry_path, validated)
        return validated

    def unregister_installation(self, installation_id: str) -> dict[str, Any]:
        """Remove an unused installation reference; never delete its executable or files."""
        current = self.load()
        if not any(item.get("id") == installation_id for item in current["installations"]):
            raise RegistryError(f"registered MO2 installation not found: {installation_id}")
        if any(item.get("installation_id") == installation_id for item in current["instances"]):
            raise RegistryError("Cannot unregister this installation while registered instances still reference it.")
        current["installations"] = [
            item for item in current["installations"] if item.get("id") != installation_id
        ]
        validated = validate_registry(current)
        save_registry(self.registry_path, validated)
        return validated

    def update_associations(
        self, instance_id: str, *, game: dict[str, Any], runtime: dict[str, Any]
    ) -> dict[str, Any]:
        """Change only an existing instance's explicit game/runtime references."""
        if not isinstance(game, dict) or not isinstance(runtime, dict):
            raise RegistryError("game and runtime associations are required")
        current = self.load()
        for index, instance in enumerate(current["instances"]):
            if instance.get("id") != instance_id:
                continue
            for collection, record in (("games", dict(game)), ("runtimes", dict(runtime))):
                identifier = record.get("id")
                if not isinstance(identifier, str) or not identifier.strip():
                    raise RegistryError(f"{collection} association requires an ID")
                existing = next((item for item in current[collection] if item.get("id") == identifier), None)
                if existing is not None and existing != record:
                    if collection == "games":
                        stable_identity = (
                            existing.get("path") == record.get("path")
                            and str(existing.get("app_id") or "") == str(record.get("app_id") or "")
                        )
                    else:
                        stable_identity = existing.get("path") == record.get("path")
                    if not stable_identity:
                        raise RegistryError(f"{collection} ID already exists with a different game/runtime path")
                    current[collection][current[collection].index(existing)] = record
                elif existing is None:
                    current[collection].append(record)
            updated = dict(instance)
            updated["game_id"] = game["id"]
            updated["runtime_id"] = runtime["id"]
            updated["prefix_path"] = game.get("prefix_path") or updated.get("prefix_path")
            current["instances"][index] = updated
            validated = validate_registry(current)
            save_registry(self.registry_path, validated)
            return validated
        raise RegistryError(f"registered MO2 instance not found: {instance_id}")

    def register(
        self, installation: dict[str, Any], proposal: dict[str, Any],
        game: dict[str, Any], runtime: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Explicitly register one inspected proposal; no MO2 files are touched."""
        if proposal.get("registration_status") != "discovered-not-registered":
            raise RegistryError("Only a fresh discovery proposal can be registered.")
        if proposal.get("owner") not in ("wyrd", "external"):
            raise RegistryError("Proposal ownership is not verified.")
        if proposal.get("installation_id") != installation.get("id"):
            raise RegistryError("Proposal does not belong to the selected installation.")
        if installation.get("owner") != proposal.get("owner"):
            raise RegistryError("Installation and proposal ownership do not match.")
        if not isinstance(game, dict) or not isinstance(game.get("id"), str) or not game["id"].strip():
            raise RegistryError("An explicit game association is required.")

        current = self.load()
        if any(item.get("id") == proposal.get("id") for item in current["instances"]):
            raise RegistryError("This MO2 instance is already registered.")
        if any(os.path.normcase(os.path.realpath(item["instance_path"])) ==
               os.path.normcase(os.path.realpath(proposal["instance_path"]))
               for item in current["instances"]):
            raise RegistryError("This MO2 instance path is already registered.")

        installation_record = dict(installation)
        game_record = dict(game)
        game_record.setdefault("prefix_path", installation_record.get("prefix_path"))
        game_record.setdefault("steam_root", installation_record.get("steam_root"))
        runtime_record = dict(runtime) if runtime is not None else None
        instance = {
            "id": proposal["id"], "installation_id": installation_record["id"],
            "instance_path": proposal["instance_path"], "instance_type": proposal["instance_type"],
            "game_id": game_record["id"],
            "runtime_id": runtime_record["id"] if runtime_record else None,
            "storage_policy": dict(proposal["default_storage_policy"]),
            "config": proposal["config"],
            "prefix_path": installation_record.get("prefix_path"),
            "storage_paths": dict(proposal.get("storage", {})),
            "game_name": proposal.get("game_name", ""),
            "is_skyrim_se": bool(proposal.get("is_skyrim_se")),
        }
        for collection, record in (
            ("installations", installation_record), ("games", game_record),
            ("runtimes", runtime_record), ("instances", instance),
        ):
            if record is None:
                continue
            existing = next((item for item in current[collection] if item.get("id") == record.get("id")), None)
            if existing is not None and existing != record:
                raise RegistryError(f"{collection} ID already exists with different metadata.")
            if existing is None:
                current[collection].append(record)
        validated = validate_registry(current)
        save_registry(self.registry_path, validated)
        return validated
