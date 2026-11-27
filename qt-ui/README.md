# Wyrd Qt6 desktop application

Run the GUI:

~~~bash
bash qt-ui/run.sh
~~~

Install a desktop menu entry:

~~~bash
bash qt-ui/install-desktop-launcher.sh
~~~

Wyrd detects Skyrim SE from Steam libraries, discovers compatible Proton runtimes, and inspects MO2 installations and instances in the selected Skyrim Proton environment. The live read-only MO2 inventory is the source of truth for instance selection and normal launch readiness; users do not need to register an instance with Wyrd before launching it.

MO2 remains responsible for profiles, mods, downloads, load order and configured executables. Wyrd verifies the selected instance and target before handing off the launch through Skyrim's existing Steam AppID 489830 Proton prefix.

Wyrd does not reset the Skyrim prefix, modify saves, or silently change the selected Proton runtime. Existing external MO2 data is not adopted or rewritten during discovery. Custom storage paths are not migrated by changing INI values.

The GUI requires Python 3, venv/pip support, and network access on first run. The project virtual environment installs PySide6 and py7zr automatically.

## Internal structure

The Qt entry point in `main.py` composes focused UI/workflow mixins. Domain
operations remain outside the widgets:

- `mo2_instances.py` discovers and validates MO2 instance configuration;
  `mo2_storage.py` inspects configured storage locations without modifying them.
- `mo2_targets.py` resolves configured Skyrim/SKSE launch targets;
  `mo2_launch.py` and `steam_mo2_launch.py` perform launch handoffs through
  Skyrim's existing Steam AppID 489830 prefix.
- `mo2_game_mapping.py` verifies the existing Proton drive mapping before
  synchronizing MO2 game paths and Bethesda registry metadata.
- `process_monitor.py` attributes game processes to a launch session;
  `launch_log_diagnostics.py` reads and classifies launch logs.
- `wyrd_settings.py` owns Wyrd preference persistence. `wyrd_mo2.py` remains
  a compatibility export module; the legacy copy operation is isolated in
  `mo2_instance_copy.py` and is not part of normal discovery or launch.
- `environment_workflow.py`, `launch_workflow.py`, and the other
  `*_workflow.py` modules coordinate UI state and user actions.

Compatibility facades remain where existing callers depend on older import
paths. They should delegate to the focused modules rather than duplicate logic.

## CI during the V1 refactor

The GitHub Actions test job is temporarily opt-in while the refactor is in
progress. The tests remain in the repository but are skipped unless the
repository Actions variable `WYRD_RUN_TESTS` is set to `true`. The source
release-bundle job remains enabled. Re-enable the test job by setting that
repository variable after the refactor is ready for verification.



## Standalone Linux executable

The release workflow builds a single-file x86_64 Linux executable with PyInstaller
and publishes it inside `Wyrd-linux-x86_64.zip`.

1. Download and extract the ZIP.
2. Open the extracted `Wyrd-linux-x86_64` directory.
3. Run:

~~~bash
chmod +x Wyrd
./Wyrd
~~~

Python, PySide6 and the Qt application runtime are bundled. Wyrd does not bundle
Steam, Proton, Skyrim Special Edition, MO2, or external archive utilities; those
remain host-installed dependencies. The executable uses the user's existing
Steam libraries, Proton installations, MO2 data and Skyrim AppID 489830
compatdata prefix in their normal locations. It does not relocate game data,
reset the prefix, or move saves.

The binary targets x86_64 Linux with glibc. Compatible host graphics,
display-server and system libraries are still required. This is a standalone
application executable, not a fully static Linux binary.

The build is defined by `qt-ui/Wyrd.spec` and
`.github/workflows/standalone.yml`. Pushes to `main` that change application
or packaging files trigger a validation build; it can also be started manually
from GitHub Actions. These runs upload a temporary ZIP artifact for 30 days.

Versioned downloads are published through GitHub Releases. After the build and
smoke test are green, create and push a version tag from `main`, for example:

~~~bash
git switch main
git pull --ff-only origin main
git tag -a v0.1.0 -m "Wyrd v0.1.0"
git push origin v0.1.0
~~~

A tag matching `v*` runs the same executable build and validation, then creates
a GitHub Release with generated release notes and attaches
`Wyrd-linux-x86_64.zip`. Ordinary pushes do not publish releases.
