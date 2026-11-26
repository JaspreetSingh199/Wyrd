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

- `mo2_instances.py` discovers and validates MO2 instance configuration.
- `mo2_storage.py` inspects configured storage locations without modifying them.
- `mo2_game_mapping.py` verifies the existing Proton drive mapping before
  synchronizing MO2 game paths and Bethesda registry metadata.
- `mo2_launch.py` and `steam_mo2_launch.py` resolve and launch configured
  MO2 targets through Skyrim's existing Steam AppID 489830 prefix.
- `process_monitor.py` attributes game processes to a launch session;
  `launch_log_diagnostics.py` reads and classifies launch logs.
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
