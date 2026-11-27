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


## Standalone Linux build (AppImage)

Wyrd can be distributed as a self-contained x86_64 AppImage. The AppImage bundles
the Python runtime, PySide6 and Qt libraries; users do not need to create a
virtual environment or install Python packages.

Download `Wyrd-x86_64.AppImage` from the GitHub Actions artifact or a published
release, then run:

~~~bash
chmod +x Wyrd-x86_64.AppImage
./Wyrd-x86_64.AppImage
~~~

The AppImage packages Wyrd itself, not the user's gaming environment. Steam,
the selected Proton runtime, Skyrim Special Edition, and any external archive
utilities remain host-installed dependencies. Wyrd continues to discover the
user's existing Steam libraries, Proton installations, MO2 instances, settings
and AppID 489830 prefix in their normal locations. The bundle does not contain,
copy, reset or relocate game data or saves.

The AppImage build is defined by `qt-ui/Wyrd.spec` and
`.github/workflows/appimage.yml`. It runs on Ubuntu 22.04 and publishes an
x86_64 AppImage artifact for 30 days. Use the workflow's manual dispatch to
build on demand, or push a packaging/application change to
`feature/mo2-environments`.
