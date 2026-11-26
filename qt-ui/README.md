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
