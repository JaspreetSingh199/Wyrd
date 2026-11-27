# Wyrd V1 — installation and configuration

Wyrd is a Qt 6 Linux desktop application for configuring the Skyrim Special Edition + Steam Proton + Mod Organizer 2 environment and launching modded Skyrim through MO2.

## Before installing

- Linux x86_64 with a graphical desktop session.
- Steam for Linux installed for the current user.
- Skyrim Special Edition installed through Steam.
- Launch Skyrim once through Steam and allow Proton's first-run setup to finish. This creates/initializes the existing Steam AppID `489830` compatibility prefix required by Wyrd.
- A compatible Proton runtime installed through Steam.
- Network access for first-run Python dependencies and the official MO2 installer download.

See [Dependencies](../docs/DEPENDENCIES.md) for Python and Linux system-library requirements.

## Run from source

From the repository root:

```bash
bash qt-ui/run.sh
```

The launcher:
1. Creates the isolated `qt-ui/.venv` environment if it does not exist.
2. Installs `qt-ui/requirements.txt` when PySide6 is not already importable.
3. Starts `qt-ui/main.py`.

To install a desktop menu entry:

```bash
bash qt-ui/install-desktop-launcher.sh
```

The desktop entry runs the current checkout's `qt-ui/run.sh`. Do not move or delete the repository checkout while relying on that launcher. Re-run the installer after moving the checkout.

## First launch and environment setup

1. Start Wyrd and allow the initial read-only environment scan to finish.
2. Review the detected Skyrim Special Edition installation.
3. Review the discovered Proton runtimes and choose the runtime you intend to use. Wyrd remembers the selection while it remains available.
4. Complete the first-run MO2 setup when prompted. Wyrd downloads the official Mod Organizer 2 2.5.2 Windows installer and runs it inside Skyrim's existing Steam Proton prefix. The installer is interactive; complete its wizard.
5. Open the MO2 instance picker and select the Skyrim Special Edition instance you want Wyrd to use.
6. Confirm the instance has a profile and configured Skyrim/SKSE executable(s).
7. Use **Launch Modded Skyrim** and choose the configured executable. Wyrd verifies the target and hands off through its SteamCompat integration.

MO2 remains the owner of profiles, mods, downloads, load order, and virtual filesystem behaviour. Configure mods and executables in MO2 itself.

## Normal operation

- **Environment:** inspect Skyrim, Proton, MO2 instance/profile and launch readiness; open MO2 folders/Mod List; launch modded Skyrim or an unmodded direct Steam session.
- **Maintenance:** run environment checks, inspect MO2 installation, manage the Wyrd-managed MO2 program installation, reset Wyrd preferences, and inspect audio compatibility options.
- **Logs:** inspect application and launch diagnostics.

When a modded launch is requested, Wyrd ensures Steam is ready, verifies Skyrim's AppID `489830` manifest and existing prefix, rechecks MO2 instance/profile/target paths, and launches through the established SteamCompat path. If Steam is already ready and minimized, Wyrd avoids invoking Steam again so its window state is preserved.

## Data and safety

- Wyrd uses the existing `steamapps/compatdata/489830` prefix. It does not create, reset, or replace it.
- Wyrd does not copy, move, or symlink saves.
- Wyrd does not silently change Proton selection.
- MO2 instance discovery is rebuilt from the live filesystem inventory; no Wyrd registration is required for normal discovery.
- Removing Wyrd-managed MO2 removes only the verified MO2 program directory. MO2 instances, profiles, mods, downloads, custom storage, Skyrim files, Steam configuration, and saves are not the removal target.
- Resetting Wyrd settings resets Wyrd preferences. It does not uninstall MO2 or remove Skyrim/Steam data. First-run setup is deferred until the next application launch.

## Standalone release

Download `Wyrd-linux-x86_64.zip` from a successful [GitHub Actions build](https://github.com/JaspreetSingh199/Wyrd/actions) or a [GitHub Release](https://github.com/JaspreetSingh199/Wyrd/releases).

Extract it and run:

```bash
cd Wyrd-linux-x86_64
chmod +x Wyrd
./Wyrd
```

The standalone bundle includes Python, PySide6, and Qt application runtime. Steam, Proton, Skyrim, MO2, and compatible host graphics/display/system libraries remain external requirements.

## More help

- [Dependencies](../docs/DEPENDENCIES.md)
- [Known limitations and troubleshooting](../docs/TROUBLESHOOTING.md)
- [V1 scope](../docs/WYRD-V1-SCOPE.md)
- [V1 completion checklist](../docs/WYRD-V1-COMPLETION-CHECKLIST.md)
