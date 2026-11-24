"""Versioned, metadata-only registry for independent MO2 installations and instances.

The registry never discovers or adopts MO2 resources. Callers must explicitly
register records after presenting a read-only inspection to the user.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

REGISTRY_SCHEMA_VERSION = 1
REGISTRY_KIND = "wyrd-mo2-registry"


class RegistryError(ValueError):
    """Invalid or unsafe Wyrd MO2 registry data."""


def empty_registry() -> dict[str, Any]:
    return {
        "kind": REGISTRY_KIND,
        "schema_version": REGISTRY_SCHEMA_VERSION,
        "installations": [],
        "instances": [],
        "games": [],
        "runtimes": [],
    }


def _required_string(record: dict, key: str, context: str) -> str:
    value = record.get(key)
    if not isinstance(value, str) or not value.strip():
        raise RegistryError(f"{context}.{key} must be a non-empty string")
    return value.strip()


def _path_identity(value: str) -> str:
    # normcase is useful on Windows and harmless on Linux; realpath also makes
    # duplicate symlink spellings collide without modifying the target.
    return os.path.normcase(os.path.realpath(os.path.expanduser(value)))


def validate_registry(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise RegistryError("registry root must be an object")
    if data.get("kind") != REGISTRY_KIND:
        raise RegistryError("unrecognized registry kind")
    version = data.get("schema_version")
    if version != REGISTRY_SCHEMA_VERSION:
        raise RegistryError(f"unsupported registry schema version: {version!r}")

    normalized = {"kind": REGISTRY_KIND, "schema_version": REGISTRY_SCHEMA_VERSION}
    for collection in ("installations", "instances", "games", "runtimes"):
        records = data.get(collection, [])
        if not isinstance(records, list):
            raise RegistryError(f"{collection} must be an array")
        normalized[collection] = records

    installation_ids: set[str] = set()
    executable_paths: dict[str, str] = {}
    for index, item in enumerate(normalized["installations"]):
        context = f"installations[{index}]"
        if not isinstance(item, dict):
            raise RegistryError(f"{context} must be an object")
        record = dict(item)
        identifier = _required_string(record, "id", context)
        executable = _required_string(record, "executable", context)
        owner = _required_string(record, "owner", context)
        if owner not in ("wyrd", "external"):
            raise RegistryError(f"{context}.owner must be 'wyrd' or 'external'")
        if identifier in installation_ids:
            raise RegistryError(f"duplicate installation id: {identifier}")
        installation_ids.add(identifier)
        identity = _path_identity(executable)
        if identity in executable_paths:
            raise RegistryError(
                f"duplicate installation executable: {executable} "
                f"(already registered as {executable_paths[identity]})"
            )
        executable_paths[identity] = identifier

    instance_ids: set[str] = set()
    instance_paths: dict[str, str] = {}
    for index, item in enumerate(normalized["instances"]):
        context = f"instances[{index}]"
        if not isinstance(item, dict):
            raise RegistryError(f"{context} must be an object")
        record = dict(item)
        identifier = _required_string(record, "id", context)
        installation_id = _required_string(record, "installation_id", context)
        instance_path = _required_string(record, "instance_path", context)
        game_id = _required_string(record, "game_id", context)
        instance_type = _required_string(record, "instance_type", context)
        if installation_id not in installation_ids:
            raise RegistryError(
                f"{context} references unknown installation: {installation_id}"
            )
        if instance_type not in ("global", "portable"):
            raise RegistryError(f"{context}.instance_type must be 'global' or 'portable'")
        if identifier in instance_ids:
            raise RegistryError(f"duplicate instance id: {identifier}")
        instance_ids.add(identifier)
        identity = _path_identity(instance_path)
        if identity in instance_paths:
            raise RegistryError(
                f"duplicate instance path: {instance_path} "
                f"(already registered as {instance_paths[identity]})"
            )
        instance_paths[identity] = identifier
        # Storage policy is descriptive only. Shared storage must be explicitly
        # selected and never implies that Wyrd owns the referenced directories.
        policies = record.get("storage_policy", {})
        if not isinstance(policies, dict):
            raise RegistryError(f"{context}.storage_policy must be an object")
        for category, policy in policies.items():
            if category not in ("mods", "downloads", "cache", "profiles", "overwrite"):
                raise RegistryError(f"{context}.storage_policy has unknown category: {category}")
            if policy not in ("instance-local", "shared", "external"):
                raise RegistryError(f"{context}.storage_policy.{category} is invalid")
            if category == "overwrite" and policy == "shared":
                raise RegistryError(f"{context}.storage_policy.overwrite cannot be shared")
        runtime_id = record.get("runtime_id")
        if runtime_id is not None and (
            not isinstance(runtime_id, str) or not runtime_id.strip()
        ):
            raise RegistryError(f"{context}.runtime_id must be null or a non-empty string")

    game_ids: set[str] = set()
    for index, item in enumerate(normalized["games"]):
        context = f"games[{index}]"
        if not isinstance(item, dict):
            raise RegistryError(f"{context} must be an object")
        identifier = _required_string(item, "id", context)
        if identifier in game_ids:
            raise RegistryError(f"duplicate game id: {identifier}")
        game_ids.add(identifier)

    runtime_ids: set[str] = set()
    for index, item in enumerate(normalized["runtimes"]):
        context = f"runtimes[{index}]"
        if not isinstance(item, dict):
            raise RegistryError(f"{context} must be an object")
        identifier = _required_string(item, "id", context)
        if identifier in runtime_ids:
            raise RegistryError(f"duplicate runtime id: {identifier}")
        runtime_ids.add(identifier)

    for index, item in enumerate(normalized["instances"]):
        runtime_id = item.get("runtime_id")
        if runtime_id is not None and runtime_id not in runtime_ids:
            raise RegistryError(
                f"instances[{index}] references unknown runtime: {runtime_id}"
            )
        if item["game_id"] not in game_ids:
            raise RegistryError(
                f"instances[{index}] references unknown game: {item['game_id']}"
            )
    return normalized


def load_registry(path: Path | str) -> dict[str, Any]:
    """Load registry without creating its parent or file; missing means empty."""
    target = Path(path).expanduser()
    try:
        raw = target.read_text(encoding="utf-8")
    except FileNotFoundError:
        return empty_registry()
    except OSError as error:
        raise RegistryError(f"cannot read registry {target}: {error}") from error
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as error:
        raise RegistryError(f"invalid registry JSON at line {error.lineno}: {error.msg}") from error
    return validate_registry(data)


def save_registry(path: Path | str, data: dict[str, Any]) -> dict[str, Any]:
    """Explicitly save validated metadata using a same-directory atomic replace."""
    validated = validate_registry(data)
    target = Path(path).expanduser()
    parent = target.parent
    try:
        parent.mkdir(parents=True, exist_ok=True)
        fd, temporary_name = tempfile.mkstemp(
            prefix=f".{target.name}.", suffix=".tmp", dir=str(parent)
        )
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(validated, handle, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_name, target)
            try:
                directory_fd = os.open(parent, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
            except OSError:
                pass
        except Exception:
            try:
                os.close(fd)
            except OSError:
                pass
            try:
                os.unlink(temporary_name)
            except OSError:
                pass
            raise
    except OSError as error:
        raise RegistryError(f"cannot save registry {target}: {error}") from error
    return validated
