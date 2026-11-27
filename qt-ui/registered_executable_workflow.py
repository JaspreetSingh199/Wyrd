"""Launch configured non-Skyrim executables through registered MO2 environments."""
from __future__ import annotations

import time
from pathlib import Path

from process_monitor import _proc_start_time
from proton_runtime import managed_paths


class RegisteredExecutableWorkflowMixin:
    def _launch_registered_custom_executable(
        self, record: dict, installation: dict, game: dict, runtime: dict, target: dict
    ) -> dict:
        """Start an MO2 custom executable under its registered Proton/game context."""
        import json
        import subprocess
        from datetime import datetime, timezone
        from wyrd_mo2 import inspect_proton_build, _runtime_environment

        executable = Path(installation.get("executable", "")).expanduser()
        instance_path = Path(record.get("instance_path", "")).expanduser()
        config = Path(record.get("config", "")).expanduser()
        prefix = Path(record.get("prefix_path") or installation.get("prefix_path") or "").expanduser()
        game_path = Path(game.get("path", "")).expanduser()
        proton = Path(runtime.get("path", "")).expanduser()
        expected_binary = Path(target.get("resolved_binary", "")).expanduser()
        title = str(target.get("title", "")).strip()
        if not title or executable.is_symlink() or not executable.is_file():
            return {"ok": False, "message": "MO2 executable or configured title is invalid."}
        if any(path.is_symlink() for path in (instance_path, config, prefix, game_path, proton, expected_binary)):
            return {"ok": False, "message": "A registered launch path became a symlink; launch refused."}
        if not instance_path.is_dir() or not config.is_file() or not game_path.is_dir():
            return {"ok": False, "message": "MO2 instance, configuration, or game directory is missing."}
        if not (prefix / "pfx/drive_c/users").is_dir() or not (proton / "proton").is_file():
            return {"ok": False, "message": "Registered Proton prefix or runtime is unavailable."}
        validation = inspect_proton_build(proton)
        if not validation.get("ok"):
            return {"ok": False, "message": validation.get("message", "Proton runtime validation failed.")}
        if not expected_binary.is_file():
            return {"ok": False, "message": "Configured game executable disappeared after selection."}

        # Re-read the actual MO2 INI at launch time. The executable list shown
        # before the confirmation dialog is only a preview and may have changed.
        from wyrd_mo2 import inspect_mo2_instances, _resolve_windows_path
        inventory = inspect_mo2_instances(
            prefix, executable.parent, require_managed=False,
            detected_game_path=Path(self.game_state["game_dir"]),
        )
        if not inventory.get("ok"):
            return {"ok": False, "message": inventory.get("message", "Could not re-inspect the selected MO2 prefix.")}
        live_instance = next(
            (item for item in inventory.get("instances", [])
             if Path(item.get("path", "")).resolve() == instance_path.resolve()),
            None,
        )
        if live_instance is None or Path(live_instance.get("config", "")).resolve() != config.resolve():
            return {"ok": False, "message": "The registered MO2 instance or its configuration changed after selection."}
        live_targets = []
        for entry in live_instance.get("executables", []):
            if str(entry.get("title", "")).strip() != title:
                continue
            binary = _resolve_windows_path(str(entry.get("binary", "")), prefix, game_path)
            if binary and binary.is_file() and not binary.is_symlink():
                live_targets.append(binary.resolve())
        if len(live_targets) != 1 or live_targets[0] != expected_binary.resolve():
            return {"ok": False, "message": "The selected executable is no longer uniquely verified in the current MO2 configuration."}
        try:
            config.resolve(strict=True).relative_to(instance_path.resolve(strict=True))
            expected_binary.resolve(strict=True)
        except (OSError, ValueError, RuntimeError):
            return {"ok": False, "message": "Instance configuration or game executable no longer resolves safely."}

        steam_root_text = game.get("steam_root")
        if steam_root_text:
            steam_root = Path(steam_root_text).expanduser()
        else:
            candidates = (
                Path.home() / ".steam/steam",
                Path.home() / ".local/share/Steam",
                Path.home() / ".steam/root",
            )
            steam_root = next((path for path in candidates if path.is_dir()), None)
        if steam_root is None or not steam_root.is_dir():
            return {"ok": False, "message": "A Steam client install root is needed by this Proton runtime but was not found."}

        env = _runtime_environment(proton, steam_root, prefix)
        app_id = str(game.get("app_id") or "").strip()
        if app_id:
            env.update({"SteamAppId": app_id, "SteamGameId": app_id, "STEAM_COMPAT_APP_ID": app_id})
        else:
            for key in ("SteamAppId", "SteamGameId", "STEAM_COMPAT_APP_ID"):
                env.pop(key, None)
        env["STEAM_COMPAT_DATA_PATH"] = str(prefix.resolve())
        import uuid
        launch_token = uuid.uuid4().hex
        env["WYRD_LAUNCHED"] = "1"
        env["WYRD_LAUNCH_ID"] = launch_token
        command = [
            str(proton / "proton"), "run", str(executable),
            "--instance", instance_path.name, "run", "--executable", title,
        ]
        logs = managed_paths()["root"] / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        log_path = logs / f"{record.get('id', 'instance')}-game-launch.log"
        try:
            with log_path.open("a", encoding="utf-8") as log:
                log.write("\n=== Wyrd registered game launch ===\n")
                log.write(f"timestamp: {datetime.now(timezone.utc).isoformat()}\n")
                log.write(f"game: {game.get('name')}\ninstance: {instance_path}\ntitle: {title}\n")
                log.write(f"binary: {expected_binary}\napp_id: {app_id or 'non-Steam'}\n")
                log.write("command: " + json.dumps(command) + "\n")
                log.flush()
                process = subprocess.Popen(
                    command, cwd=str(executable.parent), env=env,
                    stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
                log.write(f"wrapper_pid: {process.pid}\n")
                log.flush()
        except OSError as error:
            return {"ok": False, "message": f"Could not launch configured MO2 executable: {error}"}

        self.launch_session.wrapper_pid = process.pid
        self.launch_session.wrapper_start_time = _proc_start_time(process.pid)
        self.launch_session.log_path = str(log_path)
        self.launch_session.instance_name = instance_path.name
        self.launch_session.executable_title = title
        self.launch_session.started_at = time.time()
        self.launch_session.detected_processes = []
        self.launch_session.process_identities = {}
        self.launch_session.game_processes = []
        self.launch_session.prefix_root = str(prefix.resolve())
        self.launch_session.game_path = str(game_path.resolve())
        self.launch_session.app_id = app_id
        self.launch_session.target_binary = expected_binary.name
        self.launch_session.launch_token = launch_token
        self.launch_session.state = "handoff"
        self.launch_session.poll_count = 0
        self._launch_monitor.start()
        return {
            "ok": True, "pid": process.pid, "log_path": str(log_path),
            "message": f"Started '{title}' through MO2 instance '{instance_path.name}'.",
        }
