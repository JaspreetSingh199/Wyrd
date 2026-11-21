# Wyrd Qt6 desktop application

Run the GUI:

~~~bash
bash qt-ui/run.sh
~~~

Install a desktop menu entry:

~~~bash
bash qt-ui/install-desktop-launcher.sh
~~~

The active setup flow is entirely in Qt6. It detects Skyrim SE and compatible Steam Proton builds, installs the standard official MO2 7z release into Wyrd's user-local application folder, initializes a dedicated Proton prefix, and launches MO2 through that runtime.

The GUI requires Python 3, venv/pip support, and network access on first run. The project virtual environment installs PySide6 and py7zr automatically.

Wyrd does not call SteamTinkerLaunch to install or launch MO2. Existing MO2 installations outside Wyrd's managed location are detected where possible and are left untouched. The game path is registered only in Wyrd's dedicated prefix. No Steam launch options, Skyrim files, saves, game prefix, or existing MO2 instance are edited.

On first MO2 launch, create a Skyrim Special Edition instance in MO2's own instance picker. Select Skyrim or SKSE in MO2's executable dropdown and press Run to launch through MO2's virtual filesystem.


## Experimental Steam-native launcher

Wyrd can install an optional native desktop launcher intended to be added to
Steam manually as a non-Steam game. The launcher starts Wyrd natively; it does
not run Python/Qt through Proton. This is deliberate: Steam's Linux client
should own the native bootstrap process, while Wyrd continues to start MO2
through the selected Proton runtime.

Use **Install Steam launcher** in the Qt UI. Then in Steam choose **Games →
Add a Non-Steam Game**, select the Wyrd desktop launcher, and add it. This
first iteration does not edit Steam's binary shortcuts.vdf, create or change
compatibility settings, impersonate Skyrim's real AppID, or modify Skyrim's
Steam installation. Existing Steam entries remain untouched.

The integration writes a Wyrd-owned desktop file and an ownership marker under
`$XDG_DATA_HOME/applications` (normally `~/.local/share/applications`).
Use **Remove Steam launcher** to remove only those two owned files. It does not
remove the Steam Library shortcut you added manually; remove that entry in
Steam yourself.

This is an experimental handoff, not a verified Steam-managed Proton launch
for Skyrim. Do not enable Force Compatibility/Proton on the Wyrd desktop
launcher: Wyrd is a native Linux application. Audio improvement is not assumed.
